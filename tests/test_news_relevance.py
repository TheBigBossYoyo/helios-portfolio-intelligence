"""Does a story bound to a holding actually name it? Checked on real-world headline shapes."""

from __future__ import annotations

import pytest

from helios.news_relevance import classify, company_terms

MICRON = company_terms(symbol="MU", name="Micron Technology")
LILLY = company_terms(symbol="LLY", name="Eli Lilly & Co")
ACHIEVE = company_terms(symbol="ACHV", name="Achieve Life Sciences")
VUAG = company_terms(symbol="VUAG.L", name="Vanguard S&P 500 (Acc)", keywords=["S&P 500"])


def test_terms_drop_legal_suffixes_and_keep_distinctive_words() -> None:
    assert MICRON.symbol == "MU"
    assert MICRON.phrases == ("Micron Technology", "Micron")
    assert LILLY.phrases == ("Eli Lilly", "Lilly")
    # "Achieve" alone is an ordinary English word: only the full name identifies the company.
    assert ACHIEVE.phrases == ("Achieve Life Sciences", "Achieve Life")
    # The exchange suffix never becomes part of the symbol.
    assert VUAG.symbol == "VUAG"
    assert "Vanguard" not in VUAG.phrases
    assert "S&P 500" in VUAG.phrases


@pytest.mark.parametrize(
    ("headline", "expected", "matched"),
    [
        ("Micron Technology (MU) Could Be 46% Undervalued", "headline", "MU"),
        ("Jobs, Inflation, Nike, Carmax, Micron, and More to Watch", "headline", "Micron"),
        ("Why the Nasdaq Refuses to Break Even", "unconfirmed", None),
    ],
)
def test_classify_reports_where_the_company_is_named(
    headline: str, expected: str, matched: str | None
) -> None:
    result = classify(headline, None, MICRON)

    assert result.relevance == expected
    assert result.matched == matched


def test_symbol_matches_only_as_an_uppercase_whole_word() -> None:
    assert classify("A mu-metal shield for the lab", None, MICRON).relevance == "unconfirmed"
    assert classify("Emulating MUSIC stocks", None, MICRON).relevance == "unconfirmed"


def test_generic_first_word_alone_is_not_a_match() -> None:
    assert classify("How to Achieve Financial Freedom", None, ACHIEVE).relevance == "unconfirmed"
    assert classify("Achieve Life Sciences jumps on trial data", None, ACHIEVE).relevance == (
        "headline"
    )


def test_summary_mentions_count_after_stripping_markup() -> None:
    result = classify(
        "2 key checks for the AI trade",
        '<a href="https://example.com/micron">Micron</a> reports on Wednesday.',
        MICRON,
    )

    assert result.relevance == "summary"
    assert result.matched == "Micron"


def test_markup_alone_never_matches() -> None:
    result = classify("Markets wrap", '<a href="https://example.com/micron-story">link</a>', MICRON)

    assert result.relevance == "unconfirmed"


def test_items_without_a_bound_holding_are_market_wide() -> None:
    assert classify("Stocks rally", None, None).relevance == "market"


def test_configured_keywords_let_an_etf_match_what_it_tracks() -> None:
    assert classify("S&P 500 closes at a record", None, VUAG).relevance == "headline"
