from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, cast, runtime_checkable

from fastapi import Request
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from .ai import AiAnalysisService, ClaudeAiClient
from .card_history import CardHistoryService
from .client import Trading212Client
from .config import Settings, load_settings
from .db import create_engine, create_session_factory, migrate_database
from .news import NewsHttpClient, NewsReparseService, NewsSyncService
from .performance import (
    AlphaVantageMarketDataProvider,
    CompositeMarketDataProvider,
    EcbFxRateProvider,
    KenFrenchFactorDataProvider,
    MarketDataProvider,
    NullFactorDataProvider,
    NullMarketDataProvider,
    PerformanceReplayService,
    TwelveDataMarketDataProvider,
)
from .portfolio_repository import PortfolioRepository
from .portfolio_sync import PortfolioSyncService
from .raw_snapshots import RawSnapshotRepository
from .reporting import PortfolioQualityReportService
from .resolver import OpenFigiResolver
from .services import Trading212Service
from .t212_reparse import T212ReparseService
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
    news_reparse_service: NewsReparseService
    t212_reparse_service: T212ReparseService
    ai_analysis_service: AiAnalysisService
    thesis_service: ThesisService
    t212_service: Trading212Service
    card_history_service: CardHistoryService
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


def _build_single_market_data_provider(
    settings: Settings, provider_name: str, api_key: SecretStr | None
) -> MarketDataProvider:
    if provider_name == "twelvedata":
        return TwelveDataMarketDataProvider(settings, api_key=api_key)
    if provider_name == "alphavantage":
        return AlphaVantageMarketDataProvider(settings, api_key=api_key)
    return NullMarketDataProvider()


def _build_market_data_provider(settings: Settings) -> MarketDataProvider:
    primary = _build_single_market_data_provider(
        settings, settings.market_data_provider, settings.market_data_api_key
    )
    fallback_provider = settings.effective_market_data_fallback_provider
    if fallback_provider == "disabled":
        return primary
    fallback = _build_single_market_data_provider(
        settings, fallback_provider, settings.market_data_fallback_api_key
    )
    return CompositeMarketDataProvider(primary, fallback)


def build_container(settings: Settings | None = None) -> Container:
    resolved_settings = settings or load_settings()
    engine = create_engine(resolved_settings)
    session_factory = create_session_factory(engine)
    snapshot_repository = RawSnapshotRepository(session_factory)
    portfolio_repository = PortfolioRepository(session_factory)
    client = Trading212Client(settings=resolved_settings, snapshot_writer=snapshot_repository)
    resolver = OpenFigiResolver(resolved_settings)
    market_data_provider = _build_market_data_provider(resolved_settings)
    fx_rate_provider = EcbFxRateProvider(resolved_settings)
    factor_data_provider = (
        KenFrenchFactorDataProvider(resolved_settings)
        if resolved_settings.factor_data_provider == "kenfrench"
        else NullFactorDataProvider()
    )
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
    news_reparse_service = NewsReparseService(
        portfolio_repository, resolved_settings, news_feed_provider.adapters
    )
    t212_reparse_service = T212ReparseService(
        portfolio_repository, snapshot_repository, resolved_settings, session_factory
    )
    ai_client = ClaudeAiClient(resolved_settings)
    ai_service = AiAnalysisService(
        portfolio_repository, resolved_settings, performance_service, ai_client
    )
    thesis_service = ThesisService(portfolio_repository)
    service = Trading212Service(client)
    card_history_service = CardHistoryService(portfolio_repository, client, resolved_settings)
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
        news_reparse_service=news_reparse_service,
        t212_reparse_service=t212_reparse_service,
        ai_analysis_service=ai_service,
        thesis_service=thesis_service,
        t212_service=service,
        card_history_service=card_history_service,
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


def get_card_history_service(request: Request) -> CardHistoryService:
    return get_container(request).card_history_service


def get_news_sync_service(request: Request) -> NewsSyncService:
    return get_container(request).news_sync_service


def get_news_reparse_service(request: Request) -> NewsReparseService:
    return get_container(request).news_reparse_service


def get_t212_reparse_service(request: Request) -> T212ReparseService:
    return get_container(request).t212_reparse_service


def get_ai_analysis_service(request: Request) -> AiAnalysisService:
    return get_container(request).ai_analysis_service


def get_thesis_service(request: Request) -> ThesisService:
    return get_container(request).thesis_service
