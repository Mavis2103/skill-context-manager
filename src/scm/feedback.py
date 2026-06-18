"""Feedback collection and learning system — Bayesian update over skill usage."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from .db import connect, init_schema
from .models import FeedbackRecord, QueryResult


class FeedbackEngine:
    """Learn from skill usage to improve future selections using Bayesian updating."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path
        init_schema(db_path)

    def _conn(self):
        return connect(self.db_path)

    def record(self, record: FeedbackRecord):
        """Record feedback about a skill usage."""
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO feedback (query, skill_name, success, latency_ms, user_rating, timestamp)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (record.query, record.skill_name, 1 if record.success else 0,
                  record.latency_ms, record.user_rating, record.timestamp))
            # Bayesian skill weight update
            conn.execute("""
                INSERT INTO skill_weights (skill_name, successes, failures, last_updated)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(skill_name) DO UPDATE SET
                    successes = successes + ?, failures = failures + ?, last_updated = ?
            """, (record.skill_name, 1 if record.success else 0, 0 if record.success else 1,
                  record.timestamp, 1 if record.success else 0, 0 if record.success else 1,
                  record.timestamp))
            conn.execute("""
                UPDATE skill_weights SET
                    base_weight = CAST(successes + 1 AS REAL) / (successes + failures + 2)
                WHERE skill_name = ?
            """, (record.skill_name,))
            # Query pattern learning
            pattern_key = self._normalize_query(record.query)
            if pattern_key:
                existing = conn.execute(
                    "SELECT best_skill, count FROM query_patterns WHERE pattern_key = ?",
                    (pattern_key,)
                ).fetchone()
                if existing:
                    best, count = existing
                    new_count = count + 1
                    if record.success and record.skill_name != best:
                        best_stats = conn.execute(
                            "SELECT successes, failures FROM skill_weights WHERE skill_name = ?",
                            (best,)
                        ).fetchone()
                        current_stats = conn.execute(
                            "SELECT successes, failures FROM skill_weights WHERE skill_name = ?",
                            (record.skill_name,)
                        ).fetchone()
                        if best_stats and current_stats:
                            best_rate = best_stats[0] / max(best_stats[0] + best_stats[1], 1)
                            current_rate = current_stats[0] / max(current_stats[0] + current_stats[1], 1)
                            if current_rate > best_rate:
                                conn.execute("""
                                    UPDATE query_patterns SET best_skill = ?, count = ?, last_used = ?
                                    WHERE pattern_key = ?
                                """, (record.skill_name, new_count, record.timestamp, pattern_key))
                            else:
                                conn.execute("""
                                    UPDATE query_patterns SET count = ?, last_used = ?
                                    WHERE pattern_key = ?
                                """, (new_count, record.timestamp, pattern_key))
                    else:
                        conn.execute("""
                            UPDATE query_patterns SET count = ?, last_used = ?
                            WHERE pattern_key = ?
                        """, (new_count, record.timestamp, pattern_key))
                else:
                    conn.execute("""
                        INSERT INTO query_patterns (pattern_key, best_skill, count, last_used)
                        VALUES (?, ?, 1, ?)
                    """, (pattern_key, record.skill_name, record.timestamp))
            conn.commit()

    def apply_weights(self, results: list[QueryResult]) -> list[QueryResult]:
        """Apply learned weights to adjust retrieval scores (returns NEW list, no mutation)."""
        if not results:
            return results
        with self._conn() as conn:
            weighted: list[QueryResult] = []
            for r in results:
                row = conn.execute(
                    "SELECT base_weight FROM skill_weights WHERE skill_name = ?",
                    (r.skill.name,)
                ).fetchone()
                if row:
                    new_score = round(r.score * 0.7 + row[0] * 0.3, 4)
                    weighted.append(QueryResult(
                        skill=r.skill,
                        score=new_score,
                        retrieval_method=r.retrieval_method + "+weighted",
                    ))
                else:
                    weighted.append(r)
        weighted.sort(key=lambda r: r.score, reverse=True)
        return weighted

    def get_best_skill_for_query(self, query: str) -> Optional[str]:
        """Get the best known skill for this query pattern."""
        pattern_key = self._normalize_query(query)
        if not pattern_key:
            return None
        with self._conn() as conn:
            row = conn.execute(
                "SELECT best_skill FROM query_patterns WHERE pattern_key = ?", (pattern_key,)
            ).fetchone()
            return row[0] if row else None

    def get_stats(self) -> dict:
        """Return feedback statistics."""
        with self._conn() as conn:
            total = conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]
            successes = conn.execute("SELECT COUNT(*) FROM feedback WHERE success = 1").fetchone()[0]
            patterns = conn.execute("SELECT COUNT(*) FROM query_patterns").fetchone()[0]
            skills = conn.execute("SELECT COUNT(*) FROM skill_weights").fetchone()[0]
            top_skills = conn.execute("""
                SELECT skill_name, successes, failures,
                       CAST(successes AS REAL) / MAX(successes + failures, 1) as rate
                FROM skill_weights WHERE successes + failures > 0
                ORDER BY rate DESC LIMIT 10
            """).fetchall()
            return {
                "total_feedback": total,
                "success_rate": successes / max(total, 1),
                "query_patterns": patterns,
                "skills_with_feedback": skills,
                "top_skills": [
                    {"name": s[0], "successes": s[1], "failures": s[2], "rate": round(s[3], 3)}
                    for s in top_skills
                ],
            }

    @staticmethod
    def _normalize_query(query: str) -> str:
        """Normalize a query to extract its pattern key."""
        if not query or not query.strip():
            return ""
        stopwords = {'the', 'a', 'an', 'is', 'are', 'was', 'please', 'help',
                     'need', 'want', 'like', 'get', 'can', 'you', 'me', 'i',
                     'to', 'of', 'in', 'for', 'on', 'with', 'at'}
        words = re.findall(r'\w+', query.lower())
        words = [w for w in words if w not in stopwords and len(w) > 2]
        words.sort()
        return " ".join(words) if words else ""
