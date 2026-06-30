"""Skill retrieval engine — BM25 + graph-boosted search (zero local models)."""

from __future__ import annotations

import logging
import re
import sqlite3
from pathlib import Path
from typing import Optional

from .db import connect, init_schema
from .models import Skill, QueryResult

logger = logging.getLogger("scm.retriever")


class SkillRetriever:
    """Retrieve relevant skills using FTS5 BM25, with session + graph boosting."""

    # ponytail: shared model/embeddings across ALL instances (class-level cache)
    _shared_model = None
    _shared_embeddings = None
    _shared_emb_names = None

    # ponytail: terms too generic for name-boost.
    # Skills whose ONLY name match is a generic term (e.g. "pipeline") should
    # not get ×5 BM25 boost — the term carries no domain specificity.
    GENERIC_TERMS = frozenset({"pipeline", "dashboard"})

    # ponytail: synonym expansion for generic technical terms.
    # Expands a broad/generic query term into specific sub-terms so BM25
    # matches more relevant skills. One direction mapping is sufficient
    # (synonyms are added as extra search terms, never removed).
    _EXPANSIONS = {
        "pipeline": ["ci", "cd", "build", "release", "deploy"],
    }

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path
        init_schema(db_path)

    def _conn(self):
        return connect(self.db_path)

    def warmup(self):
        """Preload embedding model (cold start ~11s). No-op if loaded or unavailable."""
        try:
            model = self._embedding_model()
            if model is not None:
                self._skill_embeddings()
        except Exception:
            pass

    # ── BM25 via SQLite FTS5 ──────────────────────────────────────────

    def bm25_search(self, query: str, top_k: int = 20,
                    threshold: float = 0.0) -> list[QueryResult]:
        """BM25 search using SQLite FTS5 with LIKE fallback."""
        query = (query or "").strip()
        if not query:
            return []

        fts_queries = self._build_fts_queries(query)
        with self._conn() as conn:
            results = []
            seen_names: set[str] = set()

            for fts_query in fts_queries:
                try:
                    rows = conn.execute("""\
                        SELECT s.*, bm25(skills_fts, 5.0, 3.0, 1.0, 1.0) as rank
                        FROM skills_fts f
                        JOIN skills s ON s.rowid = f.rowid
                        WHERE skills_fts MATCH ?
                        ORDER BY rank
                        LIMIT ?
                    """, (fts_query, top_k)).fetchall()
                    for r in rows:
                        if r["name"] in seen_names:
                            continue
                        seen_names.add(r["name"])
                        score = max(0, 1.0 - float(r["rank"]) / 100.0)
                        # ponytail: cap FTS5 score to 0.85 when no query term
                        # appears in name (exact match *or* 6-char prefix) —
                        # body-text-noise results should not outrank LIKE
                        # name/description matches (score 0.9).
                        q_lower = query.lower()
                        name_lower = r["name"].lower()
                        _has_hint = any(
                            term in name_lower or term[:6] in name_lower
                            for term in q_lower.split()
                            if term not in self.GENERIC_TERMS
                        )
                        if not _has_hint:
                            score = min(score, 0.85)
                        results.append(QueryResult(
                            skill=Skill.row_to_skill(r), score=round(score, 4),
                            retrieval_method="bm25",
                        ))
                except sqlite3.OperationalError:
                    pass

            results.sort(key=lambda r: r.score, reverse=True)

            # ponytail: always supplement FTS5 with LIKE on name+description.
            # Pure FTS5 returns body-text noise for short queries; LIKE on
            # name+description catches exact matches FTS5's broad prefix matching
            # can bury (e.g. "github" matching "github-workflows"). Merge and
            # deduplicate with FTS5 results.
            like_results = self._like_fallback(conn, query, top_k)
            like_seen = set(r.skill.name for r in results)
            for lr in like_results:
                if lr.skill.name not in like_seen:
                    like_seen.add(lr.skill.name)
                    results.append(lr)

            results.sort(key=lambda r: r.score, reverse=True)

            # ponytail: threshold filter at end of BM25
            if threshold > 0:
                results = [r for r in results if r.score >= threshold]
            return results[:top_k]

    def _like_fallback(self, conn, query: str, top_k: int) -> list[QueryResult]:
        """LIKE fallback using extracted keywords, scored competitively with FTS5."""
        words = self._extract_keywords(query)
        if words:
            patterns = ["%" + w + "%" for w in words[:3]]
            conditions = " OR ".join(["name LIKE ?"] * len(patterns) + ["description LIKE ?"] * len(patterns))
            params = patterns + patterns
            rows = conn.execute(
                f"SELECT *, 0 as rank FROM skills WHERE {conditions} LIMIT ?",
                (*params, top_k)
            ).fetchall()
        else:
            like = f"%{query}%"
            rows = conn.execute(
                "SELECT *, 0 as rank FROM skills WHERE name LIKE ? OR description LIKE ? LIMIT ?",
                (like, like, top_k)
            ).fetchall()
        # ponytail: LIKE matches on name/description are high-precision, score 0.9
        # is just below a strong BM25 match (1.0) so they interleave with FTS5
        # results rather than being buried.
        return [QueryResult(skill=Skill.row_to_skill(r), score=0.9, retrieval_method="like")
                for r in rows]

    def _extract_keywords(self, query: str) -> list[str]:
        """Extract meaningful keywords from a query."""
        stopwords = {'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been',
                     'being', 'have', 'has', 'had', 'do', 'does', 'did', 'will',
                     'would', 'could', 'should', 'may', 'might', 'shall', 'can',
                     'to', 'of', 'in', 'for', 'on', 'with', 'at', 'by', 'from',
                     'as', 'into', 'through', 'during', 'before', 'after',
                     'above', 'below', 'between', 'out', 'off', 'over', 'under',
                     'again', 'further', 'then', 'once', 'here', 'there',
                     'when', 'where', 'why', 'how', 'all', 'each', 'every',
                     'both', 'few', 'more', 'most', 'other', 'some', 'such',
                     'no', 'nor', 'not', 'only', 'own', 'same', 'so', 'than',
                     'too', 'very', 'just', 'because', 'but', 'and', 'or', 'if',
                     'while', 'although', 'this', 'that', 'these', 'those',
                     'i', 'me', 'my', 'myself', 'we', 'our', 'ours', 'you',
                     'your', 'yours', 'he', 'him', 'his', 'she', 'her', 'hers',
                     'it', 'its', 'they', 'them', 'their', 'theirs',
                     'please', 'help', 'need', 'want', 'like', 'get', 'make',
                     'do', 'does', 'did', 'done', 'doing', 'go', 'went', 'gone',
                     'come', 'came', 'take', 'took', 'taken', 'give', 'gave',
                     'given', 'find', 'found', 'use', 'used', 'using'}
        return [w for w in re.sub(r'[^\w\s-]', ' ', query).lower().split()
                if w not in stopwords and len(w) > 2]

    def _expand_keywords(self, words: list[str]) -> list[str]:
        """Expand keywords with synonym terms for broader recall."""
        expanded = list(words)
        for w in words:
            ex = self._EXPANSIONS.get(w)
            if ex:
                for e in ex:
                    if e not in expanded:
                        expanded.append(e)
        return expanded

    def _build_fts_queries(self, query: str) -> list[str]:
        """Build multiple FTS5 query strategies.

        Sanitizes user input to prevent FTS5 syntax injection (quotes, parens,
        operators like NEAR/AND/OR/NOT) and to handle Unicode/special chars safely.
        """
        words = self._expand_keywords(self._extract_keywords(query))
        if not words:
            return []
        # Quote each term — FTS5 treats "word" as literal substring
        safe = [f'"{w}"' for w in words]
        queries = []
        if len(words) >= 2:
            queries.append(" ".join(safe[:3]))
        queries.append(" OR ".join(safe))
        # ponytail: try first 6 chars of long words as a broader prefix.
        # Catches spelling variants: "postgresql" → "postgre"* matches "postgres".
        trunc_words = [f'"{w[:6]}"*' for w in words if len(w) > 6]
        if trunc_words:
            queries.append(" OR ".join(trunc_words[:5]))
        # ponytail: skip prefix match for short terms (≤2 chars) —
        # they match too broadly in body text and drown real results.
        long_words = [w for w in words if len(w) > 2]
        if long_words:
            prefix = " OR ".join(f'"{w}"*' for w in long_words[:5])
            if prefix:
                queries.append(prefix)
        # Short terms get OR matching only (no prefix) — reduces noise
        if len(words) != len(long_words):
            short = [f'"{w}"' for w in words if len(w) <= 2]
            if short and long_words:
                # Mix short (exact) + long (prefix) in one OR query
                queries.append(" OR ".join(short + [f'"{w}"*' for w in long_words[:3]]))
        return queries

    # ── Fused search (BM25 + embedding RRF + graph boost + feedback) ──

    def _embedding_model(self):
        """Lazy-load all-MiniLM-L6-v2 (sentence-transformers already installed)."""
        if SkillRetriever._shared_model is not None:
            return SkillRetriever._shared_model
        # ponytail: numpy >=2 breaks sentence-transformers model loading
        import numpy as np
        if np.__version__.startswith("2."):
            return None
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            return None
        SkillRetriever._shared_model = SentenceTransformer("all-MiniLM-L6-v2")
        return SkillRetriever._shared_model

    def _skill_embeddings(self):
        """Compute + cache embeddings for all skills (name + description)."""
        if SkillRetriever._shared_embeddings is not None:
            return SkillRetriever._shared_emb_names, SkillRetriever._shared_embeddings
        model = self._embedding_model()
        with self._conn() as conn:
            rows = conn.execute("SELECT name, description FROM skills").fetchall()
        texts = [f"{r['name']}: {r['description']}" for r in rows]
        SkillRetriever._shared_emb_names = [r["name"] for r in rows]
        # ponytail: encode all at once — ~300ms for 124 skills on all-MiniLM
        SkillRetriever._shared_embeddings = model.encode(texts, show_progress_bar=False)
        return SkillRetriever._shared_emb_names, SkillRetriever._shared_embeddings

    def embedding_search(self, query: str, top_k: int = 20) -> list[QueryResult]:
        """Semantic search via all-MiniLM-L6-v2 cosine similarity."""
        try:
            model = self._embedding_model()
            if model is None:
                # ponytail: numpy >=2 breaks st → fall back to BM25
                return self.bm25_search(query, top_k=top_k)
            names, embs = self._skill_embeddings()
            q_emb = model.encode([query], show_progress_bar=False)[0]
            scores = (embs @ q_emb) / (  # cosine = dot for unit vectors
                (embs ** 2).sum(1) ** 0.5 * (q_emb ** 2).sum() ** 0.5 + 1e-10
            )
            top = scores.argsort()[-top_k:][::-1]
            results = []
            for i in top:
                with self._conn() as conn:
                    row = conn.execute(
                        "SELECT * FROM skills WHERE name = ?", (names[i],)
                    ).fetchone()
                if row:
                    results.append(QueryResult(
                        skill=Skill.row_to_skill(row),
                        score=round(float(scores[i]), 4),
                        retrieval_method="embedding",
                    ))
            return results
        except Exception:
            logger.info("Embedding search unavailable, falling back to BM25")
            return self.bm25_search(query, top_k=top_k)

    def rrf_search(self, query: str, top_k: int = 20,
                   session_skills: Optional[list[str]] = None,
                   threshold: float = 0.0) -> list[QueryResult]:
        """RRF (Reciprocal Rank Fusion) over BM25 + embedding, then graph + feedback.

        Uses BM25 for initial retrieval, then applies:
        1. Knowledge graph PPR boost (if session_skills provided)
        2. Feedback-based weight adjustment

        Args:
            query: Task description.
            top_k: Number of results.
            session_skills: Skills used in current session (for graph PPR boost).

        Returns:
            Ranked list of QueryResult.
        """
        query = (query or "").strip()
        if not query:
            return []

        # ponytail: RRF over BM25 + embedding — k=60 per SIGIR recommendation
        bm25 = self.bm25_search(query, top_k=top_k * 3)
        embed = self.embedding_search(query, top_k=top_k * 3)
        k = 60
        rrf_scores: dict[str, float] = {}
        bm25_names = {r.skill.name for r in bm25}
        embed_names = {r.skill.name for r in embed}

        for rank, r in enumerate(bm25):
            rrf_scores[r.skill.name] = rrf_scores.get(r.skill.name, 0.0) + 1.0 / (k + rank + 1)
        for rank, r in enumerate(embed):
            rrf_scores[r.skill.name] = rrf_scores.get(r.skill.name, 0.0) + 1.0 / (k + rank + 1)

        # ponytail: body-text noise penalty at RRF level.
        # BM25's _has_hint cap (score ≤0.85) and LIKE fallback (score 0.9)
        # prevent body-only matches from outranking name/desc matches in
        # pure BM25 mode, but RRF merges rank positions, not scores — the
        # cap is bypassed. Penalize skills whose name AND description both
        # lack any query term (consistent with LIKE fallback logic).
        q_terms = {w for w in query.lower().split() if len(w) > 2}
        if q_terms:
            desc_lookup = {}
            for r in bm25 + embed:
                if r.skill.name not in desc_lookup:
                    desc_lookup[r.skill.name] = r.skill.description or ""
            for name in list(rrf_scores):
                name_lower = name.lower()
                desc_lower = desc_lookup.get(name, "").lower()
                _has_hint = any(
                    term in name_lower or term[:6] in name_lower
                    or term in desc_lower
                    for term in q_terms
                    if term not in self.GENERIC_TERMS
                )
                if not _has_hint:
                    rrf_scores[name] *= 0.7

        # Build merged results sorted by RRF score
        merged_names = sorted(rrf_scores, key=lambda n: -rrf_scores[n])
        seen = set()
        results = []
        for name in merged_names:
            if name in seen:
                continue
            seen.add(name)
            method = []
            if name in bm25_names:
                method.append("bm25")
            if name in embed_names:
                method.append("embed")
            for r in bm25 + embed:
                if r.skill.name == name:
                    results.append(QueryResult(
                        skill=r.skill,
                        score=round(float(rrf_scores[name]), 4),
                        retrieval_method="+".join(method) if method else "rrf",
                    ))
                    break

        # Graph-aware boost
        if session_skills:
            results = self.apply_graph_boost(results, session_skills)

        # ponytail: threshold filter at end of RRF
        if threshold > 0:
            results = [r for r in results if r.score >= threshold]
        return results[:top_k]

    # ── Budget-aware loading ──────────────────────────────────────

    def load_budgeted(self, query: str, budget_tokens: int = 500,
                      threshold: float = 0.0,
                      method: str = "rrf") -> list[dict]:
        """Load skills up to a token budget (greedy by relevance)."""
        if method == "bm25":
            results = self.bm25_search(query, top_k=30, threshold=threshold)
        else:
            results = self.rrf_search(query, top_k=30, threshold=threshold)

        loaded: list[dict] = []
        total = 0
        for r in results:
            tokens = r.skill.token_cost_body or len(r.skill.body or "") // 4
            # ponytail: skip outliers (> 2×budget) so smaller skills can fit
            if tokens > budget_tokens * 2:
                continue
            # ponytail: always load the first result even if over budget
            if total + tokens > budget_tokens and loaded:
                break
            total += tokens
            loaded.append({
                "name": r.skill.name,
                "description": r.skill.description,
                "category": r.skill.category,
                "tags": r.skill.tags,
                "body": r.skill.body,
                "tokens": tokens,
                "score": r.score,
            })
        # ponytail: if all skills were outliers, load the best anyway
        if not loaded and results:
            r = results[0]
            loaded.append({
                "name": r.skill.name,
                "description": r.skill.description,
                "category": r.skill.category,
                "tags": r.skill.tags,
                "body": r.skill.body,
                "tokens": r.skill.token_cost_body or len(r.skill.body or "") // 4,
                "score": r.score,
            })
        return loaded

    # ── Session-aware boost ──────────────────────────────────────────

    def apply_session_boost(self, results: list[QueryResult],
                            recent_skills: list[str], boost: float = 0.5) -> list[QueryResult]:
        """Boost scores for skills used recently in the session (returns NEW list)."""
        boosted = []
        for r in results:
            if r.skill.name in recent_skills:
                boosted.append(QueryResult(
                    skill=r.skill,
                    score=round(r.score + boost, 4),
                    retrieval_method=r.retrieval_method + "+session",
                ))
            else:
                boosted.append(r)
        boosted.sort(key=lambda r: r.score, reverse=True)
        return boosted

    # ── Graph-aware boost (knowledge graph) ───────────────────────────

    def apply_graph_boost(self, results: list[QueryResult],
                          session_skills: list[str],
                          boost_weight: float = 0.3) -> list[QueryResult]:
        """Boost results based on graph proximity to session skills.

        Uses Personalized PageRank from session skills as seed set.
        Skills close to session context in the knowledge graph get a boost.

        Args:
            results: Ranked list of QueryResult to boost.
            session_skills: Skills used in the current session (PPR seeds).
            boost_weight: How much to weight PPR score vs original score (0-1).

        Returns:
            New list with graph-boosted scores.
        """
        if not session_skills or not results:
            return results

        try:
            from .graph import SkillGraph

            graph = SkillGraph(db_path=self.db_path)
            ppr_scores = graph.ppr(session_skills)

            if not ppr_scores:
                return results

            boosted = []
            for r in results:
                ppr = ppr_scores.get(r.skill.name, 0.0)
                if ppr > 0:
                    new_score = round(
                        r.score * (1.0 - boost_weight) + ppr * boost_weight, 4
                    )
                    boosted.append(QueryResult(
                        skill=r.skill, score=new_score,
                        retrieval_method=r.retrieval_method + "+graph",
                    ))
                else:
                    boosted.append(r)

            boosted.sort(key=lambda r: r.score, reverse=True)
            return boosted
        except Exception as e:
            logger.warning("Graph boost failed: %s", e)
            return results
