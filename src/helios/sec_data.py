"""Company facts and fund look-through, from the SEC's free EDGAR data.

**Company facts.** Every US-listed company files its financials in XBRL; EDGAR serves them as
one JSON per company ("companyfacts"). From it Helios derives trailing-twelve-month revenue,
profit, operating profit and diluted EPS, the latest quarter's growth on the same quarter a
year before, and shares outstanding. With a price Helios already has, that gives market value
and P/E. The company's SEC industry code (SIC) gives its industry and a broad sector.

**Fund look-through.** A UCITS ETF (VUAG, VWRP...) files nothing with the SEC, but a US fund
tracking the same index does: every quarter, its N-PORT report lists each holding with its
share of the fund and its country. Helios reads that report for a labelled proxy -- VOO for an
S&P 500 fund, VT for FTSE All-World -- and treats the ETF as holding what the proxy holds.
It is an approximation and always says so.

EDGAR asks for a User-Agent naming a contact; Helios uses the one saved for the SEC news feed
and does nothing without it. Requests are paced well under the SEC's 10 a second, and data is
refreshed weekly (facts) or when a fund files a new report.
"""

from __future__ import annotations

import io
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
from defusedxml import ElementTree
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .config import Settings
from .logging import get_logger
from .models import (
    CompanyFacts,
    DailyNav,
    FundHoldings,
    Instrument,
    MarketPriceDaily,
    PositionLive,
    WatchlistItem,
)
from .performance import RequestPacer
from .rate_limit import Clock, SystemClock

logger = get_logger(__name__)

SEC_DATA = "https://data.sec.gov"
SEC_WWW = "https://www.sec.gov"
FACTS_REFRESH = timedelta(days=7)
FUND_CHECK = timedelta(days=7)
SEC_INTERVAL_SECONDS = 0.15
#: How many of a fund's largest holdings are kept by name (the rest count as "other").
TOP_HOLDINGS = 300
#: How many of those get a sector looked up (one request each, when a new report lands).
SECTOR_LOOKUPS = 80
NPORT = "{http://www.sec.gov/edgar/nport}"
#: A figure this much older than the company's newest one is no longer reported.
STALE_FIGURE_DAYS = 550


@dataclass(frozen=True)
class FundProxy:
    symbol: str
    label: str
    #: "index": look through the proxy's holdings. "commodity": the whole fund is one asset.
    kind: str = "index"
    asset: str | None = None


#: UCITS ETFs by ISIN, and what stands in for them.
FUND_PROXIES: dict[str, FundProxy] = {
    "IE00BFMXXD54": FundProxy("VOO", "Vanguard S&P 500 ETF (VOO), same index"),  # VUAG
    "IE00B3XXRP09": FundProxy("VOO", "Vanguard S&P 500 ETF (VOO), same index"),  # VUSA
    "IE00B5BMR087": FundProxy("IVV", "iShares Core S&P 500 ETF (IVV), same index"),  # CSPX
    "IE00BK5BQT80": FundProxy(
        "VT", "Vanguard Total World Stock ETF (VT), a close proxy for FTSE All-World"
    ),  # VWRP
    "IE00B3RBWM25": FundProxy(
        "VT", "Vanguard Total World Stock ETF (VT), a close proxy for FTSE All-World"
    ),  # VWRL
    "IE00B4L5Y983": FundProxy("URTH", "iShares MSCI World ETF (URTH), same index"),  # IWDA
    "IE0032077012": FundProxy("QQQ", "Invesco QQQ Trust (QQQ), same index"),  # EQQQ
    "IE00B4NCWG09": FundProxy("", "Physical silver", kind="commodity", asset="Silver"),  # SSLN
    "JE00B1VS3333": FundProxy("", "Physical silver", kind="commodity", asset="Silver"),  # PHAG
    "IE00B579F325": FundProxy("", "Physical gold", kind="commodity", asset="Gold"),  # SGLN
    "JE00B1VS3770": FundProxy("", "Physical gold", kind="commodity", asset="Gold"),  # PHAU
}

US_STATES = frozenset(
    "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH "
    "NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY PR".split()
)

REVENUE = (
    "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "SalesRevenueNet",
    "RevenuesNetOfInterestExpense",
)
NET_INCOME = ("NetIncomeLoss", "ProfitLoss", "NetIncomeLossAvailableToCommonStockholdersBasic")
OPERATING_INCOME = ("OperatingIncomeLoss",)
EPS = ("EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted", "EarningsPerShareBasic")


def sic_sector(sic: int | None) -> str | None:
    """A broad sector from an SEC industry code (approximate by nature)."""

    if sic is None:
        return None
    ranges: tuple[tuple[int, int, str], ...] = (
        (100, 999, "Consumer Staples"),
        (1000, 1299, "Materials"),
        (1300, 1399, "Energy"),
        (1400, 1499, "Materials"),
        (1500, 1799, "Industrials"),
        (2000, 2199, "Consumer Staples"),
        (2200, 2399, "Consumer Discretionary"),
        (2400, 2799, "Materials"),
        (2800, 2829, "Materials"),
        (2830, 2836, "Health Care"),
        (2840, 2844, "Consumer Staples"),
        (2845, 2899, "Materials"),
        (2900, 2999, "Energy"),
        (3000, 3569, "Industrials"),
        (3570, 3579, "Technology"),
        (3580, 3659, "Industrials"),
        (3660, 3699, "Technology"),
        (3700, 3715, "Consumer Discretionary"),
        (3716, 3799, "Industrials"),
        (3800, 3829, "Industrials"),
        (3840, 3851, "Health Care"),
        (3852, 3999, "Consumer Discretionary"),
        (4000, 4799, "Industrials"),
        (4800, 4899, "Communication Services"),
        (4900, 4999, "Utilities"),
        (5000, 5199, "Industrials"),
        (5200, 5399, "Consumer Discretionary"),
        (5400, 5499, "Consumer Staples"),
        (5500, 5999, "Consumer Discretionary"),
        (6000, 6499, "Financials"),
        (6500, 6553, "Real Estate"),
        (6770, 6797, "Financials"),
        (6798, 6798, "Real Estate"),
        (6799, 6799, "Financials"),
        (7000, 7369, "Consumer Discretionary"),
        (7370, 7379, "Technology"),
        (7380, 7999, "Industrials"),
        (8000, 8099, "Health Care"),
        (8100, 8999, "Industrials"),
    )
    return next((sector for low, high, sector in ranges if low <= sic <= high), None)


def normalise_company(name: str) -> str:
    """ "Apple Inc." and "APPLE INC" both become "apple": for matching fund holdings to filers."""

    words = re.sub(r"[^a-z0-9 ]", " ", name.lower()).split()
    noise = {
        "inc", "corp", "corporation", "co", "company", "ltd", "limited", "plc", "the", "sa",
        "nv", "ag", "se", "class", "cl", "a", "b", "c", "holdings", "holding", "group", "adr",
        "com", "new", "del",
    }  # fmt: skip
    return " ".join(word for word in words if word not in noise)


# --- XBRL: trailing twelve months -------------------------------------------------------------


@dataclass(frozen=True)
class Fact:
    start: date | None
    end: date
    value: float
    filed: str

    @property
    def days(self) -> int:
        return (self.end - self.start).days if self.start else 0


def _facts(raw: Mapping[str, Any], names: Sequence[str], unit: str) -> list[Fact]:
    """The concept (of the given alternatives) reported most recently, one fact per period."""

    best: list[Fact] = []
    for name in names:
        units = raw.get(name, {}).get("units", {})
        entries = units.get(unit, [])
        by_period: dict[tuple[date | None, date], Fact] = {}
        for entry in entries:
            try:
                end = date.fromisoformat(entry["end"])
                start = date.fromisoformat(entry["start"]) if entry.get("start") else None
                fact = Fact(start, end, float(entry["val"]), str(entry.get("filed", "")))
            except (KeyError, ValueError, TypeError):
                continue
            current = by_period.get((start, end))
            if current is None or fact.filed > current.filed:
                by_period[(start, end)] = fact
        facts = sorted(by_period.values(), key=lambda fact: fact.end)
        if facts and (not best or facts[-1].end > best[-1].end):
            best = facts
    return best


def _near(
    target: date, candidates: Iterable[Fact], *, days: int, tolerance: int = 12
) -> Fact | None:
    matches = [
        fact
        for fact in candidates
        if abs((fact.end - target).days) <= tolerance and abs(fact.days - days) <= tolerance
    ]
    return max(matches, key=lambda fact: fact.filed) if matches else None


def trailing_twelve_months(facts: Sequence[Fact]) -> tuple[float, date] | None:
    """Last fiscal year, rolled forward by this year's year-to-date minus last year's."""

    annual = [fact for fact in facts if 350 <= fact.days <= 380]
    quarters = [fact for fact in facts if 80 <= fact.days <= 100]
    durations = [fact for fact in facts if fact.start is not None]
    if not durations:
        return None
    latest_end = max(fact.end for fact in durations)
    if annual:
        fiscal = max(annual, key=lambda fact: fact.end)
        if fiscal.end >= latest_end:
            return fiscal.value, fiscal.end
        year_to_date = [
            fact
            for fact in durations
            if fact.end == latest_end
            and fact.start is not None
            and abs((fact.start - fiscal.end).days - 1) <= 4
        ]
        if year_to_date:
            current = max(year_to_date, key=lambda fact: fact.days)
            prior = _near(latest_end - timedelta(days=364), durations, days=current.days)
            if prior is not None:
                return fiscal.value + current.value - prior.value, latest_end
    recent = sorted(quarters, key=lambda fact: fact.end)[-4:]
    if len(recent) == 4 and (recent[-1].end - recent[0].end).days <= 290:
        return sum(fact.value for fact in recent), recent[-1].end
    if annual:
        fiscal = max(annual, key=lambda fact: fact.end)
        return fiscal.value, fiscal.end
    return None


def latest_quarter(facts: Sequence[Fact]) -> tuple[Fact, Fact | None] | None:
    """The most recent quarter (a fourth quarter derived from the year) and the same one a year
    earlier, for growth."""

    quarters = [fact for fact in facts if 80 <= fact.days <= 100]
    annual = [fact for fact in facts if 350 <= fact.days <= 380]

    def fourth(fiscal: Fact) -> Fact | None:
        nine = _near(fiscal.end - timedelta(days=91), facts, days=fiscal.days - 91, tolerance=15)
        if nine is None or nine.start != fiscal.start:
            return None
        start = nine.end + timedelta(days=1)
        return Fact(start, fiscal.end, fiscal.value - nine.value, fiscal.filed)

    candidates = list(quarters)
    for fiscal in annual:
        derived = fourth(fiscal)
        if derived is not None and not any(fact.end == derived.end for fact in quarters):
            candidates.append(derived)
    if not candidates:
        return None
    last = max(candidates, key=lambda fact: fact.end)
    return last, _near(last.end - timedelta(days=364), candidates, days=last.days, tolerance=15)


def extract_figures(companyfacts: Mapping[str, Any]) -> dict[str, object]:
    facts = companyfacts.get("facts", {})
    gaap = facts.get("us-gaap", {})
    figures: dict[str, object] = {"currency": "USD"}

    for key, names, unit in (
        ("revenue", REVENUE, "USD"),
        ("net_income", NET_INCOME, "USD"),
        ("operating_income", OPERATING_INCOME, "USD"),
        ("eps", EPS, "USD/shares"),
    ):
        series = _facts(gaap, names, unit)
        ttm = trailing_twelve_months(series)
        if ttm is not None:
            figures[f"{key}_ttm"] = ttm[0]
            figures[f"{key}_ttm_end"] = ttm[1].isoformat()
        quarter = latest_quarter(series)
        if quarter is not None:
            last, before = quarter
            figures[f"{key}_quarter"] = last.value
            figures[f"{key}_quarter_end"] = last.end.isoformat()
            if before is not None and before.value:
                figures[f"{key}_growth"] = (last.value - before.value) / abs(before.value)

    shares = facts.get("dei", {}).get("EntityCommonStockSharesOutstanding", {}).get("units", {})
    counts = shares.get("shares", [])
    if counts:
        latest = max(entry["end"] for entry in counts if "end" in entry)
        # Several share classes are reported on the same date; together they are the company.
        figures["shares_outstanding"] = sum(
            float(entry["val"]) for entry in counts if entry.get("end") == latest
        )
        figures["shares_as_of"] = latest

    # A concept the company stopped reporting (a pre-revenue biotech's revenue line from years
    # ago) is not a current figure: drop anything far older than the rest.
    ends = [str(value) for key, value in figures.items() if key.endswith("_end")]
    if ends:
        newest = date.fromisoformat(max(ends))
        for key in [key for key in figures if key.endswith("_end")]:
            if (newest - date.fromisoformat(str(figures[key]))).days > STALE_FIGURE_DAYS:
                stem = key.removesuffix("_end")
                for stale in [
                    name for name in figures if name == stem or name.startswith(f"{stem}_")
                ]:
                    figures.pop(stale, None)
                base = stem.removesuffix("_ttm").removesuffix("_quarter")
                figures.pop(f"{base}_growth", None)

    revenue = figures.get("revenue_ttm")
    if isinstance(revenue, float) and revenue > 0:
        for key in ("net_income", "operating_income"):
            value = figures.get(f"{key}_ttm")
            if isinstance(value, float):
                figures[f"{key}_margin"] = value / revenue
    return figures


# --- N-PORT: what a fund holds --------------------------------------------------------------


@dataclass(frozen=True)
class NportReport:
    report_date: date | None
    holdings: list[dict[str, object]]
    countries: dict[str, float]
    total: int


def _child_text(element: Any, name: str) -> str:
    found = element.find(f"{NPORT}{name}")
    return (found.text or "").strip() if found is not None else ""


def parse_nport(document: bytes, *, keep: int = TOP_HOLDINGS) -> NportReport:
    """A fund's holdings, merged per issuer, largest first; countries summed over everything."""

    report_date: date | None = None
    issuers: dict[str, dict[str, object]] = {}
    countries: dict[str, float] = {}
    total = 0
    for _event, element in ElementTree.iterparse(io.BytesIO(document), events=("end",)):
        tag = element.tag
        if tag == f"{NPORT}repPdDate" and element.text:
            try:
                report_date = date.fromisoformat(element.text.strip())
            except ValueError:
                pass
        if tag != f"{NPORT}invstOrSec":
            continue
        total += 1

        try:
            pct = float(_child_text(element, "pctVal") or 0)
        except ValueError:
            pct = 0.0
        isin_node = element.find(f"{NPORT}identifiers/{NPORT}isin")
        isin = isin_node.get("value") if isin_node is not None else None
        country = _child_text(element, "invCountry") or "Other"
        countries[country] = countries.get(country, 0.0) + pct
        cusip = _child_text(element, "cusip")
        cusip = cusip if cusip and cusip != "000000000" else ""
        key = _child_text(element, "lei") or cusip or _child_text(element, "name")
        entry = issuers.setdefault(
            key,
            {
                "name": _child_text(element, "name") or _child_text(element, "title"),
                "cusip": cusip or None,
                "isin": isin,
                "country": country,
                "category": _child_text(element, "assetCat") or None,
                "pct": 0.0,
            },
        )
        entry["pct"] = float(entry["pct"]) + pct  # type: ignore[arg-type]
        element.clear()
    ranked = sorted(issuers.values(), key=lambda row: float(row["pct"]), reverse=True)  # type: ignore[arg-type]
    return NportReport(report_date, ranked[:keep], countries, total)


# --- the service --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Exposure:
    as_of: datetime
    total_eur: float
    companies: list[dict[str, object]]
    countries: list[dict[str, object]]
    sectors: list[dict[str, object]]
    funds: list[dict[str, object]]
    warnings: list[str]
    notes: list[str]


def _cusip(isin: str | None) -> str | None:
    """A US or Canadian ISIN carries the CUSIP inside it."""

    if isin and len(isin) == 12 and isin[:2] in {"US", "CA"}:
        return isin[2:11]
    return None


class SecDataService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        clock: Clock | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._clock = clock or SystemClock()
        self._client = client
        self._pacer = RequestPacer(SEC_INTERVAL_SECONDS)
        self._tickers: dict[str, tuple[int, str]] | None = None
        self._names: dict[str, int] | None = None
        self._sics: dict[int, tuple[int | None, str | None]] = {}

    @property
    def available(self) -> bool:
        return bool((self._settings.news_sec_user_agent or "").strip())

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=120.0,
                follow_redirects=True,
                headers={
                    "User-Agent": (self._settings.news_sec_user_agent or "").strip(),
                    "Accept-Encoding": "gzip, deflate",
                },
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()

    async def _get(self, url: str, **params: str) -> httpx.Response:
        await self._pacer.wait()
        response = await self._http().get(url, params=params or None)
        response.raise_for_status()
        return response

    async def _ticker_map(self) -> dict[str, tuple[int, str]]:
        if self._tickers is None:
            data = (await self._get(f"{SEC_WWW}/files/company_tickers.json")).json()
            rows = data.values() if isinstance(data, dict) else []
            self._tickers = {
                str(row["ticker"]).upper(): (int(row["cik_str"]), str(row["title"])) for row in rows
            }
            self._names = {}
            for cik, title in self._tickers.values():
                self._names.setdefault(normalise_company(title), cik)
        return self._tickers

    async def _submission(self, cik: int) -> dict[str, Any]:
        response = await self._get(f"{SEC_DATA}/submissions/CIK{cik:010d}.json")
        data: dict[str, Any] = response.json()
        return data

    async def _sic(self, cik: int) -> tuple[int | None, str | None]:
        if cik not in self._sics:
            submission = await self._submission(cik)
            try:
                sic: int | None = int(submission.get("sic") or 0) or None
            except ValueError:
                sic = None
            self._sics[cik] = (sic, submission.get("sicDescription") or None)
        return self._sics[cik]

    # -- refresh --

    async def _followed(self, session: AsyncSession) -> tuple[dict[str, Instrument], set[str]]:
        latest = await session.scalar(
            select(PositionLive.ts).order_by(PositionLive.ts.desc()).limit(1)
        )
        held: set[str] = set()
        if latest is not None:
            rows = await session.scalars(select(PositionLive).where(PositionLive.ts == latest))
            held = {row.t212_ticker for row in rows if row.quantity > 0}
        watched = set(await session.scalars(select(WatchlistItem.t212_ticker)))
        instruments = await session.scalars(
            select(Instrument).where(Instrument.t212_ticker.in_(held | watched))
        )
        return {row.t212_ticker: row for row in instruments}, held

    async def refresh(self, *, force: bool = False) -> dict[str, int]:
        if not self.available:
            return {"facts": 0, "funds": 0}
        now = self._clock.utcnow()
        async with self._session_factory() as session:
            instruments, held = await self._followed(session)
            known_facts = {
                row.t212_ticker: row for row in await session.scalars(select(CompanyFacts))
            }
            known_funds = {
                row.proxy_symbol: row for row in await session.scalars(select(FundHoldings))
            }
        counts = {"facts": 0, "funds": 0}
        for ticker, instrument in sorted(instruments.items()):
            if (instrument.instrument_type or "").upper() != "STOCK":
                continue
            existing = known_facts.get(ticker)
            if not force and existing is not None and now - existing.fetched_at < FACTS_REFRESH:
                continue
            try:
                counts["facts"] += await self._refresh_company(ticker, instrument, now)
            except httpx.HTTPError as exc:
                logger.warning("sec_facts_failed", ticker=ticker, error=exc.__class__.__name__)
        proxies = {
            FUND_PROXIES[instrument.isin].symbol
            for ticker, instrument in instruments.items()
            if ticker in held
            and instrument.isin in FUND_PROXIES
            and FUND_PROXIES[instrument.isin].kind == "index"
        }
        for symbol in sorted(proxies):
            existing_fund = known_funds.get(symbol)
            if not force and existing_fund and now - existing_fund.checked_at < FUND_CHECK:
                continue
            try:
                counts["funds"] += await self._refresh_fund(symbol, existing_fund, now)
            except httpx.HTTPError as exc:
                logger.warning("sec_fund_failed", fund=symbol, error=exc.__class__.__name__)
        return counts

    async def _refresh_company(self, ticker: str, instrument: Instrument, now: datetime) -> int:
        symbol = (instrument.yahoo_ticker or ticker.split("_")[0]).upper()
        found = (await self._ticker_map()).get(symbol)
        if found is None:
            await self._save_facts(
                CompanyFacts(t212_ticker=ticker, figures={}, status="not_filed", fetched_at=now)
            )
            return 0
        cik, title = found
        submission = await self._submission(cik)
        facts = (await self._get(f"{SEC_DATA}/api/xbrl/companyfacts/CIK{cik:010d}.json")).json()
        try:
            sic: int | None = int(submission.get("sic") or 0) or None
        except ValueError:
            sic = None
        where = (submission.get("addresses") or {}).get("business") or {}
        state = str(where.get("stateOrCountry") or "")
        country = "US" if state in US_STATES else (instrument.isin or "")[:2] or None
        await self._save_facts(
            CompanyFacts(
                t212_ticker=ticker,
                cik=cik,
                name=submission.get("name") or title,
                sic=sic,
                industry=submission.get("sicDescription") or None,
                sector=sic_sector(sic),
                country=country,
                fiscal_year_end=submission.get("fiscalYearEnd") or None,
                figures=extract_figures(facts),
                status="ok",
                fetched_at=now,
            )
        )
        return 1

    async def _save_facts(self, row: CompanyFacts) -> None:
        async with self._session_factory() as session, session.begin():
            await session.merge(row)

    async def _series_id(self, symbol: str) -> str | None:
        data = (await self._get(f"{SEC_WWW}/files/company_tickers_mf.json")).json()
        fields = data.get("fields", [])
        for values in data.get("data", []):
            row = dict(zip(fields, values, strict=False))
            if str(row.get("symbol", "")).upper() == symbol:
                return str(row.get("seriesId"))
        return None

    async def _refresh_fund(self, symbol: str, existing: FundHoldings | None, now: datetime) -> int:
        series = existing.series_id if existing else await self._series_id(symbol)
        if not series:
            return 0
        feed = await self._get(
            f"{SEC_WWW}/cgi-bin/browse-edgar",
            action="getcompany",
            CIK=series,
            type="NPORT-P",
            dateb="",
            owner="include",
            count="5",
            output="atom",
        )
        links = re.findall(r"<filing-href>(.*?)</filing-href>", feed.text)
        if not links:
            return 0
        index_url = links[0]
        accession = index_url.rsplit("/", 1)[-1].replace("-index.htm", "")
        if existing is not None and existing.accession == accession:
            async with self._session_factory() as session, session.begin():
                row = await session.get(FundHoldings, symbol)
                if row is not None:
                    row.checked_at = now
            return 0
        folder = index_url.rsplit("/", 1)[0]
        report = parse_nport((await self._get(f"{folder}/primary_doc.xml")).content)
        sectors = await self._sectors_for(report.holdings[:SECTOR_LOOKUPS])
        async with self._session_factory() as session, session.begin():
            await session.merge(
                FundHoldings(
                    proxy_symbol=symbol,
                    series_id=series,
                    accession=accession,
                    report_date=report.report_date,
                    holdings_count=report.total,
                    holdings=report.holdings,
                    countries=report.countries,
                    sectors=sectors,
                    fetched_at=now,
                    checked_at=now,
                )
            )
        return 1

    async def _sectors_for(self, holdings: Sequence[Mapping[str, object]]) -> dict[str, str]:
        """Sector per holding, via the SEC filer whose name matches (when one does)."""

        await self._ticker_map()
        names = self._names or {}
        sectors: dict[str, str] = {}
        for holding in holdings:
            key = str(holding.get("cusip") or holding.get("name") or "")
            cik = names.get(normalise_company(str(holding.get("name") or "")))
            if not key or cik is None:
                continue
            try:
                sic, _industry = await self._sic(cik)
            except httpx.HTTPError:
                continue
            sector = sic_sector(sic)
            if sector:
                sectors[key] = sector
        return sectors

    # -- reading --

    async def company(self, ticker: str) -> dict[str, object] | None:
        async with self._session_factory() as session:
            row = await session.get(CompanyFacts, ticker)
            if row is None or row.status != "ok":
                return None
            closes = list(
                await session.scalars(
                    select(MarketPriceDaily)
                    .where(
                        MarketPriceDaily.t212_ticker == ticker,
                        MarketPriceDaily.price_date
                        >= self._clock.utcnow().date() - timedelta(days=372),
                    )
                    .order_by(MarketPriceDaily.price_date)
                )
            )
        figures = dict(row.figures)
        price = float(closes[-1].close_price) if closes else None
        usd = bool(closes) and closes[-1].currency_code.upper() == "USD"
        shares = figures.get("shares_outstanding")
        eps = figures.get("eps_ttm")
        derived: dict[str, object] = {
            "price": price,
            "price_date": closes[-1].price_date.isoformat() if closes else None,
            "price_currency": closes[-1].currency_code if closes else None,
            "high_52w": max(float(close.close_price) for close in closes) if closes else None,
            "low_52w": min(float(close.close_price) for close in closes) if closes else None,
            "market_cap": price * shares if price and usd and isinstance(shares, float) else None,
            "pe": price / eps if price and usd and isinstance(eps, float) and eps > 0 else None,
        }
        return {
            "ticker": ticker,
            "name": row.name,
            "cik": row.cik,
            "industry": row.industry,
            "sector": row.sector,
            "country": row.country,
            "fiscal_year_end": row.fiscal_year_end,
            "fetched_at": row.fetched_at,
            "figures": figures,
            "derived": derived,
        }

    async def exposure(self) -> Exposure:
        async with self._session_factory() as session:
            latest = await session.scalar(
                select(PositionLive.ts).order_by(PositionLive.ts.desc()).limit(1)
            )
            positions = (
                [
                    row
                    for row in await session.scalars(
                        select(PositionLive).where(PositionLive.ts == latest)
                    )
                    if row.quantity > 0
                ]
                if latest
                else []
            )
            instruments = {
                row.t212_ticker: row
                for row in await session.scalars(
                    select(Instrument).where(
                        Instrument.t212_ticker.in_({row.t212_ticker for row in positions})
                    )
                )
            }
            facts = {row.t212_ticker: row for row in await session.scalars(select(CompanyFacts))}
            funds = {row.proxy_symbol: row for row in await session.scalars(select(FundHoldings))}
            nav = await session.scalar(
                select(DailyNav).order_by(DailyNav.as_of_date.desc()).limit(1)
            )
        return build_exposure(
            now=self._clock.utcnow(),
            positions=positions,
            instruments=instruments,
            facts=facts,
            funds=funds,
            cash_eur=float(nav.cash_balance_eur) if nav is not None else 0.0,
            sec_available=self.available,
        )


@dataclass
class _Company:
    key: str
    name: str
    ticker: str | None
    direct: float = 0.0
    via: dict[str, float] = field(default_factory=dict)
    #: A bucket rather than a company: a fund's remainder, cash, a fund not loaded yet.
    other: bool = False

    @property
    def total(self) -> float:
        return self.direct + sum(self.via.values())


class _Book:
    """Running totals while a portfolio is taken apart."""

    def __init__(self) -> None:
        self.companies: dict[str, _Company] = {}
        self.countries: dict[str, float] = {}
        self.sectors: dict[str, float] = {}

    def company(self, key: str, name: str, ticker: str | None, *, other: bool = False) -> _Company:
        if key not in self.companies:
            self.companies[key] = _Company(key, name, ticker, other=other)
        return self.companies[key]

    def country(self, code: str, eur: float) -> None:
        self.countries[code] = self.countries.get(code, 0.0) + eur

    def sector(self, name: str, eur: float) -> None:
        self.sectors[name] = self.sectors.get(name, 0.0) + eur


def _listing(bucket: Mapping[str, float]) -> list[dict[str, object]]:
    whole = sum(bucket.values()) or 1.0
    return [
        {"key": key, "eur": eur, "pct": eur / whole}
        for key, eur in sorted(bucket.items(), key=lambda item: item[1], reverse=True)
    ]


def _look_through(book: _Book, ticker: str, name: str, value: float, report: FundHoldings) -> None:
    covered = 0.0
    for holding in report.holdings:
        share = float(str(holding.get("pct") or 0)) / 100.0
        if share <= 0:
            continue
        covered += share
        key = str(holding.get("cusip") or holding.get("name"))
        entry = book.company(key, str(holding.get("name")), None)
        entry.via[ticker] = entry.via.get(ticker, 0.0) + value * share
        book.sector(str(report.sectors.get(key, "Unclassified")), value * share)
    rest = max(0.0, 1.0 - covered)
    if rest > 0:
        remainder = book.company(f"other:{ticker}", f"Other holdings of {name}", ticker, other=True)
        remainder.via[ticker] = value * rest
        book.sector("Unclassified", value * rest)
    total_pct = sum(report.countries.values()) or 100.0
    for code, pct in report.countries.items():
        book.country(code, value * pct / total_pct)


def build_exposure(
    *,
    now: datetime,
    positions: Sequence[PositionLive],
    instruments: Mapping[str, Instrument],
    facts: Mapping[str, CompanyFacts],
    funds: Mapping[str, FundHoldings],
    cash_eur: float,
    sec_available: bool,
) -> Exposure:
    book = _Book()
    fund_rows: list[dict[str, object]] = []

    for position in positions:
        value = float(position.wallet_current_value or 0)
        if value <= 0:
            continue
        instrument = instruments.get(position.t212_ticker)
        isin = (instrument.isin if instrument else None) or position.isin
        name = (
            (instrument.name if instrument else None)
            or position.instrument_name
            or position.t212_ticker
        )
        proxy = FUND_PROXIES.get(isin or "")
        if proxy is not None and proxy.kind == "commodity":
            asset = proxy.asset or name
            book.company(f"asset:{asset}", asset, position.t212_ticker).direct += value
            book.country("Commodity", value)
            book.sector("Commodities", value)
        elif proxy is not None:
            report = funds.get(proxy.symbol)
            fund_rows.append(
                {
                    "ticker": position.t212_ticker,
                    "name": name,
                    "proxy": proxy.symbol,
                    "proxy_label": proxy.label,
                    "report_date": report.report_date.isoformat()
                    if report and report.report_date
                    else None,
                    "holdings_count": report.holdings_count if report else None,
                    "value_eur": value,
                }
            )
            if report is None:
                pending = book.company(
                    f"fund:{position.t212_ticker}", f"{name} (holdings not loaded yet)",
                    position.t212_ticker, other=True,
                )  # fmt: skip
                pending.direct += value
                book.country("Unknown", value)
                book.sector("Unclassified", value)
            else:
                _look_through(book, position.t212_ticker, name, value, report)
        else:
            company_facts = facts.get(position.t212_ticker)
            entry = book.company(_cusip(isin) or position.t212_ticker, name, position.t212_ticker)
            entry.ticker = position.t212_ticker
            entry.direct += value
            book.country(
                (company_facts.country if company_facts and company_facts.country else None)
                or (isin or "??")[:2],
                value,
            )
            book.sector((company_facts.sector if company_facts else None) or "Unclassified", value)

    if cash_eur > 0:
        book.country("Cash", cash_eur)
        book.sector("Cash", cash_eur)
        book.company("cash", "Cash", None, other=True).direct += cash_eur

    total = sum(entry.total for entry in book.companies.values())
    ranked = sorted(book.companies.values(), key=lambda entry: entry.total, reverse=True)
    companies: list[dict[str, object]] = [
        {
            "key": entry.key,
            "name": entry.name,
            "ticker": entry.ticker,
            "direct_eur": entry.direct,
            "via": [{"ticker": ticker, "eur": eur} for ticker, eur in sorted(entry.via.items())],
            "total_eur": entry.total,
            "pct": entry.total / total if total else 0.0,
            "other": entry.other,
        }
        for entry in ranked
    ]

    warnings: list[str] = []
    for entry in ranked:
        share = entry.total / total if total else 0.0
        if entry.other or share < 0.10:
            continue
        detail = ""
        if entry.via and entry.direct > 0:
            detail = f" ({entry.direct / total:.1%} directly, the rest through your funds)"
        warnings.append(f"{entry.name} is {share:.1%} of everything you hold{detail}.")

    notes: list[str] = []
    if not sec_available:
        notes.append(
            "Add a contact for the SEC in Settings (News) to see inside your funds and each "
            "company's sector and country."
        )
    if fund_rows:
        notes.append(
            "Fund holdings come from a US fund tracking the same (or a very close) index, as "
            "reported to the SEC each quarter: a close approximation, not your fund's own list."
        )
    return Exposure(
        as_of=now.astimezone(UTC),
        total_eur=total,
        companies=companies,
        countries=_listing(book.countries),
        sectors=_listing(book.sectors),
        funds=fund_rows,
        warnings=warnings,
        notes=notes,
    )


__all__ = [
    "FUND_PROXIES",
    "Exposure",
    "SecDataService",
    "build_exposure",
    "extract_figures",
    "normalise_company",
    "parse_nport",
    "sic_sector",
    "trailing_twelve_months",
]
