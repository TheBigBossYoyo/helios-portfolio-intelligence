"""End-to-end M3 smoke: seed a ledger, replay, report, and hit the API."""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from helios.app import create_app
from helios.config import Settings
from helios.db import migrate_database
from helios.dependencies import get_performance_replay_service
from helios.models import Dividend, Instrument, OrderHistory, Transaction
from helios.performance import DailyPricePoint, FxRatePoint, PerformanceReplayService
from helios.portfolio_repository import PortfolioRepository
from helios.rate_limit import Clock
from helios.schemas import PerformanceReplaySummaryModel, PerformanceReportModel

DATA_DIR = Path(sys.argv[1])
START = date(2024, 1, 1)
END = date(2024, 5, 1)


class FixedClock(Clock):
    def now(self) -> float:
        return 0.0

    def utcnow(self) -> datetime:
        return datetime(2024, 5, 1, tzinfo=UTC)

    async def sleep(self, seconds: float) -> None:
        del seconds


class Prices:
    async def fetch_daily_closes(self, *, requests, start_date, end_date):
        del start_date, end_date
        out = {}
        for request in requests:
            # Business days only, so the forward-fill path is genuinely exercised.
            points = []
            day = START
            index = 0
            while day <= END:
                if day.weekday() < 5:
                    base = 100 if request.key == "ABC_US_EQ" else 50
                    points.append(
                        DailyPricePoint(
                            day,
                            Decimal(base) + Decimal(index % 17),
                            request.currency_code or "USD",
                            "fixture",
                            day,
                            "EXACT",
                        )
                    )
                    index += 1
                day += timedelta(days=1)
            out[request.key] = points
        return out


class Fx:
    async def fetch_eur_base_rates(self, *, currencies, start_date, end_date):
        del start_date, end_date
        out = {}
        for currency in currencies:
            points = []
            day = START
            while day <= END:
                if day.weekday() < 5:
                    points.append(
                        FxRatePoint(day, currency, Decimal("0.9"), "fixture", day, "EXACT", False)
                    )
                day += timedelta(days=1)
            out[currency] = points
        return out


async def main() -> None:
    settings = Settings(data_dir=DATA_DIR, benchmark_vwrp_currency="USD")
    await migrate_database(settings)
    engine = create_async_engine(settings.sqlite_url, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    repository = PortfolioRepository(session_factory)

    async with session_factory() as session, session.begin():
        session.add(
            Instrument(
                t212_ticker="ABC_US_EQ",
                yahoo_ticker="ABC",
                currency_code="USD",
                mapping_status="resolved",
            )
        )
        session.add(
            Transaction(
                reference="dep-1",
                ts=datetime(2024, 1, 2, tzinfo=UTC),
                transaction_type="DEPOSIT",
                currency_code="EUR",
                amount=Decimal("1000"),
            )
        )
        session.add(
            Transaction(
                reference="dep-2-usd",
                ts=datetime(2024, 3, 1, tzinfo=UTC),
                transaction_type="DEPOSIT",
                currency_code="USD",
                amount=Decimal("500"),
            )
        )
        session.add(
            OrderHistory(
                fill_id="buy-1",
                fill_timestamp=datetime(2024, 1, 2, tzinfo=UTC),
                t212_ticker="ABC_US_EQ",
                side="BUY",
                fill_type="TRADE",
                filled_quantity=Decimal("5"),
                wallet_currency="EUR",
                wallet_net_value=Decimal("450"),
            )
        )
        session.add(
            Dividend(
                reference="div-1",
                paid_on=datetime(2024, 2, 15, tzinfo=UTC),
                t212_ticker="ABC_US_EQ",
                currency_code="USD",
                amount=Decimal("12"),
                amount_in_euro=Decimal("10.8"),
            )
        )

    service = PerformanceReplayService(
        repository, settings, Prices(), Fx(), clock=FixedClock()
    )
    summary = await service.replay(as_of=END)
    print("=== REPLAY SUMMARY ===")
    print(
        json.dumps(
            PerformanceReplaySummaryModel.model_validate(
                summary, from_attributes=True
            ).model_dump(mode="json", by_alias=True),
            indent=2,
        )
    )

    report = await service.get_report()
    payload = PerformanceReportModel.model_validate(report, from_attributes=True).model_dump(
        mode="json", by_alias=True
    )
    print("=== REPORT (headline) ===")
    for key in (
        "flowTiming",
        "annualizationDays",
        "cumulativeTwr",
        "xirr",
        "annualizedReturn",
        "volatility",
        "sharpe",
        "maxDrawdown",
        "recoveryDays",
        "hhi",
        "var95_1d",
        "cvar95_1d",
        "var99_10d",
        "attribution",
        "correlationClusters",
        "ff5MomentumRegression",
    ):
        print(f"  {key}: {json.dumps(payload[key])}")
    print(f"  passiveCounterfactual.status: {payload['passiveCounterfactual']['status']}")
    print(f"  passiveCounterfactual.invested: {payload['passiveCounterfactual']['investedEur']}")
    print(f"  passiveCounterfactual.final: {payload['passiveCounterfactual']['finalValueEur']}")
    print(f"  passiveCounterfactual.actualNav: {payload['passiveCounterfactual']['actualNavEur']}")
    print(f"  passiveCounterfactual.detail: {payload['passiveCounterfactual']['detail']}")
    print(f"  contributions: {json.dumps(payload['contributions'])}")
    print(f"  betaVsBenchmarks[vwrp].beta: {json.dumps(payload['betaVsBenchmarks'][2]['beta'])}")
    print(f"  rollingVolatility30d: {len(payload['rollingVolatility30d'])} points")
    print(f"  rollingVolatility90d: {len(payload['rollingVolatility90d'])} points")
    print(f"  rollingBeta30d: {len(payload['rollingBeta30d'])} points")
    print(f"  rollingBeta90d: {len(payload['rollingBeta90d'])} points")
    print(f"  notes: {json.dumps(payload['notes'], indent=2)}")

    await engine.dispose()

    app = create_app(settings)
    app.dependency_overrides[get_performance_replay_service] = lambda: service
    with TestClient(app) as client:
        denied = client.post("/api/v1/performance/replay")
        allowed = client.post(
            "/api/v1/performance/replay", headers={"X-Helios-Local-Action": "replay"}
        )
        report_response = client.get("/api/v1/performance/report")
    print("=== API ===")
    print(f"  POST /replay without header -> {denied.status_code} {denied.json()['detail']}")
    print(f"  POST /replay with header    -> {allowed.status_code}")
    print(f"  GET  /report                -> {report_response.status_code}")
    print(f"  report keys: {len(report_response.json())}")


asyncio.run(main())
