"""Idempotent seed/upsert of the reading list of feed sources.

Reuses discovery.upsert_seed_source() -> discovery.create_source(): the same
function behind POST /sources and the admin GUI's source form, so the same
validation and url dedup apply. The key is the feed url.

For a url that is already in the database the script only ever fills in a
*missing* category. It never changes an existing source's status, type or
notes, and it says so in its output — so re-running it (or running it on a
database an admin has been curating) is safe.

Status of new sources:
  - "kandidaat"     the default and what the original 20 sources use: they
                    become "actief" through the normal instroom check or a
                    manual override in the admin GUI.
  - "actief"        sources whose feed was fetched with feedparser (exactly
                    how the scheduler ingests it) and returned HTTP 200, a
                    parseable RSS/Atom document and at least one item, on
                    the date in the note. The scheduler picks these up
                    automatically.
  - "gedeactiveerd" no working feed could be found. The reason is in `notes`
                    (visible in the admin GUI). Feed urls are never invented:
                    for these the key is the url that was checked, or the site.

discovery_method="seed" (not "manual") distinguishes "part of the initial
reading list" from a source a person adds later by hand via the admin GUI.

Usage (module mode — see scripts/hash_admin_password.py for why):

    uv run python -m scripts.seed_sources
    docker compose run --rm scoring-service python -m scripts.seed_sources
"""

from typing import NamedTuple

from sqlalchemy.orm import Session

from app import discovery, migrations
from app.database import Base, SessionLocal, engine


class SeedSource(NamedTuple):
    url: str
    type: str = "rss"
    category: str | None = None
    status: str = "kandidaat"
    notes: str | None = None


CHECKED = "Gecontroleerd 2026-09-19."

# Focus of the agent: market developments first (trends, new products and
# services, M&A, new players in SOC/SIEM/SOAR/SSE, new protocols/standards),
# Microsoft-centred but across the whole portfolio. Vulnerabilities and
# advisories are secondary — the Source model has no weight/priority field,
# so that is only visible in the category (vuln_advisory).
SEED_SOURCES: list[SeedSource] = [
    # --- Reading list from the first seed. Status untouched; the category is
    # only filled in where the url already exists without one. ---
    # Algemene vakpers
    SeedSource("https://www.darkreading.com/rss.xml", category="news"),
    SeedSource("https://www.bleepingcomputer.com/feed/", category="news"),
    SeedSource("https://feeds.feedburner.com/TheHackersNews", category="news"),
    SeedSource("https://www.securityweek.com/feed/"),
    SeedSource("https://www.helpnetsecurity.com/feed/"),
    SeedSource("https://www.csoonline.com/feed/"),
    SeedSource("https://krebsonsecurity.com/feed/", category="news"),
    SeedSource("https://www.infosecurity-magazine.com/rss/news/"),
    # Markt / funding
    SeedSource("https://techcrunch.com/category/security/feed/"),
    SeedSource("https://siliconangle.com/category/security/feed/", category="market_analysis"),
    # Vendor
    SeedSource("https://www.microsoft.com/en-us/security/blog/feed/", category="microsoft_product"),
    SeedSource("https://feeds.fortinet.com/fortinet/blog/industry-trends"),
    SeedSource("https://feeds.fortinet.com/fortinet/blog/threat-research"),
    # Sector zorg
    SeedSource("https://healthitsecurity.com/feed"),
    # MSP
    SeedSource("https://www.msspalert.com/feed/"),
    SeedSource("https://www.channele2e.com/feed/"),
    SeedSource("https://www.n-able.com/blog/rss.xml"),
    SeedSource("https://www.veeam.com/blog/feed"),
    SeedSource("https://feeds.ncsc.nl/nieuws.rss", category="news"),
    SeedSource("https://www.digitaleoverheid.nl/feed/"),
    # --- NCSC ---
    SeedSource(
        "https://advisories.ncsc.nl/rss/advisories",
        category="vuln_advisory",
        status="actief",
        notes=f"Secundair aan marktontwikkeling (advisories). {CHECKED}",
    ),
    SeedSource(
        "https://feeds.english.ncsc.nl/news.rss",
        category="news",
        status="gedeactiveerd",
        notes=(
            "Opgegeven URL werkt niet: HTTP 400 'The provided host name is not valid for this "
            "server'. ncsc.nl/rss noemt alleen de NL-nieuwsfeed en de adviezen; geen Engelse "
            f"feed gevonden. {CHECKED}"
        ),
    ),
    # --- Markt: overnames en funding ---
    SeedSource(
        "https://www.securityweek.com/category/cybersecurity-funding-news/ma/feed/",
        category="market_ma",
        status="actief",
        notes=f"SecurityWeek M&A Tracker. {CHECKED}",
    ),
    SeedSource(
        "https://www.securityweek.com/category/cybersecurity-funding-news/feed/",
        category="market_ma",
        status="actief",
        notes=(
            "SecurityWeek Funding/M&A; overlapt deels met de M&A-feed (dezelfde artikel-URL "
            f"wordt maar één keer opgeslagen). {CHECKED}"
        ),
    ),
    # --- Markt: analyse ---
    SeedSource(
        "https://www.cybersecuritydive.com/feeds/news/",
        category="market_analysis",
        status="actief",
        notes=CHECKED,
    ),
    SeedSource(
        "https://risky.biz/rss.xml",
        category="market_analysis",
        status="actief",
        notes=f"Risky Business (podcast). {CHECKED}",
    ),
    SeedSource(
        "https://news.risky.biz/rss/",
        category="news",
        status="actief",
        notes=f"Risky Bulletin (nieuwsbrief); toegevoegd naast de podcast. {CHECKED}",
    ),
    SeedSource(
        "https://www.latio.com/",
        type="web",
        category="market_analysis",
        status="gedeactiveerd",
        notes=(
            "Latio Tech (nu latio.com): geen feed gevonden — geen link rel=alternate en "
            f"/feed, /rss.xml, /blog/feed, /blog/rss.xml geven geen XML. {CHECKED}"
        ),
    ),
    SeedSource(
        "https://softwareanalyst.substack.com/feed",
        category="market_analysis",
        status="actief",
        notes=(
            "Software Analyst Cyber Research. De feed op softwareanalyst.io/feed heeft maar 1 "
            f"item (okt 2025); deze Substack-feed is de actieve. {CHECKED}"
        ),
    ),
    # --- Microsoft ---
    SeedSource(
        "https://aka.ms/MTC/RSS-Board-Footer?board.id=MicrosoftThreatProtectionBlog",
        category="microsoft_product",
        status="actief",
        notes=(
            "Microsoft Defender XDR Blog (Tech Community, incl. 'Monthly news'). Officiële "
            "abonneerlink van de pagina; verwijst door naar techcommunity.microsoft.com/t5/s/"
            f"gxcuf89792/rss/board?board.id=MicrosoftThreatProtectionBlog. {CHECKED}"
        ),
    ),
    SeedSource(
        "https://learn.microsoft.com/api/search/rss?search=%22Release+notes+-+Azure+Active+Directory%22&locale=en-us",
        category="microsoft_product",
        status="actief",
        notes=(
            "Microsoft Entra 'What's new'. RSS-URL uit de tekst van de Learn-pagina zelf, maar "
            "het is een zoekfeed: 3 items, waarvan 1 de echte release-notes-pagina en 2 oude "
            f"Q&A-threads. {CHECKED}"
        ),
    ),
    SeedSource(
        "https://learn.microsoft.com/api/search/rss?search=%22What%27s+new+in+microsoft+intune%3F+-+Azure%22&locale=en-us",
        category="microsoft_product",
        status="gedeactiveerd",
        notes=(
            "Microsoft Intune 'What's new'. RSS-URL uit de tekst van de Learn-pagina zelf, maar "
            f"de feed geeft HTTP 200 met 0 items. {CHECKED}"
        ),
    ),
    SeedSource(
        "https://www.microsoft.com/en-us/msrc/blog",
        type="web",
        category="microsoft_product",
        status="gedeactiveerd",
        notes=(
            "MSRC-blog: geen feed gevonden (geen link rel=alternate; /feed, /rss en de oude "
            "msrc.microsoft.com/blog/rss geven 404/geen XML). De enige officiële MSRC-RSS is "
            "de Security Update Guide (api.msrc.microsoft.com/update-guide/rss, ruim 5000 "
            f"CVE-items) — puur kwetsbaarheden, daarom niet toegevoegd. {CHECKED}"
        ),
    ),
    # --- Nieuws ---
    SeedSource("https://therecord.media/feed", category="news", status="actief", notes=CHECKED),
    SeedSource("https://isc.sans.edu/rssfeed.xml", category="news", status="actief", notes=CHECKED),
    # --- Threat research ---
    SeedSource("https://blog.talosintelligence.com/rss/", category="threat_research", status="actief", notes=CHECKED),
    SeedSource("https://unit42.paloaltonetworks.com/feed/", category="threat_research", status="actief", notes=CHECKED),
    SeedSource(
        "https://cloudblog.withgoogle.com/topics/threat-intelligence/rss/",
        category="threat_research",
        status="actief",
        notes=f"Mandiant/Google Threat Intelligence (Google Cloud blog). {CHECKED}",
    ),
    SeedSource(
        "https://www.crowdstrike.com/en-us/blog/feed",
        category="threat_research",
        status="actief",
        notes=f"De oude URL /blog/feed/ redirect (301) hierheen. {CHECKED}",
    ),
    SeedSource("https://securelist.com/feed/", category="threat_research", status="actief", notes=CHECKED),
    # --- Vendors ---
    SeedSource(
        "https://www.zscaler.com/blogs/feeds",
        category="vendor_product",
        status="actief",
        notes=f"Zscaler blog. Geen feed gevonden voor de newsroom/persberichten. {CHECKED}",
    ),
    SeedSource(
        "https://www.netskope.com/feed",
        category="vendor_product",
        status="actief",
        notes=(
            "Netskope (link rel=alternate van de site). De feed op /blog/feed heeft maar 1 "
            f"nietszeggend item. {CHECKED}"
        ),
    ),
    SeedSource(
        "https://www.paloaltonetworks.com/blog/feed/",
        category="vendor_product",
        status="actief",
        notes=f"Palo Alto Networks blog. Geen feed gevonden voor de newsroom. {CHECKED}",
    ),
    SeedSource(
        "https://feeds.fortinet.com/fortinet/press-releases",
        category="vendor_product",
        status="actief",
        notes=f"Fortinet newsroom (persberichten), via fortinet.com/rss-feeds. {CHECKED}",
    ),
    SeedSource(
        "https://feeds.fortinet.com/fortinet/blog/business-and-technology",
        category="vendor_product",
        status="actief",
        notes=f"Fortinet 'News & Updates', via fortinet.com/rss-feeds. {CHECKED}",
    ),
    SeedSource("https://blog.cloudflare.com/rss/", category="vendor_product", status="actief", notes=f"Cloudflare blog. {CHECKED}"),
    # --- Standaarden en protocollen ---
    SeedSource(
        "https://www.ietf.org/blog/feed/",
        category="standards_protocols",
        status="actief",
        notes=f"IETF Blog. {CHECKED}",
    ),
    SeedSource(
        "https://github.com/cabforum/servercert/releases.atom",
        category="standards_protocols",
        status="actief",
        notes=(
            "cabforum.org zelf heeft geen feed (geen link rel=alternate; /feed, /blog/feed, "
            "/?feed=rss2 en /index.xml geven 404/geen XML). Dit is de releasefeed van de "
            f"officiële GitHub-repo cabforum/servercert (Baseline Requirements). {CHECKED}"
        ),
    ),
    SeedSource(
        "https://www.nist.gov/blogs/cybersecurity-insights/rss.xml",
        category="standards_protocols",
        status="actief",
        notes=(
            "NIST Cybersecurity Insights (via nist.gov/news-events/nist-rss-feeds). Er is geen "
            "post-quantum-specifieke feed: csrc.nist.gov heeft er geen en de nist.gov-feed "
            f"'news-events/cybersecurity' geeft 0 items. {CHECKED}"
        ),
    ),
]


class SeedReport(NamedTuple):
    created: list[SeedSource]
    category_set: list[SeedSource]
    unchanged: list[SeedSource]


def seed(db: Session, sources: list[SeedSource] | None = None) -> SeedReport:
    created: list[SeedSource] = []
    category_set: list[SeedSource] = []
    unchanged: list[SeedSource] = []
    buckets = {"created": created, "category_set": category_set, "unchanged": unchanged}

    for entry in SEED_SOURCES if sources is None else sources:
        _source, outcome = discovery.upsert_seed_source(
            db,
            entry.url,
            entry.type,
            category=entry.category,
            status=entry.status,
            notes=entry.notes,
        )
        buckets[outcome].append(entry)
    return SeedReport(created, category_set, unchanged)


def main() -> None:
    Base.metadata.create_all(bind=engine)
    added_columns = migrations.add_missing_columns(engine)
    if added_columns:
        print("Schema bijgewerkt, kolommen toegevoegd: " + ", ".join(added_columns) + "\n")

    db = SessionLocal()
    try:
        report = seed(db)

        for entry in report.created:
            print(f"NIEUW      status={entry.status:<13} {entry.category or '-':<19} {entry.url}")
        for entry in report.category_set:
            print(f"CATEGORIE  bestond al, alleen lege categorie gezet op {entry.category}: {entry.url}")

        print(
            f"\n{len(report.created)} toegevoegd, {len(report.category_set)} bestaand met categorie ingevuld, "
            f"{len(report.unchanged)} bestaand ongewijzigd, van {len(SEED_SOURCES)} totaal."
        )
        print("Status, type en notities van bestaande bronnen zijn niet aangepast.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
