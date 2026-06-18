"""Tests for Skill Context Manager — session tracking and feedback."""

import tempfile
from pathlib import Path

import pytest

from scm.session import SessionTracker
from scm.feedback import FeedbackEngine, FeedbackRecord


# ── Session Tests ───────────────────────────────────────────────

@pytest.fixture
def tracker():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "sessions.db"
        yield SessionTracker(db_path=db)


class TestSessionTracker:
    def test_start_session(self, tracker):
        session = tracker.start_session("sess-1")
        assert session.session_id == "sess-1"
        assert session.started_at is not None
        assert session.context == {}

    def test_start_session_with_metadata(self, tracker):
        meta = {"user": "mavis", "channel": "discord"}
        session = tracker.start_session("sess-2", meta)
        assert session.context == meta

    def test_start_session_preserves_started_at(self, tracker):
        """Re-starting same session should NOT reset started_at."""
        s1 = tracker.start_session("sess-3")
        original = s1.started_at
        import time
        time.sleep(0.01)
        s2 = tracker.start_session("sess-3")
        assert s2.started_at == original  # Preserved

    def test_end_session(self, tracker):
        tracker.start_session("sess-4")
        tracker.end_session("sess-4")
        # Verify it ended (no crash)
        session = tracker.get_session("sess-4")
        assert session is not None

    def test_record_skill_use(self, tracker):
        tracker.start_session("sess-5")
        tracker.record_skill_use("k8s-deploy", "deploy app", True, "sess-5")
        recent = tracker.get_recent_skills("sess-5")
        assert "k8s-deploy" in recent

    def test_record_skill_use_no_session(self, tracker):
        # Should not crash
        tracker.record_skill_use("some-skill", "query", True, "nonexistent")
        # No assertion needed — just no exception

    def test_get_recent_skills_returns_last_n(self, tracker):
        tracker.start_session("sess-6")
        for i in range(10):
            tracker.record_skill_use(f"skill-{i}", f"query-{i}", session_id="sess-6")
        recent = tracker.get_recent_skills("sess-6", n=3)
        assert len(recent) == 3
        assert "skill-9" in recent
        assert "skill-0" not in recent

    def test_get_session(self, tracker):
        tracker.start_session("sess-7")
        tracker.record_skill_use("skill-a", "q1", True, "sess-7")
        tracker.record_skill_use("skill-b", "q2", False, "sess-7")
        session = tracker.get_session("sess-7")
        assert session is not None
        assert len(session.skills_used) == 2

    def test_get_nonexistent_session(self, tracker):
        session = tracker.get_session("nonexistent")
        assert session is None

    def test_optimize_skill_context(self, tracker):
        tracker.start_session("sess-8")
        tracker.record_skill_use("skill-a", session_id="sess-8")
        tracker.record_skill_use("skill-b", session_id="sess-8")
        ctx = tracker.optimize_skill_context("sess-8")
        assert "skill-a" in ctx["active_skills"]
        assert "skill-b" in ctx["active_skills"]
        assert ctx["context_size_tokens"] > 0

    def test_optimize_skill_context_no_session(self, tracker):
        ctx = tracker.optimize_skill_context()
        assert ctx["active_skills"] == []
        assert ctx["context_size_tokens"] == 0

    def test_wal_mode(self, tracker):
        import sqlite3
        with sqlite3.connect(str(tracker.db_path)) as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            assert mode == "wal"


# ── Feedback Tests ─────────────────────────────────────────────

@pytest.fixture
def feedback():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "feedback.db"
        yield FeedbackEngine(db_path=db)


class TestFeedbackEngine:
    def test_record(self, feedback):
        rec = FeedbackRecord(query="deploy app", skill_name="k8s", success=True)
        feedback.record(rec)
        stats = feedback.get_stats()
        assert stats["total_feedback"] == 1
        assert stats["success_rate"] == 1.0

    def test_record_failure(self, feedback):
        rec = FeedbackRecord(query="test", skill_name="pytest", success=False)
        feedback.record(rec)
        stats = feedback.get_stats()
        assert stats["total_feedback"] == 1
        assert stats["success_rate"] == 0.0

    def test_record_with_rating(self, feedback):
        rec = FeedbackRecord(query="build", skill_name="docker", success=True, user_rating=5)
        feedback.record(rec)
        stats = feedback.get_stats()
        assert stats["total_feedback"] == 1

    def test_apply_weights(self, feedback):
        from scm.models import Skill, QueryResult
        # Record feedback to build weights
        feedback.record(FeedbackRecord(query="deploy", skill_name="k8s", success=True))
        feedback.record(FeedbackRecord(query="deploy", skill_name="k8s", success=True))
        feedback.record(FeedbackRecord(query="deploy", skill_name="helm", success=False))

        results = [
            QueryResult(skill=Skill(name="k8s", description="K8s"), score=0.5,
                        retrieval_method="bm25"),
            QueryResult(skill=Skill(name="helm", description="Helm"), score=0.4,
                        retrieval_method="bm25"),
            QueryResult(skill=Skill(name="docker", description="Docker"), score=0.3,
                        retrieval_method="bm25"),
        ]

        weighted = feedback.apply_weights(results)
        assert weighted[0].skill.name == "k8s"  # High success rate
        assert "+weighted" in weighted[0].retrieval_method

    def test_get_best_skill_for_query(self, feedback):
        feedback.record(FeedbackRecord(query="deploy to kubernetes", skill_name="k8s", success=True))
        feedback.record(FeedbackRecord(query="deploy to kubernetes", skill_name="k8s", success=True))

        best = feedback.get_best_skill_for_query("deploy to kubernetes")
        assert best == "k8s"

    def test_get_best_skill_unknown_query(self, feedback):
        best = feedback.get_best_skill_for_query("completely new query")
        assert best is None

    def test_get_stats_empty(self, feedback):
        stats = feedback.get_stats()
        assert stats["total_feedback"] == 0
        assert stats["success_rate"] == 0
        assert stats["query_patterns"] == 0
        assert stats["skills_with_feedback"] == 0
        assert stats["top_skills"] == []

    def test_wal_mode(self, feedback):
        import sqlite3
        with sqlite3.connect(str(feedback.db_path)) as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            assert mode == "wal"

    def test_query_pattern_switching(self, feedback):
        """When a new skill outperforms the current best for a query, it should switch."""
        feedback.record(FeedbackRecord(query="deploy app", skill_name="helm", success=False))
        feedback.record(FeedbackRecord(query="deploy app", skill_name="k8s", success=True))
        feedback.record(FeedbackRecord(query="deploy app", skill_name="k8s", success=True))

        best = feedback.get_best_skill_for_query("deploy app")
        assert best == "k8s"
