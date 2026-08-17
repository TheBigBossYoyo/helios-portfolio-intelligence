from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from helios.config import Settings
from helios.db import migrate_database
from helios.models import Instrument, NewsItem, PositionLive, RawNews
from helios.news import (
    CollectedItem,
    FeedRequest,
    FeedResponse,
    MarketauxSourceAdapter,
    NewsFeedConfigError,
    NewsFeedEntry,
    NewsSyncService,
    ParsedNewsItem,
    RssSourceAdapter,
    canonical_url,
    dedupe_key,
    load_news_feeds,
    merge_collected,
    parse_feed,
    parse_published_at,
    title_key,
)
from helios.portfolio_repository import InstrumentNewsTarget, PortfolioRepository
from helios.rate_limit import Clock

NOW = datetime(2024, 5, 1, 12, 0, tzinfo=UTC)

TARGETS = [
    InstrumentNewsTarget("AAPL_US_EQ", "US0378331005", "AAPL", "Apple Inc."),
    InstrumentNewsTarget("SHEL_EQ", "GB00BP6MXD84", None, "Shell plc"),
]


class FixedClock(Clock):
    def now(self) -> float:
        return NOW.timestamp()

    def utcnow(self) -> datetime:
        return NOW

    async def sleep(self, seconds: float) -> None:
        del seconds


class FakeAdapter:
    """Serves canned bodies per URL; a URL mapped to an exception raises it."""

    provider = "rss"

    def __init__(self, bodies: dict[str, str | Exception]) -> None:
        self.bodies = bodies
        self.requested: list[str] = []

    def plan(
        self, feed: NewsFeedEntry, targets: Sequence[InstrumentNewsTarget]
    ) -> tuple[list[FeedRequest], list[str]]:
        return RssSourceAdapter.plan(self, feed, targets)  # type: ignore[arg-type]

    async def fetch(self, request: FeedRequest) -> FeedResponse | None:
        self.requested.append(request.url)
        body = self.bodies.get(request.url)
        if body is None:
            return None
        if isinstance(body, Exception):
            raise body
        return FeedResponse(
            url=request.url, http_status=200, content_type="application/rss+xml", body=body
        )

    def parse(self, body: str, request: FeedRequest) -> list[ParsedNewsItem]:
        del request
        return parse_feed(body, max_items=100)


RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Example Markets</title>
  <item>
    <title>Apple beats expectations</title>
    <link>https://example.com/a</link>
    <description>Quarterly results came in ahead.</description>
    <pubDate>Wed, 01 May 2024 09:30:00 +0000</pubDate>
  </item>
  <item>
    <title>Shell announces buyback</title>
    <link>https://example.com/b</link>
    <pubDate>Tue, 30 Apr 2024 16:00:00 +0200</pubDate>
  </item>
</channel></rss>
"""

ATOM = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>Atom headline</title>
    <link href="https://example.com/atom-1" rel="alternate"/>
    <summary>Atom summary text.</summary>
    <published>2024-05-01T08:15:00Z</published>
  </entry>
</feed>
"""

MARKETAUX_JSON = """{"data": [
  {"title": "Apple beats expectations", "url": "https://publisher.example/apple",
   "description": "Publisher wording.", "published_at": "2024-05-01T09:35:00Z"},
  {"title": "Unrelated story", "url": "https://publisher.example/other",
   "published_at": "2024-05-01T10:00:00Z"}
]}"""


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


SHIPPED_FEEDS = Path(__file__).resolve().parents[1] / "config" / "news_feeds.yaml"


def test_shipped_feed_file_enables_only_sources_needing_no_credential() -> None:
    """Helios may switch on a source that costs nothing and asks for nothing.

    It must never enable one that spends a credential or signs the operator up to a service, so
    the credentialled sources stay off until the operator turns them on deliberately.
    """
    enabled = {entry.key for entry in load_news_feeds(SHIPPED_FEEDS)}

    assert enabled == {"yahoo-finance", "google-news"}


def test_sec_feed_stays_skipped_until_a_contact_user_agent_is_configured() -> None:
    """The SEC's access policy requires a real contact.

    Fetching with a generic agent risks the deployment being blocked, so an unset contact means
    the feed is dropped rather than fetched anonymously.
    """
    without = {entry.key for entry in load_news_feeds(SHIPPED_FEEDS)}
    assert "sec-edgar" not in without

    feeds = load_news_feeds(SHIPPED_FEEDS, contact_user_agent="A Person a@example.com")
    sec = next(entry for entry in feeds if entry.key == "sec-edgar")

    assert sec.user_agent == "A Person a@example.com"


def test_shipped_feed_file_never_contains_an_email_address() -> None:
    """The contact is read from the environment; a personal address must not reach the repo."""
    text = SHIPPED_FEEDS.read_text(encoding="utf-8")

    assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text.replace("your@email.com", ""))


def test_rss_feed_requires_exactly_one_target(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "  - key: a\n    label: A\n    url: https://x/f\n    url_template: https://x/{ticker}\n",
    )

    with pytest.raises(NewsFeedConfigError, match="exactly one of"):
        load_news_feeds(path)


def test_marketaux_source_rejects_a_url(tmp_path: Path) -> None:
    path = _write(
        tmp_path, "  - key: m\n    label: M\n    provider: marketaux\n    url: https://x/f\n"
    )

    with pytest.raises(NewsFeedConfigError, match="build their own URL"):
        load_news_feeds(path)


def test_feed_rejects_non_http_schemes(tmp_path: Path) -> None:
    path = _write(tmp_path, "  - key: a\n    label: A\n    url: file:///etc/passwd\n")

    with pytest.raises(NewsFeedConfigError, match="http or https"):
        load_news_feeds(path)


def test_template_must_contain_a_known_placeholder(tmp_path: Path) -> None:
    path = _write(tmp_path, "  - key: a\n    label: A\n    url_template: https://x/static\n")

    with pytest.raises(NewsFeedConfigError, match="url_template must contain"):
        load_news_feeds(path)


def test_duplicate_feed_keys_are_rejected(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "  - key: a\n    label: A\n    url: https://x/1\n"
        "  - key: a\n    label: B\n    url: https://x/2\n",
    )

    with pytest.raises(NewsFeedConfigError, match="duplicate feed keys"):
        load_news_feeds(path)


def test_yaml_reserved_words_as_keys_fail_loudly(tmp_path: Path) -> None:
    """`on`/`off`/`yes`/`no` are booleans in YAML 1.1 — reject rather than coerce silently."""
    path = _write(tmp_path, "  - key: on\n    label: On\n    url: https://x/1\n")

    with pytest.raises(NewsFeedConfigError, match="valid string"):
        load_news_feeds(path)


def test_trust_defaults_per_provider_and_can_be_overridden(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "  - key: a\n    label: A\n    url: https://x/1\n"
        "  - key: b\n    label: B\n    url: https://x/2\n    trust: 95\n"
        "  - key: c\n    label: C\n    provider: marketaux\n",
    )

    feeds = {feed.key: feed.effective_trust for feed in load_news_feeds(path)}

    assert feeds == {"a": 50, "b": 95, "c": 60}


# ---------------------------------------------------------------------------
# Template expansion — linkage is declared, never guessed
# ---------------------------------------------------------------------------


def test_yahoo_template_uses_the_resolved_market_symbol(tmp_path: Path) -> None:
    """{yahoo_ticker} is the mapped symbol from M2, not the Trading 212 ticker."""
    path = _write(
        tmp_path,
        "  - key: yahoo\n    label: Yahoo\n"
        "    url_template: https://feeds.example/rss?s={yahoo_ticker}\n",
    )
    adapter = RssSourceAdapter(Settings(), None)  # type: ignore[arg-type]

    requests, notes = adapter.plan(load_news_feeds(path)[0], TARGETS)

    assert [request.url for request in requests] == ["https://feeds.example/rss?s=AAPL"]
    assert requests[0].t212_ticker == "AAPL_US_EQ"
    assert requests[0].isin == "US0378331005"
    # SHEL has no resolved symbol, so it is skipped with a stated reason rather than guessed.
    assert any("SHEL_EQ" in note for note in notes)


def test_name_template_is_url_encoded(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "  - key: g\n    label: G\n    url_template: https://news.example/rss?q={name}\n",
    )
    adapter = RssSourceAdapter(Settings(), None)  # type: ignore[arg-type]

    requests, _ = adapter.plan(load_news_feeds(path)[0], TARGETS[:1])

    assert requests[0].url == "https://news.example/rss?q=Apple%20Inc."


def test_fixed_feed_with_many_instruments_is_not_attributed_to_one(tmp_path: Path) -> None:
    """Attributing a shared feed to one ticker would invent a link that is not in the data."""
    path = _write(
        tmp_path,
        "  - key: mixed\n    label: Mixed\n    url: https://x/f.xml\n"
        "    tickers: [AAPL_US_EQ, SHEL_EQ]\n",
    )
    adapter = RssSourceAdapter(Settings(), None)  # type: ignore[arg-type]

    requests, notes = adapter.plan(load_news_feeds(path)[0], TARGETS)

    assert requests[0].t212_ticker is None
    assert any("market-wide" in note for note in notes)


def test_marketaux_plan_issues_one_request_for_all_symbols(tmp_path: Path) -> None:
    """The free tier is 100 requests/day, so all holdings go in one query."""
    path = _write(tmp_path, "  - key: m\n    label: Marketaux\n    provider: marketaux\n")
    settings = Settings(news_marketaux_api_key=SecretStr("k"))
    adapter = MarketauxSourceAdapter(settings, None)  # type: ignore[arg-type]

    requests, notes = adapter.plan(load_news_feeds(path)[0], TARGETS)

    assert len(requests) == 1
    assert "symbols=AAPL" in requests[0].url
    assert notes == []


def test_marketaux_plan_skips_without_a_key(tmp_path: Path) -> None:
    path = _write(tmp_path, "  - key: m\n    label: Marketaux\n    provider: marketaux\n")
    adapter = MarketauxSourceAdapter(Settings(), None)  # type: ignore[arg-type]

    requests, notes = adapter.plan(load_news_feeds(path)[0], TARGETS)

    assert requests == []
    assert any("MARKETAUX_API_KEY" in note for note in notes)


def test_marketaux_request_url_carries_no_credential(tmp_path: Path) -> None:
    """The API token is attached at fetch time so it never reaches raw_news."""
    path = _write(tmp_path, "  - key: m\n    label: Marketaux\n    provider: marketaux\n")
    settings = Settings(news_marketaux_api_key=SecretStr("super-secret"))
    adapter = MarketauxSourceAdapter(settings, None)  # type: ignore[arg-type]

    requests, _ = adapter.plan(load_news_feeds(path)[0], TARGETS)

    assert "super-secret" not in requests[0].url
    assert "api_token" not in requests[0].url


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def test_parses_rss_and_atom() -> None:
    rss = parse_feed(RSS, max_items=10)
    atom = parse_feed(ATOM, max_items=10)

    assert [item.headline for item in rss] == [
        "Apple beats expectations",
        "Shell announces buyback",
    ]
    assert rss[0].summary == "Quarterly results came in ahead."
    assert atom[0].url == "https://example.com/atom-1"
    assert atom[0].published_at == datetime(2024, 5, 1, 8, 15, tzinfo=UTC)


def test_marketaux_parses_its_json() -> None:
    adapter = MarketauxSourceAdapter(Settings(), None)  # type: ignore[arg-type]

    items = adapter.parse(MARKETAUX_JSON, _request("m", "https://x", trust=60))

    assert [item.headline for item in items] == ["Apple beats expectations", "Unrelated story"]
    assert items[0].summary == "Publisher wording."
    assert items[1].summary is None


def test_marketaux_rejects_malformed_json() -> None:
    adapter = MarketauxSourceAdapter(Settings(), None)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="unparseable marketaux"):
        adapter.parse("{not json", _request("m", "https://x", trust=60))


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2024-05-01T08:15:00Z", datetime(2024, 5, 1, 8, 15, tzinfo=UTC)),
        ("Wed, 01 May 2024 09:30:00 +0000", datetime(2024, 5, 1, 9, 30, tzinfo=UTC)),
        # No offset: documented to be read as UTC rather than dropped.
        ("2024-05-01T08:15:00", datetime(2024, 5, 1, 8, 15, tzinfo=UTC)),
        ("not a date", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_published_at(raw: str | None, expected: datetime | None) -> None:
    parsed = parse_published_at(raw)

    if expected is None:
        assert parsed is None
    else:
        assert parsed is not None
        assert parsed.astimezone(UTC) == expected


def test_parse_drops_items_without_a_headline_or_link() -> None:
    body = """<rss version="2.0"><channel>
      <item><title>No link</title></item>
      <item><link>https://example.com/no-title</link></item>
      <item><title>Good</title><link>https://example.com/good</link></item>
    </channel></rss>"""

    assert [item.headline for item in parse_feed(body, max_items=10)] == ["Good"]


def test_parse_rejects_a_javascript_link() -> None:
    body = """<rss version="2.0"><channel>
      <item><title>Bad</title><link>javascript:alert(1)</link></item>
    </channel></rss>"""

    assert parse_feed(body, max_items=10) == []


def test_parse_refuses_an_entity_expansion_bomb() -> None:
    """Feed bodies are untrusted remote input; a billion-laughs payload must not be expanded."""
    bomb = """<?xml version="1.0"?>
    <!DOCTYPE lolz [
      <!ENTITY lol "lol">
      <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
      <!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">
    ]>
    <rss version="2.0"><channel><item><title>&lol3;</title>
    <link>https://example.com/x</link></item></channel></rss>"""

    with pytest.raises(ValueError, match="unparseable feed"):
        parse_feed(bomb, max_items=10)


# ---------------------------------------------------------------------------
# Canonicalisation and cross-source merge
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("https://Example.com/a", "https://example.com/a"),
        ("https://www.example.com/a", "https://example.com/a"),
        ("https://example.com/a/", "https://example.com/a"),
        ("https://example.com/a?utm_source=x&utm_medium=y", "https://example.com/a"),
        ("https://example.com/a?fbclid=abc", "https://example.com/a"),
        ("https://example.com/a#section", "https://example.com/a"),
        ("https://example.com/a?b=1&c=2", "https://example.com/a?c=2&b=1"),
    ],
)
def test_canonical_url_collapses_cosmetic_differences(left: str, right: str) -> None:
    assert canonical_url(left) == canonical_url(right)


def test_canonical_url_keeps_meaningful_query_parameters() -> None:
    """Plenty of sites route on the query string; only tracking is stripped."""
    assert canonical_url("https://example.com/news?id=42") != canonical_url(
        "https://example.com/news"
    )


def test_title_key_normalises_punctuation_and_case() -> None:
    assert title_key("Apple Beats Expectations!") == title_key("apple beats  expectations")
    assert title_key("Apple beats expectations") != title_key("Shell announces buyback")


def test_dedupe_key_ignores_tracking_parameters() -> None:
    stamp = datetime(2024, 5, 1, 9, 30, tzinfo=UTC)

    assert dedupe_key("https://x/a?utm_source=rss", stamp) == dedupe_key("https://x/a", stamp)


def test_dedupe_key_is_timezone_independent() -> None:
    utc = datetime(2024, 5, 1, 14, 0, tzinfo=UTC)
    plus_two = datetime(2024, 5, 1, 16, 0, tzinfo=timezone(timedelta(hours=2)))

    assert dedupe_key("https://x/a", utc) == dedupe_key("https://x/a", plus_two)


def test_merge_prefers_the_higher_trust_source() -> None:
    """The same story from a filing and an aggregator collapses to the filing."""
    filing = _collected(
        "sec", "SEC EDGAR", trust=100, headline="Apple 8-K filed", url="https://sec/1"
    )
    echo = _collected("yahoo", "Yahoo", trust=40, headline="Apple 8-K Filed!", url="https://yh/1")

    result = merge_collected([echo, filing], window_hours=48)

    assert result.merged == 1
    assert [entry.request.label for entry in result.kept] == ["SEC EDGAR"]


def test_merge_collapses_the_same_url_across_sources() -> None:
    left = _collected("a", "A", trust=50, headline="One", url="https://x/story")
    right = _collected(
        "b", "B", trust=40, headline="Different wording", url="https://x/story?utm_source=b"
    )

    result = merge_collected([left, right], window_hours=48)

    assert result.merged == 1
    assert len(result.kept) == 1


def test_merge_keeps_same_headline_outside_the_window() -> None:
    """A recurring headline weeks apart is a different story, not a duplicate."""
    early = _collected(
        "a", "A", trust=50, headline="Weekly market wrap", url="https://x/1", when=NOW
    )
    late = _collected(
        "a",
        "A",
        trust=50,
        headline="Weekly market wrap",
        url="https://x/2",
        when=NOW + timedelta(days=7),
    )

    result = merge_collected([early, late], window_hours=48)

    assert result.merged == 0
    assert len(result.kept) == 2


def test_merge_keeps_genuinely_different_stories() -> None:
    left = _collected("a", "A", trust=50, headline="Apple beats expectations", url="https://x/1")
    right = _collected("a", "A", trust=50, headline="Shell announces buyback", url="https://x/2")

    result = merge_collected([left, right], window_hours=48)

    assert result.merged == 0
    assert len(result.kept) == 2


# ---------------------------------------------------------------------------
# Sync pipeline
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_reports_when_no_sources_are_enabled(tmp_path: Path) -> None:
    repository, _ = await _repository(tmp_path, "news_none.sqlite3")
    service = NewsSyncService(
        repository,
        Settings(data_dir=tmp_path, news_feeds_path=tmp_path / "absent.yaml"),
        {},
        clock=FixedClock(),
    )

    summary = await service.sync()

    assert summary.feeds_configured == 0
    assert any("No news sources configured" in note for note in summary.notes)


@pytest.mark.asyncio
async def test_sync_stores_raw_before_parsed(tmp_path: Path) -> None:
    repository, session_factory = await _repository(tmp_path, "news_raw.sqlite3")
    feeds = _write(tmp_path, "  - key: mkt\n    label: Example Markets\n    url: https://x/f\n")
    adapter = FakeAdapter({"https://x/f": RSS})
    service = NewsSyncService(
        repository,
        Settings(data_dir=tmp_path, news_feeds_path=feeds),
        {"rss": adapter},
        clock=FixedClock(),
    )

    summary = await service.sync()

    async with session_factory() as session:
        raw_rows = list(await session.scalars(select(RawNews)))
        items = list(await session.scalars(select(NewsItem)))

    assert summary.raw_stored == 1
    # The exact bytes the publisher served are kept, so a parser fix can be replayed.
    assert raw_rows[0].body == RSS
    assert len(items) == 2
    assert all(item.raw_news_id == raw_rows[0].id for item in items)
    assert all(item.canonical_url and item.title_key for item in items)


@pytest.mark.asyncio
async def test_sync_merges_the_same_story_from_two_sources(tmp_path: Path) -> None:
    """The headline arrives from an RSS feed and Marketaux under different URLs."""
    repository, session_factory = await _repository(tmp_path, "news_merge.sqlite3")
    async with session_factory() as session, session.begin():
        # A news target must be an instrument you hold, so seed the position too.
        session.add_all(
            [
                Instrument(
                    t212_ticker="AAPL_US_EQ", yahoo_ticker="AAPL", mapping_status="resolved"
                ),
                _held("AAPL_US_EQ"),
            ]
        )
    feeds = _write(
        tmp_path,
        "  - key: pub\n    label: Publisher\n    url: https://x/f\n    trust: 80\n"
        "  - key: m\n    label: Marketaux\n    provider: marketaux\n",
    )
    settings = Settings(
        data_dir=tmp_path, news_feeds_path=feeds, news_marketaux_api_key=SecretStr("k")
    )
    rss = FakeAdapter({"https://x/f": RSS})
    marketaux = _MarketauxFake(settings, MARKETAUX_JSON)
    service = NewsSyncService(
        repository, settings, {"rss": rss, "marketaux": marketaux}, clock=FixedClock()
    )

    summary = await service.sync()
    stored = await repository.list_news_items(limit=50)

    # 2 RSS + 2 Marketaux parsed; "Apple beats expectations" appears in both.
    assert summary.items_parsed == 4
    assert summary.cross_source_merges == 1
    assert summary.items_written == 3
    apple = [item for item in stored if "Apple beats" in item.headline]
    assert len(apple) == 1
    # The higher-trust publisher feed won.
    assert apple[0].source_label == "Publisher"
    assert set(summary.sources_used) == {"Publisher", "Marketaux"}


@pytest.mark.asyncio
async def test_sync_is_idempotent_across_runs(tmp_path: Path) -> None:
    repository, _ = await _repository(tmp_path, "news_dedupe.sqlite3")
    feeds = _write(tmp_path, "  - key: mkt\n    label: M\n    url: https://x/f\n")
    service = NewsSyncService(
        repository,
        Settings(data_dir=tmp_path, news_feeds_path=feeds),
        {"rss": FakeAdapter({"https://x/f": RSS})},
        clock=FixedClock(),
    )

    first = await service.sync()
    second = await service.sync()

    assert first.items_written == 2
    assert second.items_written == 0
    assert second.duplicates_skipped == 2
    assert len(await repository.list_news_items(limit=50)) == 2


@pytest.mark.asyncio
async def test_second_run_from_a_different_source_does_not_duplicate(tmp_path: Path) -> None:
    """Cross-run dedupe: the same story from a new source must not re-insert."""
    repository, _ = await _repository(tmp_path, "news_crossrun.sqlite3")
    settings = Settings(
        data_dir=tmp_path,
        news_feeds_path=_write(tmp_path, "  - key: a\n    label: A\n    url: https://x/f\n"),
    )
    service = NewsSyncService(
        repository, settings, {"rss": FakeAdapter({"https://x/f": RSS})}, clock=FixedClock()
    )
    await service.sync()

    # A different source, different URL, same headline and timestamp.
    other = """<rss version="2.0"><channel><item>
      <title>Apple beats expectations</title>
      <link>https://another.example/apple</link>
      <pubDate>Wed, 01 May 2024 09:30:00 +0000</pubDate>
    </item></channel></rss>"""
    settings_b = Settings(
        data_dir=tmp_path,
        news_feeds_path=_write(tmp_path, "  - key: b\n    label: B\n    url: https://x/g\n"),
    )
    service_b = NewsSyncService(
        repository, settings_b, {"rss": FakeAdapter({"https://x/g": other})}, clock=FixedClock()
    )

    summary = await service_b.sync()

    assert summary.items_written == 0
    assert summary.duplicates_skipped == 1


@pytest.mark.asyncio
async def test_sync_survives_one_failing_publisher(tmp_path: Path) -> None:
    repository, _ = await _repository(tmp_path, "news_fail.sqlite3")
    feeds = _write(
        tmp_path,
        "  - key: good\n    label: Good\n    url: https://x/good\n"
        "  - key: bad\n    label: Bad\n    url: https://x/bad\n",
    )
    service = NewsSyncService(
        repository,
        Settings(data_dir=tmp_path, news_feeds_path=feeds),
        {"rss": FakeAdapter({"https://x/good": RSS, "https://x/bad": RuntimeError("boom")})},
        clock=FixedClock(),
    )

    summary = await service.sync()

    assert summary.items_written == 2
    assert summary.failures == ["bad: RuntimeError"]


@pytest.mark.asyncio
async def test_sync_keeps_the_raw_body_when_parsing_fails(tmp_path: Path) -> None:
    repository, session_factory = await _repository(tmp_path, "news_parsefail.sqlite3")
    feeds = _write(tmp_path, "  - key: broken\n    label: B\n    url: https://x/f\n")
    service = NewsSyncService(
        repository,
        Settings(data_dir=tmp_path, news_feeds_path=feeds),
        {"rss": FakeAdapter({"https://x/f": "<rss><channel>"})},
        clock=FixedClock(),
    )

    summary = await service.sync()

    async with session_factory() as session:
        raw_rows = list(await session.scalars(select(RawNews)))

    assert summary.raw_stored == 1 and len(raw_rows) == 1
    assert summary.items_written == 0
    assert any("unparseable feed" in failure for failure in summary.failures)


@pytest.mark.asyncio
async def test_sync_attributes_items_to_the_configured_instrument(tmp_path: Path) -> None:
    repository, session_factory = await _repository(tmp_path, "news_attr.sqlite3")
    async with session_factory() as session, session.begin():
        session.add_all(
            [
                Instrument(
                    t212_ticker="AAPL_US_EQ",
                    isin="US0378331005",
                    yahoo_ticker="AAPL",
                    mapping_status="resolved",
                ),
                _held("AAPL_US_EQ", isin="US0378331005"),
            ]
        )
    feeds = _write(
        tmp_path,
        "  - key: apple\n    label: Example — Apple\n    url: https://x/apple\n"
        "    tickers: [AAPL_US_EQ]\n",
    )
    service = NewsSyncService(
        repository,
        Settings(data_dir=tmp_path, news_feeds_path=feeds),
        {"rss": FakeAdapter({"https://x/apple": RSS})},
        clock=FixedClock(),
    )

    await service.sync()
    items = await repository.list_news_items(t212_ticker="AAPL_US_EQ", limit=10)

    assert len(items) == 2
    for item in items:
        assert item.source_label == "Example — Apple"
        assert item.isin == "US0378331005"


@pytest.mark.asyncio
async def test_list_news_orders_newest_first_and_keeps_undated_last(tmp_path: Path) -> None:
    repository, _ = await _repository(tmp_path, "news_order.sqlite3")
    await repository.upsert_news_items(
        [
            _item("older", "https://x/1", datetime(2024, 4, 1, tzinfo=UTC)),
            _item("newer", "https://x/2", datetime(2024, 5, 1, tzinfo=UTC)),
            _item("undated", "https://x/3", None),
        ]
    )

    items = await repository.list_news_items(limit=10)

    assert [item.headline for item in items] == ["newer", "older", "undated"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _MarketauxFake(MarketauxSourceAdapter):
    def __init__(self, settings: Settings, body: str) -> None:
        super().__init__(settings, None)  # type: ignore[arg-type]
        self._body = body

    async def fetch(self, request: FeedRequest) -> FeedResponse | None:
        return FeedResponse(
            url=request.url, http_status=200, content_type="application/json", body=self._body
        )


def _request(key: str, url: str, *, trust: int) -> FeedRequest:
    return FeedRequest(
        feed_key=key, label=key, provider="rss", url=url, t212_ticker=None, isin=None, trust=trust
    )


def _collected(
    key: str,
    label: str,
    *,
    trust: int,
    headline: str,
    url: str,
    when: datetime | None = NOW,
) -> CollectedItem:
    return CollectedItem(
        request=FeedRequest(
            feed_key=key,
            label=label,
            provider="rss",
            url="https://source",
            t212_ticker=None,
            isin=None,
            trust=trust,
        ),
        item=ParsedNewsItem(headline=headline, url=url, summary=None, published_at=when),
        raw_news_id=None,
    )


def _item(headline: str, url: str, published: datetime | None) -> NewsItem:
    return NewsItem(
        dedupe_key=dedupe_key(url, published),
        feed_key="f",
        provider="rss",
        source_label="Source",
        t212_ticker=None,
        isin=None,
        headline=headline,
        summary=None,
        url=url,
        canonical_url=canonical_url(url),
        title_key=title_key(headline),
        published_at=published,
        fetched_at=NOW,
        raw_news_id=None,
    )


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / f"feeds_{abs(hash(body)) % 10**8}.yaml"
    path.write_text(f"feeds:\n{body}", encoding="utf-8")
    return path


def _held(ticker: str, *, isin: str | None = None) -> PositionLive:
    """A live position, which is what makes an instrument a news target."""
    return PositionLive(
        ts=datetime(2026, 8, 7, tzinfo=UTC),
        t212_ticker=ticker,
        isin=isin,
        quantity=Decimal("1"),
    )


async def _repository(
    tmp_path: Path, filename: str
) -> tuple[PortfolioRepository, async_sessionmaker[AsyncSession]]:
    settings = Settings(data_dir=tmp_path, sqlite_filename=filename)
    await migrate_database(settings)
    engine = create_async_engine(settings.sqlite_url, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    return PortfolioRepository(session_factory), session_factory


async def test_news_targets_are_scoped_to_held_positions_not_the_whole_catalogue(
    tmp_path: Path,
) -> None:
    """The instruments table is the full T212 catalogue (~17k rows).

    One outbound request is issued per target per feed, so scoping to the catalogue would mean
    tens of thousands of requests per sync -- enough to get blocked by the publisher, and noise
    besides. Only instruments actually held are targets.
    """
    repository, session_factory = await _repository(tmp_path, "news_targets.sqlite3")
    synced_at = datetime(2026, 8, 7, tzinfo=UTC)
    async with session_factory() as session:
        session.add_all(
            [
                Instrument(t212_ticker="HELD_US_EQ", isin="US1", yahoo_ticker="HELD", name="Held"),
                Instrument(t212_ticker="OTHER_US_EQ", isin="US2", yahoo_ticker="OTH", name="Oth"),
                PositionLive(
                    ts=synced_at,
                    t212_ticker="HELD_US_EQ",
                    isin="US1",
                    quantity=Decimal("1"),
                ),
            ]
        )
        await session.commit()

    targets = await repository.list_instrument_news_targets()

    assert [target.t212_ticker for target in targets] == ["HELD_US_EQ"]


async def test_news_targets_are_empty_before_any_position_snapshot(tmp_path: Path) -> None:
    repository, session_factory = await _repository(tmp_path, "news_targets_empty.sqlite3")
    async with session_factory() as session:
        session.add(Instrument(t212_ticker="X_US_EQ", isin="US3", yahoo_ticker="X", name="X"))
        await session.commit()

    assert await repository.list_instrument_news_targets() == []
