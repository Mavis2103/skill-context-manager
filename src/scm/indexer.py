"""Skill indexing engine — scan, parse, and index skills into the shared SCM database."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .db import connect, init_schema
from .models import Skill


class SkillIndexer:
    """Index skills from filesystem into the shared SCM database."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path  # Kept for backward compat; None = use shared db
        init_schema(db_path)

    def _conn(self):
        return connect(self.db_path)

    def index_directory(self, directory: Path, recursive: bool = True) -> int:
        """Scan a directory for SKILL.md files and index them."""
        if not directory.exists():
            print(f"⚠️  Directory not found: {directory}")
            return 0

        count = 0
        pattern = "**/SKILL.md" if recursive else "SKILL.md"
        for skill_file in sorted(directory.glob(pattern)):
            try:
                skill = Skill.from_skill_file(skill_file)
                self._upsert_skill(skill)
                count += 1
            except Exception as e:
                print(f"  ⚠️  Error indexing {skill_file}: {e}")
        return count

    def _upsert_skill(self, skill: Skill):
        """Insert or update a skill in the database + FTS5 index."""
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO skills (name, description, body, path, category, tags,
                                    token_cost_meta, token_cost_body, use_count, success_rate)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    description = excluded.description, body = excluded.body,
                    path = excluded.path, category = excluded.category,
                    tags = excluded.tags, token_cost_meta = excluded.token_cost_meta,
                    token_cost_body = excluded.token_cost_body
            """, (
                skill.name, skill.description, skill.body,
                str(skill.path) if skill.path else None,
                skill.category, json.dumps(skill.tags),
                skill.token_cost_metadata, skill.token_cost_body,
                skill.use_count, skill.success_rate,
            ))

            row = conn.execute(
                "SELECT rowid FROM skills WHERE name = ?", (skill.name,)
            ).fetchone()
            if row:
                conn.execute("DELETE FROM skills_fts WHERE rowid = ?", (row[0],))
                conn.execute("""
                    INSERT INTO skills_fts (rowid, name, description, body, tags)
                    VALUES (?, ?, ?, ?, ?)
                """, (row[0], skill.name, skill.description, skill.body, json.dumps(skill.tags)))
            conn.commit()

    def get_skill(self, name: str) -> Optional[Skill]:
        """Get a single skill by name."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM skills WHERE name = ?", (name,)
            ).fetchone()
            if row:
                return self._row_to_skill(row)
        return None

    def list_skills(self, category: Optional[str] = None) -> list[Skill]:
        """List all indexed skills, optionally filtered by category."""
        with self._conn() as conn:
            if category:
                rows = conn.execute(
                    "SELECT * FROM skills WHERE category = ? ORDER BY use_count DESC", (category,)
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM skills ORDER BY use_count DESC").fetchall()
            return [self._row_to_skill(r) for r in rows]

    def stats(self) -> dict:
        """Return indexing statistics."""
        with self._conn() as conn:
            total = conn.execute("SELECT COUNT(*) FROM skills").fetchone()[0]
            categories = conn.execute(
                "SELECT category, COUNT(*) as cnt FROM skills GROUP BY category ORDER BY cnt DESC"
            ).fetchall()
            meta = conn.execute("SELECT SUM(token_cost_meta) FROM skills").fetchone()[0] or 0
            body = conn.execute("SELECT SUM(token_cost_body) FROM skills").fetchone()[0] or 0
            return {
                "total_skills": total,
                "categories": dict(categories),
                "total_tokens_metadata": meta,
                "total_tokens_body": body,
            }

    def _row_to_skill(self, r) -> Skill:
        """Convert a DB row to a Skill object."""
        return Skill(
            name=r["name"], description=r["description"], body=r["body"],
            path=Path(r["path"]) if r["path"] else None,
            category=r["category"], tags=json.loads(r["tags"]) if r["tags"] else [],
            token_cost_metadata=r["token_cost_meta"],
            token_cost_body=r["token_cost_body"],
            use_count=r["use_count"], success_rate=r["success_rate"],
            last_used=r["last_used"],
        )
