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

    # ── New tests: skip patterns ──────────────────────────────────────

    def test_index_skips_hidden_dirs(self, indexer, tmp_path):
        """SKILL.md inside a hidden dir (starting with '.') should be skipped."""
        hidden = tmp_path / ".git"
        hidden.mkdir()
        (hidden / "SKILL.md").write_text("---\nname: should-skip\n---\nbody")

        (tmp_path / "SKILL.md").write_text("---\nname: valid\n---\nbody")

        count = indexer.index_directory(tmp_path, recursive=True)
        assert count == 1  # only 'valid', not the one in .git/

    def test_index_skips_noise_dirs(self, indexer, tmp_path):
        """SKILL.md inside known noise dirs should be skipped."""
        for d in [".venv", "node_modules", "__pycache__", ".pytest_cache"]:
            (tmp_path / d).mkdir()
            ((tmp_path / d) / "SKILL.md").write_text(f"---\nname: {d}\n---\nbody")

        (tmp_path / "SKILL.md").write_text("---\nname: real-skill\n---\nbody")
        ok_dir = tmp_path / "my-skills"
        ok_dir.mkdir()
        (ok_dir / "SKILL.md").write_text("---\nname: second-skill\n---\nbody")

        count = indexer.index_directory(tmp_path, recursive=True)
        assert count == 2  # 'real-skill' + 'second-skill'

    def test_index_skips_custom_exclude(self, indexer, tmp_path):
        """Extra exclude patterns passed by caller are respected."""
        junk = tmp_path / "old-stuff"
        junk.mkdir()
        (junk / "SKILL.md").write_text("---\nname: skip-me\n---\nbody")

        (tmp_path / "SKILL.md").write_text("---\nname: keep\n---\nbody")

        count = indexer.index_directory(tmp_path, recursive=True, exclude={"old-stuff"})
        assert count == 1

    def test_index_non_recursive_ignores_hidden(self, indexer, tmp_path):
        """Non-recursive scan should still index files in root even if subdirs are hidden."""
        hidden = tmp_path / ".git"
        hidden.mkdir()
        (hidden / "SKILL.md").write_text("---\nname: skip\n---\nbody")

        (tmp_path / "SKILL.md").write_text("---\nname: root-skill\n---\nbody")

        count = indexer.index_directory(tmp_path, recursive=False)
        assert count == 1

    # ── New tests: auto-detect ────────────────────────────────────────

    def test_detect_skill_dirs_finds_common(self, tmp_path, monkeypatch):
        """detect_skill_dirs finds existing agent skill directories."""
        agents = tmp_path / ".agents" / "skills"
        agents.mkdir(parents=True)
        (agents / "SKILL.md").write_text("---\nname: global\n---\nbody")

        hermes = tmp_path / ".hermes" / "skills"
        hermes.mkdir(parents=True)
        (hermes / "SKILL.md").write_text("---\nname: test\n---\nbody")

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        dirs = SkillIndexer.detect_skill_dirs()
        assert hermes in dirs
        assert agents in dirs

    def test_detect_skill_dirs_returns_empty_when_none_exist(self, tmp_path, monkeypatch):
        """detect_skill_dirs returns empty list when no agent dirs exist."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        dirs = SkillIndexer.detect_skill_dirs()
        assert dirs == []

    # ── New tests: progress callback ──────────────────────────────────

    def test_index_calls_progress_callback(self, indexer, skill_dir):
        """Progress callback is called during indexing."""
        calls = []
        def cb(count, total):
            calls.append((count, total))

        indexer.index_directory(skill_dir, progress_callback=cb)
        assert len(calls) >= 1
        # Last call should report total == count
        assert calls[-1][0] == calls[-1][1] == 3

    def test_index_without_callback_still_works(self, indexer, skill_dir):
        """index_directory works when progress_callback is None (default)."""
        count = indexer.index_directory(skill_dir)
        assert count == 3
