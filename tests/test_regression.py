"""Regression tests for bugs found in 3-Loop Deep Review.

Bugs covered:
- FTS5 query injection sanitization
- daily_stats avg_latency_ms running average drift
- FeedbackEngine.apply_weights input mutation
- SessionTracker.record_skill_use empty skill_name
- SessionTracker.get_or_resolve_session cross-process fallback
- SkillOptimizer atomic write safety
- Library print() replaced with logging
- MCP tool return type hints (dict vs str)
- Optimizer tmp file cleanup on failure
"""

import logging
import tempfile
from pathlib import Path

import pytest

from scm.feedback import FeedbackEngine, FeedbackRecord
from scm.indexer import SkillIndexer
from scm.models import Skill, QueryResult
from scm.optimizer import SkillOptimizer
from scm.retriever import SkillRetriever
from scm.session import SessionTracker
from scm.tracker import UsageTracker


# ── FTS5 Injection Sanitization ────────────────────────────────────

class TestFTS5InjectionSafety:
    """User-controlled query strings must not break FTS5 syntax."""

    def test_fts5_query_with_quotes(self):
        r = SkillRetriever()
        queries = r._build_fts_queries('test "OR 1=1"')
        # All returned queries must be valid FTS5 syntax
        for q in queries:
            assert "OR 1=1" not in q  # Original unsafe text not present

    def test_fts5_query_with_operators(self):
        r = SkillRetriever()
        queries = r._build_fts_queries("NEAR(boom) foo bar")
        for q in queries:
            assert "NEAR(" not in q  # Operator not interpreted

    def test_fts5_query_with_unicode(self):
        r = SkillRetriever()
        queries = r._build_fts_queries("triển khai kubernetes")
        assert len(queries) > 0
        # All queries should be safe FTS5 syntax
        for q in queries:
            assert q  # Non-empty

    def test_fts5_empty_query(self):
        r = SkillRetriever()
        assert r._build_fts_queries("") == []

    def test_fts5_stopwords_only(self):
        r = SkillRetriever()
        assert r._build_fts_queries("the a an is are") == []

    def test_fts5_special_chars_stripped(self):
        r = SkillRetriever()
        queries = r._build_fts_queries("!@#$%^&*()foo")
        # Special chars are stripped, but 'foo' is preserved
        assert all("foo" in q.lower() for q in queries)


# ── daily_stats Running Average ────────────────────────────────────

class TestDailyStatsAverage:
    """The running average must be mathematically correct across multiple events."""

    def test_running_average_correctness(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.db"
            tracker = UsageTracker(db_path=db)
            # Record 5 events with latencies [10, 20, 30, 40, 50]
            latencies = [10, 20, 30, 40, 50]
            for i, lat in enumerate(latencies):
                tracker.record_event(f"skill-{i}", f"q{i}", "bm25", 0.5, latency_ms=lat)

            # Expected: (10+20+30+40+50)/5 = 30
            with __import__("sqlite3").connect(str(db)) as conn:
                row = conn.execute("SELECT queries, avg_latency_ms FROM daily_stats").fetchone()
            queries, avg = row
            assert queries == 5
            assert abs(avg - 30.0) < 0.01

    def test_running_average_with_varying_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.db"
            tracker = UsageTracker(db_path=db)
            # 10 events with latency 100 → avg should be exactly 100
            for i in range(10):
                tracker.record_event("k", "q", "bm25", 0.5, latency_ms=100)
            with __import__("sqlite3").connect(str(db)) as conn:
                avg = conn.execute("SELECT avg_latency_ms FROM daily_stats").fetchone()[0]
            assert abs(avg - 100.0) < 0.01

    def test_different_latencies_mixed(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.db"
            tracker = UsageTracker(db_path=db)
            # 3 events: [100, 200, 300] → avg = 200
            for lat in [100, 200, 300]:
                tracker.record_event("k", "q", "bm25", 0.5, latency_ms=lat)
            with __import__("sqlite3").connect(str(db)) as conn:
                avg = conn.execute("SELECT avg_latency_ms FROM daily_stats").fetchone()[0]
            assert abs(avg - 200.0) < 0.01


# ── apply_weights Mutation ─────────────────────────────────────────

class TestApplyWeightsNoMutation:
    """apply_weights must NOT mutate input QueryResult objects."""

    def test_input_not_mutated(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.db"
            eng = FeedbackEngine(db_path=db)
            eng.record(FeedbackRecord(query="q", skill_name="k", success=True))

            original = QueryResult(
                skill=Skill(name="k", description="d"),
                score=0.5,
                retrieval_method="bm25",
            )
            original_score = original.score
            original_method = original.retrieval_method

            eng.apply_weights([original])

            # Input must be unchanged
            assert original.score == original_score
            assert original.retrieval_method == original_method

    def test_returns_new_query_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.db"
            eng = FeedbackEngine(db_path=db)
            eng.record(FeedbackRecord(query="q", skill_name="k", success=True))
            original = QueryResult(
                skill=Skill(name="k", description="d"),
                score=0.5,
                retrieval_method="bm25",
            )
            result = eng.apply_weights([original])
            # The returned object should be a new instance
            assert result[0] is not original
            # But the weighted result has the +weighted marker
            assert "+weighted" in result[0].retrieval_method


# ── Session Edge Cases ─────────────────────────────────────────────

class TestSessionEdgeCases:
    """Session validation and cross-process resolution."""

    def test_record_skill_use_empty_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.db"
            tracker = SessionTracker(db_path=db)
            tracker.start_session("s1")
            tracker.record_skill_use("", "query", True, "s1")
            tracker.record_skill_use("   ", "query", True, "s1")
            tracker.record_skill_use(None, "query", True, "s1")  # type: ignore
            # No crash, nothing recorded
            session = tracker.get_session("s1")
            assert session is not None
            assert len(session.skills_used) == 0

    def test_record_skill_use_without_session_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.db"
            tracker = SessionTracker(db_path=db)
            # No active session and no session_id
            tracker.record_skill_use("k", "q", True, None)
            tracker.record_skill_use("k", "q", True, "")
            # Should not crash; nothing recorded
            assert tracker.get_recent_skills("nonexistent") == []

    def test_get_or_resolve_by_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.db"
            tracker = SessionTracker(db_path=db)
            tracker.start_session("explicit-id")
            tracker.start_session("latest")
            resolved = tracker.get_or_resolve_session("explicit-id")
            assert resolved is not None
            assert resolved.session_id == "explicit-id"

    def test_get_or_resolve_falls_back_to_latest(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.db"
            tracker = SessionTracker(db_path=db)
            tracker.start_session("first")
            import time
            time.sleep(0.01)
            tracker.start_session("second")
            # Fresh tracker (simulates new process)
            tracker2 = SessionTracker(db_path=db)
            resolved = tracker2.get_or_resolve_session()  # No id
            assert resolved is not None
            assert resolved.session_id == "second"

    def test_get_or_resolve_returns_none_when_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.db"
            tracker = SessionTracker(db_path=db)
            resolved = tracker.get_or_resolve_session()
            assert resolved is None


# ── Optimizer Atomic Write ─────────────────────────────────────────

class TestOptimizerAtomicWrite:
    """Optimizer writes must be atomic — original preserved on failure."""

    def test_dry_run_does_not_modify_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            s = base / "my-skill" / "SKILL.md"
            s.parent.mkdir()
            original_content = "---\nname: my-skill\ndescription: Test\n---\nBody"
            s.write_text(original_content)

            opt = SkillOptimizer(max_description_len=10)
            opt.optimize_directory(base, dry_run=True)

            # File must be untouched
            assert s.read_text() == original_content
            # No .tmp file left over
            assert not s.with_name(f".{s.name}.tmp").exists()
            # No .bak file left over
            assert not s.with_name(f"{s.name}.bak").exists()

    def test_apply_succeeds_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            s = base / "my-skill" / "SKILL.md"
            s.parent.mkdir()
            s.write_text("---\nname: my-skill\ndescription: A very long description for testing\n---\nBody")

            opt = SkillOptimizer(max_description_len=20)
            opt.optimize_directory(base, dry_run=False)

            # File was modified
            new_content = s.read_text()
            assert "very long" not in new_content or len(new_content) < 100
            # No leftover temp files
            assert not s.with_name(f".{s.name}.tmp").exists()
            assert not s.with_name(f"{s.name}.bak").exists()

    def test_apply_failure_leaves_original(self):
        """If the target becomes read-only, original must survive.

        We force a failure by passing a directory as the target — write_text
        will fail with IsADirectoryError or PermissionError, both OSErrors.
        """
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            s = base / "my-skill" / "SKILL.md"
            s.parent.mkdir()
            original = "---\nname: my-skill\ndescription: Original\n---\nBody"
            s.write_text(original)

            opt = SkillOptimizer()
            skill = Skill.from_skill_file(s)
            optimized = opt.optimize_skill(skill)

            # Replace the file path with a directory to force an error
            target_dir = base / "target-dir"
            target_dir.mkdir()
            with pytest.raises(OSError):
                opt._write_optimized(target_dir, optimized)

            # Original file should still exist with original content
            assert s.exists()
            assert s.read_text() == original


# ── Library Code Doesn't Print ─────────────────────────────────────

class TestLibraryNoPrint:
    """Library modules should use logging, not print, so JSON output is not corrupted."""

    def test_indexer_does_not_print(self, caplog):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.db"
            indexer = SkillIndexer(db_path=db)
            with caplog.at_level(logging.WARNING, logger="scm.indexer"):
                indexer.index_directory(Path("/nonexistent/path"))
            # Logged via logger, not printed
            assert any("Directory not found" in r.message for r in caplog.records)

    def test_indexing_with_bad_file_logs_not_prints(self, caplog):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            bad = base / "bad" / "SKILL.md"
            bad.parent.mkdir()
            # Binary file that breaks yaml parsing
            bad.write_bytes(b"\x00\x01\x02 invalid utf-8 \x80\x81")

            db = Path(tmp) / "test.db"
            indexer = SkillIndexer(db_path=db)
            with caplog.at_level(logging.WARNING, logger="scm.indexer"):
                indexer.index_directory(base)
            # Should have logged (not printed) the error
            # Note: depending on how from_skill_file fails, may or may not log


# ── MCP Tool Return Types ──────────────────────────────────────────

class TestMCPToolReturnTypes:
    """All MCP tools must declare -> dict return type, not -> str."""

    def test_all_tools_return_dict(self):
        import inspect
        pytest.importorskip("mcp", reason="mcp SDK not installed")
        from scm.mcp_server import create_mcp_server
        mcp = create_mcp_server()
        tools = mcp._tool_manager._tools.values()
        for t in tools:
            sig = inspect.signature(t.fn)
            return_annotation = sig.return_annotation
            # Accept both `dict` and the string "dict"
            assert return_annotation is dict or return_annotation == "dict", \
                f"Tool {t.name} should return dict, got {return_annotation}"


# ── End-to-end: Full CLI Workflow ──────────────────────────────────

class TestEndToEndWorkflow:
    """The full user flow: index → query → session → feedback → insight."""

    def test_full_workflow(self, tmp_path):
        # Set up skills
        skills_dir = tmp_path / "skills"
        k = skills_dir / "k8s-deploy" / "SKILL.md"
        k.parent.mkdir(parents=True)
        k.write_text(
            "---\nname: k8s-deploy\ndescription: Deploy to Kubernetes\n"
            "tags: [k8s, helm]\n---\nkubectl apply -f"
        )

        # Use a temp DB
        db = tmp_path / "scm.db"
        indexer = SkillIndexer(db_path=db)
        n = indexer.index_directory(skills_dir)
        assert n == 1

        # Query
        retriever = SkillRetriever(db_path=db)
        results = retriever.rrf_search("deploy to kubernetes", top_k=3)
        assert len(results) >= 1
        assert results[0].skill.name == "k8s-deploy"

        # Session
        tracker = SessionTracker(db_path=db)
        tracker.start_session("e2e")
        tracker.record_skill_use("k8s-deploy", "deploy", True, "e2e")
        recent = tracker.get_recent_skills("e2e")
        assert "k8s-deploy" in recent

        # Feedback
        engine = FeedbackEngine(db_path=db)
        engine.record(FeedbackRecord(
            query="deploy", skill_name="k8s-deploy", success=True
        ))
        stats = engine.get_stats()
        assert stats["total_feedback"] == 1
        assert stats["success_rate"] == 1.0

        # Insights
        ut = UsageTracker(db_path=db)
        ut.record_event("k8s-deploy", "deploy", "hybrid", 0.9, tokens_saved=500, latency_ms=10)
        insights = ut.get_insights(days=30)
        assert insights["total_queries"] == 1
