"""SEC company facts, fund look-through and exposure."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from helios.config import Settings
from helios.db import migrate_database
from helios.models import (
    CompanyFacts,
    FundHoldings,
    Instrument,
    MarketPriceDaily,
    PositionLive,
)
from helios.rate_limit import Clock
from helios.sec_data import (
    SecDataService,
    build_exposure,
    extract_figures,
    normalise_company,
    parse_nport,
    sic_sector,
)

D = Decimal
NOW = datetime(2026, 9, 30, 10, 0, tzinfo=UTC)


class FixedClock(Clock):
    def __init__(self, current: datetime) -> None:
        self.current = current

    def now(self) -> float:
        return self.current.timestamp()

    def utcnow(self) -> datetime:
        return self.current

    async def sleep(self, seconds: float) -> None:
        return None


def fact(start: str, end: str, value: float, filed: str = "2026-08-01") -> dict[str, object]:
    return {"start": start, "end": end, "val": value, "filed": filed}


def companyfacts() -> dict[str, object]:
    """A company with a January year end, like NVIDIA: FY to 2026-01-25, then two quarters."""

    revenue = [
        fact("2025-01-27", "2026-01-25", 1000.0),  # fiscal year
        fact("2025-01-27", "2025-04-27", 200.0),  # Q1 last year
        fact("2025-04-28", "2025-07-27", 230.0),  # Q2 last year
        fact("2025-01-27", "2025-07-27", 430.0),  # six months last year
        fact("2025-01-27", "2025-10-26", 700.0),  # nine months last year
        fact("2026-01-26", "2026-04-26", 300.0),  # Q1
        fact("2026-04-27", "2026-07-26", 345.0),  # Q2
        fact("2026-01-26", "2026-07-26", 645.0),  # six months
    ]
    net = [
        fact("2025-01-27", "2026-01-25", 500.0),
        fact("2025-01-27", "2025-07-27", 200.0),
        fact("2026-01-26", "2026-07-26", 330.0),
        fact("2025-04-28", "2025-07-27", 110.0),
        fact("2026-04-27", "2026-07-26", 180.0),
    ]
    stale = [fact("2018-01-01", "2018-12-31", 0.0)]
    return {
        "facts": {
            "us-gaap": {
                "Revenues": {"units": {"USD": revenue}},
                "NetIncomeLoss": {"units": {"USD": net}},
                "OperatingIncomeLoss": {"units": {"USD": stale}},
                "EarningsPerShareDiluted": {
                    "units": {
                        "USD/shares": [
                            fact("2025-01-27", "2026-01-25", 5.0),
                            fact("2025-01-27", "2025-07-27", 2.0),
                            fact("2026-01-26", "2026-07-26", 3.3),
                        ]
                    }
                },
            },
            "dei": {
                "EntityCommonStockSharesOutstanding": {
                    "units": {
                        "shares": [
                            {"end": "2026-08-20", "val": 100},
                            {"end": "2026-08-20", "val": 50},  # a second share class
                            {"end": "2025-08-20", "val": 999},
                        ]
                    }
                }
            },
        }
    }


def test_trailing_figures_roll_the_year_forward_and_ignore_stale_concepts() -> None:
    figures = extract_figures(companyfacts())
    # FY 1000 + this year's six months 645 - last year's six months 430.
    assert figures["revenue_ttm"] == 1215.0
    assert figures["revenue_ttm_end"] == "2026-07-26"
    assert figures["net_income_ttm"] == 630.0
    assert figures["eps_ttm"] == pytest.approx(6.3)
    assert figures["revenue_quarter"] == 345.0
    assert figures["revenue_growth"] == pytest.approx(345 / 230 - 1)
    assert figures["net_income_margin"] == pytest.approx(630 / 1215)
    assert figures["shares_outstanding"] == 150.0
    # A line last reported in 2018 is not a current figure.
    assert "operating_income_ttm" not in figures
    assert "operating_income_margin" not in figures


def test_sectors_and_names() -> None:
    assert sic_sector(3674) == "Technology"
    assert sic_sector(6021) == "Financials"
    assert sic_sector(2834) == "Health Care"
    assert sic_sector(4911) == "Utilities"
    assert sic_sector(1040) == "Materials"
    assert sic_sector(None) is None
    assert normalise_company("Apple Inc.") == normalise_company("APPLE INC") == "apple"
    assert normalise_company("Alphabet Inc Class A") == "alphabet"


NPORT_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<edgarSubmission xmlns="http://www.sec.gov/edgar/nport">
  <formData>
    <genInfo><repPdDate>2026-06-30</repPdDate></genInfo>
    <invstOrSecs>
      <invstOrSec><name>NVIDIA Corp</name><lei>L1</lei><cusip>67066G104</cusip>
        <identifiers><isin value="US67066G1040"/></identifiers>
        <pctVal>7.5</pctVal><assetCat>EC</assetCat><invCountry>US</invCountry></invstOrSec>
      <invstOrSec><name>Alphabet Inc</name><lei>L2</lei><cusip>02079K305</cusip>
        <pctVal>2.0</pctVal><assetCat>EC</assetCat><invCountry>US</invCountry></invstOrSec>
      <invstOrSec><name>Alphabet Inc</name><lei>L2</lei><cusip>02079K107</cusip>
        <pctVal>1.5</pctVal><assetCat>EC</assetCat><invCountry>US</invCountry></invstOrSec>
      <invstOrSec><name>Toyota Motor Corp</name><lei>L3</lei><cusip>000000000</cusip>
        <pctVal>0.5</pctVal><assetCat>EC</assetCat><invCountry>JP</invCountry></invstOrSec>
    </invstOrSecs>
  </formData>
</edgarSubmission>"""


def test_an_nport_report_merges_share_classes_and_sums_countries() -> None:
    report = parse_nport(NPORT_XML)
    assert report.report_date == date(2026, 6, 30)
    assert report.total == 4
    assert [(row["name"], row["pct"]) for row in report.holdings] == [
        ("NVIDIA Corp", 7.5),
        ("Alphabet Inc", 3.5),
        ("Toyota Motor Corp", 0.5),
    ]
    assert report.holdings[0]["isin"] == "US67066G1040"
    assert report.holdings[2]["cusip"] is None
    assert report.countries == {"US": 11.0, "JP": 0.5}


def _position(ticker: str, isin: str, value: str) -> PositionLive:
    return PositionLive(
        ts=NOW, t212_ticker=ticker, isin=isin, quantity=D("1"), wallet_current_value=D(value)
    )


def test_exposure_merges_direct_shares_with_what_funds_hold() -> None:
    positions = [
        _position("NVDA_US_EQ", "US67066G1040", "100"),
        _position("VUAGl_EQ", "IE00BFMXXD54", "1000"),
        _position("SSLNl_EQ", "IE00B4NCWG09", "50"),
    ]
    instruments = {
        "NVDA_US_EQ": Instrument(
            t212_ticker="NVDA_US_EQ", name="Nvidia", isin="US67066G1040", instrument_type="STOCK"
        ),
        "VUAGl_EQ": Instrument(
            t212_ticker="VUAGl_EQ",
            name="Vanguard S&P 500",
            isin="IE00BFMXXD54",
            instrument_type="ETF",
        ),
        "SSLNl_EQ": Instrument(
            t212_ticker="SSLNl_EQ",
            name="iShares Physical Silver",
            isin="IE00B4NCWG09",
            instrument_type="ETF",
        ),
    }
    report = parse_nport(NPORT_XML)
    fund = FundHoldings(
        proxy_symbol="VOO",
        series_id="S1",
        accession="A1",
        report_date=report.report_date,
        holdings_count=report.total,
        holdings=report.holdings,
        countries=report.countries,
        sectors={"67066G104": "Technology"},
        fetched_at=NOW,
        checked_at=NOW,
    )
    facts = {
        "NVDA_US_EQ": CompanyFacts(
            t212_ticker="NVDA_US_EQ",
            sector="Technology",
            country="US",
            figures={},
            status="ok",
            fetched_at=NOW,
        )
    }
    exposure = build_exposure(
        now=NOW,
        positions=positions,
        instruments=instruments,
        facts=facts,
        funds={"VOO": fund},
        cash_eur=50.0,
        sec_available=True,
    )
    assert exposure.total_eur == pytest.approx(1200)
    nvidia = next(row for row in exposure.companies if row["ticker"] == "NVDA_US_EQ")
    # 100 held directly + 7.5% of the 1000 in the S&P 500 fund.
    assert nvidia["total_eur"] == pytest.approx(175)
    assert nvidia["via"] == [{"ticker": "VUAGl_EQ", "eur": pytest.approx(75)}]
    assert nvidia["pct"] == pytest.approx(175 / 1200)
    other = next(row for row in exposure.companies if row["key"] == "other:VUAGl_EQ")
    assert other["other"] is True and other["total_eur"] == pytest.approx(1000 * (1 - 0.115))
    silver = next(row for row in exposure.companies if row["name"] == "Silver")
    assert silver["total_eur"] == pytest.approx(50)
    countries = {row["key"]: row["eur"] for row in exposure.countries}
    assert countries["JP"] == pytest.approx(1000 * 0.5 / 11.5)
    assert countries["Commodity"] == pytest.approx(50) and countries["Cash"] == pytest.approx(50)
    sectors = {row["key"]: row["eur"] for row in exposure.sectors}
    assert sectors["Technology"] == pytest.approx(175)
    assert exposure.warnings == [
        "Nvidia is 14.6% of everything you hold (8.3% directly, the rest through your funds)."
    ]
    assert exposure.funds[0]["proxy"] == "VOO"


async def _factory(tmp_path: Path) -> async_sessionmaker[AsyncSession]:
    settings = Settings(data_dir=tmp_path, sqlite_filename="sec.sqlite3")
    await migrate_database(settings)
    engine = create_async_engine(settings.sqlite_url, poolclass=NullPool)
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.mark.asyncio
async def test_refresh_fetches_facts_and_fund_reports_once_then_waits(tmp_path: Path) -> None:
    factory = await _factory(tmp_path)
    async with factory() as session, session.begin():
        session.add_all(
            [
                Instrument(
                    t212_ticker="NVDA_US_EQ",
                    name="Nvidia",
                    isin="US67066G1040",
                    instrument_type="STOCK",
                    yahoo_ticker="NVDA",
                    mapping_status="resolved",
                ),
                Instrument(
                    t212_ticker="VUAGl_EQ",
                    name="Vanguard S&P 500",
                    isin="IE00BFMXXD54",
                    instrument_type="ETF",
                    yahoo_ticker="VUAG.L",
                    mapping_status="resolved",
                ),
                _position("NVDA_US_EQ", "US67066G1040", "100"),
                _position("VUAGl_EQ", "IE00BFMXXD54", "1000"),
                MarketPriceDaily(
                    price_date=date(2026, 9, 29),
                    t212_ticker="NVDA_US_EQ",
                    provider_symbol="NVDA",
                    currency_code="USD",
                    close_price=D("126"),
                    provider="twelvedata",
                    source_date=date(2026, 9, 29),
                    provenance="exact",
                ),
            ]
        )
    seen: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        seen.append(request.url.path)
        assert request.headers["user-agent"] == "Helios test contact@example.com"
        if url.endswith("/files/company_tickers.json"):
            return httpx.Response(
                200, json={"0": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"}}
            )
        if "/submissions/CIK0001045810.json" in url:
            return httpx.Response(
                200,
                json={
                    "name": "NVIDIA CORP",
                    "sic": "3674",
                    "sicDescription": "Semiconductors",
                    "fiscalYearEnd": "0125",
                    "addresses": {"business": {"stateOrCountry": "CA"}},
                },
            )
        if "/api/xbrl/companyfacts/CIK0001045810.json" in url:
            return httpx.Response(200, json=companyfacts())
        if url.endswith("/files/company_tickers_mf.json"):
            return httpx.Response(
                200,
                json={
                    "fields": ["cik", "seriesId", "classId", "symbol"],
                    "data": [[36405, "S000002839", "C1", "VOO"]],
                },
            )
        if "browse-edgar" in url:
            return httpx.Response(
                200,
                text="<feed><entry><filing-href>https://www.sec.gov/Archives/edgar/data/36405/000003640526000473/0000036405-26-000473-index.htm</filing-href></entry></feed>",
            )
        if url.endswith("/000003640526000473/primary_doc.xml"):
            return httpx.Response(200, content=NPORT_XML)
        return httpx.Response(404)

    settings = Settings(data_dir=tmp_path, news_sec_user_agent="Helios test contact@example.com")
    service = SecDataService(
        factory,
        settings,
        clock=FixedClock(NOW),
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(respond),
            headers={"User-Agent": "Helios test contact@example.com"},
        ),
    )
    first = await service.refresh()
    asked = len(seen)
    second = await service.refresh()

    assert first == {"facts": 1, "funds": 1}
    assert second == {"facts": 0, "funds": 0} and len(seen) == asked  # nothing due yet

    facts = await service.company("NVDA_US_EQ")
    assert facts is not None
    assert facts["sector"] == "Technology" and facts["country"] == "US"
    derived = facts["derived"]
    assert isinstance(derived, dict)
    assert derived["market_cap"] == pytest.approx(126 * 150)
    assert derived["pe"] == pytest.approx(126 / 6.3)

    exposure = await service.exposure()
    nvidia = next(row for row in exposure.companies if row["ticker"] == "NVDA_US_EQ")
    assert nvidia["total_eur"] == pytest.approx(175)

    # A week later the fund is checked again, but an unchanged report is not downloaded again.
    service._clock = FixedClock(NOW + timedelta(days=8))
    seen.clear()
    assert (await service.refresh())["funds"] == 0
    assert not any(path.endswith("primary_doc.xml") for path in seen)


@pytest.mark.asyncio
async def test_without_an_sec_contact_nothing_is_asked(tmp_path: Path) -> None:
    factory = await _factory(tmp_path)

    def fail(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request expected")

    service = SecDataService(
        factory,
        Settings(data_dir=tmp_path),
        client=httpx.AsyncClient(transport=httpx.MockTransport(fail)),
    )
    assert await service.refresh() == {"facts": 0, "funds": 0}
    exposure = await service.exposure()
    assert exposure.notes and "SEC" in exposure.notes[0]
    assert json.dumps(exposure.companies) == "[]"
