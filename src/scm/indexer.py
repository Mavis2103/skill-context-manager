"""Skill indexing engine — scan, parse, and index skills into the shared SCM database."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Callable, Optional

from .db import connect, init_schema
from .models import Skill

logger = logging.getLogger("scm.indexer")


class SkillIndexer:
    """Index skills from filesystem into the shared SCM database."""

    # Directory names to skip during recursive scan
    DEFAULT_EXCLUDE = {
        ".git", ".svn", ".hg",
        ".venv", "venv", "env", ".env",
        "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache",
        "node_modules", ".next", ".turbo",
        "dist", "build", ".build",
        ".idea", ".vscode",
        ".DS_Store",
    }

    # Common agent skill directories (relative to $HOME)
    AGENT_SKILL_DIRS = [
        ".agents/skills",          # Global skills (multi-agent)
        ".hermes/skills",
        ".claude/skills",
        ".cursor/skills",
        ".codeium/windsurf/skills",
        ".codex/skills",
        ".config/goose/skills",
        ".continue/skills",
    ]

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path  # Kept for backward compat; None = use shared db
        init_schema(db_path)

    def _conn(self):
        return connect(self.db_path)

    @classmethod
    def detect_skill_dirs(cls) -> list[Path]:
        """Find all agent skill directories that exist on this system."""
        home = Path.home()
        return [home / p for p in cls.AGENT_SKILL_DIRS if (home / p).is_dir()]

    def index_directory(
        self,
        directory: Path,
        recursive: bool = True,
        exclude: Optional[set[str]] = None,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> int:
        """Scan a directory for SKILL.md files and index them.

        Skips hidden directories (names starting with '.') and common
        development-noise directories (.git, node_modules, __pycache__, etc.)
        to prevent accidental full-home scans.

        Args:
            directory: Root path to scan.
            recursive: Whether to descend into subdirectories.
            exclude: Extra basename patterns to skip (added to DEFAULT_EXCLUDE).
            progress_callback: Optional fn(file_count, total_count) called periodically.
        """
        if not directory.exists():
            logger.warning("Directory not found: %s", directory)
            return 0

        exclude_set = set(SkillIndexer.DEFAULT_EXCLUDE) | (exclude or set())

        if not recursive:
            search: list[Path] = sorted(
                p for p in directory.iterdir() if p.name == "SKILL.md"
            )
        else:
            # Walk manually so we can filter out excluded/hidden paths
            found: list[Path] = []
            for path in directory.rglob("SKILL.md"):
                # Check if any parent segment matches exclude set or starts with '.'
                parts = path.relative_to(directory).parts[:-1]  # exclude the filename
                if any(part in exclude_set or (part.startswith(".") and part not in SkillIndexer.DEFAULT_EXCLUDE) for part in parts):
                    continue
                found.append(path)
            search = sorted(found)

        count = 0
        total = len(search)
        for i, skill_file in enumerate(search):
            try:
                skill = Skill.from_skill_file(skill_file)
                self._upsert_skill(skill)
                count += 1
            except Exception as e:
                logger.warning("Error indexing %s: %s", skill_file, e)
            if progress_callback and (i % 50 == 0 or i == total - 1):
                progress_callback(i + 1, total)
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
