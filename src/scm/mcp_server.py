#!/usr/bin/env python3
"""
SCM MCP Server — Expose Skill Context Manager as MCP tools.

Usage:
    python3 -m scm.mcp_server          # stdio transport (for Hermes/OpenCode)
    python3 -m scm.mcp_server --http   # HTTP transport (for remote access)

Tools exposed:
    skill_query            - Find relevant skills for a task
    skill_index            - Index skills from a directory
    skill_session_start    - Start a tracking session
    skill_session_use      - Record skill usage
    skill_session_context  - Get current session context
    skill_stats            - Get indexing statistics
    skill_feedback         - Record usage feedback
"""

from __future__ import annotations

import json
import sqlite3
import sys
import time
from functools import wraps
from pathlib import Path
from typing import Optional

# Local imports
from scm.models import FeedbackRecord
from scm.indexer import SkillIndexer
from scm.retriever import SkillRetriever
from scm.reranker import SkillReranker
from scm.session import SessionTracker
from scm.optimizer import SkillOptimizer
from scm.feedback import FeedbackEngine
from scm.tracker import UsageTracker


# ── Safe call decorator ───────────────────────────────────────────

def safe_call(fn):
    """Catch exceptions, return clean JSON error (native dict, not pre-serialized)."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            return {
                "error": str(e),
                "type": type(e).__name__,
            }
    return wrapper


# ── MCP Server ────────────────────────────────────────────────────

def create_mcp_server() -> "FastMCP":  # noqa: F821 — lazy import inside fn body
    """Create and configure the SCM MCP server."""
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("scm-mcp")

    # ── Layer 1: Skill Query ───────────────────────────────────────

    @mcp.tool()
    @safe_call
    def skill_query(
        query: str,
        top_k: int = 5,
        method: str = "hybrid",
        session_id: str = "",
        use_reranker: bool = True,
    ) -> dict:
        """Find the most relevant skills for a task.

        Args:
            query: Task description (e.g. "deploy app to kubernetes")
            top_k: Number of results (default: 5)
            method: Search method — "bm25", "embedding", "hybrid", or "rrf" (default)
            session_id: Optional session ID for session-aware boosting
            use_reranker: Whether to use cross-encoder reranking (default: true)
        """
        retriever = SkillRetriever()
        reranker = SkillReranker()

        start = time.time()

        # Stage 1: Retrieve
        if method == "bm25":
            results = retriever.bm25_search(query, top_k=top_k * 4)
        elif method == "embedding":
            results = retriever.embedding_search(query, top_k=top_k * 4)
        elif method == "rrf":
            results = retriever.rrf_search(query, top_k=top_k * 4)
        else:
            results = retriever.hybrid_search(query, top_k=top_k * 4)

        # Session boost
        if session_id:
            tracker = SessionTracker()
            recent = tracker.get_recent_skills(session_id)
            if recent:
                results = retriever.apply_session_boost(results, recent, boost=0.5)

        # Feedback weights
        feedback = FeedbackEngine()
        results = feedback.apply_weights(results)

        # Stage 2: Rerank
        if use_reranker and len(results) > 1:
            results = reranker.rerank(query, results, top_k=top_k)

        elapsed_ms = (time.time() - start) * 1000

        # Record usage
        try:
            ut = UsageTracker()
            for r in results[:top_k]:
                tokens_saved = r.skill.token_cost_body
                ut.record_event(r.skill.name, query, r.retrieval_method,
                                r.score, tokens_saved, int(elapsed_ms))
        except sqlite3.Error:
            pass

        return {
            "query": query,
            "latency_ms": round(elapsed_ms, 1),
            "candidates_scanned": len(results),
            "results": [
                {
                    "name": r.skill.name,
                    "description": r.skill.description,
                    "score": round(r.score, 4),
                    "method": r.retrieval_method,
                    "category": r.skill.category,
                    "tags": r.skill.tags,
                    "token_cost_metadata": r.skill.token_cost_metadata,
                    "token_cost_body": r.skill.token_cost_body,
                }
                for r in results[:top_k]
            ],
        }

    # ── Layer 2: Index Management ──────────────────────────────────

    @mcp.tool()
    @safe_call
    def skill_index(directory: str, recursive: bool = True) -> dict:
        """Index skills from a directory into the search database.

        Args:
            directory: Path to skills directory (e.g. "/home/user/.hermes/skills/")
            recursive: Scan subdirectories recursively (default: true)
        """
        dir_path = Path(directory).expanduser().resolve()
        if not dir_path.exists():
            return {"error": f"Directory not found: {directory}"}

        indexer = SkillIndexer()
        count = indexer.index_directory(dir_path, recursive=recursive)
        stats = indexer.stats()

        return {
            "indexed": count,
            "directory": str(dir_path),
            "total_skills": stats["total_skills"],
            "total_tokens_metadata": stats["total_tokens_metadata"],
            "total_tokens_body": stats["total_tokens_body"],
            "categories": list(stats.get("categories", {}).keys()) if stats.get("categories") else [],
        }

    @mcp.tool()
    @safe_call
    def skill_stats() -> dict:
        """Get statistics about the indexed skill database."""
        indexer = SkillIndexer()
        stats = indexer.stats()

        feedback = FeedbackEngine()
        fb_stats = feedback.get_stats()

        return {
            **stats,
            "feedback_records": fb_stats.get("total_feedback", 0),
            "feedback_success_rate": fb_stats.get("success_rate", 0),
        }

    # ── Layer 3: Session Tracking ──────────────────────────────────

    @mcp.tool()
    @safe_call
    def skill_session_start(session_id: str, metadata: str = "") -> dict:
        """Start a new skill usage tracking session.

        Args:
            session_id: Unique session identifier (e.g. "chat-abc-123")
            metadata: Optional JSON metadata string
        """
        meta = {}
        if metadata:
            try:
                meta = json.loads(metadata)
            except json.JSONDecodeError:
                meta = {"raw": metadata}

        tracker = SessionTracker()
        session = tracker.start_session(session_id, meta)
        return {
            "session_id": session.session_id,
            "started_at": session.started_at,
            "status": "started",
        }

    @mcp.tool()
    @safe_call
    def skill_session_use(
        session_id: str,
        skill_name: str,
        query: str = "",
        success: Optional[bool] = None,
    ) -> dict:
        """Record that a skill was used in a session.

        Args:
            session_id: Session identifier
            skill_name: Name of the skill used
            query: The task/query that triggered this skill
            success: Whether the skill was effective
        """
        tracker = SessionTracker()
        tracker.record_skill_use(skill_name, query, success, session_id)

        # Also record feedback if success is specified
        if success is not None:
            feedback = FeedbackEngine()
            feedback.record(FeedbackRecord(
                query=query or "", skill_name=skill_name, success=success,
            ))

        # Get recent skills for context
        recent = tracker.get_recent_skills(session_id, n=5)
        return {
            "recorded": True,
            "skill": skill_name,
            "session_id": session_id,
            "recent_skills": recent,
        }

    @mcp.tool()
    @safe_call
    def skill_session_context(session_id: str, query: str = "") -> dict:
        """Get token-optimized context block for session-aware prompting.

        Args:
            session_id: Session identifier
            query: Optional current task to find related skills
        """
        tracker = SessionTracker()
        context = tracker.optimize_skill_context(session_id, query)

        # Find related skills for current query
        if query:
            retriever = SkillRetriever()
            results = retriever.hybrid_search(query, top_k=3)
            context["related_skills"] = [
                {"name": r.skill.name, "description": r.skill.description}
                for r in results
            ]

        return context

    @mcp.tool()
    @safe_call
    def skill_session_end(session_id: str) -> dict:
        """End a tracking session.

        Args:
            session_id: Session identifier
        """
        tracker = SessionTracker()
        recent = tracker.get_recent_skills(session_id)
        tracker.end_session(session_id)
        return {
            "session_id": session_id,
            "ended": True,
            "skills_used": recent,
        }

    # ── Layer 4: Optimize ──────────────────────────────────────────

    @mcp.tool()
    @safe_call
    def skill_optimize(directory: str, dry_run: bool = True) -> dict:
        """Analyze and optimize skill metadata for token efficiency.

        Args:
            directory: Path to skills directory
            dry_run: Preview changes without applying (default: true)
        """
        dir_path = Path(directory).expanduser().resolve()
        if not dir_path.exists():
            return {"error": f"Directory not found: {directory}"}

        optimizer = SkillOptimizer()
        results = optimizer.optimize_directory(dir_path, dry_run=dry_run)

        changed = [r for r in results if r.get("changed")]
        errors = [r for r in results if "error" in r]

        before_tokens = sum(r.get("before_tokens", 0) for r in changed)
        after_tokens = sum(r.get("after_tokens", 0) for r in changed)

        return {
            "total_skills": len(results),
            "changed": len(changed),
            "errors": len(errors),
            "dry_run": dry_run,
            "tokens_before": before_tokens,
            "tokens_after": after_tokens,
            "tokens_saved": before_tokens - after_tokens,
            "details": changed[:10] if changed else [],
        }

    # ── Layer 5: Feedback ──────────────────────────────────────────

    @mcp.tool()
    @safe_call
    def skill_feedback(
        query: str,
        skill_name: str,
        success: bool = True,
        rating: Optional[int] = None,
    ) -> dict:
        """Record feedback about a skill usage.

        Args:
            query: The task/query
            skill_name: Name of the skill used
            success: Whether it was effective (default: true)
            rating: Optional user rating 1-5
        """
        engine = FeedbackEngine()
        engine.record(FeedbackRecord(
            query=query, skill_name=skill_name,
            success=success, user_rating=rating,
        ))
        return {"recorded": True, "skill": skill_name, "success": success}

    @mcp.tool()
    @safe_call
    def skill_feedback_stats() -> dict:
        """Get feedback and learning statistics."""
        engine = FeedbackEngine()
        return engine.get_stats()

    # ── Layer 6: Insights ──────────────────────────────────────────

    @mcp.tool()
    @safe_call
    def skill_insights(days: int = 30) -> dict:
        """Get usage insights for the last N days.

        Args:
            days: Number of days to analyze (default: 30)
        """
        tracker = UsageTracker()
        return tracker.get_insights(days=days)

    # ── Prompts ────────────────────────────────────────────────────

    @mcp.prompt()
    def skill_selection_guide() -> str:
        """Guide: How to use SCM tools for effective skill selection."""
        return """\
# Skill Context Manager — Usage Guide

When you need to help an agent select the right skill:

1. **Query first**: Call `skill_query` with the user's task to find relevant skills.
2. **Track session**: Use `skill_session_start` at conversation start.
3. **Record usage**: Use `skill_session_use` when a skill is chosen.
4. **Get context**: Use `skill_session_context` to generate a token-minimal prompt block.
5. **Give feedback**: Use `skill_feedback` to improve future selections.

Example flow:
```
query = "deploy application to kubernetes"
results = skill_query(query, top_k=3)
# → Returns ranked skills with scores and token costs
```

The key insight: instead of loading ALL skill definitions into context,
load ONLY the top 2-3 matching skills. This saves 85-98% on skill context tokens.
"""

    return mcp


# ── Entry points ──────────────────────────────────────────────────

def run_stdio():
    """Run MCP server in stdio mode (for Hermes Agent, OpenCode, etc.)."""
    mcp = create_mcp_server()
    mcp.run(transport="stdio")


def run_http(port: int = 8321):
    """Run MCP server in HTTP/SSE mode using uvicorn."""
    mcp = create_mcp_server()
    import uvicorn
    app = mcp.sse_app()
    print(f"🔌 SCM MCP Server listening on http://0.0.0.0:{port}", file=sys.stderr)
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    if "--http" in sys.argv or "--sse" in sys.argv:
        port = 8321
        for i, arg in enumerate(sys.argv):
            if arg in ("--port", "-p") and i + 1 < len(sys.argv):
                port = int(sys.argv[i + 1])
        run_http(port)
    else:
        run_stdio()
