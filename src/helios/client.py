from __future__ import annotations

import base64
from dataclasses import dataclass

import httpx
from pydantic import TypeAdapter, ValidationError

from .config import Settings, T212Credentials
from .rate_limit import (
    Clock,
    EndpointLimiter,
    SystemClock,
    default_rate_limit_policies,
    endpoint_policy_key,
    retry_delay_from_headers,
)
from .raw_snapshots import JsonValue, SnapshotWriter, encode_payload
from .schemas import (
    DividendItem,
    HistoricalOrderItem,
    HistoryPage,
    InstrumentMetadata,
    Position,
    TransactionItem,
)


class Trading212Error(Exception):
    pass


class Trading212CredentialsError(Trading212Error):
    pass


class Trading212MethodNotAllowedError(Trading212Error):
    pass


class Trading212HTTPError(Trading212Error):
    def __init__(self, status_code: int, path: str) -> None:
        super().__init__(f"Trading 212 request failed with status {status_code} for {path}")
        self.status_code = status_code
        self.path = path


class Trading212ParseError(Trading212Error):
    pass


class Trading212TransportError(Trading212Error):
    pass


@dataclass(frozen=True)
class RequestResult:
    payload: JsonValue
    status_code: int


class Trading212Client:
    _positions_adapter = TypeAdapter(list[Position])
    _instruments_adapter = TypeAdapter(list[InstrumentMetadata])
    _history_orders_page_adapter = TypeAdapter(HistoryPage[HistoricalOrderItem])
    _history_dividends_page_adapter = TypeAdapter(HistoryPage[DividendItem])
    _history_transactions_page_adapter = TypeAdapter(HistoryPage[TransactionItem])

    def __init__(
        self,
        *,
        settings: Settings,
        snapshot_writer: SnapshotWriter,
        http_client: httpx.AsyncClient | None = None,
        limiter: EndpointLimiter | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._settings = settings
        self._snapshot_writer = snapshot_writer
        self._clock = clock or SystemClock()
        self._limiter = limiter or EndpointLimiter(self._clock, default_rate_limit_policies())
        base_url = httpx.URL(settings.t212_base_url)
        port = f":{base_url.port}" if base_url.port is not None else ""
        self._api_origin = f"{base_url.scheme}://{base_url.host}{port}"
        self._owned_client = http_client is None
        self._http_client = http_client or httpx.AsyncClient(
            base_url=settings.t212_base_url,
            timeout=settings.t212_timeout_seconds,
        )

    async def aclose(self) -> None:
        if self._owned_client:
            await self._http_client.aclose()

    async def get_positions(self) -> list[Position]:
        payload = await self.request_json("GET", "/equity/positions")
        try:
            return self._positions_adapter.validate_python(payload)
        except ValidationError as exc:
            raise Trading212ParseError("Failed to parse Trading 212 positions payload") from exc

    async def get_instruments(self) -> list[InstrumentMetadata]:
        payload = await self.request_json("GET", "/equity/metadata/instruments")
        try:
            return self._instruments_adapter.validate_python(payload)
        except ValidationError as exc:
            raise Trading212ParseError("Failed to parse Trading 212 instruments payload") from exc

    async def get_history_orders(self) -> list[HistoricalOrderItem]:
        return await self._get_history_items(
            "/equity/history/orders",
            self._history_orders_page_adapter,
            "Failed to parse Trading 212 history orders payload",
        )

    async def get_history_dividends(self) -> list[DividendItem]:
        return await self._get_history_items(
            "/equity/history/dividends",
            self._history_dividends_page_adapter,
            "Failed to parse Trading 212 history dividends payload",
        )

    async def get_history_transactions(self) -> list[TransactionItem]:
        return await self._get_history_items(
            "/equity/history/transactions",
            self._history_transactions_page_adapter,
            "Failed to parse Trading 212 history transactions payload",
        )

    async def request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
    ) -> JsonValue:
        result = await self._request(method, path, params=params)
        return result.payload

    async def _get_history_items[TItem](
        self,
        path: str,
        adapter: TypeAdapter[HistoryPage[TItem]],
        parse_error_message: str,
    ) -> list[TItem]:
        next_path: str | None = path
        params: dict[str, str] | None = {"limit": "50"}
        items: list[TItem] = []
        while next_path is not None:
            payload = await self.request_json("GET", next_path, params=params)
            try:
                page = adapter.validate_python(payload)
            except ValidationError as exc:
                raise Trading212ParseError(parse_error_message) from exc
            items.extend(page.items)
            next_path = page.next_page_path
            params = None
        return items

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
    ) -> RequestResult:
        if method.upper() != "GET":
            raise Trading212MethodNotAllowedError("Helios only permits GET requests to Trading 212")
        credentials = self._settings.t212_credentials()
        if credentials is None:
            raise Trading212CredentialsError("Trading 212 credentials are not configured")
        endpoint_key = endpoint_policy_key(path)
        request_url = self._resolve_request_url(path)
        for attempt in range(self._settings.t212_max_retries + 1):
            await self._limiter.acquire(endpoint_key)
            try:
                response = await self._http_client.request(
                    method="GET",
                    url=request_url,
                    params=params,
                    headers={"Authorization": build_basic_auth_header(credentials)},
                )
            except httpx.TransportError as exc:
                self._limiter.refund(endpoint_key)
                if attempt >= self._settings.t212_max_retries:
                    raise Trading212TransportError("Trading 212 transport error") from exc
                await self._clock.sleep(self._backoff_seconds(attempt, {}))
                continue

            content = await response.aread()
            headers = {key.lower(): value for key, value in response.headers.items()}
            payload = encode_payload(content, headers.get("content-type"))
            await self._snapshot_writer.append_snapshot(
                endpoint=path,
                recorded_at=self._clock.utcnow(),
                http_status=response.status_code,
                content_type=headers.get("content-type"),
                payload=payload,
            )
            self._limiter.observe(endpoint_key, headers, response.status_code)

            if response.status_code >= 400:
                should_retry = (
                    response.status_code in {429, 500, 502, 503, 504}
                    and attempt < self._settings.t212_max_retries
                )
                if should_retry:
                    await self._clock.sleep(self._backoff_seconds(attempt, headers))
                    continue
                raise Trading212HTTPError(response.status_code, path)

            if not _is_json_payload(payload):
                raise Trading212ParseError("Expected Trading 212 JSON payload")
            return RequestResult(payload=payload, status_code=response.status_code)
        raise AssertionError("Retry loop exhausted unexpectedly")

    def _backoff_seconds(self, attempt: int, headers: dict[str, str]) -> float:
        hinted_delay = retry_delay_from_headers(headers, self._clock)
        if hinted_delay is not None:
            return float(min(hinted_delay, 30.0))
        return float(min(0.5 * (2**attempt), 8.0))

    def _resolve_request_url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            url = httpx.URL(path)
            origin = f"{url.scheme}://{url.host}"
            if url.port is not None:
                origin = f"{origin}:{url.port}"
            if origin != self._api_origin or not url.path.startswith("/api/v0/"):
                raise Trading212ParseError("Trading 212 pagination URL escaped the API origin")
            return path
        if path.startswith("/api/v0/"):
            return f"{self._api_origin}{path}"
        return path


def build_basic_auth_header(credentials: T212Credentials) -> str:
    combined = f"{credentials.api_key}:{credentials.api_secret}".encode()
    return f"Basic {base64.b64encode(combined).decode('ascii')}"


def _is_json_payload(payload: JsonValue) -> bool:
    return not (isinstance(payload, dict) and payload.get("kind") == "non_json")
