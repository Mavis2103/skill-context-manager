"""Learning-to-Rank (LTR) module for skill retrieval.

LambdaMART-based ranking that learns to weight retrieval signals optimally
from user feedback. Bootstraps with cross-encoder pseudo-labels until
enough real feedback data is available (target: 100+ records).

Usage:
    # Bootstrap from existing cross-encoder scores
    python3 scripts/train-ltr.py --bootstrap

    # Train from feedback data
    python3 scripts/train-ltr.py
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from .models import QueryResult

logger = logging.getLogger("scm.ltr")


# Feature indices for the LTR model
FT_BM25 = 0
FT_BM25_ORIG = 1
FT_EMBEDDING = 2
FT_CROSS_ENCODER = 3
FT_RRF = 4
FT_PPR = 5
FT_CATEGORY_MATCH = 6
FT_TAG_JACCARD = 7
FT_TAG_OVERLAP = 8
FT_TOKEN_COST = 9
FT_USAGE_FREQ = 10
FT_USAGE_FREQ_7D = 11
FT_RECENCY = 12
FT_SUCCESS_RATE = 13
FT_FEEDBACK_COUNT = 14
FT_SESSION_COOCCUR_MAX = 15
FT_SESSION_COOCCUR_SUM = 16
FT_SESSION_RECENCY = 17
FT_QUERY_LEN = 18
FT_QUERY_STOPWORD_RATIO = 19
FT_DESCRIPTION_LEN = 20
FT_TEXT_LENGTH_MATCH = 21
FT_NAME_QUERY_OVERLAP = 22
FT_CATEGORY_SIZE = 23
FT_EMBEDDING_UNCERTAINTY = 24

N_FEATURES = 25


def extract_features(
    result: QueryResult,
    query: str,
    session_skills: Optional[list[str]] = None,
    ppr_scores: Optional[dict[str, float]] = None,
    graph: Optional["SkillGraph"] = None,
) -> list[float]:
    """Extract 25 features for a single (query, skill) pair.

    These features are used by the LambdaMART LTR model to predict
    the relevance score.

    Args:
        result: A single query result to extract features for.
        query: The original user query.
        session_skills: Skills used in the current session.
        ppr_scores: Pre-computed PPR scores (from graph).
        graph: SkillGraph instance for graph-based features.

    Returns:
        List of 25 float features.
    """
    import re
    import math
    from .db import connect

    skill = result.skill
    features = [0.0] * N_FEATURES

    # ── Category A: Text Retrieval (0-4) ──
    features[FT_BM25] = result.score if result.retrieval_method == "bm25" else 0.0
    features[FT_EMBEDDING] = result.score if "embedding" in result.retrieval_method else 0.0
    features[FT_CROSS_ENCODER] = result.score if result.retrieval_method == "reranked" else 0.0
    features[FT_RRF] = result.score if result.retrieval_method == "rrf" else 0.0

    # ── Category B: Graph (5) ──
    if ppr_scores:
        features[FT_PPR] = ppr_scores.get(skill.name, 0.0)

    # ── Category C: Metadata (6-9) ──
    query_words = set(re.findall(r'\w+', query.lower()))
    skill_words = set(re.findall(r'\w+', f"{skill.name} {skill.description}".lower()))
    overlap = query_words & skill_words

    features[FT_CATEGORY_MATCH] = 1.0 if skill.category and skill.category != "uncategorized" else 0.0

    if skill.tags:
        skill_tags = set(skill.tags)
        query_tags = {w for w in query_words if w in skill_tags or any(w in t for t in skill_tags)}
        features[FT_TAG_JACCARD] = len(query_tags & skill_tags) / max(len(query_tags | skill_tags), 1)
        features[FT_TAG_OVERLAP] = float(len(query_tags & skill_tags))

    features[FT_TOKEN_COST] = 1.0 / max(skill.token_cost_body, 1)

    # ── Category D: History (10-14) ──
    try:
        with connect(None) as conn:
            row = conn.execute(
                "SELECT successes, failures FROM skill_weights WHERE skill_name = ?",
                (skill.name,),
            ).fetchone()
            if row:
                successes, failures = row
                total = successes + failures
                features[FT_SUCCESS_RATE] = successes / max(total, 1)
                features[FT_FEEDBACK_COUNT] = math.log(total + 1)

            # Usage frequency
            usage = conn.execute(
                "SELECT COUNT(*) FROM usage_events WHERE skill_name = ?", (skill.name,)
            ).fetchone()
            if usage:
                features[FT_USAGE_FREQ] = math.log(usage[0] + 1)

            # Recency (days since last use)
            last = conn.execute(
                "SELECT MAX(timestamp) FROM usage_events WHERE skill_name = ?",
                (skill.name,),
            ).fetchone()
            if last and last[0]:
                from datetime import datetime, timezone
                try:
                    last_time = datetime.fromisoformat(last[0])
                    delta_days = (datetime.now(timezone.utc) - last_time).days
                    features[FT_RECENCY] = math.exp(-delta_days / 7.0)
                except (ValueError, TypeError):
                    pass
    except Exception:
        logger.debug("Failed to extract history features for %s", skill.name)

    # ── Category E: Session (15-17) ──
    if session_skills:
        features[FT_SESSION_COOCCUR_MAX] = 1.0 if skill.name in session_skills else 0.0
        features[FT_SESSION_COOCCUR_SUM] = float(len(set(session_skills) & {skill.name}))

    # ── Query features (18-24) ──
    stopwords = {'the', 'a', 'an', 'is', 'are', 'was', 'to', 'of', 'in',
                 'for', 'on', 'with', 'at', 'by', 'from', 'as', 'into'}
    query_len = len(query_words)
    features[FT_QUERY_LEN] = min(query_len / 20.0, 1.0)
    if query_len > 0:
        stopword_count = sum(1 for w in query_words if w in stopwords)
        features[FT_QUERY_STOPWORD_RATIO] = stopword_count / query_len

    desc_len = len(skill.description.split()) if skill.description else 0
    features[FT_DESCRIPTION_LEN] = min(desc_len / 50.0, 1.0)
    features[FT_TEXT_LENGTH_MATCH] = 1.0 - min(abs(query_len - desc_len) / max(query_len + desc_len, 1), 1.0)
    features[FT_NAME_QUERY_OVERLAP] = len(overlap) / max(len(query_words | skill_words), 1)

    return features


def feature_names() -> list[str]:
    """Return names of all 25 features for interpretability."""
    return [
        "bm25_score", "bm25_original", "embedding_cosine", "cross_encoder",
        "rrf_score", "ppr_score", "category_match", "tag_jaccard",
        "tag_overlap", "token_cost_inv", "usage_frequency", "usage_frequency_7d",
        "recency", "success_rate", "feedback_count", "session_cooccur_max",
        "session_cooccur_sum", "session_recency", "query_length",
        "query_stopword_ratio", "description_length", "text_length_match",
        "name_query_overlap", "category_size", "embedding_uncertainty",
    ]


class LTRRanker:
    """Learning-to-Rank wrapper for LambdaMART.

    LightGBM LambdaRANK model that scores skills by fusing all 25 features.
    Graceful degradation: if model not found → skip LTR.
    """

    def __init__(self, model_path: Optional[Path] = None):
        self.model_path = model_path or Path.home() / ".scm" / "ltr_model.txt"
        self._model = None

    def load(self) -> bool:
        """Load trained model. Returns True if loaded, False if not found."""
        if not self.model_path.exists():
            logger.info("LTR model not found at %s — skipping", self.model_path)
            return False
        try:
            import lightgbm as lgb
            self._model = lgb.Booster(model_file=str(self.model_path))
            logger.info("LTR model loaded: %s", self.model_path)
            return True
        except ImportError:
            logger.info("lightgbm not installed — skipping LTR")
            return False
        except Exception as e:
            logger.warning("LTR model load failed: %s", e)
            return False

    def score(self, features: list[float]) -> float:
        """Score a single skill using the LTR model."""
        if self._model is None:
            return 0.0
        import numpy as np
        return float(self._model.predict(np.array([features]))[0])

    def rerank(self, results: list[QueryResult], query: str,
               session_skills: Optional[list[str]] = None,
               ppr_scores: Optional[dict[str, float]] = None) -> list[QueryResult]:
        """Rerank results using LTR model.

        Falls back to input ordering if model not loaded.
        """
        if self._model is None and not self.load():
            return results

        if not results:
            return results

        scored: list[tuple[float, QueryResult]] = []
        for r in results:
            feats = extract_features(r, query, session_skills, ppr_scores)
            ltr_score = self.score(feats)
            scored.append((ltr_score, r))

        scored.sort(key=lambda x: -x[0])
        return [
            QueryResult(
                skill=r.skill,
                score=round(s, 4),
                retrieval_method="ltr",
            )
            for s, r in scored
        ]

    def train(self, training_data_path: Path, output_path: Optional[Path] = None):
        """Train LambdaMART model from prepared data.

        Expected format: LightGBM Ranking format:
        relevance qid:group_id feat1:val1 feat2:val2 ...

        Args:
            training_data_path: Path to training data in LightGBM format.
            output_path: Path to save trained model.
        """
        import lightgbm as lgb

        output = output_path or self.model_path
        output.parent.mkdir(parents=True, exist_ok=True)

        train_data = lgb.Dataset(
            str(training_data_path),
            params={"format": "tsv", "label_column": "0", "group_column": "1"},
        )

        params = {
            "objective": "lambdarank",
            "metric": "ndcg",
            "ndcg_eval_at": [5],
            "boosting_type": "gbdt",
            "num_leaves": 31,
            "learning_rate": 0.1,
            "min_data_in_leaf": 5,
            "num_iterations": 500,
            "early_stopping_round": 50,
            "verbosity": -1,
        }

        model = lgb.train(
            params,
            train_data,
            valid_sets=[train_data],
        )

        model.save_model(str(output))
        logger.info("LTR model saved to %s", output)
