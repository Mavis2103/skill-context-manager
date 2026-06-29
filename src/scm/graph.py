"""Knowledge graph for skill relationships — co-occurrence + content + feedback.

Builds a directed weighted graph between skills from multiple data sources,
then uses Personalized PageRank to boost query results based on graph proximity
to recently-used or already-matched skills.
"""

from __future__ import annotations

import json
import logging
import math
import re
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Optional

logger = logging.getLogger("scm.graph")


class SkillGraph:
    """Directed weighted graph of skill relationships.

    Edge types:
    - co_occur: PPMI-weighted undirected (from session usage)
    - content: similarity-weighted undirected (category + tags overlap)
    - feedback: success-weighted undirected (from agent feedback)
    - text_overlap: Jaccard shingle similarity on name+description
    - path_shared: skills sharing the same parent directory

    At query time, Personalized PageRank (PPR) is run from seed skills
    (session history, query-matched skills) to find related skills.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path
        # Adjacency: {skill_name: [(neighbor, weight, edge_type), ...]}
        self._graph: dict[str, list[tuple[str, float, str]]] = defaultdict(list)
        self._loaded = False

    def _conn(self):
        from .db import connect
        return connect(self.db_path)

    def build_from_db(self):
        """Build graph from all available data sources in DB."""
        with self._conn() as conn:
            self._build_cooccurrence(conn)
            self._build_content(conn)
            self._build_feedback(conn)
            self._build_text_overlap(conn)
            self._build_path_shared(conn)
        self._loaded = True
        logger.info("Graph built: %d nodes, %d edges",
                     len(self._graph),
                     sum(len(v) for v in self._graph.values()) // 2)

    def _build_cooccurrence(self, conn):
        """PPMI-weighted co-occurrence from session_skills table.

        PPMI = max(0, log(P(a,b) / (P(a) * P(b))))
        Filters out spurious co-occurrence from high-frequency skills.
        """
        rows = conn.execute("""
            SELECT s1.skill_name as a, s2.skill_name as b, COUNT(*) as cnt
            FROM session_skills s1
            JOIN session_skills s2 ON s1.session_id = s2.session_id
                AND s1.skill_name < s2.skill_name
            GROUP BY s1.skill_name, s2.skill_name
        """).fetchall()

        if not rows:
            logger.debug("No session co-occurrence data")
            return

        total = sum(r["cnt"] for r in rows)

        # Individual frequencies
        freq = defaultdict(int)
        for r in rows:
            freq[r["a"]] += r["cnt"]
            freq[r["b"]] += r["cnt"]

        edge_count = 0
        for r in rows:
            p_ab = r["cnt"] / total
            p_a = freq[r["a"]] / total
            p_b = freq[r["b"]] / total
            ppmi = max(0.0, math.log2(p_ab / (p_a * p_b + 1e-10)))
            if ppmi > 0:
                self._graph[r["a"]].append((r["b"], round(ppmi, 4), "co_occur"))
                self._graph[r["b"]].append((r["a"], round(ppmi, 4), "co_occur"))
                edge_count += 1

        logger.debug("Co-occurrence edges: %d", edge_count)

    def _build_content(self, conn):
        """Content similarity edges from category + tags overlap.

        Rules:
        - Same category (not uncategorized): +0.3
        - Tag Jaccard similarity: +0.4 × Jaccard
        """
        rows = conn.execute(
            "SELECT name, category, tags FROM skills"
        ).fetchall()

        skills = {
            r["name"]: {
                "category": r["category"],
                "tags": set(json.loads(r["tags"] or "[]")),
            }
            for r in rows
        }
        names = list(skills.keys())

        edge_count = 0
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                a, b = names[i], names[j]
                sim = 0.0

                # Category match (same category, not uncategorized)
                cat_a = skills[a]["category"]
                cat_b = skills[b]["category"]
                if cat_a == cat_b and cat_a not in ("uncategorized", ""):
                    sim += 0.3

                # Tag Jaccard
                tags_a = skills[a]["tags"]
                tags_b = skills[b]["tags"]
                if tags_a and tags_b:
                    intersection = len(tags_a & tags_b)
                    union = len(tags_a | tags_b)
                    if union > 0:
                        jaccard = intersection / union
                        sim += 0.4 * jaccard

                if sim > 0:
                    self._graph[a].append((b, round(sim, 4), "content"))
                    self._graph[b].append((a, round(sim, 4), "content"))
                    edge_count += 1

        logger.debug("Content similarity edges: %d", edge_count)

    def _build_feedback(self, conn):
        """Feedback co-success edges from skill_weights.

        If two skills both have above-average success rates,
        they get a weak positive edge (0.1).
        """
        rows = conn.execute("""
            SELECT skill_name, successes, failures
            FROM skill_weights WHERE successes + failures > 0
        """).fetchall()

        if len(rows) < 2:
            return

        skills_data = {}
        for r in rows:
            total = r["successes"] + r["failures"]
            rate = r["successes"] / total if total > 0 else 0.5
            skills_data[r["skill_name"]] = rate

        avg_rate = sum(skills_data.values()) / len(skills_data)
        # Edge if both above average
        edge_count = 0
        names = list(skills_data.keys())
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                a, b = names[i], names[j]
                if skills_data[a] > avg_rate and skills_data[b] > avg_rate:
                    weight = 0.1 * (skills_data[a] + skills_data[b]) / 2.0
                    self._graph[a].append((b, round(weight, 4), "feedback"))
                    self._graph[b].append((a, round(weight, 4), "feedback"))
                    edge_count += 1

        if edge_count:
            logger.debug("Feedback co-success edges: %d", edge_count)

    # ── Text overlap from graphify ───────────────────────────────────

    @staticmethod
    def _shingles(text: str, k: int = 3) -> set[str]:
        """Normalize text → k-grams (mượn từ graphify dedup.py)."""
        text = unicodedata.normalize("NFKC", text).casefold()
        text = re.sub(r"[\W_]+", " ", text).strip()
        if len(text) < k:
            return {text} if text else set()
        return {text[i:i + k] for i in range(len(text) - k + 1)}

    def _build_text_overlap(self, conn):
        """Text overlap edges via Jaccard shingle similarity.

        Edge = Jaccard(name+description shingles) ≥ 0.3
        Zero local models — pure string algorithm.
        """
        rows = conn.execute(
            "SELECT name, description FROM skills"
        ).fetchall()

        skills = {
            r["name"]: self._shingles(f"{r['name']} {r['description'] or ''}")
            for r in rows
        }
        names = list(skills.keys())

        edge_count = 0
        for i in range(len(names)):
            s1 = skills[names[i]]
            if not s1:
                continue
            for j in range(i + 1, len(names)):
                s2 = skills[names[j]]
                if not s2:
                    continue
                intersection = len(s1 & s2)
                union = len(s1 | s2)
                if union > 0 and intersection / union >= 0.3:
                    sim = round(intersection / union, 4)
                    self._graph[names[i]].append((names[j], sim, "text_overlap"))
                    self._graph[names[j]].append((names[i], sim, "text_overlap"))
                    edge_count += 1

        if edge_count:
            logger.debug("Text overlap edges: %d", edge_count)

    # ── Path shared edges ────────────────────────────────────────────

    def _build_path_shared(self, conn):
        """Path-based edges: skills sharing the same directory.

        - Same parent directory → edge weight 0.4
        - Same grandparent (project root) → edge weight 0.2
        """
        rows = conn.execute(
            "SELECT name, path FROM skills WHERE path IS NOT NULL AND path != ''"
        ).fetchall()

        # Map directory → skill names
        parent_map: dict[str, list[str]] = defaultdict(list)
        grandparent_map: dict[str, list[str]] = defaultdict(list)

        for r in rows:
            p = Path(r["path"])
            if len(p.parts) >= 2:
                parent = str(p.parent)
                parent_map[parent].append(r["name"])
            if len(p.parts) >= 3:
                grandparent = str(p.parent.parent)
                grandparent_map[grandparent].append(r["name"])

        edge_count = 0
        # Same parent → 0.4
        for _, names in parent_map.items():
            if len(names) >= 2:
                for i in range(len(names)):
                    for j in range(i + 1, len(names)):
                        # Check not already connected with higher weight
                        existing = [w for n, w, t in self._graph[names[i]]
                                    if n == names[j] and t in ("co_occur", "text_overlap")]
                        if existing and max(existing) >= 0.4:
                            continue
                        self._graph[names[i]].append((names[j], 0.4, "path_shared"))
                        self._graph[names[j]].append((names[i], 0.4, "path_shared"))
                        edge_count += 1

        # Same grandparent → 0.2 (only if not already connected via parent)
        for _, names in grandparent_map.items():
            if len(names) >= 2:
                for i in range(len(names)):
                    for j in range(i + 1, len(names)):
                        existing = [n for n, _, _ in self._graph[names[i]]]
                        if names[j] in existing:
                            continue
                        self._graph[names[i]].append((names[j], 0.2, "path_shared"))
                        self._graph[names[j]].append((names[i], 0.2, "path_shared"))
                        edge_count += 1

        if edge_count:
            logger.debug("Path shared edges: %d", edge_count)

    def get_neighbors(self, skill_name: str,
                      edge_types: Optional[set[str]] = None) -> list[tuple[str, float, str]]:
        """Get neighbors with optional edge type filter."""
        neighbors = self._graph.get(skill_name, [])
        if edge_types:
            return [(n, w, t) for n, w, t in neighbors if t in edge_types]
        return sorted(neighbors, key=lambda x: -x[1])

    def ppr(self, seed_skills: list[str], alpha: float = 0.85,
            max_iter: int = 20, tol: float = 1e-6) -> dict[str, float]:
        """Personalized PageRank from seed skills.

        Ranks all skills by their proximity to the seed set.
        Higher score = more relevant to the query context.

        Args:
            seed_skills: Skills to start the random walk from.
            alpha: Teleport probability (default 0.85).
            max_iter: Maximum PPR iterations.
            tol: Convergence tolerance.

        Returns:
            {skill_name: ppr_score} sorted descending.
        """
        if not self._loaded:
            self.build_from_db()

        if not seed_skills:
            return {}

        if not self._graph:
            return {}

        # Validate seed skills exist in graph
        valid_seeds = [s for s in seed_skills if s in self._graph]
        if not valid_seeds:
            # Fall back to empty PPR
            return {}

        # Initialize scores — only nodes in the graph
        scores = {node: 0.0 for node in self._graph}

        # Teleport vector: uniform over valid seeds
        teleport = {s: 1.0 / len(valid_seeds) for s in valid_seeds}

        # PPR iteration
        for iteration in range(max_iter):
            prev = scores.copy()
            max_change = 0.0

            for node in self._graph:
                rank = (1.0 - alpha) * teleport.get(node, 0.0)

                # Add contributions from in-links
                for neighbor, weight, _ in self._graph[node]:
                    # Normalize: outbound weight / total outbound weight of neighbor
                    out_total = sum(w for _, w, _ in self._graph[neighbor])
                    if out_total > 0:
                        rank += alpha * prev.get(neighbor, 0.0) * (weight / out_total)

                scores[node] = rank
                change = abs(scores[node] - prev.get(node, 0.0))
                if change > max_change:
                    max_change = change

            if max_change < tol:
                logger.debug("PPR converged in %d iterations", iteration + 1)
                break

        # Sort descending and return
        return dict(sorted(scores.items(), key=lambda x: -x[1]))

    def get_stats(self) -> dict:
        """Return graph statistics."""
        if not self._loaded:
            self.build_from_db()

        node_count = len(self._graph)
        edge_count = sum(len(v) for v in self._graph.values()) // 2

        edge_types = defaultdict(int)
        for neighbors in self._graph.values():
            for _, _, t in neighbors:
                edge_types[t] += 1

        return {
            "nodes": node_count,
            "edges": edge_count,
            "edge_types": dict(edge_types),
            "loaded": self._loaded,
        }
