"""Adaptive retrieval — dynamic result count, clustering, and diverse selection.

Beyond fixed top-k: determines optimal result count per query via elbow method,
groups skills by similarity via DBSCAN, and ensures cluster diversity.
"""

from __future__ import annotations

import logging
from math import isclose
from pathlib import Path
from typing import Optional

import numpy as np

from .models import QueryResult

logger = logging.getLogger("scm.adaptive")


# ── Elbow Detection ─────────────────────────────────────────────

def detect_elbow(
    scores: list[float],
    min_results: int = 1,
    max_results: int = 10,
    min_gap_ratio: float = 2.0,
) -> int:
    """Find the optimal cutoff using elbow method on sorted scores.

    Scores should be sorted descending. The "elbow" is the position
    where the gap to the next score is significantly larger than the
    average gap.

    Args:
        scores: Sorted list of scores (descending).
        min_results: Minimum results to return (even if no clear elbow).
        max_results: Maximum results to return.
        min_gap_ratio: Gap must be at least this × the average gap to count as elbow.

    Returns:
        Optimal number of results to return (0 = no relevant results).
    """
    if not scores:
        return 0

    if len(scores) <= min_results:
        return len(scores)

    # Compute pairwise deltas
    deltas = [scores[i] - scores[i + 1] for i in range(len(scores) - 1)]

    if not deltas:
        return min(len(scores), max_results)

    avg_gap = sum(deltas) / len(deltas)

    # Flat or near-flat scores — no signal
    if isclose(avg_gap, 0.0) or avg_gap <= 0:
        return 0

    # Find largest gap
    max_gap = max(deltas)
    max_gap_idx = deltas.index(max_gap)

    # Only cut if gap is significantly larger than average
    if max_gap >= avg_gap * min_gap_ratio:
        k = max_gap_idx + 1  # +1 because index 0 = between result 0 and 1
    else:
        k = len(scores)

    return max(min_results, min(k, max_results))


def adaptive_query(
    results: list[QueryResult],
    min_results: int = 1,
    max_results: int = 10,
    min_gap_ratio: float = 2.0,
) -> dict:
    """Adaptive result selection with elbow detection.

    Returns:
        dict with:
        - results: filtered skill list
        - adaptive_k: number of results returned
        - elbow_found: whether an elbow was detected
        - score_range: (low, high) of returned scores
        - message: info string (for vague queries)
    """
    if not results:
        return {
            "results": [],
            "adaptive_k": 0,
            "elbow_found": False,
            "score_range": (0.0, 0.0),
            "message": "No matching skills found",
        }

    scores = [r.score for r in results]
    k = detect_elbow(scores, min_results, max_results, min_gap_ratio)

    if k == 0:
        return {
            "results": [],
            "adaptive_k": 0,
            "elbow_found": False,
            "score_range": (0.0, 0.0),
            "message": "Vague or ambiguous query — no strongly matching skills",
        }

    selected = results[:k]
    return {
        "results": selected,
        "adaptive_k": k,
        "elbow_found": k < len(scores),
        "score_range": (round(selected[-1].score, 4), round(selected[0].score, 4)),
        "message": f"Found {k} relevant skill(s) (elbow{' ' if k < len(scores) else ' not'} detected)",
    }


# ── DBSCAN Clustering ────────────────────────────────────────────

class SkillClusterer:
    """Cluster skills by embedding similarity using DBSCAN.

    Provides diverse skill selection: instead of returning top-k skills
    (which may all be from 1-2 clusters), returns at most 1 skill per
    cluster for better topic coverage.
    """

    def __init__(self, db_path: Optional[Path] = None, eps: float = 0.5,
                 min_samples: int = 2):
        self.db_path = db_path
        self.eps = eps
        self.min_samples = min_samples
        self._embeddings: dict[str, np.ndarray] = {}

    def load_embeddings(self):
        """Load skill embeddings from database."""
        from .db import connect
        with connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT name, embedding FROM skills WHERE embedding IS NOT NULL"
            ).fetchall()
            self._embeddings = {}
            for r in rows:
                emb_bytes = r["embedding"]
                if emb_bytes:
                    self._embeddings[r["name"]] = np.frombuffer(
                        emb_bytes, dtype=np.float32
                    )
        logger.debug("Loaded %d embeddings", len(self._embeddings))

    def cluster_all(self) -> dict[str, int]:
        """Run DBSCAN on all loaded embeddings.

        Returns:
            {skill_name: cluster_id} — cluster -1 = noise/unclustered.
        """
        if len(self._embeddings) < 2:
            return {name: -1 for name in self._embeddings}

        try:
            from sklearn.cluster import DBSCAN
        except ImportError:
            logger.warning("scikit-learn not installed, skipping clustering")
            return {name: -1 for name in self._embeddings}

        names = list(self._embeddings.keys())
        matrix = np.array([self._embeddings[n] for n in names])

        clustering = DBSCAN(
            eps=self.eps, min_samples=self.min_samples, metric="cosine"
        ).fit(matrix)

        return dict(zip(names, clustering.labels_.tolist()))

    def get_cluster_info(self, cluster_id: int, skill_names: list[str]) -> dict:
        """Get cluster metadata: size, representative, members."""
        members = [n for n in skill_names if n in self._embeddings]
        if not members:
            return {"cluster_id": cluster_id, "size": 0, "representative": "", "members": []}

        # Representative = skill closest to centroid
        embs = np.array([self._embeddings[m] for m in members])
        centroid = embs.mean(axis=0)
        norm = np.linalg.norm(centroid)
        if norm > 0:
            centroid = centroid / norm
        sims = embs @ centroid
        rep_idx = int(np.argmax(sims))

        return {
            "cluster_id": cluster_id,
            "size": len(members),
            "representative": members[rep_idx],
            "members": members,
        }

    def filter_to_diverse(self, results: list[QueryResult],
                          top_k: int = 5) -> list[QueryResult]:
        """Re-rank results to ensure cluster diversity.

        Returns at most 1 result per cluster until top_k is reached,
        then fills remaining slots from the best overall results.

        This solves the "variant pollution" problem where kubernetes-deploy-v2,
        kubernetes-deploy-v3 all appear in the same result set.
        """
        if not results or top_k <= 1:
            return results

        labels = self.cluster_all()

        # Group results by cluster, keeping only the best per cluster
        cluster_best: dict[int, list[QueryResult]] = {}
        seen_order: list[int] = []

        for r in results:
            cid = labels.get(r.skill.name, -1)
            if cid not in cluster_best:
                cluster_best[cid] = []
                seen_order.append(cid)
            cluster_best[cid].append(r)

        # Take top-1 from each cluster in rank order
        selected = []
        selected_names = set()

        for cid in seen_order:
            if len(selected) >= top_k:
                break
            best = cluster_best[cid][0]
            if best.skill.name not in selected_names:
                selected.append(best)
                selected_names.add(best.skill.name)

        # If still under top_k, fill with next-best from any cluster
        if len(selected) < top_k:
            for r in results:
                if r.skill.name not in selected_names:
                    selected.append(r)
                    selected_names.add(r.skill.name)
                if len(selected) >= top_k:
                    break

        return selected[:top_k]
