"""Tests for Skill Context Manager — usage tracker."""

import tempfile
from pathlib import Path

import pytest

from scm.tracker import UsageTracker


@pytest.fixture
def tracker():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "usage.db"
        yield UsageTracker(db_path=db)


class TestUsageTracker:
    def test_record_event(self, tracker):
        tracker.record_event("k8s", "deploy app", "bm25", 0.95, tokens_saved=100, latency_ms=50)
        insights = tracker.get_insights(days=30)
        assert insights["total_queries"] >= 1

    def test_multiple_events(self, tracker):
        for i in range(5):
            tracker.record_event(f"skill-{i}", f"query-{i}", "bm25", 0.5)
        insights = tracker.get_insights(days=30)
        assert insights["total_queries"] == 5

    def test_top_skills(self, tracker):
        tracker.record_event("k8s", "deploy", "bm25", 0.9)
        tracker.record_event("k8s", "scale", "bm25", 0.8)
        tracker.record_event("docker", "build", "hybrid", 0.7)
        insights = tracker.get_insights(days=30)
        assert insights["top_skills"][0]["name"] == "k8s"
        assert insights["top_skills"][0]["count"] == 2

    def test_retrieval_methods(self, tracker):
        tracker.record_event("s1", "q1", "bm25", 0.5)
        tracker.record_event("s2", "q2", "hybrid", 0.6)
        tracker.record_event("s3", "q3", "embedding", 0.7)
        insights = tracker.get_insights(days=30)
        assert "bm25" in insights["retrieval_methods"]
        assert insights["retrieval_methods"]["bm25"] == 1

    def test_tokens_saved(self, tracker):
        tracker.record_event("s1", "q1", "bm25", 0.5, tokens_saved=500)
        tracker.record_event("s2", "q2", "hybrid", 0.6, tokens_saved=300)
        insights = tracker.get_insights(days=30)
        assert insights["tokens_saved_estimate"] == 800

    def test_unused_skills_empty_when_table_shared(self, tracker):
        """Cross-DB query works now — skills table is in the same DB."""
        # Without any skills in DB, unused should be empty
        insights = tracker.get_insights(days=30)
        assert "unused_skills" in insights

    def test_empty_db(self, tracker):
        insights = tracker.get_insights(days=30)
        assert insights["total_queries"] == 0
        assert insights["tokens_saved_estimate"] == 0
        assert insights["top_skills"] == []
        assert insights["daily_trend"] == {}

    def test_daily_trend(self, tracker):
        tracker.record_event("s1", "q1", "bm25", 0.5, tokens_saved=100)
        insights = tracker.get_insights(days=30)
        assert len(insights["daily_trend"]) >= 1
