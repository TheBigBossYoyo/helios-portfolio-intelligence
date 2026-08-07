from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from helios.config import Settings
from helios.db import migrate_database
from helios.models import DailyHolding
from helios.portfolio_repository import PortfolioRepository
from helios.rate_limit import Clock
from helios.thesis import (
    STATUSES,
    TRANSITIONS,
    ThesisError,
    ThesisNotFoundError,
    ThesisService,
    allowed_transitions,
    is_editable,
)

NOW = datetime(2024, 5, 1, 12, 0, tzinfo=UTC)


class FixedClock(Clock):
    def now(self) -> float:
        return NOW.timestamp()

    def utcnow(self) -> datetime:
        return NOW

    async def sleep(self, seconds: float) -> None:
        del seconds


# ---------------------------------------------------------------------------
# The state machine
# ---------------------------------------------------------------------------


def test_every_status_has_a_declared_transition_set() -> None:
    assert set(TRANSITIONS) == set(STATUSES)


def test_closed_is_terminal() -> None:
    """A closed thesis cannot quietly reopen — that would let hindsight rewrite history."""
    assert allowed_transitions("closed") == []


def test_a_draft_cannot_jump_straight_to_an_outcome() -> None:
    """Validated/invalidated only mean something if the thesis was actually live."""
    assert "validated" not in allowed_transitions("draft")
    assert "invalidated" not in allowed_transitions("draft")


def test_only_a_draft_is_editable() -> None:
    assert is_editable("draft") is True
    for status in ("active", "validated", "invalidated", "closed"):
        assert is_editable(status) is False


# ---------------------------------------------------------------------------
# Creation and validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_records_the_reasoning_and_starts_as_draft(tmp_path: Path) -> None:
    service = await _service(tmp_path, "thesis_create.sqlite3")

    thesis = await service.create(
        title="Apple moat",
        body="Services revenue is compounding faster than hardware.",
        t212_ticker="AAPL_US_EQ",
        conviction="high",
    )

    assert thesis.status == "draft"
    assert thesis.conviction == "high"
    assert thesis.opened_on == date(2024, 5, 1)
    assert thesis.created_at == NOW


@pytest.mark.asyncio
async def test_a_thesis_without_reasoning_is_rejected(tmp_path: Path) -> None:
    """The body is the entire point of recording a thesis."""
    service = await _service(tmp_path, "thesis_empty.sqlite3")

    with pytest.raises(ThesisError, match="reasoning is the point"):
        await service.create(title="Apple", body="   ")


@pytest.mark.asyncio
async def test_title_is_required(tmp_path: Path) -> None:
    service = await _service(tmp_path, "thesis_title.sqlite3")

    with pytest.raises(ThesisError, match="needs a title"):
        await service.create(title="  ", body="Reasoning.")


@pytest.mark.asyncio
async def test_unknown_conviction_is_rejected(tmp_path: Path) -> None:
    service = await _service(tmp_path, "thesis_conv.sqlite3")

    with pytest.raises(ThesisError, match="conviction must be one of"):
        await service.create(title="T", body="B", conviction="certain")


# ---------------------------------------------------------------------------
# Immutability once live
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_draft_can_be_edited(tmp_path: Path) -> None:
    service = await _service(tmp_path, "thesis_edit.sqlite3")
    thesis = await service.create(title="Old", body="Old reasoning.")

    edited = await service.edit(thesis.id, title="New", body="New reasoning.")

    assert edited.title == "New"
    assert edited.body == "New reasoning."


@pytest.mark.asyncio
async def test_an_active_thesis_is_frozen(tmp_path: Path) -> None:
    """Rewriting the original reasoning after the fact destroys the only thing it is for."""
    service = await _service(tmp_path, "thesis_frozen.sqlite3")
    thesis = await service.create(title="T", body="Original reasoning.")
    await service.transition(thesis.id, to_status="active")

    with pytest.raises(ThesisError, match="frozen"):
        await service.edit(thesis.id, body="Actually I meant something else.")

    stored = await service.get(thesis.id)
    assert stored.body == "Original reasoning."


# ---------------------------------------------------------------------------
# Transitions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_happy_path_draft_active_validated(tmp_path: Path) -> None:
    service = await _service(tmp_path, "thesis_happy.sqlite3")
    thesis = await service.create(title="T", body="B")

    await service.transition(thesis.id, to_status="active")
    closed = await service.transition(
        thesis.id, to_status="validated", outcome_note="Services grew as expected."
    )

    assert closed.status == "validated"
    assert closed.outcome_note == "Services grew as expected."
    assert closed.closed_at == NOW


@pytest.mark.asyncio
async def test_an_illegal_transition_names_what_is_allowed(tmp_path: Path) -> None:
    service = await _service(tmp_path, "thesis_illegal.sqlite3")
    thesis = await service.create(title="T", body="B")

    with pytest.raises(ThesisError, match="allowed from here"):
        await service.transition(thesis.id, to_status="validated", outcome_note="n")


@pytest.mark.asyncio
async def test_closing_requires_saying_what_happened(tmp_path: Path) -> None:
    """A closed thesis with no outcome note teaches you nothing later."""
    service = await _service(tmp_path, "thesis_note.sqlite3")
    thesis = await service.create(title="T", body="B")
    await service.transition(thesis.id, to_status="active")

    with pytest.raises(ThesisError, match="outcome note"):
        await service.transition(thesis.id, to_status="invalidated")


@pytest.mark.asyncio
async def test_a_closed_thesis_cannot_be_reopened(tmp_path: Path) -> None:
    service = await _service(tmp_path, "thesis_reopen.sqlite3")
    thesis = await service.create(title="T", body="B")
    await service.transition(thesis.id, to_status="active")
    await service.transition(thesis.id, to_status="closed", outcome_note="Sold out.")

    with pytest.raises(ThesisError, match="it is closed"):
        await service.transition(thesis.id, to_status="active")


@pytest.mark.asyncio
async def test_unknown_thesis_raises_not_found(tmp_path: Path) -> None:
    service = await _service(tmp_path, "thesis_404.sqlite3")

    with pytest.raises(ThesisNotFoundError):
        await service.get(999)


# ---------------------------------------------------------------------------
# Journal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_journal_entries_attach_to_a_thesis_or_stand_alone(tmp_path: Path) -> None:
    service = await _service(tmp_path, "journal_scope.sqlite3")
    thesis = await service.create(title="T", body="B")

    await service.add_journal_entry(note="Bought more.", thesis_id=thesis.id)
    await service.add_journal_entry(note="Market feels frothy.", tags="macro")

    attached = await service.list_journal(thesis_id=thesis.id)
    everything = await service.list_journal()

    assert [entry.note for entry in attached] == ["Bought more."]
    assert len(everything) == 2


@pytest.mark.asyncio
async def test_a_journal_entry_needs_a_note(tmp_path: Path) -> None:
    service = await _service(tmp_path, "journal_empty.sqlite3")

    with pytest.raises(ThesisError, match="needs a note"):
        await service.add_journal_entry(note="  ")


@pytest.mark.asyncio
async def test_a_journal_entry_cannot_reference_a_missing_thesis(tmp_path: Path) -> None:
    service = await _service(tmp_path, "journal_missing.sqlite3")

    with pytest.raises(ThesisNotFoundError):
        await service.add_journal_entry(note="n", thesis_id=404)


@pytest.mark.asyncio
async def test_a_frozen_thesis_still_accepts_journal_entries(tmp_path: Path) -> None:
    """Later thinking goes in the journal — that is the outlet the edit ban points to."""
    service = await _service(tmp_path, "journal_frozen.sqlite3")
    thesis = await service.create(title="T", body="B")
    await service.transition(thesis.id, to_status="active")

    entry = await service.add_journal_entry(note="Revised view.", thesis_id=thesis.id)

    assert entry.note == "Revised view."


# ---------------------------------------------------------------------------
# Context linking
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_context_links_a_thesis_to_current_weight(tmp_path: Path) -> None:
    service, repository = await _service_and_repo(tmp_path, "thesis_ctx.sqlite3")
    await repository.replace_daily_replay(
        holdings=[
            _holding("AAPL_US_EQ", "600"),
            _holding("SHEL_EQ", "400"),
        ],
        nav_rows=[],
    )
    thesis = await service.create(title="T", body="B", t212_ticker="AAPL_US_EQ")

    context = await service.context_for(thesis)

    assert context.t212_ticker == "AAPL_US_EQ"
    assert context.weight == pytest.approx(0.6)


@pytest.mark.asyncio
async def test_context_is_empty_for_an_instrument_no_longer_held(tmp_path: Path) -> None:
    """A thesis for something you exited is still a valid thesis."""
    service = await _service(tmp_path, "thesis_ctx_empty.sqlite3")
    thesis = await service.create(title="T", body="B", t212_ticker="GONE_EQ")

    context = await service.context_for(thesis)

    assert context.weight is None
    assert context.news_count == 0


@pytest.mark.asyncio
async def test_context_for_a_portfolio_wide_thesis_has_no_ticker(tmp_path: Path) -> None:
    service = await _service(tmp_path, "thesis_ctx_wide.sqlite3")
    thesis = await service.create(title="Macro", body="Rates matter.")

    context = await service.context_for(thesis)

    assert context.t212_ticker is None
    assert context.news_count == 0


@pytest.mark.asyncio
async def test_list_filters_by_status(tmp_path: Path) -> None:
    service = await _service(tmp_path, "thesis_filter.sqlite3")
    draft = await service.create(title="Draft", body="B")
    live = await service.create(title="Live", body="B")
    await service.transition(live.id, to_status="active")

    drafts = await service.list_theses(status="draft")
    actives = await service.list_theses(status="active")

    assert [row.id for row in drafts] == [draft.id]
    assert [row.id for row in actives] == [live.id]


@pytest.mark.asyncio
async def test_list_rejects_an_unknown_status(tmp_path: Path) -> None:
    service = await _service(tmp_path, "thesis_badfilter.sqlite3")

    with pytest.raises(ThesisError, match="status must be one of"):
        await service.list_theses(status="maybe")


def _holding(ticker: str, value: str) -> DailyHolding:
    return DailyHolding(
        as_of_date=date(2024, 5, 1),
        t212_ticker=ticker,
        quantity=Decimal("1"),
        price_currency="EUR",
        close_price=Decimal(value),
        price_provenance="EXACT",
        fx_rate_to_eur=Decimal("1"),
        fx_provenance="EXACT",
        market_value_local=Decimal(value),
        market_value_eur=Decimal(value),
        valuation_status="VALUED",
    )


async def _service(tmp_path: Path, filename: str) -> ThesisService:
    service, _ = await _service_and_repo(tmp_path, filename)
    return service


async def _service_and_repo(
    tmp_path: Path, filename: str
) -> tuple[ThesisService, PortfolioRepository]:
    settings = Settings(data_dir=tmp_path, sqlite_filename=filename)
    await migrate_database(settings)
    engine = create_async_engine(settings.sqlite_url, poolclass=NullPool)
    session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    repository = PortfolioRepository(session_factory)
    return ThesisService(repository, clock=FixedClock()), repository
