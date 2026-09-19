import pytest
from sqlalchemy import create_engine, inspect, text

from app import migrations


def _old_sources_engine(tmp_path):
    """A database as it looked before Source got category/notes."""
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE sources ("
                "id INTEGER PRIMARY KEY, url VARCHAR NOT NULL UNIQUE, type VARCHAR NOT NULL, "
                "status VARCHAR NOT NULL, discovery_method VARCHAR NOT NULL, "
                "running_avg_score FLOAT, created_at DATETIME NOT NULL)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO sources (url, type, status, discovery_method, created_at) "
                "VALUES ('https://old.example.com/feed', 'rss', 'actief', 'seed', '2026-01-01 00:00:00')"
            )
        )
    return engine


def test_adds_missing_columns_and_keeps_existing_rows(tmp_path):
    engine = _old_sources_engine(tmp_path)

    added = migrations.add_missing_columns(engine)

    assert set(added) == {"sources.category", "sources.notes"}
    assert {c["name"] for c in inspect(engine).get_columns("sources")} >= {"category", "notes"}
    with engine.connect() as conn:
        row = conn.execute(text("SELECT url, status, category, notes FROM sources")).one()
    assert tuple(row) == ("https://old.example.com/feed", "actief", None, None)


def test_is_idempotent(tmp_path):
    engine = _old_sources_engine(tmp_path)

    migrations.add_missing_columns(engine)

    assert migrations.add_missing_columns(engine) == []


def test_leaves_missing_tables_to_create_all(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'empty.db'}")

    assert migrations.add_missing_columns(engine) == []
    assert inspect(engine).get_table_names() == []


def test_refuses_to_guess_for_a_missing_not_null_column(tmp_path, monkeypatch):
    from sqlalchemy import Column, MetaData, String, Table

    engine = create_engine(f"sqlite:///{tmp_path / 'x.db'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE widgets (id INTEGER PRIMARY KEY)"))

    fake = MetaData()
    Table("widgets", fake, Column("id", String, primary_key=True), Column("name", String, nullable=False))
    monkeypatch.setattr(migrations.Base, "metadata", fake)

    with pytest.raises(RuntimeError, match="widgets.name"):
        migrations.add_missing_columns(engine)
