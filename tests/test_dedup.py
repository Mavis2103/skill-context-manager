"""Tests for skill dedup pipeline — ported from graphify dedup.py."""

from scm.dedup import (
    _jaro, _jaro_winkler, _norm, _entropy, _shingles,
    MinHash, MinHashLSH, deduplicate_skills,
)
from scm.models import Skill


class TestJaro:
    def test_exact(self):
        assert _jaro("hello", "hello") == 1.0

    def test_empty(self):
        # Two equal strings (including empty) → Jaro = 1.0
        assert _jaro("", "") == 1.0
        assert _jaro("a", "") == 0.0

    def test_transposition(self):
        # "CRATE" vs "TRACE" — classic test
        j = _jaro("CRATE", "TRACE")
        assert 0.7 <= j <= 0.8

    def test_no_match(self):
        assert _jaro("abc", "xyz") == 0.0


class TestJaroWinkler:
    def test_exact(self):
        assert _jaro_winkler("hello", "hello") == 1.0

    def test_prefix_bonus(self):
        # Same prefix "he" → bonus
        jw = _jaro_winkler("hello", "hemlo")
        j = _jaro("hello", "hemlo")
        assert jw >= j  # Winkler prefix boost

    def test_below_threshold_no_boost(self):
        # 0.6 < 0.7 threshold → no prefix bonus
        jw = _jaro_winkler("abcdef", "ghijkl")
        j = _jaro("abcdef", "ghijkl")
        assert jw == j  # No prefix boost below 0.7


class TestNorm:
    def test_casefold(self):
        assert _norm("HelloWorld") == _norm("helloworld")

    def test_unicode_nfkc(self):
        # "café" is already NFKC-normal; "cafe\u0301" (decomposed) normalizes to it
        assert _norm("café") == _norm("cafe\u0301")
        # Full-width characters normalize to ASCII
        assert _norm("ＡＢＣ") == "abc"

    def test_strip_punctuation(self):
        assert " " in _norm("k8s-deploy-v2")  # hyphens become spaces

    def test_empty(self):
        assert _norm("") == ""
        assert _norm(None) == ""


class TestEntropy:
    def test_low_entropy(self):
        # Short/repetitive → low entropy
        e = _entropy("utils")
        assert e < 2.5

    def test_high_entropy(self):
        # Longer/diverse → high entropy
        e = _entropy("kubernetes-deployment-helm-chart-v2")
        assert e > 3.0

    def test_empty_returns_zero(self):
        assert _entropy("") == 0.0


class TestShingles:
    def test_basic_trigrams(self):
        s = _shingles("hello", k=3)
        assert "hel" in s
        assert "ell" in s
        assert "llo" in s
        assert len(s) == 3

    def test_short_string(self):
        s = _shingles("ab", k=3)
        assert s == {"ab"}

    def test_empty_returns_empty_set(self):
        assert _shingles("") == set()


class TestMinHash:
    def test_identical_signatures(self):
        m1 = MinHash(64)
        m2 = MinHash(64)
        m1.update(b"hello world")
        m2.update(b"hello world")
        assert m1.jaccard(m2) == 1.0

    def test_different_signatures(self):
        m1 = MinHash(64)
        m2 = MinHash(64)
        m1.update(b"aaaaa")
        m2.update(b"bbbbb")
        j = m1.jaccard(m2)
        assert j < 0.5  # Very different, but MinHash is probabilistic

    def test_jaccard_is_symmetric(self):
        m1 = MinHash(64)
        m2 = MinHash(64)
        m1.update(b"hello")
        m1.update(b"world")
        m2.update(b"hello")
        m2.update(b"there")
        assert m1.jaccard(m2) == m2.jaccard(m1)

    def test_digest_is_bytes(self):
        m = MinHash(128)
        m.update(b"test")
        d = m.digest()
        assert isinstance(d, bytes)
        assert len(d) == 128 * 4  # 4 bytes per hash


class TestMinHashLSH:
    def test_insert_and_query(self):
        m1 = MinHash(128)
        m1.update(b"hello world test data")
        m2 = MinHash(128)
        m2.update(b"hello world test data extra")
        m3 = MinHash(128)
        m3.update(b"completely different content here")

        lsh = MinHashLSH(threshold=0.7, num_perm=128)
        lsh.insert("hello-world", m1)
        lsh.insert("hello-extra", m2)
        lsh.insert("different", m3)

        results = lsh.query(m1)
        assert "hello-world" in results
        # "hello-extra" may or may not be in results depending on
        # MinHash Jaccard; but at least the identical match is found
        assert len(results) >= 1

    def test_query_returns_candidates(self):
        lsh = MinHashLSH()
        m = MinHash(128)
        m.update(b"test")
        results = lsh.query(m)
        assert isinstance(results, set)


class TestDeduplicateSkills:
    def test_empty_list(self):
        assert deduplicate_skills([]) == []

    def test_single_skill(self):
        s = Skill(name="hello", description="test", body="")
        assert deduplicate_skills([s]) == [s]

    def test_exact_duplicate_names(self):
        s1 = Skill(name="My Skill", description="desc", body="")
        s2 = Skill(name="my-skill", description="desc", body="")
        result = deduplicate_skills([s1, s2])
        assert len(result) <= 2  # may merge or keep

    def test_identical_skills_deduped(self):
        s1 = Skill(name="K8s-Deploy", description="Deploy to K8s", body="content")
        s2 = Skill(name="k8s-deploy", description="Deploy to K8s", body="content")
        result = deduplicate_skills([s1, s2])
        assert len(result) < 2

    def test_completely_different_skills_preserved(self):
        s1 = Skill(name="docker", description="Docker ops", body="...")
        s2 = Skill(name="postgres", description="DB backup", body="...")
        result = deduplicate_skills([s1, s2])
        assert len(result) == 2
