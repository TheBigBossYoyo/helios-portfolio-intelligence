from __future__ import annotations

import argparse
import asyncio
import sys
from decimal import Decimal

from .client import Trading212Error
from .config import load_settings
from .dependencies import build_container
from .schemas import Position


def main() -> int:
    parser = argparse.ArgumentParser(prog="helios")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("positions")
    args = parser.parse_args()
    if args.command == "positions":
        return asyncio.run(_run_positions())
    parser.error("unknown command")
    return 2


async def _run_positions() -> int:
    settings = load_settings()
    container = build_container(settings)
    try:
        await container.startup()
        positions = await container.t212_service.get_positions()
    except Trading212Error as exc:
        print(f"error|{exc}", file=sys.stderr)
        return 2
    finally:
        await container.shutdown()
    print(render_positions(positions))
    return 0


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


if __name__ == "__main__":
    raise SystemExit(main())
