"""One-time seed script: adds an initial reading list of RSS sources as
"kandidaat" Source records.

Deliberately reuses discovery.create_source() — the exact function behind
POST /sources and the admin GUI's source form — instead of writing to the
database directly. That means the same dedup check (skip if the URL already
exists) applies automatically, and every seeded source still has to pass the
normal instroom check via evaluate_source()/evaluate_all_sources() once real
items come in; nothing here sets status="actief".

discovery_method="seed" (not "manual") distinguishes "part of the initial
reading list" from a source a person adds later by hand via the admin GUI.

Idempotent: safe to re-run — URLs already present are skipped, not duplicated.

Usage:
    uv run python scripts/seed_sources.py
"""

from app import discovery
from app.database import Base, SessionLocal, engine

# (url, type) — see the accompanying report for how each entry was chosen/verified.
SEED_SOURCES: list[tuple[str, str]] = [
    # Algemene vakpers
    ("https://www.darkreading.com/rss.xml", "rss"),
    ("https://www.bleepingcomputer.com/feed/", "rss"),
    ("https://feeds.feedburner.com/TheHackersNews", "rss"),
    ("https://www.securityweek.com/feed/", "rss"),
    ("https://www.helpnetsecurity.com/feed/", "rss"),
    ("https://www.csoonline.com/feed/", "rss"),
    ("https://krebsonsecurity.com/feed/", "rss"),
    ("https://www.infosecurity-magazine.com/rss/news/", "rss"),
    # Markt / funding
    ("https://techcrunch.com/category/security/feed/", "rss"),
    ("https://siliconangle.com/category/security/feed/", "rss"),
    # Vendor
    ("https://www.microsoft.com/en-us/security/blog/feed/", "rss"),
    ("https://feeds.fortinet.com/fortinet/blog/industry-trends", "rss"),
    ("https://feeds.fortinet.com/fortinet/blog/threat-research", "rss"),
    # Sector zorg
    ("https://healthitsecurity.com/feed", "rss"),
    # MSP
    ("https://www.msspalert.com/feed/", "rss"),
    ("https://www.channele2e.com/feed/", "rss"),
    # Hieronder: geverifieerd via HEAD/GET + HTML <link rel="alternate">-check
    # (zie rapportage) — niet blind een geraden pad opgeslagen.
    ("https://www.n-able.com/blog/rss.xml", "rss"),
    ("https://www.veeam.com/blog/feed", "rss"),
    ("https://feeds.ncsc.nl/nieuws.rss", "rss"),
    ("https://www.digitaleoverheid.nl/feed/", "rss"),
]


def main() -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        added: list[str] = []
        skipped: list[str] = []
        for url, type_ in SEED_SOURCES:
            source = discovery.create_source(db, url, type_, discovery_method="seed")
            if source is None:
                skipped.append(url)
                print(f"SKIP (bestaat al): {url}")
            else:
                added.append(url)
                print(f"OK  status={source.status:<12} {url}")

        print(f"\n{len(added)} toegevoegd, {len(skipped)} overgeslagen (bestonden al) van {len(SEED_SOURCES)} totaal.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
