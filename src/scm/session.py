"""Session state manager — track skill usage within a conversation session."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from .db import connect, init_schema
from .models import SessionState


class SessionTracker:
    """Track skill usage within agent sessions."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path
        self._active_session: Optional[SessionState] = None
        init_schema(db_path)

    def _conn(self):
        return connect(self.db_path)

    def start_session(self, session_id: str, metadata: Optional[dict] = None) -> SessionState:
        """Start a new session or resume an existing one (preserves started_at)."""
        existing = self.get_session(session_id)
        if existing:
            self._active_session = existing
            return existing

        self._active_session = SessionState(
            session_id=session_id,
            started_at=datetime.utcnow().isoformat(),
            context=metadata or {},
        )
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO sessions (session_id, started_at, metadata)
                VALUES (?, ?, ?)
            """, (session_id, self._active_session.started_at, json.dumps(metadata or {})))
            conn.commit()
        return self._active_session

    def end_session(self, session_id: Optional[str] = None):
        """End a session."""
        sid = session_id or (self._active_session.session_id if self._active_session else None)
        if not sid:
            return
        with self._conn() as conn:
            conn.execute("UPDATE sessions SET ended_at = ? WHERE session_id = ?",
                         (datetime.utcnow().isoformat(), sid))
            conn.commit()
        if self._active_session and self._active_session.session_id == sid:
            self._active_session = None

    def record_skill_use(self, skill_name: str, query: str = "",
                         success: Optional[bool] = None,
                         session_id: Optional[str] = None):
        """Record that a skill was used."""
        if not skill_name or not skill_name.strip():
            return
        if not session_id:
            return
        sid = session_id
        timestamp = datetime.utcnow().isoformat()
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO session_skills (session_id, skill_name, query, timestamp, success)
                VALUES (?, ?, ?, ?, ?)
            """, (sid, skill_name.strip(), query, timestamp, 1 if success else 0))
            conn.commit()
        if self._active_session and self._active_session.session_id == sid:
            self._active_session.record_skill_use(skill_name, query, success)

    def get_session(self, session_id: str) -> Optional[SessionState]:
        """Load a session from the database."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            if not row:
                return None
            session = SessionState(
                session_id=row["session_id"],
                started_at=row["started_at"],
                context=json.loads(row["metadata"]) if row["metadata"] else {},
            )
            skill_rows = conn.execute(
                "SELECT skill_name, query, timestamp, success FROM session_skills "
                "WHERE session_id = ? ORDER BY timestamp", (session_id,)
            ).fetchall()
            for sr in skill_rows:
                session.skills_used.append({
                    "skill": sr["skill_name"],
                    "query": sr["query"],
                    "timestamp": sr["timestamp"],
                    "success": bool(sr["success"]),
                })
            return session

    def get_recent_skills(self, session_id: str, n: int = 5) -> list[str]:
        """Get the N most recently used skills in this session."""
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT DISTINCT skill_name FROM session_skills
                WHERE session_id = ? ORDER BY timestamp DESC LIMIT ?
            """, (session_id, n)).fetchall()
            return [r[0] for r in rows]

    def get_active_session(self) -> Optional[SessionState]:
        return self._active_session

    def get_or_resolve_session(self, session_id: Optional[str] = None) -> Optional[SessionState]:
        """Resolve session by ID, or fall back to in-memory active session, or
        last-started session in the DB (for cross-process CLI usage)."""
        if session_id:
            return self.get_session(session_id)
        if self._active_session:
            return self._active_session
        # Fallback: latest started session in DB
        with self._conn() as conn:
            row = conn.execute(
                "SELECT session_id FROM sessions ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
            if row:
                return self.get_session(row[0])
        return None

    def optimize_skill_context(self, session_id: Optional[str] = None,
                               query: str = "") -> dict:
        """Generate a token-optimized context block for the agent."""
        sid = session_id or (self._active_session.session_id if self._active_session else None)
        if not sid:
            return {"active_skills": [], "related_skills": [], "context_size_tokens": 0}
        recent = self.get_recent_skills(sid, n=5)
        return {
            "session_id": sid,
            "active_skills": recent,
            "context_size_tokens": len(recent) * 15,
        }
