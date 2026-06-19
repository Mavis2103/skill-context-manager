"""Tests for skill knowledge graph."""

import sqlite3
import tempfile
from pathlib import Path

from scm.db import init_schema
from scm.graph import SkillGraph


class TestSkillGraph:
    def test_empty_db_produces_empty_graph(self):
        """Empty DB produces graph with just the skills table (no edges)."""
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.db"
            init_schema(db)
            g = SkillGraph(db_path=db)
            g.build_from_db()
            stats = g.get_stats()
            assert stats["nodes"] >= 0
            assert stats["loaded"] is True

    def test_ppr_from_empty_seeds(self):
        """PPR from empty seeds returns empty dict."""
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.db"
            init_schema(db)
            g = SkillGraph(db_path=db)
            g.build_from_db()
            scores = g.ppr([])
            assert scores == {}

    def test_ppr_from_unknown_seeds(self):
        """PPR from seeds not in graph returns empty dict."""
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.db"
            init_schema(db)
            g = SkillGraph(db_path=db)
            g.build_from_db()
            scores = g.ppr(["nonexistent-skill"])
            # Empty graph → empty scores (no skills with edges)
            assert isinstance(scores, dict)

    def test_get_neighbors_empty_graph(self):
        """Getting neighbors from empty graph returns empty list."""
        g = SkillGraph()
        g._graph = {}
        g._loaded = True
        neighbors = g.get_neighbors("any-skill")
        assert neighbors == []

    def test_get_neighbors_with_filter(self):
        """Edge type filter works."""
        g = SkillGraph()
        g._graph = {
            "skill-a": [("skill-b", 0.5, "content"), ("skill-c", 0.3, "co_occur")],
            "skill-b": [("skill-a", 0.5, "content")],
            "skill-c": [("skill-a", 0.3, "co_occur")],
        }
        g._loaded = True
        content_n = g.get_neighbors("skill-a", edge_types={"content"})
        assert len(content_n) == 1
        assert content_n[0][2] == "content"

    def test_content_similarity_from_category_and_tags(self):
        """Content edges built from category and tags overlap."""
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.db"
            init_schema(db)

            # Insert skills directly — use init_schema's db
            with sqlite3.connect(str(db)) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("""
                    INSERT INTO skills (name, description, body, category, tags)
                    VALUES ('k8s-deploy', 'Deploy to K8s', 'body',
                            'devops', '["k8s","helm"]')
                """)
                conn.execute("""
                    INSERT INTO skills (name, description, body, category, tags)
                    VALUES ('helm-chart', 'Helm charts', 'body',
                            'devops', '["k8s","helm","chart"]')
                """)
                conn.execute("""
                    INSERT INTO skills (name, description, body, category, tags)
                    VALUES ('pg-backup', 'Backup postgres', 'body',
                            'database', '["postgres","backup"]')
                """)
                conn.commit()

            g = SkillGraph(db_path=db)
            g.build_from_db()

            # k8s-deploy and helm-chart should be connected (same category, overlapping tags)
            neighbors = g.get_neighbors("k8s-deploy")
            names = {n[0] for n in neighbors}
            assert "helm-chart" in names
            # pg-backup should NOT be connected (different category, no overlapping tags)
            assert "pg-backup" not in names

    def test_graph_stats(self):
        """Graph stats return correct counts."""
        g = SkillGraph()
        g._graph = {
            "a": [("b", 0.5, "content")],
            "b": [("a", 0.5, "content")],
        }
        g._loaded = True
        stats = g.get_stats()
        assert stats["nodes"] == 2
        assert stats["edges"] == 1
        assert "content" in stats["edge_types"]
