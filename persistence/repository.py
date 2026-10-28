"""Repository for learning sessions and user management (Repository pattern).

The abstract repositories are the only interfaces the service/API layer depends on,
so the underlying storage engines stay swappable (Postgres / in-memory).
"""
import json
import logging
import os
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

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
        """Upsert the learning_session dict. ``title`` is set on insert; preserved if None on update."""

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


class PostgresLearningSessionRepository(LearningSessionRepository):
    """Production store: one JSONB row per session. SQLAlchemy/Postgres imports are done lazily so
    importing this module (e.g. for the in-memory store, or in tests) needs no live database."""

    def get(self, user_id: str, session_id: str) -> Optional[dict]:
        from persistence.db import get_sessionmaker
        from persistence.models import LearningSessionRow

        Session = get_sessionmaker()
        with Session() as s:
            row = s.get(LearningSessionRow, (user_id, session_id))
            return row.data if row is not None else None

    def save(self, user_id: str, session_id: str, ls: dict, title: Optional[str] = None) -> None:
        from persistence.db import get_sessionmaker
        from persistence.models import LearningSessionRow
        from sqlalchemy.dialects.postgresql import insert

        Session = get_sessionmaker()
        with Session() as s:
            values = {
                "user_id": user_id,
                "session_id": session_id,
                "data": ls,
                "title": title or "",
            }
            update_set = {
                "data": ls,
                "updated_at": datetime.now(timezone.utc),
            }
            if title is not None:
                update_set["title"] = title
            stmt = (
                insert(LearningSessionRow)
                .values(**values)
                .on_conflict_do_update(
                    index_elements=["user_id", "session_id"],
                    set_=update_set,
                )
            )
            s.execute(stmt)
            s.commit()
            
            # Phase 3.4: Sync progress to Neo4j
            try:
                from persistence.neo4j_graph import write_session_progress
                write_session_progress(session_id, ls.get("node_state", {}))
            except Exception as e:
                logger.warning(f"Failed to sync progress to Neo4j for session {session_id}: {e}")

    def list_sessions(self, user_id: str) -> list[dict]:
        from persistence.db import get_sessionmaker
        from persistence.models import LearningSessionRow
        from sqlalchemy import select

        Session = get_sessionmaker()
        with Session() as s:
            stmt = (
                select(
                    LearningSessionRow.session_id,
                    LearningSessionRow.title,
                    LearningSessionRow.updated_at,
                )
                .where(LearningSessionRow.user_id == user_id)
                .order_by(LearningSessionRow.updated_at.desc())
            )
            rows = s.execute(stmt).all()
            return [
                {"session_id": r.session_id, "title": r.title, "updated_at": r.updated_at.isoformat()}
                for r in rows
            ]

    def delete(self, user_id: str, session_id: str) -> None:
        from persistence.db import get_sessionmaker
        from persistence.models import LearningSessionRow
        from sqlalchemy import delete

        Session = get_sessionmaker()
        with Session() as s:
            s.execute(
                delete(LearningSessionRow).where(
                    LearningSessionRow.user_id == user_id,
                    LearningSessionRow.session_id == session_id,
                )
            )
            s.commit()

    def list_all(self) -> list[tuple[str, str, dict]]:
        from persistence.db import get_sessionmaker
        from persistence.models import LearningSessionRow
        from sqlalchemy import select

        Session = get_sessionmaker()
        with Session() as s:
            stmt = (
                select(
                    LearningSessionRow.user_id,
                    LearningSessionRow.session_id,
                    LearningSessionRow.data,
                )
                .order_by(LearningSessionRow.updated_at.desc())
                .limit(LIST_ALL_LIMIT)
            )
            return [(r.user_id, r.session_id, r.data) for r in s.execute(stmt).all()]


class InMemoryLearningSessionRepository(LearningSessionRepository):
    """In-memory store for unit tests and local runs without Postgres. Holds dicts keyed by (user_id, session_id)."""

    def __init__(self) -> None:
        self._store: dict[tuple[str, str], dict] = {}

    def get(self, user_id: str, session_id: str) -> Optional[dict]:
        row = self._store.get((user_id, session_id))
        return json.loads(row["data"]) if row is not None else None

    def save(self, user_id: str, session_id: str, ls: dict, title: Optional[str] = None) -> None:
        key = (user_id, session_id)
        existing = self._store.get(key)
        kept_title = existing["title"] if (existing and not title) else (title or "")
        self._store[key] = {
            "data": json.dumps(ls),
            "title": kept_title,
            "updated_at": datetime.now(timezone.utc),
        }
        
        # Phase 3.4: Sync progress to Neo4j
        try:
            from persistence.neo4j_graph import write_session_progress
            write_session_progress(session_id, ls.get("node_state", {}))
        except Exception:
            pass

    def list_sessions(self, user_id: str) -> list[dict]:
        rows = [
            (sid, row) for (uid, sid), row in self._store.items() if uid == user_id
        ]
        rows.sort(key=lambda t: t[1]["updated_at"], reverse=True)
        return [
            {"session_id": sid, "title": row["title"], "updated_at": row["updated_at"].isoformat()}
            for sid, row in rows
        ]

    def delete(self, user_id: str, session_id: str) -> None:
        self._store.pop((user_id, session_id), None)

    def list_all(self) -> list[tuple[str, str, dict]]:
        return [
            (uid, sid, json.loads(row["data"])) for (uid, sid), row in self._store.items()
        ]


_memory_singleton = InMemoryLearningSessionRepository()


def get_default_repository() -> LearningSessionRepository:
    """Return the configured repository. ``MINDMORPH_STORE=memory`` selects the in-memory store
    (zero-infra dev/test); anything else (default) uses Postgres."""
    store = os.getenv("MINDMORPH_STORE", "postgres").lower()
    if store == "memory":
        return _memory_singleton
    return PostgresLearningSessionRepository()
