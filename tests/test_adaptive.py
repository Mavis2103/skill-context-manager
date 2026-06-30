"""Tests for adaptive retrieval — elbow detection and diverse filtering."""

from scm.adaptive import detect_elbow, adaptive_query, diverse_filter
from scm.models import Skill, QueryResult


def make_results(scores):
    """Create QueryResult list from scores."""
    return [
        QueryResult(
            skill=Skill(name=f"skill-{i}", description=f"desc-{i}"),
            score=s,
            retrieval_method="rrf",
        )
        for i, s in enumerate(scores)
    ]


# ── Elbow Detection ────────────────────────────────────────────


class TestElbowDetection:
    def test_sharp_elbow_returns_early(self):
        """Clear elbow at position 2 — return 2 results."""
        scores = [1.20, 1.18, 1.05, 1.04, 1.04, 1.03]
        k = detect_elbow(scores)
        assert k == 2  # Gap 1.18→1.05 = 0.13 > avg 0.034 * 2

    def test_flat_scores_return_zero(self):
        """All scores similar — vague query, no signal."""
        scores = [0.50, 0.50, 0.50, 0.50]
        k = detect_elbow(scores)
        assert k == 0

    def test_gradual_decline_returns_all(self):
        """Scores decline gradually — no clear elbow, return up to max."""
        scores = [1.04, 1.03, 1.02, 1.01, 1.00, 0.99]
        k = detect_elbow(scores, max_results=6)
        assert k == 6

    def test_empty_scores(self):
        assert detect_elbow([]) == 0

    def test_single_result(self):
        assert detect_elbow([1.20]) == 1

    def test_two_results_with_gap(self):
        scores = [1.20, 1.00]
        k = detect_elbow(scores, min_gap_ratio=0.9)
        assert k == 1  # Gap 0.20 > avg 0.20 * 0.9 = 0.18

    def test_three_results_two_tiers(self):
        """Two clear tiers — return tier 1."""
        scores = [1.15, 1.14, 1.05, 1.04, 1.03]
        k = detect_elbow(scores, min_gap_ratio=2.0)
        # Gap 1.14→1.05 = 0.09, avg gap = 0.03
        # 0.09 >= 0.03 * 2 = 0.06 → elbow at position 2
        assert k == 2

    def test_min_results_floor(self):
        scores = [1.20, 1.00]
        k = detect_elbow(scores, min_results=1)
        assert k >= 1

    def test_high_min_gap_ratio_no_elbow(self):
        """Very high min_gap_ratio means no elbow detected."""
        scores = [1.20, 1.18, 1.05, 1.04]
        k = detect_elbow(scores, min_gap_ratio=100.0)
        assert k == 4  # No gap qualifies

    def test_same_score_twice_then_drop(self):
        """Two top scores identical, then sharp drop."""
        scores = [1.20, 1.20, 1.00, 0.99]
        # Deltas: [0, 0.20, 0.01], avg = 0.07
        # Max gap = 0.20 at idx 1 → return 2
        k = detect_elbow(scores)
        assert k == 2


# ── Adaptive Query ────────────────────────────────────────────


class TestAdaptiveQuery:
    def test_sharp_elbow_filters(self):
        results = make_results([1.20, 1.18, 1.05, 1.04])
        out = adaptive_query(results)
        assert len(out["results"]) == 2
        assert out["elbow_found"] is True

    def test_vague_query_returns_empty(self):
        results = make_results([0.50, 0.50, 0.50])
        out = adaptive_query(results)
        assert len(out["results"]) == 0
        assert "Vague" in out["message"]

    def test_empty_results(self):
        out = adaptive_query([])
        assert len(out["results"]) == 0
        assert "No matching" in out["message"]

    def test_min_results_floor(self):
        results = make_results([1.20, 1.00])
        out = adaptive_query(results, min_results=1)
        assert len(out["results"]) >= 1

    def test_adaptive_k_in_output(self):
        results = make_results([1.20, 1.18, 1.05, 1.04])
        out = adaptive_query(results)
        assert out["adaptive_k"] == 2

    def test_score_range(self):
        results = make_results([1.20, 1.00])
        out = adaptive_query(results)
        low, high = out["score_range"]
        assert low <= high


# ── Diverse Filter ────────────────────────────────────────────


class TestDiverseFilter:
    def test_empty(self):
        assert diverse_filter([], top_k=5) == []

    def test_single(self):
        results = make_results([1.20])
        filtered = diverse_filter(results, top_k=5)
        assert len(filtered) == 1

    def test_deduplicates_by_category(self):
        # Two skills with same name prefix → only one kept
        results = [
            QueryResult(
                skill=Skill(name="k8s-deploy-prod", description="Prod deploy",
                            category="devops"),
                score=1.0,
                retrieval_method="rrf",
            ),
            QueryResult(
                skill=Skill(name="k8s-deploy-staging", description="Staging deploy",
                            category="devops"),
                score=0.9,
                retrieval_method="rrf",
            ),
            QueryResult(
                skill=Skill(name="pg-backup", description="Backup",
                            category="databases"),
                score=0.8,
                retrieval_method="rrf",
            ),
        ]
        filtered = diverse_filter(results, top_k=5)
        # Should keep first (highest score) in each category, then dedup prefix
        assert len(filtered) >= 2  # At least one devops + one databases
