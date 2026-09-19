"""Splits "marktontwikkeling" from plain security news.

"markt" is what is new or changing in the security market: innovations, new
products and services, new developments (trends, new players, new protocols
and standards) — and, as before, the business side: acquisitions, funding,
market figures. "nieuws" is the rest: incidents, vulnerabilities, patches,
advisories, threat research.

Deliberately keyword-based, not a model call: it is transparent (you can read
exactly why an item counts as market), free, and needs no extra Voyage
request (free tier: 3 req/min). It runs on title + summary — the same text the
digest shows — both when an item is scored and when /items/top is queried, so
no database column (and thus no migration; the schema is only ever created
with create_all) is needed and older items are covered too.

A pattern only counts once per item, however often it repeats. A hit in the
title weighs double a hit in the summary, and an item is "markt" from a total
of 2: one title hit, or two different hits in the summary.

Words like "launches" and "releases" are just as common in attack and patch
news ("hackers launch new campaign", "Apple releases new version to fix a
zero-day"), so the innovation patterns are checked in context: a hit does not
count when an attacker/actor word stands right before it or inside it, or when
patching/vulnerability language follows it directly.
"""

import re
from typing import Literal

ItemCategory = Literal["markt", "nieuws"]

_TITLE_HIT_WEIGHT = 2
_SUMMARY_HIT_WEIGHT = 1
_MARKET_THRESHOLD = 2

# --- the business side: acquisitions, funding, market figures ------------------

_BUSINESS_PATTERNS = [
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
        r"(?<!remote )(?<!account )(?<!device )(?<!domain )(?<!subdomain )\btakeover\b",
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
        r"\bnamed a leader\b",
        r"\b(?:industry|market|research) analysts?\b",
        r"\bsurvey\b",
        r"\boutlook\b",
        r"\bspending\b",
        r"\bbudgets?\b",
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

# --- innovation: new products, services and developments -----------------------

# Nouns that make "launches/introduces/announces/releases ..." a product story.
_PRODUCT_NOUN = (
    r"(?:platforms?|products?|solutions?|services?|features?|capabilit(?:y|ies)|tools?|agents?|assistants?|"
    r"suites?|offerings?|copilots?|editions?|versions?|frameworks?|engines?|integrations?|"
    r"edr|xdr|siem|soar|sse|sase|ztna|ndr|mdr|ai)"
)

_INNOVATION_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        # Words that say "new thing" on their own
        r"\bunveil(?:s|ed|ing)?\b",
        r"\bdebut(?:s|ed|ing)?\b",
        r"\brolls? out\b|\brolling out\b",
        r"\b(?:now|generally) available\b|\bgeneral availability\b",
        r"\b(?:public|private) preview\b|\bin preview\b|\bearly access\b",
        r"\bnext-?gen(?:eration)?\b",
        r"\binnovat(?:e|es|ed|ion|ions|ive)\b",
        r"\bbreakthrough\b",
        r"\broadmap\b",
        # "Introducing X", "What's new in X", "Monthly news": product announcements
        r"\bintroducing\b|\bannouncing\b",
        r"\bwhat.?s new\b|\bmonthly news\b|\bthe latest in\b",
        r"\bnow (?:supports?|integrates?|offers?|includes?)\b",
        # "new <product>"
        r"\bnew (?:products?|services?|platforms?|solutions?|features?|capabilit(?:y|ies)|offerings?|suites?|"
        r"editions?|versions?|standards?|protocols?|specifications?|architectures?|generations?|models?|"
        r"integrations?|enhancements?|improvements?)\b",
        # launches / introduces / announces / releases <product>
        rf"\b(?:launch(?:es|ed|ing)?|introduc(?:es|ed|ing)|announc(?:es|ed|ing)|releas(?:es|ed|ing))\b"
        rf"[^.!?]{{0,60}}?\b{_PRODUCT_NOUN}\b",
        # adds/expands <feature>
        r"\b(?:adds?|expands?)\b[^.!?]{0,40}?\b(?:features?|capabilit(?:y|ies)|support)\b",
        # New developments: new players
        r"\bemerges? from stealth\b|\bout of stealth\b|\bstealth mode\b",
        r"\bstart-?ups?\b",
        # New protocols and standards
        r"\bpost-quantum\b|\bpqc\b",
        r"\brfc ?\d{3,5}\b",
        r"\bfips ?\d{3}\b",
        r"\bstandardi[sz]\w+\b",
        # Nederlands
        r"\b(?:lanceert|introduceert|presenteert|onthult|innovatie\w*|innovatief|innovatieve|vernieuwde?|"
        r"nu beschikbaar|algemeen beschikbaar)\b",
        r"\bnieuwe? (?:dienst|diensten|oplossing|oplossingen|functie|functies|product|producten|"
        r"standaard|standaarden)\b",
    )
]

# "Trends" and "emerging" say little on their own: "market trends" is a market
# story, "ransomware trends" or "emerging threats" is threat research. They
# only count when the text is not about threats.
_GENERIC_PATTERNS = [re.compile(r"\btrends?\b", re.IGNORECASE), re.compile(r"\bemerging\b", re.IGNORECASE)]
_THREAT_TOPIC = re.compile(
    r"\b(?:ransomware|malware|phishing|attacks?|attackers?|threats?|vulnerabilit\w+|breach\w*|exploit\w*|"
    r"zero-?days?|botnets?|ddos)\b",
    re.IGNORECASE,
)

# Who is doing it: an actor word before (or inside) a hit means an attack
# story, not a product story ("Ransomware gang launches new leak site").
_ACTOR = re.compile(
    r"\b(?:attackers?|hackers?|threat actors?|cybercriminals?|(?:ransomware|malware|hacking|apt)\s*"
    r"(?:gangs?|groups?|operators?|actors?)|gangs?|botnets?|campaigns?|apt ?\d+)\b",
    re.IGNORECASE,
)
# What follows: patching language right after a hit means a security-fix story
# ("Apple releases new version to fix a zero-day").
_PATCHING = re.compile(r"\b(?:fix(?:es|ed|ing)?|patch(?:es|ed|ing)?|flaws?|zero-?days?|exploit\w*|cve-\d+)\b", re.IGNORECASE)

_LOOKBEHIND_CHARS = 80
_LOOKAHEAD_CHARS = 40


def _is_product_context(text: str, match: re.Match) -> bool:
    before_and_inside = text[max(0, match.start() - _LOOKBEHIND_CHARS) : match.end()]
    after = text[match.end() : match.end() + _LOOKAHEAD_CHARS]
    return not _ACTOR.search(before_and_inside) and not _PATCHING.search(after)


def _count_hits(text: str) -> int:
    hits = sum(1 for pattern in _BUSINESS_PATTERNS if pattern.search(text))
    for pattern in _INNOVATION_PATTERNS:
        if any(_is_product_context(text, match) for match in pattern.finditer(text)):
            hits += 1
    if not _THREAT_TOPIC.search(text):
        hits += sum(1 for pattern in _GENERIC_PATTERNS if pattern.search(text))
    return hits


def market_strength(title: str, summary: str) -> int:
    """Weighted number of distinct market patterns found; 0 = plain news."""
    return _TITLE_HIT_WEIGHT * _count_hits(title) + _SUMMARY_HIT_WEIGHT * _count_hits(summary)


def classify(title: str, summary: str) -> ItemCategory:
    return "markt" if market_strength(title, summary) >= _MARKET_THRESHOLD else "nieuws"
