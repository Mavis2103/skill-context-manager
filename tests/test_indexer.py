"""Tests for Skill Context Manager — indexing engine."""

import tempfile
from pathlib import Path

import pytest

from scm.indexer import SkillIndexer


@pytest.fixture
def indexer():
    """Fresh indexer with temp DB for each test."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "test_index.db"
        idx = SkillIndexer(db_path=db)
        yield idx


@pytest.fixture
def skill_dir():
    """Create a temp directory with test skills."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        # Skill 1: with full frontmatter
        s1 = base / "k8s-deploy" / "SKILL.md"
        s1.parent.mkdir()
        s1.write_text("""\
---
name: k8s-deploy
description: Deploy to Kubernetes
tags: [k8s, helm, deploy]
---
How to deploy to k8s
""")
        # Skill 2: minimal
        s2 = base / "pytest-runner" / "SKILL.md"
        s2.parent.mkdir()
        s2.write_text("""\
---
name: pytest-runner
description: Run pytest tests
---
pytest -v
""")
        # Skill 3: no frontmatter
        s3 = base / "bare-skill" / "SKILL.md"
        s3.parent.mkdir()
        s3.write_text("Just content")
        yield base


class TestSkillIndexer:
    def test_index_directory(self, indexer, skill_dir):
        count = indexer.index_directory(skill_dir)
        assert count == 3
        stats = indexer.stats()
        assert stats["total_skills"] == 3

    def test_index_empty_directory(self, indexer):
        with tempfile.TemporaryDirectory() as tmp:
            count = indexer.index_directory(Path(tmp))
            assert count == 0

    def test_index_nonexistent_directory(self, indexer):
        count = indexer.index_directory(Path("/nonexistent/path"))
        assert count == 0

    def test_get_skill(self, indexer, skill_dir):
        indexer.index_directory(skill_dir)
        skill = indexer.get_skill("k8s-deploy")
        assert skill is not None
        assert skill.name == "k8s-deploy"
        assert skill.description == "Deploy to Kubernetes"
        assert skill.tags == ["k8s", "helm", "deploy"]

    def test_get_skill_not_found(self, indexer):
        skill = indexer.get_skill("nonexistent")
        assert skill is None

    def test_list_skills(self, indexer, skill_dir):
        indexer.index_directory(skill_dir)
        skills = indexer.list_skills()
        assert len(skills) == 3
        names = [s.name for s in skills]
        assert "k8s-deploy" in names
        assert "pytest-runner" in names
        assert "bare-skill" in names

    def test_list_skills_by_category(self, indexer, skill_dir):
        indexer.index_directory(skill_dir)
        # All are uncategorized by default
        skills = indexer.list_skills(category="uncategorized")
        assert len(skills) == 3
        skills = indexer.list_skills(category="nonexistent")
        assert len(skills) == 0

    def test_reindex_updates_existing(self, indexer, skill_dir):
        """Re-indexing updates skill without creating duplicates."""
        indexer.index_directory(skill_dir)
        stats1 = indexer.stats()
        assert stats1["total_skills"] == 3

        # Modify a skill file
        skill_file = skill_dir / "k8s-deploy" / "SKILL.md"
        skill_file.write_text("""\
---
name: k8s-deploy
description: Updated description
---
Updated body
""")

        indexer.index_directory(skill_dir)
        stats2 = indexer.stats()
        assert stats2["total_skills"] == 3  # No duplicates

        skill = indexer.get_skill("k8s-deploy")
        assert skill.description == "Updated description"

    def test_stats(self, indexer, skill_dir):
        indexer.index_directory(skill_dir)
        stats = indexer.stats()
        assert stats["total_skills"] == 3
        assert stats["total_tokens_metadata"] > 0
        assert stats["total_tokens_body"] > 0
        assert "uncategorized" in stats["categories"]

    def test_empty_db_stats(self, indexer):
        stats = indexer.stats()
        assert stats["total_skills"] == 0
        assert stats["total_tokens_metadata"] == 0
        assert stats["total_tokens_body"] == 0

    def test_wal_mode_enabled(self, indexer, skill_dir):
        """WAL mode should be set on each connection."""
        import sqlite3
        with sqlite3.connect(str(indexer.db_path)) as conn:
            journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            assert journal_mode == "wal"
