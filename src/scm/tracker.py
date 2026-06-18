"""Usage analytics tracker — collect and analyze skill usage patterns."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from .db import connect, init_schema


class UsageTracker:
    """Track and analyze skill usage over time. Uses the shared SCM database."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path
        init_schema(db_path)

    def _conn(self):
        return connect(self.db_path)

    def record_event(self, skill_name: str, query: str, retrieval_method: str,
                     score: float, tokens_saved: int = 0, latency_ms: int = 0):
        """Record a skill selection event."""
        now = datetime.utcnow()
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO usage_events
                (timestamp, skill_name, query, retrieval_method, score, tokens_saved, latency_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (now.isoformat(), skill_name, query, retrieval_method,
                  score, tokens_saved, latency_ms))
            date_str = now.strftime("%Y-%m-%d")
            conn.execute("""
                INSERT INTO daily_stats (date, queries, skills_loaded, tokens_saved, avg_latency_ms)
                VALUES (?, 1, 1, ?, ?)
                ON CONFLICT(date) DO UPDATE SET
                    queries = queries + 1, skills_loaded = skills_loaded + 1,
                    tokens_saved = tokens_saved + ?,
                    avg_latency_ms = (avg_latency_ms * queries + ?) / (queries + 1)
            """, (date_str, tokens_saved, latency_ms, tokens_saved, latency_ms))
            conn.commit()

    def get_insights(self, days: int = 30) -> dict:
        """Get usage insights for the last N days."""
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        with self._conn() as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM usage_events WHERE timestamp > ?", (cutoff,)
            ).fetchone()[0]
            top_skills = conn.execute("""
                SELECT skill_name, COUNT(*) as cnt FROM usage_events
                WHERE timestamp > ? GROUP BY skill_name ORDER BY cnt DESC LIMIT 10
            """, (cutoff,)).fetchall()
            methods = conn.execute("""
                SELECT retrieval_method, COUNT(*) as cnt FROM usage_events
                WHERE timestamp > ? GROUP BY retrieval_method
            """, (cutoff,)).fetchall()
            tokens = conn.execute(
                "SELECT SUM(tokens_saved) FROM usage_events WHERE timestamp > ?", (cutoff,)
            ).fetchone()[0] or 0
            daily = conn.execute("""
                SELECT date, queries, skills_loaded, tokens_saved FROM daily_stats
                WHERE date > ? ORDER BY date
            """, ((datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d"),)).fetchall()
            # Cross-DB unused skills query — now works since skills is in same DB
            unused = conn.execute("""
                SELECT s.name FROM skills s
                WHERE s.name NOT IN (
                    SELECT DISTINCT skill_name FROM usage_events
                ) LIMIT 20
            """).fetchall()
            return {
                "period_days": days,
                "total_queries": total,
                "top_skills": [{"name": s[0], "count": s[1]} for s in top_skills],
                "retrieval_methods": {m[0]: m[1] for m in methods},
                "tokens_saved_estimate": tokens,
                "daily_trend": {
                    row[0]: {"queries": row[1], "skills_loaded": row[2], "tokens_saved": row[3]}
                    for row in daily
                },
                "unused_skills": [s[0] for s in unused],
            }
