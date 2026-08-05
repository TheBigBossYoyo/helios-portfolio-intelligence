from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

import httpx
import yaml
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

from .config import Settings

MappingStatus = Literal["resolved", "unresolved", "ambiguous", "override_required", "not_required"]
MappingSource = Literal["override", "openfigi", "missing_isin"]

# OpenFIGI/Bloomberg exchange-code conventions observed for U.S. composite and
# major U.S. venues. We only auto-resolve USD instruments when the distinct
# ticker is supported by at least one of these explicitly U.S. exchange codes;
# otherwise we retain evidence and require manual intervention.
ACCEPTED_US_EXCHANGE_CODES = frozenset({"US", "UA", "UN", "UQ", "UR", "UW"})
MAX_CANDIDATE_EVIDENCE = 25


class OverrideEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    isin: str
    yahoo_ticker: str = Field(validation_alias=AliasChoices("yahoo_ticker", "yahooTicker"))
    preferred_exchange: str = Field(
        validation_alias=AliasChoices("preferred_exchange", "preferredExchange")
    )
    quote_currency: str = Field(
        validation_alias=AliasChoices("quote_currency", "quoteCurrency")
    )
    reason: str

    @field_validator("isin", "yahoo_ticker", "preferred_exchange", "quote_currency", "reason")
    @classmethod
    def validate_non_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("override values must be non-blank")
        return stripped


class OverrideFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    overrides: list[OverrideEntry] = Field(default_factory=list)


@dataclass(frozen=True)
class InstrumentResolutionRequest:
    t212_ticker: str
    isin: str | None
    name: str | None
    currency_code: str | None


@dataclass(frozen=True)
class MappingCandidate:
    figi: str | None
    ticker: str | None
    name: str | None
    exch_code: str | None
    composite_figi: str | None
    security_type: str | None
    security_type2: str | None
    market_sector: str | None
    share_class_figi: str | None
    security_description: str | None

    def as_details(self) -> dict[str, object]:
        return {
            "figi": self.figi,
            "ticker": self.ticker,
            "name": self.name,
            "exchCode": self.exch_code,
            "compositeFIGI": self.composite_figi,
            "securityType": self.security_type,
            "securityType2": self.security_type2,
            "marketSector": self.market_sector,
            "shareClassFIGI": self.share_class_figi,
            "securityDescription": self.security_description,
        }


@dataclass(frozen=True)
class InstrumentMappingResult:
    status: MappingStatus
    source: MappingSource | str | None
    yahoo_ticker: str | None
    details: dict[str, object] | None


class InstrumentResolver(Protocol):
    async def resolve(self, request: InstrumentResolutionRequest) -> InstrumentMappingResult: ...


def load_instrument_overrides(path: Path) -> dict[str, OverrideEntry]:
    if not path.is_file():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    document = OverrideFile.model_validate(raw or {"overrides": []})
    overrides: dict[str, OverrideEntry] = {}
    for entry in document.overrides:
        if entry.isin in overrides:
            raise ValueError(f"Duplicate override ISIN: {entry.isin}")
        overrides[entry.isin] = entry
    return overrides


class OpenFigiResolver:
    def __init__(
        self,
        settings: Settings,
        *,
        overrides: dict[str, OverrideEntry] | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._overrides = overrides if overrides is not None else load_instrument_overrides(
            settings.instrument_overrides_path
        )
        self._owned_client = http_client is None
        self._http_client = http_client or httpx.AsyncClient(
            base_url=settings.openfigi_base_url,
            timeout=settings.resolver_timeout_seconds,
        )

    async def aclose(self) -> None:
        if self._owned_client:
            await self._http_client.aclose()

    async def resolve(self, request: InstrumentResolutionRequest) -> InstrumentMappingResult:
        if request.isin is None:
            return InstrumentMappingResult(
                status="unresolved",
                source="missing_isin",
                yahoo_ticker=None,
                details={"reason": "missing_isin"},
            )

        override = self._overrides.get(request.isin)
        if override is not None:
            return InstrumentMappingResult(
                status="resolved",
                source="override",
                yahoo_ticker=override.yahoo_ticker,
                details={
                    "isin": override.isin,
                    "preferred_exchange": override.preferred_exchange,
                    "quote_currency": override.quote_currency,
                    "reason": override.reason,
                },
            )

        try:
            response = await self._http_client.post(
                "/mapping",
                headers=self._headers(),
                json=[{"idType": "ID_ISIN", "idValue": request.isin}],
            )
        except httpx.HTTPError as exc:
            return InstrumentMappingResult(
                status="unresolved",
                source="openfigi",
                yahoo_ticker=None,
                details={"error": exc.__class__.__name__},
            )

        if response.status_code >= 400:
            return InstrumentMappingResult(
                status="unresolved",
                source="openfigi",
                yahoo_ticker=None,
                details={"error": f"http_{response.status_code}"},
            )

        try:
            payload = response.json()
        except json.JSONDecodeError:
            return InstrumentMappingResult(
                status="unresolved",
                source="openfigi",
                yahoo_ticker=None,
                details={"error": "invalid_json"},
            )

        return self._resolve_openfigi_payload(request, payload)

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._settings.openfigi_api_key is not None:
            headers["X-OPENFIGI-APIKEY"] = self._settings.openfigi_api_key.get_secret_value()
        return headers

    def _resolve_openfigi_payload(
        self,
        request: InstrumentResolutionRequest,
        payload: object,
    ) -> InstrumentMappingResult:
        if not isinstance(payload, list) or not payload:
            return InstrumentMappingResult(
                status="unresolved",
                source="openfigi",
                yahoo_ticker=None,
                details={"error": "invalid_payload"},
            )
        first = payload[0]
        if not isinstance(first, dict):
            return InstrumentMappingResult(
                status="unresolved",
                source="openfigi",
                yahoo_ticker=None,
                details={"error": "invalid_payload"},
            )
        if "error" in first:
            return InstrumentMappingResult(
                status="unresolved",
                source="openfigi",
                yahoo_ticker=None,
                details={"error": str(first["error"])},
            )

        raw_candidates = first.get("data", [])
        if not isinstance(raw_candidates, list) or not raw_candidates:
            return InstrumentMappingResult(
                status="unresolved",
                source="openfigi",
                yahoo_ticker=None,
                details={
                    "candidate_count": 0,
                    "candidates": [],
                    "evidence_truncated": False,
                },
            )

        try:
            candidates = [self._candidate_from_raw(item) for item in raw_candidates]
        except ValueError:
            return InstrumentMappingResult(
                status="unresolved",
                source="openfigi",
                yahoo_ticker=None,
                details={"error": "invalid_candidate"},
            )

        evidence = _candidate_evidence(candidates)
        request_currency = (request.currency_code or "").upper()

        if request_currency in {"GBP", "GBX"}:
            return InstrumentMappingResult(
                status="override_required",
                source="openfigi",
                yahoo_ticker=None,
                details=evidence,
            )

        if request_currency != "USD":
            return InstrumentMappingResult(
                status="override_required",
                source="openfigi",
                yahoo_ticker=None,
                details=evidence,
            )

        us_candidates = [
            candidate
            for candidate in candidates
            if candidate.exch_code in ACCEPTED_US_EXCHANGE_CODES and candidate.ticker is not None
        ]
        distinct_tickers = sorted(
            {candidate.ticker for candidate in us_candidates if candidate.ticker}
        )

        if len(distinct_tickers) == 1:
            unique_ticker = distinct_tickers[0]
            yahoo_ticker = unique_ticker.replace(".", "-")
            return InstrumentMappingResult(
                status="resolved",
                source="openfigi",
                yahoo_ticker=yahoo_ticker,
                details=evidence,
            )

        if len(distinct_tickers) > 1:
            return InstrumentMappingResult(
                status="ambiguous",
                source="openfigi",
                yahoo_ticker=None,
                details=evidence,
            )

        all_distinct_tickers = {
            candidate.ticker for candidate in candidates if candidate.ticker is not None
        }
        if len(all_distinct_tickers) > 1:
            return InstrumentMappingResult(
                status="ambiguous",
                source="openfigi",
                yahoo_ticker=None,
                details=evidence,
            )

        if any(
            candidate.exch_code is not None
            and candidate.exch_code not in ACCEPTED_US_EXCHANGE_CODES
            for candidate in candidates
        ):
            return InstrumentMappingResult(
                status="override_required",
                source="openfigi",
                yahoo_ticker=None,
                details=evidence,
            )

        return InstrumentMappingResult(
            status="unresolved",
            source="openfigi",
            yahoo_ticker=None,
            details=evidence,
        )

    def _candidate_from_raw(self, raw: object) -> MappingCandidate:
        if not isinstance(raw, dict):
            raise ValueError("OpenFIGI candidate must be an object")
        return MappingCandidate(
            figi=_as_optional_str(raw.get("figi")),
            ticker=_as_optional_str(raw.get("ticker")),
            name=_as_optional_str(raw.get("name")),
            exch_code=_as_optional_str(raw.get("exchCode")),
            composite_figi=_as_optional_str(raw.get("compositeFIGI")),
            security_type=_as_optional_str(raw.get("securityType")),
            security_type2=_as_optional_str(raw.get("securityType2")),
            market_sector=_as_optional_str(raw.get("marketSector")),
            share_class_figi=_as_optional_str(raw.get("shareClassFIGI")),
            security_description=_as_optional_str(raw.get("securityDescription")),
        )


def _as_optional_str(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return str(value)


def _candidate_evidence(candidates: list[MappingCandidate]) -> dict[str, object]:
    ordered = sorted(
        candidates,
        key=lambda candidate: (
            candidate.exch_code not in ACCEPTED_US_EXCHANGE_CODES,
            candidate.ticker or "",
            candidate.exch_code or "",
            candidate.figi or "",
        ),
    )
    return {
        "candidate_count": len(candidates),
        "candidates": [
            candidate.as_details() for candidate in ordered[:MAX_CANDIDATE_EVIDENCE]
        ],
        "evidence_truncated": len(candidates) > MAX_CANDIDATE_EVIDENCE,
    }
