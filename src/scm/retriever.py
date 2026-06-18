"""Skill retrieval engine — BM25 + embedding-based search."""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from pathlib import Path
from typing import Optional

from .db import connect, init_schema
from .models import Skill, QueryResult

logger = logging.getLogger("scm.retriever")


class SkillRetriever:
    """Retrieve relevant skills using BM25, embedding similarity, or hybrid search."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path
        self._embedding_model = None  # Lazy load
        init_schema(db_path)

    def _conn(self):
        return connect(self.db_path)

    # ── BM25 via SQLite FTS5 ──────────────────────────────────────────

    def bm25_search(self, query: str, top_k: int = 20) -> list[QueryResult]:
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
                    rows = conn.execute(f"""
                        SELECT s.*, rank
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
                        results.append(QueryResult(
                            skill=Skill.row_to_skill(r), score=round(score, 4),
                            retrieval_method="bm25",
                        ))
                except sqlite3.OperationalError:
                    pass

            results.sort(key=lambda r: r.score, reverse=True)

            if not results:
                results = self._like_fallback(conn, query, top_k)

            return results[:top_k]

    def _like_fallback(self, conn, query: str, top_k: int) -> list[QueryResult]:
        """LIKE fallback using extracted keywords."""
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
        return [QueryResult(skill=Skill.row_to_skill(r), score=0.5, retrieval_method="like")
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

    def _build_fts_queries(self, query: str) -> list[str]:
        """Build multiple FTS5 query strategies."""
        words = self._extract_keywords(query)
        if not words:
            return [query]
        queries = []
        if len(words) >= 2:
            queries.append(f'"{ " ".join(words[:3]) }"')
        queries.append(" OR ".join(words))
        prefix = " OR ".join(f"{w}*" for w in words)
        if prefix:
            queries.append(prefix)
        return queries

    # ── Embedding-based search ───────────────────────────────────────

    def embedding_search(self, query: str, top_k: int = 20) -> list[QueryResult]:
        """Semantic search using local embeddings. Falls back to BM25 if unavailable."""
        query = (query or "").strip()
        if not query:
            return []
        try:
            return self._embedding_search_inner(query, top_k)
        except ImportError:
            logger.info("sentence-transformers not installed, falling back to BM25")
            return self.bm25_search(query, top_k)
        except Exception as e:
            logger.warning(f"Embedding search error: {e}, falling back to BM25")
            return self.bm25_search(query, top_k)

    def _embedding_search_inner(self, query: str, top_k: int = 20) -> list[QueryResult]:
        """Actual embedding search with memory-efficient loading + caching."""
        from sentence_transformers import SentenceTransformer
        import numpy as np

        if self._embedding_model is None:
            self._embedding_model = SentenceTransformer('all-MiniLM-L6-v2', device='cpu')

        query_emb = self._embedding_model.encode(query, normalize_embeddings=True)

        with self._conn() as conn:
            rows = conn.execute("""
                SELECT name, description, SUBSTR(body, 1, 512) as body_snippet,
                       path, category, tags, token_cost_meta, token_cost_body,
                       use_count, success_rate, last_used, embedding
                FROM skills
            """).fetchall()

        results = []
        skills_to_cache: list[tuple[str, bytes]] = []

        for r in rows:
            skill = Skill.row_to_skill(r)
            emb_bytes = r["embedding"]
            if emb_bytes:
                skill_emb = np.frombuffer(emb_bytes, dtype=np.float32)
            else:
                text = f"{skill.name} {skill.description} {skill.body[:512]}"
                skill_emb = self._embedding_model.encode(text, normalize_embeddings=True)
                skills_to_cache.append((skill.name, skill_emb.tobytes()))

            score = float(np.dot(query_emb, skill_emb))
            results.append(QueryResult(
                skill=skill, score=round(score, 4), retrieval_method="embedding"
            ))

        # Cache newly computed embeddings
        if skills_to_cache:
            try:
                with self._conn() as conn:
                    conn.executemany(
                        "UPDATE skills SET embedding = ? WHERE name = ?",
                        [(emb, name) for name, emb in skills_to_cache]
                    )
                    conn.commit()
            except sqlite3.Error as e:
                logger.warning(f"Failed to cache embeddings: {e}")

        results.sort(key=lambda r: r.score, reverse=True)
        return results[:top_k]

    # ── Hybrid search ────────────────────────────────────────────────

    def hybrid_search(self, query: str, top_k: int = 20,
                      bm25_weight: float = 0.3, embed_weight: float = 0.7) -> list[QueryResult]:
        """Combine BM25 + embedding search with weighted fusion."""
        bm25_results = self.bm25_search(query, top_k=top_k * 2)
        embed_results = self.embedding_search(query, top_k=top_k * 2)

        skill_scores: dict[str, dict] = {}
        for r in bm25_results:
            skill_scores[r.skill.name] = {"result": r, "score_bm25": r.score, "score_embed": 0.0}
        for r in embed_results:
            entry = skill_scores.setdefault(r.skill.name, {"result": r, "score_bm25": 0.0, "score_embed": 0.0})
            entry["score_embed"] = r.score

        fused = []
        for name, data in skill_scores.items():
            hybrid = data["score_bm25"] * bm25_weight + data["score_embed"] * embed_weight
            fused.append(QueryResult(
                skill=data["result"].skill, score=round(hybrid, 4), retrieval_method="hybrid",
            ))
        fused.sort(key=lambda r: r.score, reverse=True)
        return fused[:top_k]

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
