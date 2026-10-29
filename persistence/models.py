"""ORM models for persisted learning state.

``learning_sessions``: the entire ``learning_session`` dict in a single JSONB column keyed by
``(user_id, session_id)`` (read/written as a whole — a blob keeps the mapping trivial).

``rag_chunks``: per-user ingested material for RAG (P2 #9), one row per chunk with its FastEmbed
vector in a pgvector column, queried by cosine distance scoped to ``user_id``.

``users``: user authentication identity and credentials for JWT security.
"""
from datetime import datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from persistence.db import Base
from rag.embeddings import EMBED_DIM


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class LearningSessionRow(Base):
    __tablename__ = "learning_sessions"

    user_id: Mapped[str] = mapped_column(String, primary_key=True)
    session_id: Mapped[str] = mapped_column(String, primary_key=True)
    # The whole learning_session dict (skill_graph, node_state, lessons, …). Portable JSON dict.
    data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1", default=1)
    # A short human label for the session list (the original learning query / summary).
    title: Mapped[str] = mapped_column(String, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, server_default=func.now()
    )


class RagChunkRow(Base):
    __tablename__ = "rag_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Scope every chunk to the uploading user; retrieval filters on this.
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    source: Mapped[str] = mapped_column(String, nullable=False)  # filename, shown as the citation
    source_id: Mapped[str | None] = mapped_column(String, nullable=True)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBED_DIM), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )


class UserRow(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    email: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    hashed_password: Mapped[str] = mapped_column(String, nullable=False)
    full_name: Mapped[str] = mapped_column(String, nullable=True, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )
