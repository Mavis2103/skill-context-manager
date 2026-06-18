"""Shared database utilities for SCM — single DB file, WAL mode, connection helper.

All SCM modules use this single source of truth for database paths and connections,
eliminating cross-DB bugs and duplicated WAL/connection code.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


SCM_DB_DIR = Path.home() / ".scm"
SCM_DB_PATH = SCM_DB_DIR / "scm.db"


def utcnow() -> datetime:
    """Current UTC time as a naive datetime.

    Uses timezone-aware ``datetime.now(timezone.utc)`` (``datetime.utcnow()`` is
    deprecated in 3.12+) but strips the tzinfo so the resulting ``.isoformat()``
    string keeps the same shape as legacy rows already in the database.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def get_db_path(db_path: Optional[Path] = None) -> Path:
    """Return the database path, using override if provided, otherwise shared."""
    if db_path is not None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        return db_path
    SCM_DB_DIR.mkdir(parents=True, exist_ok=True)
    return SCM_DB_PATH


def connect(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Open a WAL-mode connection. Defaults to shared SCM database."""
    conn = sqlite3.connect(str(get_db_path(db_path)))
    _enable_wal(conn)
    conn.row_factory = sqlite3.Row
    return conn


def _enable_wal(conn: sqlite3.Connection):
    """Enable WAL mode + busy timeout for concurrent read safety."""
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")


def init_schema(db_path: Optional[Path] = None):
    """Create all SCM tables if they don't exist. Idempotent."""
    with connect(db_path) as conn:
        # ── Skills index ──
        conn.execute("""
            CREATE TABLE IF NOT EXISTS skills (
                name TEXT PRIMARY KEY,
                description TEXT,
                body TEXT,
                path TEXT,
                category TEXT DEFAULT 'uncategorized',
                tags TEXT DEFAULT '[]',
                token_cost_meta INTEGER DEFAULT 0,
                token_cost_body INTEGER DEFAULT 0,
                use_count INTEGER DEFAULT 0,
                success_rate REAL DEFAULT 0.0,
                last_used TEXT,
                embedding BLOB
            )
        """)
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS skills_fts
            USING fts5(name, description, body, tags)
        """)

        # ── Sessions ──
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                started_at TEXT,
                ended_at TEXT,
                skill_usage TEXT DEFAULT '[]',
                queries TEXT DEFAULT '[]',
                metadata TEXT DEFAULT '{}'
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS session_skills (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                skill_name TEXT NOT NULL,
                query TEXT DEFAULT '',
                timestamp TEXT NOT NULL,
                success INTEGER
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_session_skills_lookup
            ON session_skills(session_id, timestamp DESC)
        """)

        # ── Feedback / Learning ──
        conn.execute("""
            CREATE TABLE IF NOT EXISTS feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query TEXT,
                skill_name TEXT,
                success INTEGER,
                latency_ms INTEGER DEFAULT 0,
                user_rating INTEGER,
                timestamp TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS skill_weights (
                skill_name TEXT PRIMARY KEY,
                base_weight REAL DEFAULT 1.0,
                successes INTEGER DEFAULT 0,
                failures INTEGER DEFAULT 0,
                avg_latency_ms REAL DEFAULT 0,
                last_updated TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS query_patterns (
                pattern_key TEXT PRIMARY KEY,
                best_skill TEXT,
                count INTEGER DEFAULT 0,
                last_used TEXT
            )
        """)

        # ── Usage tracking ──
        conn.execute("""
            CREATE TABLE IF NOT EXISTS usage_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                skill_name TEXT,
                query TEXT,
                retrieval_method TEXT,
                score REAL,
                tokens_saved INTEGER DEFAULT 0,
                latency_ms INTEGER DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS daily_stats (
                date TEXT PRIMARY KEY,
                queries INTEGER DEFAULT 0,
                skills_loaded INTEGER DEFAULT 0,
                tokens_saved INTEGER DEFAULT 0,
                avg_latency_ms REAL DEFAULT 0
            )
        """)

        conn.commit()
