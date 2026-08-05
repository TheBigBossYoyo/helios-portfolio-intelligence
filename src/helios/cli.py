from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Awaitable, Callable
from decimal import Decimal

from .config import load_settings
from .dependencies import Container, build_container
from .schemas import PortfolioSyncSummary, Position, QualityReport


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "positions":
        return asyncio.run(_run_positions())
    if args.command == "sync":
        return asyncio.run(_run_sync(force_metadata=args.force_metadata))
    if args.command == "quality":
        return asyncio.run(_run_quality())
    parser.error("unknown command")
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="helios")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("positions")
    sync_parser = subparsers.add_parser("sync")
    sync_parser.add_argument("--force-metadata", action="store_true")
    subparsers.add_parser("quality")
    return parser


async def _run_positions() -> int:
    try:
        positions = await _run_with_container(_fetch_positions)
    except Exception as exc:
        _print_error("helios.positions.error", exc.__class__.__name__)
        return 2
    print(render_positions(positions))
    return 0


async def _run_sync(*, force_metadata: bool) -> int:
    try:
        summary = await _run_with_container(
            lambda container: container.portfolio_sync_service.sync(force_metadata=force_metadata)
        )
    except Exception as exc:
        _print_error("helios.sync.error", exc.__class__.__name__)
        return 2
    print(_render_json(summary))
    return 0


async def _run_quality() -> int:
    try:
        report = await _run_with_container(_fetch_quality_report)
    except Exception as exc:
        _print_error("helios.quality.error", exc.__class__.__name__)
        return 2
    print(_render_json(report))
    return 0


async def _run_with_container[T](operation: Callable[[Container], Awaitable[T]]) -> T:
    settings = load_settings()
    container = build_container(settings)
    try:
        await container.startup()
        result = await operation(container)
    finally:
        await container.shutdown()
    return result


async def _fetch_positions(container: Container) -> list[Position]:
    return await container.t212_service.get_positions()


async def _fetch_quality_report(container: Container) -> QualityReport:
    return await container.portfolio_quality_report_service.get_report()


def render_positions(positions: list[Position]) -> str:
    headers = ["ticker", "quantity", "current_price", "average_price", "wallet_value"]
    rows = [headers]
    for position in positions:
        rows.append(
            [
                position.instrument.ticker,
                _fmt_decimal(position.quantity),
                _fmt_decimal(position.current_price),
                _fmt_decimal(position.average_price_paid),
                _fmt_decimal(
                    position.wallet_impact.current_value if position.wallet_impact else None
                ),
            ]
        )
    widths = [max(len(row[index]) for row in rows) for index in range(len(headers))]
    return "\n".join(
        " | ".join(cell.ljust(widths[index]) for index, cell in enumerate(row)) for row in rows
    )


def _fmt_decimal(value: Decimal | None) -> str:
    if value is None:
        return "-"
    return format(value, "f")


def _render_json(model: PortfolioSyncSummary | QualityReport) -> str:
    return json.dumps(model.model_dump(mode="json", by_alias=True), indent=2)


def _print_error(prefix: str, error_name: str) -> None:
    print(f"{prefix}: {error_name}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
