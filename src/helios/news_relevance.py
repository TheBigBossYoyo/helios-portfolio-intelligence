"""Is a news item actually about the holding its feed was configured for?

A feed is bound to an instrument in config (see ``news.py``), but a per-ticker feed does not
promise every item is about that company: Yahoo's per-symbol headline feed, for one, mixes in
general market commentary ("2 Top Dividend Stocks to Buy") under every symbol. This module
checks the text for the company itself and reports what it found, so the dashboard can lead
with the stories that name your holdings.

It never *assigns* a story to a company -- the binding still comes only from config. It only
answers, for a binding that already exists, whether the headline or summary mentions the
company, and which words matched. That answer is shown to the reader, so a match is always
explainable.

Terms, derived from data Helios already holds:

* the market symbol (``MU``, ``NVDA``), matched case-sensitively as a whole word, so ``MU``
  never matches "mu" inside a sentence;
* the company name with legal suffixes removed (``Micron Technology``), its first word when that
  word is distinctive (``Micron``), and, for names whose first word is not (``Eli Lilly``), the
  last word (``Lilly``) -- all case-insensitive, whole words;
* any ``news_keywords`` configured for the ISIN in ``config/instrument_overrides.yaml`` -- the
  way to make an ETF match stories about what it tracks ("S&P 500", "silver").
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

Relevance = Literal["headline", "summary", "unconfirmed", "market"]

#: Legal-form and share-class words that say nothing about which company it is.
_SUFFIXES = frozenset(
    {
        "inc",
        "corp",
        "corporation",
        "co",
        "company",
        "plc",
        "ltd",
        "limited",
        "holdings",
        "holding",
        "group",
        "sa",
        "nv",
        "ag",
        "se",
        "the",
        "&",
        "and",
        "class",
        "a",
        "b",
        "c",
        "adr",
        "ucits",
        "etf",
        "etc",
        "acc",
        "dist",
    }
)

#: Words too common, or too shared between issuers, to identify a company on their own. They
#: still count inside a longer phrase ("Achieve Life Sciences").
_GENERIC = frozenset(
    {
        "achieve",
        "advanced",
        "american",
        "applied",
        "british",
        "capital",
        "chase",
        "digital",
        "energy",
        "financial",
        "first",
        "general",
        "global",
        "great",
        "health",
        "industries",
        "international",
        "life",
        "national",
        "new",
        "physical",
        "royal",
        "sciences",
        "solutions",
        "systems",
        "technologies",
        "technology",
        "united",
        # ETF issuers: an issuer's name matches every one of its funds and all its corporate
        # news, so it never identifies the holding by itself.
        "amundi",
        "invesco",
        "ishares",
        "spdr",
        "vanguard",
        "wisdomtree",
        "xtrackers",
    }
)

_TAG = re.compile(r"<[^>]+>")
_WORD = re.compile(r"[A-Za-z0-9&.'-]+")


@dataclass(frozen=True)
class CompanyTerms:
    """What to look for in a story about one instrument."""

    symbol: str | None
    phrases: tuple[str, ...]


@dataclass(frozen=True)
class RelevanceResult:
    relevance: Relevance
    matched: str | None


def company_terms(
    *,
    symbol: str | None,
    name: str | None,
    keywords: Sequence[str] = (),
) -> CompanyTerms:
    """Build the search terms for one instrument from its symbol, name and configured keywords."""

    base_symbol = symbol.split(".")[0].strip().upper() if symbol else None
    if base_symbol is not None and len(base_symbol) < 2:
        base_symbol = None

    phrases: list[str] = []
    words = _name_words(name)
    if words:
        phrases.append(" ".join(words))
        if len(words) > 1:
            phrases.append(" ".join(words[:2]))
        first = words[0]
        if len(first) >= 4 and first.lower() not in _GENERIC:
            phrases.append(first)
        elif len(words) > 1:
            last = words[-1]
            if len(last) >= 4 and last.lower() not in _GENERIC:
                phrases.append(last)
    phrases.extend(keyword.strip() for keyword in keywords if keyword.strip())
    unique = tuple(dict.fromkeys(phrase for phrase in phrases if phrase))
    return CompanyTerms(symbol=base_symbol, phrases=unique)


def classify(
    headline: str,
    summary: str | None,
    terms: CompanyTerms | None,
) -> RelevanceResult:
    """Where the company is named: the headline, only the summary, or nowhere."""

    if terms is None:
        return RelevanceResult("market", None)
    matched = _find(headline, terms)
    if matched is not None:
        return RelevanceResult("headline", matched)
    if summary:
        matched = _find(_TAG.sub(" ", summary), terms)
        if matched is not None:
            return RelevanceResult("summary", matched)
    return RelevanceResult("unconfirmed", None)


def _find(text: str, terms: CompanyTerms) -> str | None:
    if terms.symbol and _contains(text, terms.symbol, case_sensitive=True):
        return terms.symbol
    for phrase in terms.phrases:
        if _contains(text, phrase, case_sensitive=False):
            return phrase
    return None


def _contains(text: str, term: str, *, case_sensitive: bool) -> bool:
    pattern = rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])"
    return re.search(pattern, text, 0 if case_sensitive else re.IGNORECASE) is not None


def _name_words(name: str | None) -> list[str]:
    if not name:
        return []
    without_brackets = re.sub(r"\([^)]*\)", " ", name)
    words: Iterable[str] = _WORD.findall(without_brackets)
    return [word for word in words if word.lower().strip(".") not in _SUFFIXES]
