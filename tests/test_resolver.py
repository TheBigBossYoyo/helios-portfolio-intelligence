from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from helios.config import Settings
from helios.resolver import (
    ACCEPTED_US_EXCHANGE_CODES,
    InstrumentResolutionRequest,
    OpenFigiResolver,
    load_instrument_overrides,
)


def test_load_instrument_overrides_validates_yaml(tmp_path: Path) -> None:
    path = tmp_path / "instrument_overrides.yaml"
    path.write_text(
        """
overrides:
  - isin: US88160R1014
    yahooTicker: TSLA
    preferredExchange: NASDAQ
    quoteCurrency: USD
    reason: Verified manually.
""".strip(),
        encoding="utf-8",
    )

    overrides = load_instrument_overrides(path)

    assert overrides["US88160R1014"].yahoo_ticker == "TSLA"


def test_load_instrument_overrides_accepts_snake_case_and_aliases(tmp_path: Path) -> None:
    path = tmp_path / "instrument_overrides.yaml"
    path.write_text(
        """
overrides:
  - isin: US88160R1014
    yahoo_ticker: TSLA
    preferred_exchange: NASDAQ
    quote_currency: USD
    reason: Verified manually.
  - isin: US0846707026
    yahooTicker: BRK-B
    preferredExchange: NYSE
    quoteCurrency: USD
    reason: Legacy alias compatibility.
""".strip(),
        encoding="utf-8",
    )

    overrides = load_instrument_overrides(path)

    assert overrides["US88160R1014"].preferred_exchange == "NASDAQ"
    assert overrides["US0846707026"].quote_currency == "USD"


def test_load_instrument_overrides_rejects_duplicate_isin(tmp_path: Path) -> None:
    path = tmp_path / "instrument_overrides.yaml"
    path.write_text(
        """
overrides:
  - isin: US88160R1014
    yahoo_ticker: TSLA
    preferred_exchange: NASDAQ
    quote_currency: USD
    reason: First.
  - isin: US88160R1014
    yahoo_ticker: TSLA
    preferred_exchange: NASDAQ
    quote_currency: USD
    reason: Duplicate.
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Duplicate override ISIN"):
        load_instrument_overrides(path)


@pytest.mark.asyncio
async def test_override_precedence_skips_provider_call(tmp_path: Path) -> None:
    called = False

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(500, json={"error": "should not be called"})

    resolver = OpenFigiResolver(
        Settings(data_dir=tmp_path),
        overrides=load_instrument_overrides(_write_override_file(tmp_path)),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.openfigi.com/v3"),
    )

    result = await resolver.resolve(
        InstrumentResolutionRequest(
            t212_ticker="TSLA_US_EQ",
            isin="US88160R1014",
            name="Tesla",
            currency_code="USD",
        )
    )

    assert result.status == "resolved"
    assert result.source == "override"
    assert result.yahoo_ticker == "TSLA"
    assert called is False


@pytest.mark.asyncio
async def test_resolver_returns_unresolved_without_isin(tmp_path: Path) -> None:
    resolver = OpenFigiResolver(Settings(data_dir=tmp_path), overrides={})

    result = await resolver.resolve(
        InstrumentResolutionRequest(
            t212_ticker="NOISIN",
            isin=None,
            name="Unknown",
            currency_code="USD",
        )
    )

    assert result.status == "unresolved"
    assert result.source == "missing_isin"
    assert result.yahoo_ticker is None

    await resolver.aclose()


@pytest.mark.asyncio
async def test_unique_usd_openfigi_mapping_resolves_yahoo_ticker(tmp_path: Path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/v3/mapping"
        assert json.loads(request.content.decode("utf-8")) == [
            {"idType": "ID_ISIN", "idValue": "US0846707026"}
        ]
        return httpx.Response(
            200,
            json=[
                {
                    "data": [
                        {
                            "ticker": "BRK.B",
                            "exchCode": "US",
                            "name": "Berkshire Hathaway",
                        }
                    ]
                }
            ],
        )

    resolver = OpenFigiResolver(
        Settings(data_dir=tmp_path),
        overrides={},
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.openfigi.com/v3"),
    )

    result = await resolver.resolve(
        InstrumentResolutionRequest(
            t212_ticker="BRK_B_US_EQ",
            isin="US0846707026",
            name="Berkshire Hathaway",
            currency_code="USD",
        )
    )

    assert result.status == "resolved"
    assert result.yahoo_ticker == "BRK-B"


@pytest.mark.asyncio
async def test_usd_openfigi_mapping_resolves_for_known_us_exchange_codes(tmp_path: Path) -> None:
    assert "US" in ACCEPTED_US_EXCHANGE_CODES
    assert "UW" in ACCEPTED_US_EXCHANGE_CODES

    responses = iter(
        [
            httpx.Response(200, json=[{"data": [{"ticker": "TSLA", "exchCode": "UW"}]}]),
            httpx.Response(200, json=[{"data": [{"ticker": "BRK.B", "exchCode": "US"}]}]),
        ]
    )

    async def handler(_request: httpx.Request) -> httpx.Response:
        return next(responses)

    resolver = OpenFigiResolver(
        Settings(data_dir=tmp_path),
        overrides={},
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://api.openfigi.com/v3",
        ),
    )

    tsla = await resolver.resolve(
        InstrumentResolutionRequest(
            t212_ticker="TSLA_US_EQ",
            isin="US88160R1014",
            name="Tesla",
            currency_code="USD",
        )
    )
    brkb = await resolver.resolve(
        InstrumentResolutionRequest(
            t212_ticker="BRK_B_US_EQ",
            isin="US0846707026",
            name="Berkshire Hathaway",
            currency_code="USD",
        )
    )

    assert tsla.yahoo_ticker == "TSLA"
    assert brkb.yahoo_ticker == "BRK-B"


@pytest.mark.asyncio
async def test_unique_us_candidate_resolves_despite_foreign_listings(tmp_path: Path) -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {
                    "data": [
                        {"ticker": "TSLA", "exchCode": "UW"},
                        {"ticker": "TL0", "exchCode": "GY"},
                    ]
                }
            ],
        )

    resolver = OpenFigiResolver(
        Settings(data_dir=tmp_path),
        overrides={},
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://api.openfigi.com/v3",
        ),
    )

    result = await resolver.resolve(
        InstrumentResolutionRequest(
            t212_ticker="TSLA_US_EQ",
            isin="US88160R1014",
            name="Tesla",
            currency_code="USD",
        )
    )

    assert result.status == "resolved"
    assert result.yahoo_ticker == "TSLA"


@pytest.mark.asyncio
async def test_ambiguous_and_gbx_results_require_manual_intervention(tmp_path: Path) -> None:
    responses = iter(
        [
            httpx.Response(
                200,
                json=[{"data": [{"ticker": "ABC"}, {"ticker": "ABC.A"}]}],
            ),
            httpx.Response(
                200,
                json=[{"data": [{"ticker": "VOD", "exchCode": "LN"}]}],
            ),
        ]
    )

    async def handler(_request: httpx.Request) -> httpx.Response:
        return next(responses)

    resolver = OpenFigiResolver(
        Settings(data_dir=tmp_path),
        overrides={},
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.openfigi.com/v3"),
    )

    ambiguous = await resolver.resolve(
        InstrumentResolutionRequest(
            t212_ticker="ABC_US_EQ",
            isin="US0000000001",
            name="ABC",
            currency_code="USD",
        )
    )
    gbx = await resolver.resolve(
        InstrumentResolutionRequest(
            t212_ticker="VOD_GB_EQ",
            isin="GB00BH4HKS39",
            name="Vodafone",
            currency_code="GBX",
        )
    )

    assert ambiguous.status == "ambiguous"
    assert gbx.status == "override_required"


@pytest.mark.asyncio
async def test_usd_candidate_with_foreign_or_unknown_exchange_does_not_resolve(
    tmp_path: Path,
) -> None:
    responses = iter(
        [
            httpx.Response(200, json=[{"data": [{"ticker": "SHOP", "exchCode": "CN"}]}]),
            httpx.Response(200, json=[{"data": [{"ticker": "XYZ"}]}]),
        ]
    )

    async def handler(_request: httpx.Request) -> httpx.Response:
        return next(responses)

    resolver = OpenFigiResolver(
        Settings(data_dir=tmp_path),
        overrides={},
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://api.openfigi.com/v3",
        ),
    )

    foreign = await resolver.resolve(
        InstrumentResolutionRequest(
            t212_ticker="SHOP_US_EQ",
            isin="CA82509L1076",
            name="Shopify",
            currency_code="USD",
        )
    )
    unknown = await resolver.resolve(
        InstrumentResolutionRequest(
            t212_ticker="XYZ_US_EQ",
            isin="US0000000002",
            name="Unknown",
            currency_code="USD",
        )
    )

    assert foreign.status == "override_required"
    assert foreign.yahoo_ticker is None
    assert unknown.status == "unresolved"
    assert unknown.yahoo_ticker is None


@pytest.mark.asyncio
async def test_openfigi_header_uses_secret_value(tmp_path: Path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-OPENFIGI-APIKEY"] == "secret-key"
        return httpx.Response(200, json=[{"data": []}])

    resolver = OpenFigiResolver(
        Settings(data_dir=tmp_path, openfigi_api_key=SecretStr("secret-key")),
        overrides={},
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://api.openfigi.com/v3",
        ),
    )

    await resolver.resolve(
        InstrumentResolutionRequest(
            t212_ticker="TSLA_US_EQ",
            isin="US88160R1014",
            name="Tesla",
            currency_code="USD",
        )
    )


@pytest.mark.asyncio
async def test_provider_failure_is_nonfatal_and_returns_unresolved(tmp_path: Path) -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    resolver = OpenFigiResolver(
        Settings(data_dir=tmp_path),
        overrides={},
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.openfigi.com/v3"),
    )

    result = await resolver.resolve(
        InstrumentResolutionRequest(
            t212_ticker="TSLA_US_EQ",
            isin="US88160R1014",
            name="Tesla",
            currency_code="USD",
        )
    )

    assert result.status == "unresolved"
    assert result.source == "openfigi"
    assert result.yahoo_ticker is None
    assert result.details == {"error": "ConnectError"}


def _write_override_file(tmp_path: Path) -> Path:
    path = tmp_path / "instrument_overrides.yaml"
    path.write_text(
        """
overrides:
  - isin: US88160R1014
    yahooTicker: TSLA
    preferredExchange: NASDAQ
    quoteCurrency: USD
    reason: Verified from official listing.
""".strip(),
        encoding="utf-8",
    )
    return path
