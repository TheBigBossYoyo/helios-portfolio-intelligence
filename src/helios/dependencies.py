from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from .client import Trading212Client
from .config import Settings, load_settings
from .db import create_engine, create_session_factory, initialize_database
from .raw_snapshots import RawSnapshotRepository
from .services import Trading212Service


@dataclass
class Container:
    settings: Settings
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    snapshot_repository: RawSnapshotRepository
    t212_client: Trading212Client
    t212_service: Trading212Service

    async def startup(self) -> None:
        await initialize_database(self.engine)

    async def shutdown(self) -> None:
        await self.t212_client.aclose()
        await self.engine.dispose()


def build_container(settings: Settings | None = None) -> Container:
    resolved_settings = settings or load_settings()
    engine = create_engine(resolved_settings)
    session_factory = create_session_factory(engine)
    snapshot_repository = RawSnapshotRepository(session_factory)
    client = Trading212Client(settings=resolved_settings, snapshot_writer=snapshot_repository)
    service = Trading212Service(client)
    return Container(
        settings=resolved_settings,
        engine=engine,
        session_factory=session_factory,
        snapshot_repository=snapshot_repository,
        t212_client=client,
        t212_service=service,
    )


def get_container(request: Request) -> Container:
    return cast(Container, request.app.state.container)


def get_t212_service(request: Request) -> Trading212Service:
    return get_container(request).t212_service
