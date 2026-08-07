from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, cast, runtime_checkable

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from .ai import AiAnalysisService, ClaudeAiClient
from .client import Trading212Client
from .config import Settings, load_settings
from .db import create_engine, create_session_factory, migrate_database
from .news import NewsHttpClient, NewsSyncService
from .performance import (
    AlphaVantageMarketDataProvider,
    EcbFxRateProvider,
    NullFactorDataProvider,
    NullMarketDataProvider,
    PerformanceReplayService,
)
from .portfolio_repository import PortfolioRepository
from .portfolio_sync import PortfolioSyncService
from .raw_snapshots import RawSnapshotRepository
from .reporting import PortfolioQualityReportService
from .resolver import OpenFigiResolver
from .services import Trading212Service
from .thesis import ThesisService


@dataclass
class Container:
    settings: Settings
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    snapshot_repository: RawSnapshotRepository
    portfolio_repository: PortfolioRepository
    t212_client: Trading212Client
    instrument_resolver: OpenFigiResolver
    portfolio_sync_service: PortfolioSyncService
    portfolio_quality_report_service: PortfolioQualityReportService
    performance_replay_service: PerformanceReplayService
    news_sync_service: NewsSyncService
    ai_analysis_service: AiAnalysisService
    thesis_service: ThesisService
    t212_service: Trading212Service
    market_data_provider: object
    fx_rate_provider: object
    factor_data_provider: object
    news_feed_provider: object
    ai_client: object

    async def startup(self) -> None:
        await migrate_database(self.settings)

    async def shutdown(self) -> None:
        await self.instrument_resolver.aclose()
        await self.t212_client.aclose()
        for provider in (
            self.market_data_provider,
            self.fx_rate_provider,
            self.factor_data_provider,
            self.news_feed_provider,
            self.ai_client,
        ):
            if isinstance(provider, AsyncCloseable):
                await provider.aclose()
        await self.engine.dispose()


@runtime_checkable
class AsyncCloseable(Protocol):
    async def aclose(self) -> None: ...


def build_container(settings: Settings | None = None) -> Container:
    resolved_settings = settings or load_settings()
    engine = create_engine(resolved_settings)
    session_factory = create_session_factory(engine)
    snapshot_repository = RawSnapshotRepository(session_factory)
    portfolio_repository = PortfolioRepository(session_factory)
    client = Trading212Client(settings=resolved_settings, snapshot_writer=snapshot_repository)
    resolver = OpenFigiResolver(resolved_settings)
    market_data_provider = (
        AlphaVantageMarketDataProvider(resolved_settings)
        if resolved_settings.market_data_provider == "alphavantage"
        else NullMarketDataProvider()
    )
    fx_rate_provider = EcbFxRateProvider(resolved_settings)
    factor_data_provider = NullFactorDataProvider()
    sync_service = PortfolioSyncService(
        settings=resolved_settings,
        session_factory=session_factory,
        client=client,
        repository=portfolio_repository,
        resolver=resolver,
    )
    report_service = PortfolioQualityReportService(portfolio_repository, resolved_settings)
    performance_service = PerformanceReplayService(
        portfolio_repository,
        resolved_settings,
        market_data_provider,
        fx_rate_provider,
        factor_data_provider,
    )
    news_feed_provider = NewsHttpClient(resolved_settings)
    news_service = NewsSyncService(
        portfolio_repository, resolved_settings, news_feed_provider.adapters
    )
    ai_client = ClaudeAiClient(resolved_settings)
    ai_service = AiAnalysisService(
        portfolio_repository, resolved_settings, performance_service, ai_client
    )
    thesis_service = ThesisService(portfolio_repository)
    service = Trading212Service(client)
    return Container(
        settings=resolved_settings,
        engine=engine,
        session_factory=session_factory,
        snapshot_repository=snapshot_repository,
        portfolio_repository=portfolio_repository,
        t212_client=client,
        instrument_resolver=resolver,
        portfolio_sync_service=sync_service,
        portfolio_quality_report_service=report_service,
        performance_replay_service=performance_service,
        news_sync_service=news_service,
        ai_analysis_service=ai_service,
        thesis_service=thesis_service,
        t212_service=service,
        market_data_provider=market_data_provider,
        fx_rate_provider=fx_rate_provider,
        factor_data_provider=factor_data_provider,
        news_feed_provider=news_feed_provider,
        ai_client=ai_client,
    )


def get_container(request: Request) -> Container:
    return cast(Container, request.app.state.container)


def get_t212_service(request: Request) -> Trading212Service:
    return get_container(request).t212_service


def get_portfolio_sync_service(request: Request) -> PortfolioSyncService:
    return get_container(request).portfolio_sync_service


def get_portfolio_quality_report_service(request: Request) -> PortfolioQualityReportService:
    return get_container(request).portfolio_quality_report_service


def get_performance_replay_service(request: Request) -> PerformanceReplayService:
    return get_container(request).performance_replay_service


def get_news_sync_service(request: Request) -> NewsSyncService:
    return get_container(request).news_sync_service


def get_ai_analysis_service(request: Request) -> AiAnalysisService:
    return get_container(request).ai_analysis_service


def get_thesis_service(request: Request) -> ThesisService:
    return get_container(request).thesis_service
