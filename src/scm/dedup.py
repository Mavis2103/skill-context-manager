"""Entity deduplication pipeline for skill names — ported from graphify dedup.py.

Pipeline: exact normalization → entropy gate → MinHash/LSH blocking →
Jaro-Winkler verification → union-find merge.

graphify (https://github.com/safishamsi/graphify) MIT-licensed.
Adapted for SCM skill dedup: simplified (no community boost, no cross-file guards).
"""
from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from collections import defaultdict

from .models import Skill


# ── Pure-Python Jaro-Winkler ────────────────────────────────────────────────

def _jaro(a: str, b: str) -> float:
    """Jaro similarity (0-1)."""
    if a == b:
        return 1.0
    len_a, len_b = len(a), len(b)
    if len_a == 0 or len_b == 0:
        return 0.0

    match_dist = max(len_a, len_b) // 2 - 1
    if match_dist < 0:
        match_dist = 0

    a_matches = [False] * len_a
    b_matches = [False] * len_b
    matches = 0
    transpositions = 0

    for i in range(len_a):
        start = max(0, i - match_dist)
        end = min(len_b, i + match_dist + 1)
        for j in range(start, end):
            if b_matches[j] or a[i] != b[j]:
                continue
            a_matches[i] = True
            b_matches[j] = True
            matches += 1
            break

    if matches == 0:
        return 0.0

    k = 0
    for i in range(len_a):
        if not a_matches[i]:
            continue
        while k < len_b and not b_matches[k]:
            k += 1
        if k < len_b and a[i] != b[k]:
            transpositions += 1
        k += 1

    return (matches / len_a + matches / len_b + (matches - transpositions / 2) / matches) / 3.0


def _jaro_winkler(a: str, b: str, prefix_weight: float = 0.1) -> float:
    """Jaro-Winkler similarity (0-1) with prefix bonus."""
    js = _jaro(a, b)
    if js < 0.7:
        return js
    prefix = 0
    for i in range(min(4, len(a), len(b))):
        if a[i] == b[i]:
            prefix += 1
        else:
            break
    return js + prefix * prefix_weight * (1.0 - js)


# ── Pure-Python MinHash + LSH ──────────────────────────────────────────────

_MAX_HASH = (1 << 32) - 1  # Max 32-bit hash sentinel


class MinHash:
    """MinHash signature with seeded hash functions.

    Uses zlib.adler32 for fast permutation hashing — same approach as
    graphify's _minhash.py. Each permutation i uses a unique seed to
    simulate independent hash functions.
    """

    def __init__(self, num_perm: int = 128):
        self.num_perm = num_perm
        self._sig: list[int] = [_MAX_HASH] * num_perm

    def update(self, data: bytes):
        for i in range(self.num_perm):
            h = hashlib.sha1(str(i).encode() + data).digest()
            hv = int.from_bytes(h[:4], "big")
            if hv < self._sig[i]:
                self._sig[i] = hv

    def jaccard(self, other: "MinHash") -> float:
        return sum(1 for a, b in zip(self._sig, other._sig) if a == b) / self.num_perm

    def digest(self) -> bytes:
        return b"".join(h.to_bytes(4, "big") for h in self._sig)


class MinHashLSH:
    """Locality-Sensitive Hashing index for MinHash."""

    def __init__(self, threshold: float = 0.7, num_perm: int = 128):
        self.threshold = threshold
        self.num_perm = num_perm
        self.b = 20  # bands
        self.r = num_perm // self.b  # rows per band
        self._buckets: list[dict[bytes, list[str]]] = [defaultdict(list) for _ in range(self.b)]
        self._band_size = self.r * 4  # 4 bytes per hash in digest

    def insert(self, key: str, minhash: MinHash):
        sig = minhash.digest()
        for b in range(self.b):
            start = b * self._band_size
            band_key = sig[start:start + self._band_size]
            self._buckets[b][band_key].append(key)

    def query(self, minhash: MinHash) -> set[str]:
        sig = minhash.digest()
        candidates: set[str] = set()
        for b in range(self.b):
            start = b * self._band_size
            band_key = sig[start:start + self._band_size]
            for k in self._buckets[b].get(band_key, []):
                candidates.add(k)
        return candidates


# ── Normalization ──────────────────────────────────────────────────────────

def _norm(label: str | None) -> str:
    """Unicode NFKC normalization + collapse non-alphanumeric runs."""
    if not isinstance(label, str):
        label = "" if label is None else str(label)
    label = unicodedata.normalize("NFKC", label)
    return re.sub(r"[\W_]+", " ", label.casefold(), flags=re.UNICODE).strip()


def _entropy(label: str) -> float:
    """Shannon entropy in bits/char."""
    s = _norm(label)
    if not s:
        return 0.0
    freq: dict[str, int] = defaultdict(int)
    for ch in s:
        freq[ch] += 1
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in freq.values())


def _shingles(text: str, k: int = 3) -> set[str]:
    """k-gram character shingles."""
    if len(text) < k:
        return {text} if text else set()
    return {text[i:i + k] for i in range(len(text) - k + 1)}


def _make_minhash(text: str, num_perm: int = 128) -> MinHash:
    m = MinHash(num_perm=num_perm)
    for shingle in _shingles(text.replace(" ", "")):
        m.update(shingle.encode("utf-8"))
    return m


# ── Guards ─────────────────────────────────────────────────────────────────

def _is_variant_pair(a: str, b: str) -> bool:
    """Check if labels are sibling model/variant suffixes (e.g. skill-v1 vs skill-v2)."""
    if a == b or max(len(a), len(b)) >= 12:
        return False
    pattern = re.compile(r"^(.*[a-z])([0-9]+[a-z]*|[a-z]{2,})$")
    ma, mb = pattern.match(a), pattern.match(b)
    if not (ma and mb):
        return False
    return ma.group(1) == mb.group(1) and ma.group(2) != mb.group(2)


# ── Union-Find ─────────────────────────────────────────────────────────────

class _UF:
    def __init__(self):
        self._parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        self._parent.setdefault(x, x)
        while self._parent[x] != x:
            self._parent[x] = self._parent[self._parent[x]]
            x = self._parent[x]
        return x

    def union(self, x: str, y: str) -> None:
        self._parent.setdefault(x, x)
        self._parent.setdefault(y, y)
        rx, ry = self.find(x), self.find(y)
        if rx != ry:
            self._parent[ry] = rx

    def components(self) -> dict[str, list[str]]:
        groups: dict[str, list[str]] = defaultdict(list)
        for x in self._parent:
            groups[self.find(x)].append(x)
        return dict(groups)


# ── Constants ──────────────────────────────────────────────────────────────

_ENTROPY_THRESHOLD = 2.5     # skip low-information labels (e.g. "utils", "helper")
_LSH_THRESHOLD = 0.7         # MinHash Jaccard threshold for LSH
_MERGE_THRESHOLD = 0.92      # Jaro-Winkler threshold for merge
_NUM_PERM = 128              # number of MinHash permutations


# ── Main entry point ──────────────────────────────────────────────────────

def deduplicate_skills(
    skills: list[Skill],
    entropy_threshold: float = _ENTROPY_THRESHOLD,
    merge_threshold: float = _MERGE_THRESHOLD,
) -> list[Skill]:
    """Deduplicate skills by name similarity.

    Pipeline:
    1. Exact normalization — merge skills with identical normalized names
       (e.g. "K8s-Deploy" and "k8s-deploy").
    2. Entropy gate — skip labels with <2.5 bits Shannon entropy
       ("utils", "test", "helper" etc. — too short/generic).
    3. MinHash/LSH blocking — find candidate pairs (Jaccard ≥ 0.7).
    4. Jaro-Winkler verification — merge candidates scoring ≥ 0.92.
    5. Union-find merge → pick canonical survivor.

    Args:
        skills: List of Skill objects to deduplicate.
        entropy_threshold: Minimum Shannon entropy for fuzzy matching.
        merge_threshold: Jaro-Winkler threshold for considering a merge.

    Returns:
        Deduplicated list with same ordering (first occurrence of each
        canonical skill preserved).
    """
    if len(skills) <= 1:
        return skills

    # Build lookup tables
    skills_by_name: dict[str, Skill] = {}
    for s in skills:
        skills_by_name[s.name] = s

    uf = _UF()

    # ── Pass 1: exact normalization ──────────────────────────────────
    norm_names: dict[str, list[str]] = defaultdict(list)
    for s in skills:
        key = _norm(s.name)
        if key:
            norm_names[key].append(s.name)

    exact_merges = 0
    for key, group in norm_names.items():
        if len(group) <= 1:
            continue
        winner = group[0]  # first occurrence wins
        for name in group[1:]:
            uf.union(winner, name)
            exact_merges += 1

    # ── Pass 2: MinHash/LSH + Jaro-Winkler ──────────────────────────
    # Only high-entropy labels qualify for fuzzy matching
    candidates: list[str] = []
    for s in skills:
        if _entropy(s.name) >= entropy_threshold:
            candidates.append(s.name)

    fuzzy_merges = 0
    if len(candidates) >= 2:
        lsh = MinHashLSH(threshold=_LSH_THRESHOLD, num_perm=_NUM_PERM)
        minhashes: dict[str, MinHash] = {}

        for name in candidates:
            m = _make_minhash(name)
            minhashes[name] = m
            try:
                lsh.insert(name, m)
            except ValueError:
                pass

        norm_cache = {name: _norm(name) for name in candidates}
        seeds = set(candidates)

        for name in candidates:
            if name not in seeds:
                continue
            neighbors = lsh.query(minhashes[name])
            for nb_name in neighbors:
                if nb_name == name or uf.find(name) == uf.find(nb_name):
                    continue
                if nb_name not in seeds:
                    continue

                a, b = norm_cache[name], norm_cache[nb_name]
                if _is_variant_pair(a, b):
                    continue
                # Prefix-extension pairs (e.g. "getSkill" / "getSkills")
                lo, hi = sorted((a, b), key=len)
                if hi.startswith(lo) and hi != lo:
                    continue

                jw = _jaro_winkler(a, b)
                if jw >= merge_threshold:
                    uf.union(name, nb_name)
                    fuzzy_merges += 1

    if exact_merges == 0 and fuzzy_merges == 0:
        return skills

    # ── Build remap ─────────────────────────────────────────────────
    components = uf.components()
    remap: dict[str, str] = {}

    for root, members in components.items():
        if len(members) == 1:
            continue
        # Winner = first occurrence in original list
        ranked = sorted(members, key=lambda n: next(
            (i for i, s in enumerate(skills) if s.name == n), float("inf")
        ))
        winner = ranked[0]
        for member in members:
            if member != winner:
                remap[member] = winner

    if not remap:
        return skills

    total = len(remap)
    print(f"[scm.dedup] Deduplicated {total} skill(s)"
          f" ({exact_merges} exact, {fuzzy_merges} fuzzy).", flush=True)

    # ── Apply remap — merge attributes into survivors ───────────────
    deduped: list[Skill] = []
    seen: set[str] = set()
    for s in skills:
        target = remap.get(s.name, s.name)
        if target == s.name:
            if target not in seen:
                seen.add(target)
                deduped.append(s)
        else:
            # Merge this skill's attributes into the survivor
            survivor = skills_by_name.get(target)
            if survivor and survivor.name not in seen:
                if not survivor.description and s.description:
                    survivor.description = s.description
                if s.tags:
                    existing_tags = set(survivor.tags)
                    for t in s.tags:
                        if t not in existing_tags:
                            survivor.tags.append(t)
                seen.add(survivor.name)
                deduped.append(survivor)

    return deduped
