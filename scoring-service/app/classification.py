"""Splits "marktontwikkeling" (funding, overnames, marktcijfers, ...) from
plain security news (kwetsbaarheden, incidenten, patches, ...).

Deliberately keyword-based, not a model call: it is transparent (you can read
exactly why an item counts as market), free, and needs no extra Voyage
request (free tier: 3 req/min). It runs on title + summary — the same text the
digest shows — both when an item is scored and when /items/top is queried, so
no database column (and thus no migration; the schema is only ever created
with create_all) is needed and older items are covered too.

A pattern only counts once per item, however often it repeats. A hit in the
title weighs double a hit in the summary, and an item is "markt" from a total
of 2: one title hit, or two different hits in the summary.
"""

import re
from typing import Literal

ItemCategory = Literal["markt", "nieuws"]

_TITLE_HIT_WEIGHT = 2
_SUMMARY_HIT_WEIGHT = 1
_MARKET_THRESHOLD = 2

_MARKET_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        # Funding / investeren
        r"\braises? [$€£]?\d",
        r"\bfunding\b",
        r"\bseries [a-f]\b",
        r"\bseed round\b",
        r"\bventure (?:capital|funding|round)\b",
        r"\bvaluation\b",
        r"\bunicorn\b",
        r"\binvest(?:s|ed|ment|ments)\b",
        r"\bipo\b",
        r"\bgo(?:es)? public\b",
        # Overnames / fusies (bewust niet "acquired": "attackers acquired credentials")
        r"\bacquires\b",
        r"\bacquisition\b",
        r"\bto acquire\b",
        r"\bacquired by\b",
        r"\bhas acquired\b",
        r"\bmerger\b",
        r"\bmerges? with\b",
        r"\btakeover\b",
        r"\bbuyout\b",
        r"\bdivest(?:s|iture)?\b",
        r"\bm&a\b",
        # Marktonderzoek / analisten
        r"\bmarket (?:share|size|growth|forecast|report|leader|opportunity|trends?|outlook)\b",
        r"\bcagr\b",
        r"\bgartner\b",
        r"\bforrester\b",
        r"\bidc\b",
        r"\bmagic quadrant\b",
        # Bedrijfsresultaten / strategie
        r"\brevenue\b",
        r"\bearnings\b",
        r"\bquarterly results\b",
        r"\blayoffs?\b",
        r"\bjob cuts\b",
        r"\brestructuring\b",
        r"\bpartners? with\b",
        r"\b(?:strategic )?partnership\b",
        r"\bjoint venture\b",
        r"\bexpands? (?:into|its)\b",
        # Nederlands
        r"\bovername\b",
        r"\bneemt .{1,60} over\b",
        r"\bfusie\b",
        r"\bfinanciering\b",
        r"\binvestering(?:en)?\b",
        r"\binvesteert\b",
        r"\bmarkt(?:aandeel|onderzoek|ontwikkeling(?:en)?|groei|verwachting)\b",
        r"\bomzet\b",
        r"\bbeursgang\b",
        r"\bpartnerschap\b",
    )
]


def _count_hits(text: str) -> int:
    return sum(1 for pattern in _MARKET_PATTERNS if pattern.search(text))


def market_strength(title: str, summary: str) -> int:
    """Weighted number of distinct market patterns found; 0 = plain news."""
    return _TITLE_HIT_WEIGHT * _count_hits(title) + _SUMMARY_HIT_WEIGHT * _count_hits(summary)


def classify(title: str, summary: str) -> ItemCategory:
    return "markt" if market_strength(title, summary) >= _MARKET_THRESHOLD else "nieuws"
