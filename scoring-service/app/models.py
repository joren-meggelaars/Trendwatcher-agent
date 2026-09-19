import json
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from app.config import settings
from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class EmbeddingVector(TypeDecorator):
    """Portable embedding column.

    On SQLite it stores the vector as JSON text. On PostgreSQL it transparently
    switches to pgvector's native `Vector` column. The dialect check happens
    here only — Item/Feedback and everything in scoring.py or main.py stay
    untouched when DATABASE_URL moves from sqlite:// to postgresql://.
    """

    impl = Text
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            from pgvector.sqlalchemy import Vector

            return dialect.type_descriptor(Vector(settings.embedding_dim))
        return dialect.type_descriptor(Text())

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if dialect.name == "postgresql":
            return value
        return json.dumps(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if dialect.name == "postgresql":
            return list(value)
        return json.loads(value)


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    url: Mapped[str] = mapped_column(String, unique=True, index=True)
    type: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="kandidaat", index=True)
    discovery_method: Mapped[str] = mapped_column(String)
    # Both nullable and added after the table existed: see app/migrations.py.
    # category is one of schemas.SourceCategory; notes is free text, e.g. why a
    # source was seeded as "gedeactiveerd".
    category: Mapped[str | None] = mapped_column(String, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    running_avg_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    items: Mapped[list["Item"]] = relationship(back_populates="source_ref")


class Item(Base):
    __tablename__ = "items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, index=True, default=settings.default_user_id)
    source: Mapped[str] = mapped_column(String)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String)
    url: Mapped[str] = mapped_column(String)
    raw_content: Mapped[str] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(EmbeddingVector, nullable=True)
    relevance_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    # Set when the item was mailed in the scheduled daily digest, so the next
    # digest picks the best items that have not been mailed yet. Nullable and
    # added after the table existed: see app/migrations.py.
    digested_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    feedback: Mapped[list["Feedback"]] = relationship(back_populates="item")
    source_ref: Mapped["Source | None"] = relationship(back_populates="items")


class DigestSettings(Base):
    """Single-row table (id is always 1) holding the digest tuning knobs the
    admin GUI can edit at runtime, so changing them doesn't require touching
    .env / restarting the scheduler container. The scheduler reads this via
    GET /settings/digest instead of only trusting its own static env vars.
    """

    __tablename__ = "digest_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    digest_hour: Mapped[int] = mapped_column(Integer, default=7)
    digest_top_n: Mapped[int] = mapped_column(Integer, default=5)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class Digest(Base):
    """One digest as it was put together: which items went into it. The mail's
    links point at /admin/digest/<id>, so the page shows exactly that digest,
    not "the best items right now"."""

    __tablename__ = "digests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, index=True)
    kind: Mapped[str] = mapped_column(String, default="daily")  # "daily" | "preview" (the "verstuur nu" button)
    mailed: Mapped[bool] = mapped_column(Boolean, default=False)  # False: dry run, or the send failed

    entries: Mapped[list["DigestItem"]] = relationship(
        back_populates="digest", order_by="DigestItem.position", cascade="all, delete-orphan"
    )


class DigestItem(Base):
    __tablename__ = "digest_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    digest_id: Mapped[int] = mapped_column(ForeignKey("digests.id"), index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"), index=True)
    category: Mapped[str] = mapped_column(String)  # "markt" | "nieuws", as it was in the mail
    position: Mapped[int] = mapped_column(Integer, default=0)

    digest: Mapped["Digest"] = relationship(back_populates="entries")
    item: Mapped["Item"] = relationship()


class RuntimeSetting(Base):
    """One row per setting from app/runtime_settings.py that can be changed in
    the admin GUI without touching .env or restarting anything.

    `override` is what an admin typed in (NULL = follow .env). `env_value` is
    the .env value the owning service last reported, so the GUI can show what
    is really in effect; only the scheduler reports (the scoring-service can
    read its own env directly). Values are stored as text and parsed by the
    registry. Never holds secrets: only registry keys are accepted.
    """

    __tablename__ = "runtime_settings"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    override: Mapped[str | None] = mapped_column(String, nullable=True)
    env_value: Mapped[str | None] = mapped_column(String, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class Feedback(Base):
    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"), index=True)
    user_id: Mapped[str] = mapped_column(String, index=True, default=settings.default_user_id)
    label: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    item: Mapped["Item"] = relationship(back_populates="feedback")
