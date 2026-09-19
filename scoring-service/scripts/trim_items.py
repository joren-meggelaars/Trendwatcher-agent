"""One-off: cut the items table down to N items (default 50).

Nothing in the service ever deletes items, so the table only grows. This is
for a deliberate fresh start — NOT something to run on every build or
startup, which would delete items on every deploy.

Duplicates (the same article stored more than once, see app/dedupe.py) are
collapsed first: only the best-ranked copy is a candidate, its twins are removed.

Which items stay, in this order of priority:
  1. items that have feedback (that is the training signal for scoring),
  2. market developments ("markt", see app/classification.py) — the digest
     only carries those, so they are the ones worth giving feedback on,
  3. newest first.

Dry-run by default: it only prints what would stay and what would go. Add
--apply to actually delete. Feedback rows of deleted items are deleted with
them (foreign key). Take a pg_dump first — this cannot be undone.

Usage (module mode, like the other scripts):

    docker compose run --rm scoring-service python -m scripts.trim_items
    docker compose run --rm scoring-service python -m scripts.trim_items --apply
    docker compose run --rm scoring-service python -m scripts.trim_items --keep 30 --apply
"""

import argparse

from sqlalchemy.orm import Session, load_only

from app import classification, dedupe, models
from app.database import SessionLocal
from app.textclean import clean_text

DEFAULT_KEEP = 50
_DELETE_CHUNK = 500  # keep IN (...) lists well below database parameter limits


def select_ids_to_keep(db: Session, keep: int) -> tuple[list[int], list[int]]:
    """Return (ids_to_keep, ids_to_delete). Pure selection, deletes nothing."""
    with_feedback = {row[0] for row in db.query(models.Feedback.item_id).distinct().all()}
    items = db.query(models.Item).options(
        # skip raw_content/embedding
        load_only(
            models.Item.id, models.Item.title, models.Item.summary,
            models.Item.url, models.Item.source, models.Item.source_id,
        )
    ).all()

    def priority(item: models.Item) -> tuple[bool, bool, int]:
        is_market = classification.classify(clean_text(item.title), clean_text(item.summary)) == "markt"
        # False sorts before True, so negate: feedback first, then market, then newest (highest id).
        return (item.id not in with_feedback, not is_market, -item.id)

    ranked = sorted(items, key=priority)

    # The same article stored more than once is kept once (the best-ranked copy,
    # so one with feedback wins); its twins always go, whatever `keep` is.
    deduper = dedupe.Deduper()
    unique = [item for item in ranked if not deduper.is_duplicate(item)]
    unique_ids = {item.id for item in unique}
    duplicate_ids = [item.id for item in ranked if item.id not in unique_ids]
    return [i.id for i in unique[:keep]], [i.id for i in unique[keep:]] + duplicate_ids


def delete_items(db: Session, item_ids: list[int]) -> None:
    for start in range(0, len(item_ids), _DELETE_CHUNK):
        chunk = item_ids[start : start + _DELETE_CHUNK]
        db.query(models.Feedback).filter(models.Feedback.item_id.in_(chunk)).delete(synchronize_session=False)
        db.query(models.Item).filter(models.Item.id.in_(chunk)).delete(synchronize_session=False)
    db.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--keep", type=int, default=DEFAULT_KEEP, help=f"items to keep (default {DEFAULT_KEEP})")
    parser.add_argument("--apply", action="store_true", help="actually delete; without it, dry-run only")
    args = parser.parse_args()
    if args.keep < 0:
        parser.error("--keep must be >= 0")

    db = SessionLocal()
    try:
        keep_ids, delete_ids = select_ids_to_keep(db, args.keep)
        total = len(keep_ids) + len(delete_ids)
        print(f"{total} items in de database: {len(keep_ids)} blijven, {len(delete_ids)} gaan weg.\n")

        if keep_ids:
            print("Blijft staan:")
            kept = {i.id: i for i in db.query(models.Item).filter(models.Item.id.in_(keep_ids)).all()}
            for item_id in keep_ids:
                item = kept[item_id]
                category = classification.classify(item.title, item.summary or "")
                print(f"  #{item.id:<5} {category:<6} {item.title[:80]}")

        if not args.apply:
            print("\nDry-run: er is niets verwijderd. Voeg --apply toe om echt te verwijderen.")
            return
        if not delete_ids:
            print("\nNiets te verwijderen.")
            return

        delete_items(db, delete_ids)
        print(f"\n{len(delete_ids)} items (en hun feedback) verwijderd; {len(keep_ids)} over.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
