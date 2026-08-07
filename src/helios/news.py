"""Milestone 5 news ingestion.

Sourcing rules this module enforces, not just documents:

* **You choose the publishers.** Helios ships zero enabled feeds. `config/news_feeds.yaml` is
  where you declare sources you are entitled to read.
* **Feeds and documented APIs only — never scraping.** Helios fetches the URL you configured and
  stores the headline, summary and link the publisher put in it. It does not follow the article
  link, and never fetches or stores article bodies, so paywalled or copyrighted text is never
  copied.
* **Attribution is mandatory.** Every stored item carries its source key, the publisher label you
  configured, and the original URL.
* **Instrument linkage is declared, never guessed.** An item is attached to a ticker/ISIN only
  because the source it came from was configured for that instrument. Helios does not scan
  headlines for company names.

## Why combining sources needs more than (url, published_at)

The single-source dedupe rule breaks the moment two sources cover the same story: Yahoo links to
`finance.yahoo.com/news/...`, Marketaux links to the publisher, and a publisher feed links to its
own canonical page — three different URLs for one article. So this module dedupes on two keys:

1. a **canonical URL** (tracking parameters stripped, host normalised), and
2. a **title key** (normalised headline) within a bounded time window.

When a duplicate is found, the copy from the **more trusted** source wins — a primary regulatory
filing outranks aggregator commentary about it. Trust is per-source and configurable.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Literal, Protocol
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

# Element is only a type here; parsing always goes through defusedxml below.
from xml.etree.ElementTree import Element

import httpx
import yaml
from defusedxml import ElementTree as SafeElementTree
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .config import Settings
from .models import NewsItem, RawNews
from .portfolio_repository import InstrumentNewsTarget, PortfolioRepository
from .rate_limit import Clock, SystemClock

ALLOWED_SCHEMES = frozenset({"http", "https"})
ATOM_NS = "{http://www.w3.org/2005/Atom}"

ProviderName = Literal["rss", "marketaux"]

#: Placeholders a `url_template` may contain. Every one is filled from data Helios already holds
#: about the instrument — nothing is inferred.
TICKER_PLACEHOLDER = "{ticker}"
YAHOO_PLACEHOLDER = "{yahoo_ticker}"
ISIN_PLACEHOLDER = "{isin}"
NAME_PLACEHOLDER = "{name}"
PLACEHOLDERS = (TICKER_PLACEHOLDER, YAHOO_PLACEHOLDER, ISIN_PLACEHOLDER, NAME_PLACEHOLDER)

#: Default trust by provider. Higher wins when the same story arrives from several sources.
#: Operators override per source with `trust:`.
DEFAULT_TRUST: dict[str, int] = {"rss": 50, "marketaux": 60}

#: Query parameters that identify a campaign, not a document. Stripped before comparing URLs.
TRACKING_PARAMS = frozenset(
    {
        "fbclid",
        "gclid",
        "igshid",
        "mc_cid",
        "mc_eid",
        "msclkid",
        "ref",
        "ref_src",
        "spm",
        "yclid",
    }
)

_NON_ALNUM = re.compile(r"[^0-9a-z]+")


class NewsFeedConfigError(ValueError):
    pass


class NewsFeedEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    key: str
    label: str
    provider: ProviderName = "rss"
    url: str | None = None
    url_template: str | None = None
    tickers: list[str] = Field(default_factory=list)
    isins: list[str] = Field(default_factory=list)
    enabled: bool = True
    #: Higher wins when the same story arrives from several sources. Defaults per provider.
    trust: int | None = None
    #: Some publishers (notably the SEC) require a User-Agent identifying a real contact.
    user_agent: str | None = None

    @model_validator(mode="after")
    def validate_target(self) -> NewsFeedEntry:
        if not self.key.strip() or not self.label.strip():
            raise ValueError("feed key and label must be non-blank")
        if self.provider == "marketaux":
            if self.url is not None or self.url_template is not None:
                raise ValueError("marketaux sources build their own URL; omit url/url_template")
            return self
        if (self.url is None) == (self.url_template is None):
            raise ValueError("each rss feed needs exactly one of 'url' or 'url_template'")
        if self.url_template is not None and not any(
            token in self.url_template for token in PLACEHOLDERS
        ):
            raise ValueError(f"url_template must contain one of {', '.join(PLACEHOLDERS)}")
        for candidate in (self.url, self.url_template):
            if candidate is None:
                continue
            scheme = urlsplit(candidate).scheme.lower()
            if scheme not in ALLOWED_SCHEMES:
                # Blocks file://, ftp:// and friends reaching the fetcher.
                raise ValueError(f"feed URL scheme must be http or https, got '{scheme or 'none'}'")
        return self

    @property
    def effective_trust(self) -> int:
        return self.trust if self.trust is not None else DEFAULT_TRUST.get(self.provider, 50)


class NewsFeedFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feeds: list[NewsFeedEntry] = Field(default_factory=list)


def load_news_feeds(path: Path) -> list[NewsFeedEntry]:
    """Load the operator's source list. A missing file simply means 'no sources configured'."""
    if not path.exists():
        return []
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        return []
    if not isinstance(raw, dict):
        raise NewsFeedConfigError("news feed file must be a mapping with a 'feeds' key")
    try:
        parsed = NewsFeedFile.model_validate(raw)
    except ValueError as error:
        raise NewsFeedConfigError(str(error)) from error
    keys = [entry.key for entry in parsed.feeds]
    duplicates = {key for key in keys if keys.count(key) > 1}
    if duplicates:
        raise NewsFeedConfigError(f"duplicate feed keys: {sorted(duplicates)}")
    return [entry for entry in parsed.feeds if entry.enabled]


@dataclass(frozen=True)
class FeedRequest:
    feed_key: str
    label: str
    provider: str
    url: str
    t212_ticker: str | None
    isin: str | None
    trust: int
    user_agent: str | None = None


@dataclass(frozen=True)
class FeedResponse:
    url: str
    http_status: int
    content_type: str | None
    body: str


@dataclass(frozen=True)
class ParsedNewsItem:
    headline: str
    url: str
    summary: str | None
    published_at: datetime | None


@dataclass(frozen=True)
class CollectedItem:
    """A parsed item plus the provenance needed to rank it against duplicates."""

    request: FeedRequest
    item: ParsedNewsItem
    raw_news_id: int | None


@dataclass(frozen=True)
class NewsSyncSummary:
    as_of: datetime
    feeds_configured: int
    feeds_fetched: int
    raw_stored: int
    items_parsed: int
    items_written: int
    duplicates_skipped: int
    cross_source_merges: int
    sources_used: list[str]
    failures: list[str]
    notes: list[str]


# ---------------------------------------------------------------------------
# URL canonicalisation and title keys — the basis of cross-source dedupe
# ---------------------------------------------------------------------------


def canonical_url(url: str) -> str:
    """Normalise a URL so the same article from two sources compares equal.

    Lowercases scheme and host, drops a leading `www.`, removes `utm_*` and known click-tracking
    parameters, sorts what remains, and drops the fragment. Deliberately conservative: path case
    and non-tracking query parameters are preserved, because plenty of sites route on them.
    """
    parts = urlsplit(url.strip())
    host = parts.hostname or ""
    if host.startswith("www."):
        host = host[4:]
    netloc = host
    if parts.port and parts.port not in (80, 443):
        netloc = f"{host}:{parts.port}"
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=False)
        if not key.lower().startswith("utm_") and key.lower() not in TRACKING_PARAMS
    ]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit(
        (parts.scheme.lower(), netloc, path, urlencode(sorted(query), doseq=True), "")
    )


def title_key(headline: str) -> str:
    """A normalised headline for near-duplicate matching across sources.

    Lowercases, strips everything that isn't alphanumeric, and collapses runs. Two sources
    rewriting punctuation or casing around the same headline collapse to one key; genuinely
    different headlines do not.
    """
    return _NON_ALNUM.sub(" ", headline.lower()).strip()


def dedupe_key(url: str, published_at: datetime | None) -> str:
    """Stable primary key for a stored article: canonical URL plus its timestamp."""
    stamp = published_at.astimezone(UTC).isoformat() if published_at is not None else ""
    return hashlib.sha256(f"{canonical_url(url)}\n{stamp}".encode()).hexdigest()


# ---------------------------------------------------------------------------
# Timestamps
# ---------------------------------------------------------------------------


def parse_published_at(value: str | None) -> datetime | None:
    """Normalise an RSS (RFC 822) or Atom/JSON (RFC 3339) timestamp to a tz-aware datetime.

    A timestamp without an offset is read as UTC. Feeds overwhelmingly publish tz-aware stamps;
    dropping the rare naive one would lose more than assuming the common case, so this is
    documented rather than silent.
    """
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    for parse in (_parse_rfc3339, _parse_rfc822):
        parsed = parse(text)
        if parsed is not None:
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


def _parse_rfc3339(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_rfc822(value: str) -> datetime | None:
    try:
        return parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Source adapters
# ---------------------------------------------------------------------------


class NewsSourceAdapter(Protocol):
    provider: str

    def plan(
        self, feed: NewsFeedEntry, targets: Sequence[InstrumentNewsTarget]
    ) -> tuple[list[FeedRequest], list[str]]: ...

    async def fetch(self, request: FeedRequest) -> FeedResponse | None: ...

    def parse(self, body: str, request: FeedRequest) -> list[ParsedNewsItem]: ...


class RssSourceAdapter:
    """RSS 2.0 and Atom.

    One adapter covers every feed-shaped source: publisher feeds, Yahoo Finance per-ticker
    headlines, SEC EDGAR filing feeds, and Google News search feeds. They differ only in URL
    template and trust.
    """

    provider = "rss"

    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        self._settings = settings
        self._client = client

    def plan(
        self, feed: NewsFeedEntry, targets: Sequence[InstrumentNewsTarget]
    ) -> tuple[list[FeedRequest], list[str]]:
        notes: list[str] = []
        if feed.url is not None:
            ticker = feed.tickers[0] if len(feed.tickers) == 1 else None
            isin = feed.isins[0] if len(feed.isins) == 1 else None
            if len(feed.tickers) > 1 or len(feed.isins) > 1:
                notes.append(
                    f"Source '{feed.key}' lists several instruments with a fixed url; its items "
                    "are stored as market-wide news rather than attributed to one of them."
                )
            if ticker is not None:
                match = next((t for t in targets if t.t212_ticker == ticker), None)
                isin = match.isin if match is not None else isin
            return (
                [
                    FeedRequest(
                        feed_key=feed.key,
                        label=feed.label,
                        provider=self.provider,
                        url=feed.url,
                        t212_ticker=ticker,
                        isin=isin,
                        trust=feed.effective_trust,
                        user_agent=feed.user_agent,
                    )
                ],
                notes,
            )

        template = feed.url_template
        assert template is not None  # guaranteed by NewsFeedEntry validation
        selected = (
            [t for t in targets if t.t212_ticker in set(feed.tickers)]
            if feed.tickers
            else list(targets)
        )
        if not selected:
            notes.append(f"Source '{feed.key}' has no instruments to expand against; skipped.")
            return [], notes
        requests: list[FeedRequest] = []
        for target in selected:
            url = _fill_template(template, target)
            if url is None:
                notes.append(
                    f"Source '{feed.key}' needs a field {target.t212_ticker} does not have "
                    "(unresolved mapping); skipped."
                )
                continue
            requests.append(
                FeedRequest(
                    feed_key=feed.key,
                    label=feed.label,
                    provider=self.provider,
                    url=url,
                    t212_ticker=target.t212_ticker,
                    isin=target.isin,
                    trust=feed.effective_trust,
                    user_agent=feed.user_agent,
                )
            )
        return requests, notes

    async def fetch(self, request: FeedRequest) -> FeedResponse | None:
        headers = {"user-agent": request.user_agent or self._settings.news_user_agent}
        response = await self._client.get(request.url, headers=headers)
        response.raise_for_status()
        body = _bounded_body(response.text, self._settings.news_max_body_bytes)
        return FeedResponse(
            url=request.url,
            http_status=response.status_code,
            content_type=response.headers.get("content-type"),
            body=body,
        )

    def parse(self, body: str, request: FeedRequest) -> list[ParsedNewsItem]:
        del request
        return parse_feed(body, max_items=self._settings.news_max_items_per_feed)


class MarketauxSourceAdapter:
    """Marketaux `news/all` — a documented JSON API with entity tagging.

    Its free tier is 100 requests/day, so this issues **one** request covering every configured
    symbol rather than one per instrument. Items come back tagged with the symbols they mention,
    and that tagging — the provider's, not ours — is what attributes each item.
    """

    provider = "marketaux"

    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        self._settings = settings
        self._client = client

    def plan(
        self, feed: NewsFeedEntry, targets: Sequence[InstrumentNewsTarget]
    ) -> tuple[list[FeedRequest], list[str]]:
        if self._settings.news_marketaux_api_key is None:
            return [], [f"Source '{feed.key}' needs HELIOS_NEWS_MARKETAUX_API_KEY; skipped."]
        selected = (
            [t for t in targets if t.t212_ticker in set(feed.tickers)]
            if feed.tickers
            else list(targets)
        )
        symbols = [t.yahoo_ticker for t in selected if t.yahoo_ticker]
        if not symbols:
            return [], [f"Source '{feed.key}' has no resolved provider symbols to query; skipped."]
        query = urlencode(
            {
                "symbols": ",".join(sorted(set(symbols))),
                "filter_entities": "true",
                "language": "en",
                "limit": str(min(self._settings.news_max_items_per_feed, 100)),
            }
        )
        # The key is appended at fetch time so it never reaches the stored raw URL.
        return (
            [
                FeedRequest(
                    feed_key=feed.key,
                    label=feed.label,
                    provider=self.provider,
                    url=f"{self._settings.news_marketaux_base_url}/news/all?{query}",
                    t212_ticker=None,
                    isin=None,
                    trust=feed.effective_trust,
                )
            ],
            [],
        )

    async def fetch(self, request: FeedRequest) -> FeedResponse | None:
        key = self._settings.news_marketaux_api_key
        if key is None:
            return None
        response = await self._client.get(
            request.url,
            params={"api_token": key.get_secret_value()},
            headers={"user-agent": self._settings.news_user_agent},
        )
        response.raise_for_status()
        body = _bounded_body(response.text, self._settings.news_max_body_bytes)
        return FeedResponse(
            # The stored URL is the key-free one, so the credential never lands in raw_news.
            url=request.url,
            http_status=response.status_code,
            content_type=response.headers.get("content-type"),
            body=body,
        )

    def parse(self, body: str, request: FeedRequest) -> list[ParsedNewsItem]:
        del request
        try:
            payload = json.loads(body)
        except ValueError as error:
            raise ValueError(
                f"unparseable marketaux payload: {error.__class__.__name__}"
            ) from error
        rows = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            return []
        items: list[ParsedNewsItem] = []
        for row in rows[: self._settings.news_max_items_per_feed]:
            if not isinstance(row, dict):
                continue
            headline = row.get("title")
            url = row.get("url")
            if not isinstance(headline, str) or not isinstance(url, str):
                continue
            if urlsplit(url).scheme.lower() not in ALLOWED_SCHEMES:
                continue
            summary = row.get("description") or row.get("snippet")
            items.append(
                ParsedNewsItem(
                    headline=headline.strip(),
                    url=url,
                    summary=summary.strip()
                    if isinstance(summary, str) and summary.strip()
                    else None,
                    published_at=parse_published_at(row.get("published_at")),
                )
            )
        return items


def _fill_template(template: str, target: InstrumentNewsTarget) -> str | None:
    """Substitute placeholders; return None when the instrument lacks a required field."""
    values = {
        TICKER_PLACEHOLDER: target.t212_ticker,
        YAHOO_PLACEHOLDER: target.yahoo_ticker,
        ISIN_PLACEHOLDER: target.isin,
        NAME_PLACEHOLDER: target.name,
    }
    url = template
    for token, value in values.items():
        if token not in url:
            continue
        if not value:
            return None
        url = url.replace(token, quote(value, safe=""))
    return url


def _bounded_body(body: str, limit: int) -> str:
    if len(body.encode("utf-8", errors="ignore")) > limit:
        raise ValueError(f"response body exceeds {limit} bytes")
    return body


# ---------------------------------------------------------------------------
# Feed parsing
# ---------------------------------------------------------------------------


def parse_feed(body: str, *, max_items: int) -> list[ParsedNewsItem]:
    """Parse RSS 2.0 or Atom into a common shape, using a hardened XML parser.

    Feed bodies are untrusted remote input, so this goes through `defusedxml` — plain
    ElementTree would still expand internal entities (billion-laughs).
    """
    if not body.strip():
        return []
    try:
        root = SafeElementTree.fromstring(body)
    except Exception as error:  # defusedxml raises its own subclasses plus ParseError
        raise ValueError(f"unparseable feed: {error.__class__.__name__}") from error

    elements = [*root.iter("item"), *root.iter(f"{ATOM_NS}entry")]
    parsed: list[ParsedNewsItem] = []
    for element in elements[:max_items]:
        headline = _text(element, "title") or _text(element, f"{ATOM_NS}title")
        url = _text(element, "link") or _atom_link(element)
        if not headline or not url:
            # No headline or no link means we could neither attribute nor link out to it.
            continue
        if urlsplit(url).scheme.lower() not in ALLOWED_SCHEMES:
            continue
        summary = (
            _text(element, "description")
            or _text(element, f"{ATOM_NS}summary")
            or _text(element, f"{ATOM_NS}content")
        )
        published = parse_published_at(
            _text(element, "pubDate")
            or _text(element, f"{ATOM_NS}published")
            or _text(element, f"{ATOM_NS}updated")
        )
        parsed.append(
            ParsedNewsItem(headline=headline, url=url, summary=summary, published_at=published)
        )
    return parsed


def _text(element: Element, tag: str) -> str | None:
    found = element.find(tag)
    if found is None or found.text is None:
        return None
    stripped = found.text.strip()
    return stripped or None


def _atom_link(element: Element) -> str | None:
    for link in element.iterfind(f"{ATOM_NS}link"):
        if link.get("rel") in (None, "alternate"):
            href = link.get("href")
            if href:
                return href
    return None


# ---------------------------------------------------------------------------
# Cross-source merge
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MergeResult:
    kept: list[CollectedItem]
    merged: int


def merge_collected(collected: Sequence[CollectedItem], *, window_hours: int) -> MergeResult:
    """Collapse the same story arriving from several sources down to the most trusted copy.

    Two items are the same story when their canonical URLs match, or when their title keys match
    and their timestamps are within ``window_hours`` of each other. Undated items match on title
    alone — a source that omits timestamps would otherwise duplicate on every sync.

    Ordering matters: the highest-trust copy is processed first and therefore wins, so a primary
    filing outranks the aggregator commentary that quotes it.
    """
    window = timedelta(hours=window_hours)
    ordered = sorted(
        collected,
        key=lambda entry: (
            -entry.request.trust,
            entry.item.published_at is None,
            -(entry.item.published_at or datetime.min.replace(tzinfo=UTC)).timestamp(),
            entry.item.headline,
        ),
    )
    kept: list[CollectedItem] = []
    by_url: dict[str, CollectedItem] = {}
    by_title: dict[str, list[CollectedItem]] = {}
    merged = 0
    for entry in ordered:
        url_key = canonical_url(entry.item.url)
        if url_key in by_url:
            merged += 1
            continue
        key = title_key(entry.item.headline)
        if key and _title_conflict(entry, by_title.get(key, []), window):
            merged += 1
            continue
        by_url[url_key] = entry
        if key:
            by_title.setdefault(key, []).append(entry)
        kept.append(entry)
    return MergeResult(kept=kept, merged=merged)


def _title_conflict(entry: CollectedItem, seen: Sequence[CollectedItem], window: timedelta) -> bool:
    for other in seen:
        left = entry.item.published_at
        right = other.item.published_at
        if left is None or right is None:
            # At least one side is undated: the headline is all we have to go on.
            return True
        if abs(left - right) <= window:
            return True
    return False


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class NewsSyncService:
    def __init__(
        self,
        repository: PortfolioRepository,
        settings: Settings,
        adapters: Mapping[str, NewsSourceAdapter] | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._repository = repository
        self._settings = settings
        self._adapters = dict(adapters or {})
        self._clock = clock or SystemClock()

    async def sync(self) -> NewsSyncSummary:
        now = self._clock.utcnow()
        empty = NewsSyncSummary(
            as_of=now,
            feeds_configured=0,
            feeds_fetched=0,
            raw_stored=0,
            items_parsed=0,
            items_written=0,
            duplicates_skipped=0,
            cross_source_merges=0,
            sources_used=[],
            failures=[],
            notes=[],
        )
        try:
            feeds = load_news_feeds(self._settings.news_feeds_path)
        except NewsFeedConfigError as error:
            return replace(empty, failures=[f"news feed config invalid: {error}"])
        if not feeds:
            return replace(
                empty,
                notes=[
                    "No news sources configured. Enable the presets in "
                    f"{self._settings.news_feeds_path}."
                ],
            )

        targets = await self._repository.list_instrument_news_targets()
        collected: list[CollectedItem] = []
        notes: list[str] = []
        failures: list[str] = []
        fetched = 0
        raw_stored = 0
        parsed_total = 0
        sources_used: set[str] = set()

        for feed in feeds:
            adapter = self._adapters.get(feed.provider)
            if adapter is None:
                failures.append(f"{feed.key}: no adapter for provider '{feed.provider}'")
                continue
            requests, plan_notes = adapter.plan(feed, targets)
            notes.extend(plan_notes)
            for request in requests:
                try:
                    response = await adapter.fetch(request)
                except Exception as error:
                    # A single bad publisher must not abort the whole sync.
                    failures.append(f"{request.feed_key}: {error.__class__.__name__}")
                    continue
                if response is None:
                    continue
                fetched += 1
                raw_id = await self._repository.insert_raw_news(
                    RawNews(
                        feed_key=request.feed_key,
                        url=response.url,
                        ts=now,
                        http_status=response.http_status,
                        content_type=response.content_type,
                        body=response.body,
                    )
                )
                raw_stored += 1
                try:
                    items = adapter.parse(response.body, request)
                except ValueError as error:
                    failures.append(f"{request.feed_key}: {error}")
                    continue
                parsed_total += len(items)
                if items:
                    sources_used.add(request.label)
                collected.extend(
                    CollectedItem(request=request, item=item, raw_news_id=raw_id) for item in items
                )

        merge = merge_collected(collected, window_hours=self._settings.news_dedupe_window_hours)
        rows = [
            NewsItem(
                dedupe_key=dedupe_key(entry.item.url, entry.item.published_at),
                feed_key=entry.request.feed_key,
                provider=entry.request.provider,
                source_label=entry.request.label,
                t212_ticker=entry.request.t212_ticker,
                isin=entry.request.isin,
                headline=entry.item.headline,
                summary=entry.item.summary,
                url=entry.item.url,
                canonical_url=canonical_url(entry.item.url),
                title_key=title_key(entry.item.headline),
                published_at=entry.item.published_at,
                fetched_at=now,
                raw_news_id=entry.raw_news_id,
            )
            for entry in merge.kept
        ]
        written = await self._repository.upsert_news_items(
            rows, dedupe_window_hours=self._settings.news_dedupe_window_hours
        )
        return NewsSyncSummary(
            as_of=now,
            feeds_configured=len(feeds),
            feeds_fetched=fetched,
            raw_stored=raw_stored,
            items_parsed=parsed_total,
            items_written=written,
            duplicates_skipped=len(rows) - written,
            cross_source_merges=merge.merged,
            sources_used=sorted(sources_used),
            failures=failures,
            notes=sorted(set(notes)),
        )

    async def list_news(
        self,
        *,
        t212_ticker: str | None = None,
        isin: str | None = None,
        limit: int = 50,
    ) -> list[NewsItem]:
        return await self._repository.list_news_items(
            t212_ticker=t212_ticker, isin=isin, limit=limit
        )


def build_news_adapters(
    settings: Settings, client: httpx.AsyncClient
) -> dict[str, NewsSourceAdapter]:
    return {
        "rss": RssSourceAdapter(settings, client),
        "marketaux": MarketauxSourceAdapter(settings, client),
    }


class NewsHttpClient:
    """Owns the shared httpx client so the container can close it on shutdown."""

    def __init__(self, settings: Settings) -> None:
        self.client = httpx.AsyncClient(
            timeout=settings.news_timeout_seconds,
            follow_redirects=True,
        )
        self.adapters = build_news_adapters(settings, self.client)

    async def aclose(self) -> None:
        await self.client.aclose()


def iter_placeholders(template: str) -> Iterable[str]:
    return (token for token in PLACEHOLDERS if token in template)
