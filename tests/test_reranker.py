"""Tests for Skill Context Manager — cross-encoder reranker."""



from scm.models import Skill, QueryResult
from scm.reranker import SkillReranker


class TestSkillReranker:
    def test_rerank_empty_list(self):
        reranker = SkillReranker()
        results = reranker.rerank("test query", [], top_k=5)
        assert results == []

    def test_rerank_fallback_when_transformers_missing(self):
        """Should gracefully fall back to input ordering."""
        reranker = SkillReranker()
        skills = [
            QueryResult(skill=Skill(name="a", description="first"), score=0.9, retrieval_method="bm25"),
            QueryResult(skill=Skill(name="b", description="second"), score=0.5, retrieval_method="bm25"),
        ]
        # Patch transformers import to simulate missing package
        import builtins
        from unittest.mock import patch
        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == 'transformers':
                raise ImportError("No module named 'transformers'")
            return original_import(name, *args, **kwargs)

        with patch.object(builtins, '__import__', mock_import):
            results = reranker.rerank("test query", skills, top_k=5)
        # Falls back to input ordering
        assert len(results) == 2
        assert results[0].skill.name == "a"

    def test_rerank_preserves_top_k(self):
        reranker = SkillReranker()
        skills = [QueryResult(
            skill=Skill(name=f"s{i}", description=f"desc {i}"),
            score=1.0 - i * 0.1, retrieval_method="bm25",
        ) for i in range(10)]
        results = reranker.rerank("test", skills, top_k=3)
        assert len(results) == 3

    def test_rerank_single_candidate(self):
        reranker = SkillReranker()
        skills = [QueryResult(
            skill=Skill(name="only", description="only one"),
            score=0.5, retrieval_method="bm25",
        )]
        results = reranker.rerank("test", skills, top_k=5)
        assert len(results) == 1
        assert results[0].skill.name == "only"

    def test_rerank_model_name_default(self):
        reranker = SkillReranker()
        assert reranker.model_name == "cross-encoder/ms-marco-MiniLM-L6-v2"

    def test_rerank_custom_model(self):
        reranker = SkillReranker(model_name="custom-model")
        assert reranker.model_name == "custom-model"
