from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Awaitable, Callable, Iterable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from .backup import BackupError, backup_database, format_size
from .config import load_settings
from .dependencies import Container, build_container
from .news import NewsReparseSummary
from .schemas import (
    AiAnalysisModel,
    NewsItemModel,
    NewsSyncSummaryModel,
    PerformanceReplaySummaryModel,
    PerformanceReportModel,
    PortfolioSyncSummary,
    Position,
    QualityReport,
    ThesisModel,
)
from .storage import compact_database
from .t212_reparse import T212ReparseSummary


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "positions":
        return asyncio.run(_run_positions())
    if args.command == "sync":
        return asyncio.run(_run_sync(force_metadata=args.force_metadata))
    if args.command == "quality":
        return asyncio.run(_run_quality())
    if args.command == "performance-replay":
        return asyncio.run(_run_performance_replay())
    if args.command == "performance-report":
        return asyncio.run(_run_performance_report())
    if args.command == "news-sync":
        return asyncio.run(_run_news_sync())
    if args.command == "news-reparse":
        return asyncio.run(_run_news_reparse())
    if args.command == "t212-reparse":
        return asyncio.run(_run_t212_reparse())
    if args.command == "compact":
        return _run_compact(vacuum=args.vacuum)
    if args.command == "backup":
        return _run_backup(dest=args.dest, keep=args.keep)
    if args.command == "news":
        return asyncio.run(_run_news(ticker=args.ticker, isin=args.isin, limit=args.limit))
    if args.command == "ai-analyse":
        return asyncio.run(_run_ai_analyse())
    if args.command == "ai-latest":
        return asyncio.run(_run_ai_latest())
    if args.command == "thesis":
        return asyncio.run(_run_thesis(args))
    if args.command == "journal":
        return asyncio.run(_run_journal(args))
    parser.error("unknown command")
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="helios")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("positions")
    sync_parser = subparsers.add_parser("sync")
    sync_parser.add_argument("--force-metadata", action="store_true")
    subparsers.add_parser("quality")
    subparsers.add_parser("performance-replay")
    subparsers.add_parser("performance-report")
    subparsers.add_parser("news-sync")
    subparsers.add_parser("news-reparse")
    subparsers.add_parser("t212-reparse")
    news_parser = subparsers.add_parser("news")
    news_parser.add_argument("--ticker")
    news_parser.add_argument("--isin")
    news_parser.add_argument("--limit", type=int, default=20)
    subparsers.add_parser("ai-analyse")
    subparsers.add_parser("ai-latest")

    compact_parser = subparsers.add_parser("compact")
    compact_parser.add_argument(
        "--vacuum",
        action="store_true",
        help="Rewrite the file even when little of it is free.",
    )

    backup_parser = subparsers.add_parser("backup")
    backup_parser.add_argument(
        "--dest",
        help="Directory to write the backup into (default: <data_dir>/backups).",
    )
    backup_parser.add_argument(
        "--keep",
        type=int,
        default=14,
        help="Number of this database's backups to retain after pruning (default: 14).",
    )

    thesis_parser = subparsers.add_parser("thesis")
    thesis_sub = thesis_parser.add_subparsers(dest="thesis_command", required=True)
    thesis_sub.add_parser("list").add_argument("--status")
    create = thesis_sub.add_parser("create")
    create.add_argument("--title", required=True)
    create.add_argument("--body", required=True)
    create.add_argument("--ticker")
    create.add_argument("--isin")
    create.add_argument("--conviction", default="medium")
    show = thesis_sub.add_parser("show")
    show.add_argument("thesis_id", type=int)
    move = thesis_sub.add_parser("transition")
    move.add_argument("thesis_id", type=int)
    move.add_argument("--to", required=True, dest="to_status")
    move.add_argument("--note")

    journal_parser = subparsers.add_parser("journal")
    journal_sub = journal_parser.add_subparsers(dest="journal_command", required=True)
    add = journal_sub.add_parser("add")
    add.add_argument("--note", required=True)
    add.add_argument("--thesis-id", type=int, dest="thesis_id")
    add.add_argument("--tags")
    listing = journal_sub.add_parser("list")
    listing.add_argument("--thesis-id", type=int, dest="thesis_id")
    listing.add_argument("--limit", type=int, default=50)
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


async def _run_performance_replay() -> int:
    try:
        summary = await _run_with_container(
            lambda container: container.performance_replay_service.replay()
        )
    except Exception as exc:
        _print_error("helios.performance_replay.error", exc.__class__.__name__)
        return 2
    print(_render_json(PerformanceReplaySummaryModel.model_validate(summary, from_attributes=True)))
    return 0


async def _run_performance_report() -> int:
    try:
        report = await _run_with_container(
            lambda container: container.performance_replay_service.get_report()
        )
    except Exception as exc:
        _print_error("helios.performance_report.error", exc.__class__.__name__)
        return 2
    print(_render_json(PerformanceReportModel.model_validate(report, from_attributes=True)))
    return 0


async def _run_news_sync() -> int:
    try:
        summary = await _run_with_container(lambda container: container.news_sync_service.sync())
    except Exception as exc:
        _print_error("helios.news_sync.error", exc.__class__.__name__)
        return 2
    print(_render_json(NewsSyncSummaryModel.model_validate(summary, from_attributes=True)))
    return 0


async def _run_news_reparse() -> int:
    try:
        summary = await _run_with_container(
            lambda container: container.news_reparse_service.reparse()
        )
    except Exception as exc:
        _print_error("helios.news_reparse.error", exc.__class__.__name__)
        return 2
    print(_render_reparse_summary(summary))
    return 0


async def _run_t212_reparse() -> int:
    try:
        summary = await _run_with_container(
            lambda container: container.t212_reparse_service.reparse()
        )
    except Exception as exc:
        _print_error("helios.t212_reparse.error", exc.__class__.__name__)
        return 2
    print(_render_t212_reparse_summary(summary))
    return 0


def _run_compact(*, vacuum: bool) -> int:
    settings = load_settings()
    path = settings.sqlite_path
    if not path.exists():
        print(f"No database at {path}")
        return 1
    result = compact_database(
        path,
        now=datetime.now(UTC),
        raw_news_retention_days=settings.raw_news_retention_days,
        force_vacuum=vacuum,
    )
    print(
        f"Dropped {result.raw_news_pruned} raw feed bodies and {result.snapshots_pruned} "
        f"snapshots; {'shrank the file' if result.vacuumed else 'no VACUUM needed'}: "
        f"{format_size(result.bytes_before)} -> {format_size(result.bytes_after)}"
    )
    return 0


def _run_backup(*, dest: str | None, keep: int) -> int:
    settings = load_settings()
    dest_dir = Path(dest) if dest else settings.data_dir / "backups"
    try:
        result = backup_database(settings.sqlite_path, dest_dir=dest_dir, keep=keep)
    except BackupError as exc:
        _print_error("helios.backup.error", exc.__class__.__name__)
        print(str(exc), file=sys.stderr)
        return 2
    print(f"{result.path} ({format_size(result.size_bytes)})")
    return 0


async def _run_news(*, ticker: str | None, isin: str | None, limit: int) -> int:
    try:
        items = await _run_with_container(
            lambda container: container.news_sync_service.list_news(
                t212_ticker=ticker, isin=isin, limit=limit
            )
        )
    except Exception as exc:
        _print_error("helios.news.error", exc.__class__.__name__)
        return 2
    print(render_news([NewsItemModel.model_validate(item, from_attributes=True) for item in items]))
    return 0


async def _run_ai_analyse() -> int:
    try:
        analysis = await _run_with_container(
            lambda container: container.ai_analysis_service.analyse()
        )
    except Exception as exc:
        _print_error("helios.ai_analyse.error", exc.__class__.__name__)
        return 2
    print(_render_json(AiAnalysisModel.model_validate(analysis, from_attributes=True)))
    return 0


async def _run_ai_latest() -> int:
    try:
        analysis = await _run_with_container(
            lambda container: container.ai_analysis_service.latest()
        )
    except Exception as exc:
        _print_error("helios.ai_latest.error", exc.__class__.__name__)
        return 2
    if analysis is None:
        print("No AI analysis has been run yet. Run `helios ai-analyse` first.")
        return 0
    print(_render_json(AiAnalysisModel.model_validate(analysis, from_attributes=True)))
    return 0


async def _run_thesis(args: argparse.Namespace) -> int:
    from .thesis import format_thesis_line

    try:
        if args.thesis_command == "list":
            rows = await _run_with_container(
                lambda c: c.thesis_service.list_theses(status=args.status)
            )
            print(_lines(format_thesis_line(row) for row in rows) or "No theses yet.")
            return 0
        if args.thesis_command == "create":
            thesis = await _run_with_container(
                lambda c: c.thesis_service.create(
                    title=args.title,
                    body=args.body,
                    t212_ticker=args.ticker,
                    isin=args.isin,
                    conviction=args.conviction,
                )
            )
            print(format_thesis_line(thesis))
            return 0
        if args.thesis_command == "show":
            thesis = await _run_with_container(lambda c: c.thesis_service.get(args.thesis_id))
            print(_render_json(ThesisModel.model_validate(thesis, from_attributes=True)))
            return 0
        thesis = await _run_with_container(
            lambda c: c.thesis_service.transition(
                args.thesis_id, to_status=args.to_status, outcome_note=args.note
            )
        )
        print(format_thesis_line(thesis))
        return 0
    except Exception as exc:
        _print_error("helios.thesis.error", exc.__class__.__name__)
        return 2


async def _run_journal(args: argparse.Namespace) -> int:
    from .thesis import format_journal_line

    try:
        if args.journal_command == "add":
            entry = await _run_with_container(
                lambda c: c.thesis_service.add_journal_entry(
                    note=args.note, thesis_id=args.thesis_id, tags=args.tags
                )
            )
            print(format_journal_line(entry))
            return 0
        rows = await _run_with_container(
            lambda c: c.thesis_service.list_journal(thesis_id=args.thesis_id, limit=args.limit)
        )
        print(_lines(format_journal_line(row) for row in rows) or "No journal entries yet.")
        return 0
    except Exception as exc:
        _print_error("helios.journal.error", exc.__class__.__name__)
        return 2


def _lines(values: Iterable[str]) -> str:
    return "\n".join(values)


def render_news(items: list[NewsItemModel]) -> str:
    if not items:
        return "No stored news. Configure sources in config/news_feeds.yaml, then run news-sync."
    headers = ["published", "source", "ticker", "headline"]
    rows = [headers]
    for item in items:
        rows.append(
            [
                item.published_at.isoformat() if item.published_at else "-",
                item.source_label,
                item.t212_ticker or "-",
                item.headline,
            ]
        )
    widths = [max(len(row[index]) for row in rows) for index in range(len(headers))]
    return "\n".join(
        " | ".join(cell.ljust(widths[index]) for index, cell in enumerate(row)) for row in rows
    )


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


def _render_reparse_summary(summary: NewsReparseSummary) -> str:
    # No pydantic DTO here: this mirrors the shape of NewsSyncSummaryModel's JSON by hand rather
    # than adding a schema, since `schemas.py` is owned by concurrent work on the sync summary.
    payload = {
        "asOf": summary.as_of.isoformat(),
        "rawRead": summary.raw_read,
        "itemsParsed": summary.items_parsed,
        "itemsWritten": summary.items_written,
        "duplicatesSkipped": summary.duplicates_skipped,
        "crossSourceMerges": summary.cross_source_merges,
        "rawSkipped": summary.raw_skipped,
        "failures": summary.failures,
        "notes": summary.notes,
    }
    return json.dumps(payload, indent=2)


def _render_t212_reparse_summary(summary: T212ReparseSummary) -> str:
    # No pydantic DTO here either, for the same reason as `_render_reparse_summary`.
    payload = {
        "asOf": summary.as_of.isoformat(),
        "snapshotsRead": summary.snapshots_read,
        "endpoints": [
            {
                "endpoint": endpoint.endpoint,
                "replayed": endpoint.replayed,
                "failed": endpoint.failed,
                "skipped": endpoint.skipped,
            }
            for endpoint in summary.endpoints
        ],
        "itemsParsed": summary.items_parsed,
        "rowsWritten": summary.rows_written,
        "duplicatesUnchanged": summary.duplicates_unchanged,
        "reconciliationRowsWritten": summary.reconciliation_rows_written,
        "failures": summary.failures,
        "notes": summary.notes,
    }
    return json.dumps(payload, indent=2)


def _render_json(
    model: PortfolioSyncSummary
    | QualityReport
    | PerformanceReplaySummaryModel
    | PerformanceReportModel
    | NewsSyncSummaryModel
    | AiAnalysisModel
    | ThesisModel,
) -> str:
    return json.dumps(model.model_dump(mode="json", by_alias=True), indent=2)


def _print_error(prefix: str, error_name: str) -> None:
    print(f"{prefix}: {error_name}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
