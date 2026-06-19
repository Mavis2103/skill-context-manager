"""Skill retrieval engine — BM25 + embedding-based search."""

from __future__ import annotations

import logging
import os
import re
import sqlite3
from pathlib import Path
from typing import Optional

from .db import connect, init_schema
from .models import Skill, QueryResult

logger = logging.getLogger("scm.retriever")


class SkillRetriever:
    """Retrieve relevant skills using BM25, embedding similarity, or hybrid search."""

    # Default model — override via env or config
    DEFAULT_EMBEDDING_MODEL = "all-MiniLM-L6-v2"
    # INSTRUCTIONS: To switch back to BGE-base, change the line above to
    # "BAAI/bge-base-en-v1.5" and clear cached embeddings from DB.

    def __init__(self, db_path: Optional[Path] = None,
                 embedding_model: Optional[str] = None,
                 use_onnx: Optional[bool] = None):
        self.db_path = db_path
        self._embedding_model = None  # Lazy load
        self._emb_tokenizer = None
        self._emb_mode: Optional[str] = None  # "onnx", "sentence_tr", None
        self._model_name = embedding_model or self.DEFAULT_EMBEDDING_MODEL
        # Auto-detect ONNX: prefer PyTorch by default (ONNX int8 quantization
        # can degrade BERT embeddings to near-identical vectors on some models).
        # Set SCM_USE_ONNX=1 to force ONNX despite quality risk.
        if use_onnx is None:
            self._use_onnx = os.environ.get("SCM_USE_ONNX") == "1"
        else:
            self._use_onnx = use_onnx
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
                    rows = conn.execute("""
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
        """Build multiple FTS5 query strategies.

        Sanitizes user input to prevent FTS5 syntax injection (quotes, parens,
        operators like NEAR/AND/OR/NOT) and to handle Unicode/special chars safely.
        """
        words = self._extract_keywords(query)
        if not words:
            return []
        # Quote each term — FTS5 treats "word" as literal substring
        safe = [f'"{w}"' for w in words]
        queries = []
        if len(words) >= 2:
            queries.append(" ".join(safe[:3]))
        queries.append(" OR ".join(safe))
        # Prefix match on first char of each word
        prefix = " OR ".join(f'"{w}"*' for w in words)
        if prefix:
            queries.append(prefix)
        return queries

    # ── Embedding-based search ───────────────────────────────────────

    def _load_embedding_model(self):
        """Lazy-load embedding model: ONNX → sentence-transformers → None.
        
        Graceful degradation chain:
        1. ONNX int8 (fastest, if model files exist)
        2. sentence-transformers (slower, works everywhere)
        3. None (BM25 fallback)
        """
        if self._embedding_model is not None:
            return

        # Try ONNX first
        if self._use_onnx:
            try:
                from optimum.onnxruntime import ORTModelForFeatureExtraction
                from transformers import AutoTokenizer
                import numpy as np

                onnx_path = Path.home() / ".scm" / "models" / "bge-base-int8-onnx"
                if onnx_path.exists():
                    self._embedding_model = ORTModelForFeatureExtraction.from_pretrained(
                        str(onnx_path), provider="CPUExecutionProvider",
                        file_name="model_quantized.onnx",
                    )
                    self._emb_tokenizer = AutoTokenizer.from_pretrained(self._model_name)
                    self._emb_mode = "onnx"
                    logger.info("Loaded ONNX int8 embedding model")
                    return
            except ImportError:
                logger.debug("optimum/transformers not available for ONNX")
            except Exception as e:
                logger.warning("ONNX model load failed: %s", e)

        # Fallback: sentence-transformers
        try:
            from sentence_transformers import SentenceTransformer

            # Try local cache first
            local_model_path = Path.home() / ".scm" / "models" / self._model_name.split("/")[-1]
            model_source = (
                str(local_model_path) if local_model_path.exists()
                else self._model_name
            )
            self._embedding_model = SentenceTransformer(
                model_source, device='cpu',
                cache_folder=str(Path.home() / ".scm" / "models"),
            )
            self._emb_mode = "sentence_tr"
            logger.info("Loaded sentence-transformers model: %s", self._model_name)
        except ImportError:
            logger.info("sentence-transformers not installed, embedding disabled")
            self._embedding_model = None
        except Exception as e:
            logger.warning("Embedding model load failed: %s", e)
            self._embedding_model = None

    def _encode_query(self, query: str):
        """Encode query using loaded model. Returns normalized embedding."""
        import numpy as np

        if self._emb_mode == "onnx":
            tokens = self._emb_tokenizer(
                query, padding=True, truncation=True, max_length=512,
                return_tensors="pt",
            )
            import torch
            outputs = self._embedding_model(**tokens)
            # Mean pooling
            attention_mask = tokens["attention_mask"]
            token_embeddings = outputs.last_hidden_state
            input_mask_expanded = attention_mask.unsqueeze(-1).expand(token_embeddings.size()).float()
            emb = (token_embeddings * input_mask_expanded).sum(1) / input_mask_expanded.sum(1)
            # Normalize
            emb = emb / np.linalg.norm(emb)
            return emb.detach().numpy().flatten()
        else:
            # sentence-transformers
            return self._embedding_model.encode(query, normalize_embeddings=True)

    def _encode_skill_text(self, text: str) -> "np.ndarray":
        """Encode skill text using loaded model."""
        import numpy as np

        if self._emb_mode == "onnx":
            tokens = self._emb_tokenizer(
                text, padding=True, truncation=True, max_length=512,
                return_tensors="pt",
            )
            import torch
            outputs = self._embedding_model(**tokens)
            attention_mask = tokens["attention_mask"]
            token_embeddings = outputs.last_hidden_state
            input_mask_expanded = attention_mask.unsqueeze(-1).expand(token_embeddings.size()).float()
            emb = (token_embeddings * input_mask_expanded).sum(1) / input_mask_expanded.sum(1)
            emb = emb / np.linalg.norm(emb)
            return emb.detach().numpy().flatten()
        else:
            return self._embedding_model.encode(text, normalize_embeddings=True)

    def embedding_search(self, query: str, top_k: int = 20) -> list[QueryResult]:
        """Semantic search using local embeddings. Falls back to BM25 if unavailable."""
        query = (query or "").strip()
        if not query:
            return []
        try:
            self._load_embedding_model()
            logger.debug("Embedding model loaded, mode=%s", self._emb_mode)
            if self._embedding_model is None:
                logger.info("No embedding model available, falling back to BM25")
                return self.bm25_search(query, top_k)
            return self._embedding_search_inner(query, top_k)
        except ImportError:
            logger.info("Embedding dependencies not installed, falling back to BM25")
            return self.bm25_search(query, top_k)
        except Exception as e:
            logger.warning(f"Embedding search error: {e}, falling back to BM25")
            return self.bm25_search(query, top_k)

    def _embedding_search_inner(self, query: str, top_k: int = 20) -> list[QueryResult]:
        """Actual embedding search with memory-efficient loading + caching."""
        import numpy as np

        logger.debug("Encoding query...")
        query_emb = self._encode_query(query)
        logger.debug("Query encoded, shape=%s", query_emb.shape)

        with self._conn() as conn:
            logger.debug("Fetching skill rows...")
            rows = conn.execute("""
                SELECT name, description, body,
                       SUBSTR(body, 1, 512) as body_snippet,
                       path, category, tags, token_cost_meta, token_cost_body,
                       use_count, success_rate, last_used, embedding
                FROM skills
            """).fetchall()
            logger.debug("Fetched %d skill rows", len(rows))

        results = []
        skills_to_cache: list[tuple[str, bytes]] = []

        for r in rows:
            skill = Skill.row_to_skill(r)
            emb_bytes = r["embedding"]
            if emb_bytes:
                skill_emb = np.frombuffer(emb_bytes, dtype=np.float32)
            else:
                text = f"{skill.name} {skill.description} {skill.body[:512]}"
                skill_emb = self._encode_skill_text(text)
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

    # ── RRF Fusion ────────────────────────────────────────────────────

    def rrf_search(self, query: str, top_k: int = 20, k: float = 60.0) -> list[QueryResult]:
        """Reciprocal Rank Fusion of BM25 + embedding results.

        RRF scores: 1/(k + rank) per list, summed across lists.
        k=60 recommended by SIGIR papers for robust fusion.
        No score normalization needed — works across arbitrary scoring scales.
        """
        from collections import defaultdict

        query = (query or "").strip()
        if not query:
            return []

        bm25_results = self.bm25_search(query, top_k=top_k * 2)
        embed_results = self.embedding_search(query, top_k=top_k * 2)

        rrf_scores = defaultdict(float)
        seen = {}

        for rank, r in enumerate(bm25_results, 1):
            rrf_scores[r.skill.name] += 1.0 / (k + rank)
            seen[r.skill.name] = r

        for rank, r in enumerate(embed_results, 1):
            rrf_scores[r.skill.name] += 1.0 / (k + rank)
            if r.skill.name not in seen:
                seen[r.skill.name] = r

        fused = []
        for name, score in sorted(rrf_scores.items(), key=lambda x: -x[1]):
            fused.append(QueryResult(
                skill=seen[name].skill,
                score=round(score, 4),
                retrieval_method="rrf",
            ))

        return fused[:top_k]

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
