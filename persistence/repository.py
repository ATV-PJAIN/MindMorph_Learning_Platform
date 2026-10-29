"""Repository for learning sessions and user management (Repository pattern).

The abstract ``LearningSessionRepository`` and ``UserRepository`` are the interfaces the service/API layer depends on, so
the Postgres engine stays swappable (a future Redis/in-memory impl, or a different DB, drops in without
touching callers). The Postgres implementation persists each session as a single JSONB row.
"""
import json
import logging
import os
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Optional
from threading import RLock
from functools import wraps

from persistence.errors import SessionSnapshot, SessionConflictError, StorageUnavailableError
from services.generation_privacy import private_context

logger = logging.getLogger(__name__)

# Safety cap for list_all() — it loads full session blobs into memory (operator/analytics only).
LIST_ALL_LIMIT = 5000


class UserRepository(ABC):
    """Storage-agnostic interface for user persistence."""

    @abstractmethod
    def create(self, user_id: str, email: str, hashed_password: str, full_name: str = "") -> dict:
        """Create and return user record dict."""

    @abstractmethod
    def get_by_email(self, email: str) -> Optional[dict]:
        """Fetch user record by email, or None."""

    @abstractmethod
    def get_by_id(self, user_id: str) -> Optional[dict]:
        """Fetch user record by user_id, or None."""


class PostgresUserRepository(UserRepository):
    """PostgreSQL user repository."""

    def create(self, user_id: str, email: str, hashed_password: str, full_name: str = "") -> dict:
        from persistence.db import get_sessionmaker
        from persistence.models import UserRow

        Session = get_sessionmaker()
        with Session() as s:
            user = UserRow(
                id=user_id,
                email=email.strip().lower(),
                hashed_password=hashed_password,
                full_name=full_name.strip(),
            )
            s.add(user)
            s.commit()
            return {
                "id": user.id,
                "email": user.email,
                "full_name": user.full_name,
                "created_at": user.created_at.isoformat() if user.created_at else None,
            }

    def get_by_email(self, email: str) -> Optional[dict]:
        from persistence.db import get_sessionmaker
        from persistence.models import UserRow
        from sqlalchemy import select

        Session = get_sessionmaker()
        with Session() as s:
            stmt = select(UserRow).where(UserRow.email == email.strip().lower())
            user = s.execute(stmt).scalar_one_or_none()
            if not user:
                return None
            return {
                "id": user.id,
                "email": user.email,
                "hashed_password": user.hashed_password,
                "full_name": user.full_name,
                "created_at": user.created_at.isoformat() if user.created_at else None,
            }

    def get_by_id(self, user_id: str) -> Optional[dict]:
        from persistence.db import get_sessionmaker
        from persistence.models import UserRow
        from sqlalchemy import select

        Session = get_sessionmaker()
        with Session() as s:
            stmt = select(UserRow).where(UserRow.id == user_id)
            user = s.execute(stmt).scalar_one_or_none()
            if not user:
                return None
            return {
                "id": user.id,
                "email": user.email,
                "hashed_password": user.hashed_password,
                "full_name": user.full_name,
                "created_at": user.created_at.isoformat() if user.created_at else None,
            }


class InMemoryUserRepository(UserRepository):
    """In-memory user repository for zero-infra test/dev."""

    def __init__(self) -> None:
        self._by_id: dict[str, dict] = {}
        self._by_email: dict[str, dict] = {}

    def create(self, user_id: str, email: str, hashed_password: str, full_name: str = "") -> dict:
        norm_email = email.strip().lower()
        if norm_email in self._by_email:
            raise ValueError(f"User with email '{norm_email}' already exists")
        record = {
            "id": user_id,
            "email": norm_email,
            "hashed_password": hashed_password,
            "full_name": full_name.strip(),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._by_id[user_id] = record
        self._by_email[norm_email] = record
        return {
            "id": record["id"],
            "email": record["email"],
            "full_name": record["full_name"],
            "created_at": record["created_at"],
        }

    def get_by_email(self, email: str) -> Optional[dict]:
        return self._by_email.get(email.strip().lower())

    def get_by_id(self, user_id: str) -> Optional[dict]:
        return self._by_id.get(user_id)


_user_memory_singleton = InMemoryUserRepository()


def get_default_user_repository() -> UserRepository:
    """Return the configured UserRepository."""
    store = os.getenv("MINDMORPH_STORE", "postgres").lower()
    if store == "memory":
        return _user_memory_singleton
    return PostgresUserRepository()


class LearningSessionRepository(ABC):
    """Storage-agnostic interface for persisting ``learning_session`` dicts."""

    @abstractmethod
    def get(self, user_id: str, session_id: str) -> Optional[dict]:
        """Return the stored learning_session dict, or None if absent."""

    @abstractmethod
    def save(self, user_id: str, session_id: str, ls: dict, title: Optional[str] = None) -> None:
        """Insert a new dict, or save a snapshot returned by get().

        Updates compare the snapshot's revision; stale snapshots and unversioned overwrites raise
        SessionConflictError. Missing titles preserve the existing label.
        """

    @abstractmethod
    def list_sessions(self, user_id: str) -> list[dict]:
        """Return session metadata for a user, most-recently-updated first."""

    @abstractmethod
    def delete(self, user_id: str, session_id: str) -> None:
        """Remove a session (no-op if absent)."""

    @abstractmethod
    def list_all(self) -> list[tuple[str, str, dict]]:
        """Return every stored session as (user_id, session_id, learning_session). Operator/analytics
        use only — loads full blobs across all users, so callers must gate access (see /admin/funnel)."""


def _storage_errors(method):
    @wraps(method)
    def wrapped(*args, **kwargs):
        from sqlalchemy.exc import SQLAlchemyError
        try:
            with private_context():
                return method(*args, **kwargs)
        except SQLAlchemyError as exc:
            logger.error("Database operation failed: %s (%s)", method.__name__, type(exc).__name__)
            raise StorageUnavailableError("Progress could not be saved or loaded. Please try again.") from exc
    return wrapped


def _sync_progress_to_neo4j(session_id: str, ls: dict, user_id: str | None = None, revision: int = 1) -> None:
    """Best-effort mirror of node progress into Neo4j; the session row is already saved, so never raise."""
    try:
        if not user_id or not ls.get("skill_graph", {}).get("nodes"):
            return
        from persistence.private_graph import write_session_snapshot
        with private_context():
            write_session_snapshot(user_id, session_id, ls["skill_graph"], ls.get("node_state", {}), revision)
    except Exception:
        logger.warning("neo4j progress sync failed (non-fatal)")


class PostgresLearningSessionRepository(LearningSessionRepository):
    """Durable storage only. Updates compare the version read by the caller."""

    @_storage_errors
    def get(self, user_id, session_id):
        from persistence.db import get_sessionmaker
        from persistence.models import LearningSessionRow
        with get_sessionmaker()() as session:
            row = session.get(LearningSessionRow, (user_id, session_id))
            return SessionSnapshot(row.data, row.revision) if row is not None else None

    @_storage_errors
    def save(self, user_id, session_id, ls, title=None):
        from sqlalchemy import update, func
        from sqlalchemy.dialects.postgresql import insert
        from persistence.db import get_sessionmaker
        from persistence.models import LearningSessionRow as Row
        expected = getattr(ls, "revision", None)
        if expected is None:
            stmt = insert(Row).values(user_id=user_id, session_id=session_id, data=dict(ls),
                                      title=title or "", revision=1).on_conflict_do_nothing()
        else:
            values = {"data": dict(ls), "updated_at": func.now(), "revision": Row.revision + 1}
            if title is not None:
                values["title"] = title
            stmt = update(Row).where(Row.user_id == user_id, Row.session_id == session_id,
                                     Row.revision == expected).values(**values)
        with get_sessionmaker()() as session:
            revision = session.execute(stmt.returning(Row.revision)).scalar_one_or_none()
            if revision is None:
                raise SessionConflictError("Your progress changed in another action. Reload the path before trying again.")
            session.commit()
        if isinstance(ls, SessionSnapshot):
            ls.revision = revision
        logger.info("persistence: saved session revision %d", revision)

        _sync_progress_to_neo4j(session_id, ls, user_id, revision)

    @_storage_errors
    def list_sessions(self, user_id):
        from sqlalchemy import select
        from persistence.db import get_sessionmaker
        from persistence.models import LearningSessionRow as Row
        with get_sessionmaker()() as session:
            rows = session.execute(select(Row).where(Row.user_id == user_id)
                                   .order_by(Row.updated_at.desc(), Row.session_id)).scalars().all()
            return [{"session_id": r.session_id, "title": r.title,
                     "updated_at": r.updated_at.isoformat() if r.updated_at else None} for r in rows]

    @_storage_errors
    def delete(self, user_id, session_id):
        from sqlalchemy import delete
        from persistence.db import get_sessionmaker
        from persistence.models import LearningSessionRow as Row
        with get_sessionmaker()() as session:
            session.execute(delete(Row).where(Row.user_id == user_id, Row.session_id == session_id))
            session.commit()

    @_storage_errors
    def list_all(self):
        from sqlalchemy import select
        from persistence.db import get_sessionmaker
        from persistence.models import LearningSessionRow as Row
        with get_sessionmaker()() as session:
            rows = session.execute(select(Row).limit(LIST_ALL_LIMIT)).scalars().all()
            return [(r.user_id, r.session_id, dict(r.data)) for r in rows]


class InMemoryLearningSessionRepository(LearningSessionRepository):
    """Explicit process-local development/test store. Never a hidden database fallback.
    NOT durable across process restarts — use Postgres for real persistence.
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._store: dict[tuple[str, str], dict] = {}
        self._save_sequence = 0

    def get(self, user_id: str, session_id: str) -> Optional[dict]:
        with self._lock:
            row = self._store.get((user_id, session_id))
            return SessionSnapshot(json.loads(row["data"]), row["revision"]) if row is not None else None

    def save(self, user_id: str, session_id: str, ls: dict, title: Optional[str] = None) -> None:
        data = json.dumps(ls)
        with self._lock:
            key = (user_id, session_id)
            existing = self._store.get(key)
            expected = getattr(ls, "revision", None)
            current = existing["revision"] if existing else None
            if current != expected:
                raise SessionConflictError("Your progress changed in another action. Reload the path before trying again.")
            revision = (current or 0) + 1
            kept_title = existing["title"] if existing and title is None else (title or "")
            self._save_sequence += 1
            self._store[key] = {
                "data": data, "title": kept_title, "updated_at": datetime.now(timezone.utc),
                "save_sequence": self._save_sequence, "revision": revision,
            }
            if isinstance(ls, SessionSnapshot):
                ls.revision = revision

        _sync_progress_to_neo4j(session_id, ls, user_id, revision)

    def list_sessions(self, user_id: str) -> list[dict]:
        with self._lock:
            rows = [(sid, row.copy()) for (uid, sid), row in self._store.items() if uid == user_id]
        rows.sort(key=lambda t: (t[1]["updated_at"], t[1]["save_sequence"]), reverse=True)
        return [
            {"session_id": sid, "title": row["title"], "updated_at": row["updated_at"].isoformat()}
            for sid, row in rows
        ]

    def delete(self, user_id: str, session_id: str) -> None:
        with self._lock:
            self._store.pop((user_id, session_id), None)

    def list_all(self) -> list[tuple[str, str, dict]]:
        with self._lock:
            return [(uid, sid, json.loads(row["data"])) for (uid, sid), row in self._store.items()]


_memory_singleton = InMemoryLearningSessionRepository()


def get_default_repository() -> LearningSessionRepository:
    """Return the configured repository. ``MINDMORPH_STORE=memory`` selects the in-memory store
    (zero-infra dev/test); ``postgres`` (default) selects durable storage."""
    store = os.getenv("MINDMORPH_STORE", "postgres").lower()
    if store == "memory":
        return _memory_singleton
    if store != "postgres":
        raise StorageUnavailableError("Unknown storage mode. Select memory or postgres.")
    return PostgresLearningSessionRepository()
