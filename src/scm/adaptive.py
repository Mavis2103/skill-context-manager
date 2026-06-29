"""Adaptive retrieval — dynamic result count and diverse selection.

Beyond fixed top-k: determines optimal result count per query via elbow method,
and ensures topic diversity via category-based dedup.
"""

from __future__ import annotations

import logging
from math import isclose
from typing import Optional

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


# ── Category-based Diverse Filter ───────────────────────────────

def diverse_filter(
    results: list[QueryResult],
    top_k: int = 5,
) -> list[QueryResult]:
    """Re-rank results to ensure category diversity.

    At most 2 skills per category. Prevents "variant pollution" where
    kubernetes-deploy-v1, kubernetes-deploy-v2 all appear in the same set.

    Args:
        results: Ranked list of QueryResult.
        top_k: Maximum results to return.

    Returns:
        Re-ranked list with category diversity.
    """
    if not results or top_k <= 1:
        return results

    selected = []
    selected_names: set[str] = set()
    category_count: dict[str, int] = {}

    for r in results:
        if len(selected) >= top_k:
            break
        cat = r.skill.category or "uncategorized"
        count = category_count.get(cat, 0)
        if count >= 2:
            continue
        if r.skill.name not in selected_names:
            selected.append(r)
            selected_names.add(r.skill.name)
            category_count[cat] = count + 1

    return selected[:top_k]
