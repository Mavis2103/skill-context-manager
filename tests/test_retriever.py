"""Tests for Skill Context Manager — retrieval engine."""

import tempfile
from pathlib import Path

import pytest

from scm.indexer import SkillIndexer
from scm.retriever import SkillRetriever


@pytest.fixture
def indexed_skills():
    """Set up indexer + retriever with sample skills in temp DB."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "test.db"

        # Index some test skills
        base = Path(tmp) / "skills"
        skills_data = {
            "k8s-deploy": {
                "description": "Deploy applications to Kubernetes using Helm",
                "tags": "[k8s, helm, deploy]",
                "body": "kubectl apply -f k8s/\nhelm upgrade --install\nkubectl get pods",
            },
            "pytest-run": {
                "description": "Run Python unit tests with pytest",
                "tags": "[python, test, pytest]",
                "body": "pytest -v --cov=src\ntests/test_*.py",
            },
            "pg-backup": {
                "description": "Backup and restore PostgreSQL databases",
                "tags": "[postgres, backup, pg_dump]",
                "body": "pg_dump -h host -U user dbname > backup.sql",
            },
            "docker-build": {
                "description": "Build and push Docker images",
                "tags": "[docker, build, registry]",
                "body": "docker build -t myapp:latest .",
            },
            "monitoring": {
                "description": "System monitoring with Prometheus and Grafana",
                "tags": "[monitoring, prometheus, metrics]",
                "body": "Deploy Prometheus\nDeploy Grafana dashboards",
            },
        }

        for name, info in skills_data.items():
            skill_dir = base / name
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                f"---\nname: {name}\ndescription: {info['description']}\n"
                f"tags: {info['tags']}\n---\n{info['body']}"
            )

        indexer = SkillIndexer(db_path=db)
        indexer.index_directory(base)
        retriever = SkillRetriever(db_path=db)
        yield retriever


class TestRetriever:
    def test_bm25_exact_match(self, indexed_skills):
        results = indexed_skills.bm25_search("kubernetes deploy", top_k=3)
        assert len(results) >= 1
        assert results[0].skill.name == "k8s-deploy"

    def test_bm25_partial_match(self, indexed_skills):
        results = indexed_skills.bm25_search("docker", top_k=3)
        assert len(results) >= 1
        assert results[0].skill.name == "docker-build"

    def test_bm25_no_match(self, indexed_skills):
        results = indexed_skills.bm25_search("xyznonexistent123", top_k=3)
        assert len(results) == 0

    def test_bm25_multiple_results(self, indexed_skills):
        results = indexed_skills.bm25_search("test", top_k=5)
        assert len(results) >= 1

    def test_bm25_empty_result(self, indexed_skills):
        """BM25 (lexical) returns empty for non-matching queries."""
        results = indexed_skills.bm25_search("xyznonexistent", top_k=3)
        assert len(results) == 0

    def test_session_boost(self, indexed_skills):
        results = indexed_skills.bm25_search("monitoring", top_k=3)
        assert len(results) >= 1
        original_score = results[0].score

        boosted = indexed_skills.apply_session_boost(
            results, recent_skills=["monitoring"], boost=0.5
        )
        assert boosted[0].score == round(original_score + 0.5, 4)
        assert "+session" in boosted[0].retrieval_method

    def test_session_boost_no_match(self, indexed_skills):
        results = indexed_skills.bm25_search("docker", top_k=3)
        boosted = indexed_skills.apply_session_boost(
            results, recent_skills=["nonexistent"], boost=0.5
        )
        # Scores unchanged
        assert boosted[0].score == results[0].score

    def test_empty_database(self):
        """Retriever should handle empty DB gracefully."""
        with tempfile.TemporaryDirectory() as tmp:
            # Create fresh DB with empty skills table
            db = Path(tmp) / "empty.db"
            # Don't index anything — just create with init
            SkillIndexer(db_path=db)
            # The DB is initialized but empty
            retriever = SkillRetriever(db_path=db)
            results = retriever.bm25_search("anything", top_k=5)
            assert len(results) == 0

    def test_rrf_returns_diverse_results(self, indexed_skills):
        """RRF returns BM25 + graph-boosted results."""
        results = indexed_skills.rrf_search("deploy kubernetes", top_k=5)
        assert len(results) > 0
        assert all(r.score > 0 for r in results)

    def test_rrf_empty_query(self, indexed_skills):
        """Empty query returns empty list."""
        results = indexed_skills.rrf_search("", top_k=5)
        assert results == []

    def test_rrf_respects_top_k(self, indexed_skills):
        """RRF returns at most top_k results."""
        for k in [1, 3, 10]:
            results = indexed_skills.rrf_search("database", top_k=k)
            assert len(results) <= k
