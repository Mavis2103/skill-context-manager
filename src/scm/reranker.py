"""Cross-encoder reranker — deep semantic matching for top-k refinement.

Stage 2 of the retrieval pipeline. Takes top candidates from Stage 1 (retriever)
and performs deeper cross-attention between query and skill full text to produce
more accurate relevance scores.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from .models import Skill, QueryResult

logger = logging.getLogger("scm.reranker")


class SkillReranker:
    """Rerank retrieved skills using a cross-encoder model.

    Why rerank?
    - Bi-encoder (embedding) encodes query and skills independently → fast but loses
      interaction information
    - Cross-encoder processes query + skill TOGETHER → slower but much more accurate
    - Reranking top 20 from retriever is the sweet spot: fast + accurate

    Reference: SkillRouter (CVPR 2026) — 91.7% of reranker attention on skill body
    """

    def __init__(self, model_name: str = "cross-encoder/ms-marco-MiniLM-L6-v2"):
        self.model_name = model_name
        self._model = None
        self._tokenizer = None

    def rerank(self, query: str, candidates: list[QueryResult],
               top_k: int = 5) -> list[QueryResult]:
        """Rerank candidate skills using cross-encoder.

        Falls back to input ordering if cross-encoder is not available.
        """
        if not candidates:
            return []

        try:
            return self._rerank_inner(query, candidates, top_k)
        except ImportError:
            logger.info("cross-encoder not installed, using retriever scores")
            return candidates[:top_k]
        except Exception as e:
            logger.warning("Reranker error: %s, using retriever scores", e)
            return candidates[:top_k]

    def _rerank_inner(self, query: str, candidates: list[QueryResult],
                      top_k: int) -> list[QueryResult]:
        """Actual cross-encoder reranking."""
        from transformers import AutoTokenizer, AutoModelForSequenceClassification
        import torch

        if self._model is None:
            self._tokenizer = AutoTokenizer.from_pretrained(self.model_name)
            self._model = AutoModelForSequenceClassification.from_pretrained(self.model_name)
            self._model.eval()

        # Prepare pairs: query + skill full text
        pairs = []
        for r in candidates:
            # Use full body text for better accuracy (per SkillRouter findings)
            skill_text = f"{r.skill.name}: {r.skill.description}\n{r.skill.body[:1024]}"
            pairs.append((query, skill_text))

        # Tokenize and score
        with torch.no_grad():
            inputs = self._tokenizer(
                pairs, padding=True, truncation=True, max_length=512,
                return_tensors="pt"
            )
            outputs = self._model(**inputs)
            scores = outputs.logits.squeeze(-1).tolist()

        if isinstance(scores, float):
            scores = [scores]

        # Update scores
        reranked = []
        for r, score in zip(candidates, scores):
            reranked.append(QueryResult(
                skill=r.skill,
                score=round(float(score), 4),
                retrieval_method="reranked",
            ))

        # Sort by new score
        reranked.sort(key=lambda r: r.score, reverse=True)
        return reranked[:top_k]
