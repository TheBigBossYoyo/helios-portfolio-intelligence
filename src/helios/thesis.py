"""Milestone 7 — investment theses and the journal.

A thesis records *why* you hold something, written before the outcome is known. Its value comes
entirely from being honest after the fact, so this module is built around one rule:

**The original reasoning is immutable once the thesis leaves draft.** `body`, `conviction` and
`opened_on` can be edited while it is a draft; once you activate it, they are frozen. Later
thinking goes into journal entries and, at close, an `outcome_note`. Editing the thesis to match
what happened would destroy the only thing it is for.

Status transitions are a small state machine rather than a free-text field, so "validated" always
means the same thing and a closed thesis cannot quietly reopen.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from .models import JournalEntry, Thesis
from .portfolio_repository import PortfolioRepository
from .rate_limit import Clock, SystemClock

ThesisStatus = Literal["draft", "active", "validated", "invalidated", "closed"]
Conviction = Literal["low", "medium", "high"]

STATUSES: tuple[ThesisStatus, ...] = ("draft", "active", "validated", "invalidated", "closed")
CONVICTIONS: tuple[Conviction, ...] = ("low", "medium", "high")

#: Allowed moves. A thesis can be abandoned from anywhere, but never resurrected from `closed`,
#: and never jump straight from draft to an outcome without having been live.
TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"active", "closed"}),
    "active": frozenset({"validated", "invalidated", "closed"}),
    "validated": frozenset({"closed"}),
    "invalidated": frozenset({"closed"}),
    "closed": frozenset(),
}

#: Statuses after which the original reasoning is frozen.
EDITABLE_STATUSES = frozenset({"draft"})

TERMINAL_STATUSES = frozenset({"validated", "invalidated", "closed"})


class ThesisError(ValueError):
    """Invalid thesis input or an illegal status transition."""


class ThesisNotFoundError(LookupError):
    pass


@dataclass(frozen=True)
class ThesisContext:
    """Read-only links from a thesis to what Helios already knows about the instrument.

    Populated lazily and allowed to be empty — a thesis for an instrument you no longer hold,
    or one with no news configured, is still a valid thesis.
    """

    t212_ticker: str | None
    weight: float | None
    contribution: float | None
    news_count: int
    latest_news_headline: str | None


class ThesisService:
    def __init__(
        self,
        repository: PortfolioRepository,
        clock: Clock | None = None,
    ) -> None:
        self._repository = repository
        self._clock = clock or SystemClock()

    async def create(
        self,
        *,
        title: str,
        body: str,
        t212_ticker: str | None = None,
        isin: str | None = None,
        conviction: str = "medium",
        opened_on: date | None = None,
    ) -> Thesis:
        title = title.strip()
        body = body.strip()
        if not title:
            raise ThesisError("a thesis needs a title")
        if not body:
            raise ThesisError("a thesis needs a body — the reasoning is the point")
        if conviction not in CONVICTIONS:
            raise ThesisError(f"conviction must be one of {list(CONVICTIONS)}")
        now = self._clock.utcnow()
        return await self._repository.insert_thesis(
            Thesis(
                t212_ticker=t212_ticker or None,
                isin=isin or None,
                title=title,
                body=body,
                conviction=conviction,
                status="draft",
                opened_on=opened_on or now.date(),
                created_at=now,
                updated_at=now,
            )
        )

    async def edit(
        self,
        thesis_id: int,
        *,
        title: str | None = None,
        body: str | None = None,
        conviction: str | None = None,
    ) -> Thesis:
        """Edit a draft. Refuses once the thesis is live — that is the whole discipline."""
        thesis = await self._require(thesis_id)
        if thesis.status not in EDITABLE_STATUSES:
            raise ThesisError(
                f"a '{thesis.status}' thesis is frozen; add a journal entry instead of "
                "rewriting the original reasoning"
            )
        if conviction is not None and conviction not in CONVICTIONS:
            raise ThesisError(f"conviction must be one of {list(CONVICTIONS)}")
        return await self._repository.update_thesis(
            thesis_id,
            title=title.strip() if title else None,
            body=body.strip() if body else None,
            conviction=conviction,
            updated_at=self._clock.utcnow(),
        )

    async def transition(
        self, thesis_id: int, *, to_status: str, outcome_note: str | None = None
    ) -> Thesis:
        thesis = await self._require(thesis_id)
        if to_status not in STATUSES:
            raise ThesisError(f"status must be one of {list(STATUSES)}")
        allowed = TRANSITIONS[thesis.status]
        if to_status not in allowed:
            raise ThesisError(
                f"cannot move a thesis from '{thesis.status}' to '{to_status}'; "
                f"allowed from here: {sorted(allowed) or 'nothing — it is closed'}"
            )
        if to_status in TERMINAL_STATUSES and not (outcome_note or "").strip():
            raise ThesisError(
                f"closing a thesis as '{to_status}' needs an outcome note saying what happened"
            )
        now = self._clock.utcnow()
        return await self._repository.update_thesis(
            thesis_id,
            status=to_status,
            outcome_note=(outcome_note or "").strip() or None,
            closed_at=now if to_status in TERMINAL_STATUSES else None,
            updated_at=now,
        )

    async def list_theses(self, *, status: str | None = None) -> list[Thesis]:
        if status is not None and status not in STATUSES:
            raise ThesisError(f"status must be one of {list(STATUSES)}")
        return await self._repository.list_theses(status=status)

    async def get(self, thesis_id: int) -> Thesis:
        return await self._require(thesis_id)

    async def add_journal_entry(
        self, *, note: str, thesis_id: int | None = None, tags: str | None = None
    ) -> JournalEntry:
        note = note.strip()
        if not note:
            raise ThesisError("a journal entry needs a note")
        if thesis_id is not None:
            await self._require(thesis_id)
        return await self._repository.insert_journal_entry(
            JournalEntry(
                thesis_id=thesis_id,
                created_at=self._clock.utcnow(),
                note=note,
                tags=(tags or "").strip() or None,
            )
        )

    async def list_journal(
        self, *, thesis_id: int | None = None, limit: int = 100
    ) -> list[JournalEntry]:
        return await self._repository.list_journal_entries(thesis_id=thesis_id, limit=limit)

    async def context_for(self, thesis: Thesis) -> ThesisContext:
        """Link a thesis to current analytics and news, without ever writing to them."""
        ticker = thesis.t212_ticker
        if ticker is None:
            return ThesisContext(None, None, None, 0, None)
        weight, contribution = await self._repository.latest_holding_weight(ticker)
        news = await self._repository.list_news_items(t212_ticker=ticker, limit=5)
        return ThesisContext(
            t212_ticker=ticker,
            weight=weight,
            contribution=contribution,
            news_count=len(news),
            latest_news_headline=news[0].headline if news else None,
        )

    async def _require(self, thesis_id: int) -> Thesis:
        thesis = await self._repository.get_thesis(thesis_id)
        if thesis is None:
            raise ThesisNotFoundError(f"no thesis with id {thesis_id}")
        return thesis


def allowed_transitions(status: str) -> list[str]:
    return sorted(TRANSITIONS.get(status, frozenset()))


def is_editable(status: str) -> bool:
    return status in EDITABLE_STATUSES


def utcnow_date(clock: Clock) -> date:
    return clock.utcnow().date()


def format_thesis_line(thesis: Thesis) -> str:
    ticker = thesis.t212_ticker or "portfolio"
    return f"#{thesis.id} [{thesis.status}] {ticker} — {thesis.title}"


def format_journal_line(entry: JournalEntry) -> str:
    stamp = entry.created_at.isoformat() if isinstance(entry.created_at, datetime) else ""
    scope = f"thesis #{entry.thesis_id}" if entry.thesis_id else "general"
    return f"{stamp} [{scope}] {entry.note}"
