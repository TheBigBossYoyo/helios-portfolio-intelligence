from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from .client import Trading212Client
from .config import Settings, load_settings
from .db import create_engine, create_session_factory, migrate_database
from .portfolio_repository import PortfolioRepository
from .portfolio_sync import PortfolioSyncService
from .raw_snapshots import RawSnapshotRepository
from .reporting import PortfolioQualityReportService
from .resolver import OpenFigiResolver
from .services import Trading212Service


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
    t212_service: Trading212Service

    async def startup(self) -> None:
        await migrate_database(self.settings)

    async def shutdown(self) -> None:
        await self.instrument_resolver.aclose()
        await self.t212_client.aclose()
        await self.engine.dispose()


def build_container(settings: Settings | None = None) -> Container:
    resolved_settings = settings or load_settings()
    engine = create_engine(resolved_settings)
    session_factory = create_session_factory(engine)
    snapshot_repository = RawSnapshotRepository(session_factory)
    portfolio_repository = PortfolioRepository(session_factory)
    client = Trading212Client(settings=resolved_settings, snapshot_writer=snapshot_repository)
    resolver = OpenFigiResolver(resolved_settings)
    sync_service = PortfolioSyncService(
        settings=resolved_settings,
        session_factory=session_factory,
        client=client,
        repository=portfolio_repository,
        resolver=resolver,
    )
    report_service = PortfolioQualityReportService(portfolio_repository, resolved_settings)
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
        t212_service=service,
    )


def get_container(request: Request) -> Container:
    return cast(Container, request.app.state.container)


def get_t212_service(request: Request) -> Trading212Service:
    return get_container(request).t212_service


def get_portfolio_sync_service(request: Request) -> PortfolioSyncService:
    return get_container(request).portfolio_sync_service


def get_portfolio_quality_report_service(request: Request) -> PortfolioQualityReportService:
    return get_container(request).portfolio_quality_report_service
