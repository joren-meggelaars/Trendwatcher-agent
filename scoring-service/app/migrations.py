"""Additive schema migration for columns added after a table already existed.

Base.metadata.create_all only creates missing *tables*; it never adds a column
to a table that is already there, and the VM's Postgres database (like any
older local SQLite file) already has `sources`/`items`. There is no Alembic in
this project, so this does the one thing that is needed and safe: for every
model column missing from its table, run a plain
`ALTER TABLE ... ADD COLUMN`.

Deliberately limited to nullable columns without a default, which both SQLite
and PostgreSQL add without touching existing rows. Anything else (NOT NULL,
renames, type changes, drops) raises instead of guessing — that is a real
migration and should be written by hand.

Idempotent: a second run finds nothing missing and changes nothing.
"""

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from app.database import Base


def add_missing_columns(engine: Engine) -> list[str]:
    """Add model columns that are missing from existing tables.

    Returns the added columns as "table.column" (empty when up to date).
    Tables that do not exist yet are left to create_all.
    """
    dialect = engine.dialect
    quote = dialect.identifier_preparer.quote
    # Postgres can skip a column another process just added (e.g. the service
    # and a seed run starting together); SQLite has no IF NOT EXISTS here, but
    # is single-writer anyway.
    if_not_exists = "IF NOT EXISTS " if dialect.name == "postgresql" else ""

    added: list[str] = []
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                continue
            existing = {column["name"] for column in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing:
                    continue
                if column.primary_key or not column.nullable or column.server_default is not None:
                    raise RuntimeError(
                        f"{table.name}.{column.name} is missing from the database but is not a plain "
                        "nullable column; write a manual migration for it."
                    )
                conn.execute(
                    text(
                        f"ALTER TABLE {quote(table.name)} ADD COLUMN {if_not_exists}"
                        f"{quote(column.name)} {column.type.compile(dialect=dialect)}"
                    )
                )
                added.append(f"{table.name}.{column.name}")
    return added
