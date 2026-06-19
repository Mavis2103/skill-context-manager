"""Data models for Skill Context Manager."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from .db import utcnow


@dataclass
class Skill:
    """Representation of a single agent skill."""
    name: str
    description: str
    body: str = ""
    path: Optional[Path] = None
    category: str = "uncategorized"
    tags: list[str] = field(default_factory=list)
    token_cost_metadata: int = 0
    token_cost_body: int = 0
    last_used: Optional[str] = None
    use_count: int = 0
    success_rate: float = 0.0
    embedding: Optional[list[float]] = None

    @property
    def metadata_str(self) -> str:
        """Return token-efficient metadata string."""
        tags_str = f" [{', '.join(self.tags[:3])}]" if self.tags else ""
        return f"{self.name}: {self.description}{tags_str}"

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if k != "embedding"}

    @classmethod
    def row_to_skill(cls, row) -> "Skill":
        """Convert a sqlite3.Row (or dict-like) to a Skill object."""
        # sqlite3.Row uses [] access, not .get()
        body_val = row["body"]
        if not body_val:
            try:
                body_val = row["body_snippet"]
            except (IndexError, KeyError):
                body_val = ""
        try:
            path_val = Path(row["path"]) if row["path"] else None
        except (IndexError, KeyError):
            path_val = None
        try:
            cat = row["category"]
        except (IndexError, KeyError):
            cat = "uncategorized"
        return cls(
            name=row["name"], description=row["description"], body=body_val,
            path=path_val,
            category=cat,
            tags=json.loads(row["tags"]) if row["tags"] else [],
            token_cost_metadata=row["token_cost_meta"],
            token_cost_body=row["token_cost_body"],
            use_count=row["use_count"],
            success_rate=row["success_rate"],
            last_used=row["last_used"] if "last_used" in row.keys() else None,
        )

    @classmethod
    def _estimate_tokens(cls, text: str) -> int:
        """Estimate token count, using tiktoken if available."""
        if not text:
            return 0
        try:
            import tiktoken
            enc = tiktoken.get_encoding("cl100k_base")
            return len(enc.encode(text))
        except ImportError:
            # Fallback: ~4 chars per token for English
            return len(text) // 4

    @classmethod
    def from_skill_file(cls, path: Path) -> "Skill":
        """Parse a SKILL.md file into a Skill object using proper YAML parsing."""
        content = path.read_text(encoding="utf-8")
        name = path.parent.name
        description = ""
        body = content
        tags = []
        category = "uncategorized"

        # Parse YAML frontmatter using proper YAML parser
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                frontmatter_text = parts[1]
                body = parts[2].strip()
                try:
                    import yaml
                    fm = yaml.safe_load(frontmatter_text)
                    if isinstance(fm, dict):
                        name = str(fm.get("name", name))
                        description = str(fm.get("description", ""))
                        category = str(fm.get("category", category))
                        raw_tags = fm.get("tags", [])
                        if isinstance(raw_tags, list):
                            tags = [str(t).strip() for t in raw_tags]
                        elif isinstance(raw_tags, str):
                            tags = [t.strip() for t in raw_tags.strip("[]\"").split(",")]
                except (ImportError, Exception):
                    # Fallback: naive parser if yaml not installed or parsing fails
                    for line in frontmatter_text.strip().split("\n"):
                        if ":" in line:
                            key, _, val = line.partition(":")
                            val = val.strip()
                            if key.strip() == "name":
                                name = val
                            elif key.strip() == "description":
                                description = val
                            elif key.strip() == "category":
                                category = val
                            elif key.strip() == "tags":
                                tags = [t.strip() for t in val.strip("[]\"").split(",")]

        # Estimate token costs
        token_cost_metadata = cls._estimate_tokens(f"{name} {description}")
        token_cost_body = cls._estimate_tokens(body)

        return cls(
            name=name,
            description=description,
            body=body,
            path=path,
            category=category,
            tags=tags,
            token_cost_metadata=token_cost_metadata,
            token_cost_body=token_cost_body,
        )


@dataclass
class QueryResult:
    """Result of a skill query."""
    skill: Skill
    score: float
    retrieval_method: str  # "embedding", "bm25", "hybrid", "reranked"


@dataclass
class SessionState:
    """Tracks skill usage within a session."""
    session_id: str
    started_at: str = ""
    skills_used: list[dict] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    context: dict = field(default_factory=dict)

    def record_skill_use(self, skill_name: str, query: str = "", success: Optional[bool] = None):
        self.skills_used.append({
            "skill": skill_name,
            "query": query,
            "timestamp": utcnow().isoformat(),
            "success": success,
        })

    def get_recent_skills(self, n: int = 5) -> list[str]:
        return [s["skill"] for s in self.skills_used[-n:]]

    def to_json(self) -> str:
        return json.dumps(asdict(self))


@dataclass
class FeedbackRecord:
    """Record of skill usage feedback."""
    query: str
    skill_name: str
    success: bool
    latency_ms: int = 0
    user_rating: Optional[int] = None  # 1-5
    timestamp: str = field(default_factory=lambda: utcnow().isoformat())
