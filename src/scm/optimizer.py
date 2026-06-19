"""Skill metadata optimizer — compress and enhance skill descriptions for token-efficient loading."""

from __future__ import annotations

import os
import re
from collections import Counter
from pathlib import Path

from .models import Skill


class SkillOptimizer:
    """Optimize skill metadata for maximum relevance per token.

    Problems this solves:
    - Long descriptions waste tokens (50-200 tokens per skill)
    - Vague descriptions cause wrong selection
    - Missing keywords reduce BM25 recall
    - Inconsistent naming confuses embedding models
    """

    def __init__(self, min_description_len: int = 20, max_description_len: int = 120):
        self.min_len = min_description_len
        self.max_len = max_description_len

    def optimize_skill(self, skill: Skill) -> Skill:
        """Optimize a single skill's metadata."""
        optimized = Skill(
            name=skill.name,
            description=skill.description,
            body=skill.body,
            path=skill.path,
            category=skill.category,
            tags=list(set(skill.tags + self._extract_tags(skill))),
            token_cost_metadata=skill.token_cost_metadata,
            token_cost_body=skill.token_cost_body,
            use_count=skill.use_count,
            success_rate=skill.success_rate,
            last_used=skill.last_used,
        )

        # Compress description if too long
        if len(optimized.description) > self.max_len:
            optimized.description = self._compress_description(
                optimized.description, self.max_len
            )

        # Expand description if too short or missing
        if len(optimized.description) < self.min_len:
            optimized.description = self._expand_description(optimized)

        # Add action-prefix for better semantic matching
        if not any(
            optimized.description.lower().startswith(prefix)
            for prefix in ["when", "for", "how", "manage", "create", "deploy",
                           "run", "build", "test", "analyze", "monitor", "configure"]
        ):
            optimized.description = self._infer_action_prefix(optimized)

        # Update token cost
        optimized.token_cost_metadata = len(optimized.description) // 4 + len(optimized.name) // 4

        return optimized

    def optimize_directory(self, directory: Path, dry_run: bool = False) -> list[dict]:
        """Optimize all skills in a directory."""
        results = []
        for skill_file in sorted(directory.glob("**/SKILL.md")):
            try:
                skill = Skill.from_skill_file(skill_file)
                optimized = self.optimize_skill(skill)
                if not dry_run:
                    self._write_optimized(skill_file, optimized)
                results.append({
                    "name": skill.name,
                    "before_tokens": skill.token_cost_metadata,
                    "after_tokens": optimized.token_cost_metadata,
                    "before_desc_len": len(skill.description),
                    "after_desc_len": len(optimized.description),
                    "changed": skill.description != optimized.description,
                })
            except Exception as e:
                results.append({
                    "name": skill_file.parent.name,
                    "error": str(e),
                })
        return results

    def _extract_tags(self, skill: Skill) -> list[str]:
        """Extract meaningful keywords from skill body for tags."""
        words = re.findall(r'[A-Z][a-z]+|[a-z]+', skill.body[:500])
        # Count word frequency, skip common words
        stopwords = {'the', 'this', 'that', 'with', 'from', 'your', 'will',
                     'should', 'would', 'could', 'have', 'been', 'their',
                     'which', 'when', 'where', 'what', 'there', 'these'}
        word_counts = Counter(w.lower() for w in words if w.lower() not in stopwords and len(w) > 3)
        return [w for w, _ in word_counts.most_common(5)]

    def _compress_description(self, description: str, max_len: int) -> str:
        """Compress a long description to fit within token budget."""
        # Strategy: remove articles, conjunctions, and redundant phrases
        compressed = description
        replacements = [
            (r'\b(this skill|this tool|the tool|the skill)\b', 'it'),
            (r'\byou can use (this|the)\b', 'use'),
            (r'\bis used to\b', '→'),
            (r'\ballows you to\b', 'enables'),
            (r'\bprovides\b', 'gives'),
            (r'\bin order to\b', 'to'),
            (r'\bas well as\b', '&'),
            (r'\bcapabilities\b', 'features'),
            (r'\bimplementation\b', 'impl'),
            (r'\bconfiguration\b', 'config'),
            (r'\binformation\b', 'info'),
            (r'\bdocumentation\b', 'docs'),
            (r'\butilization\b', 'use'),
            (r'\badditionally,\b', 'also'),
            (r'\bfurthermore,\b', ''),
            (r'\bhowever,\b', 'but'),
            (r'\btherefore,\b', 'so'),
        ]
        for pattern, replacement in replacements:
            compressed = re.sub(pattern, replacement, compressed, flags=re.IGNORECASE)

        if len(compressed) <= max_len:
            return compressed

        # Final: truncate at last sentence boundary within limit
        truncated = compressed[:max_len]
        last_period = truncated.rfind('.')
        if last_period > max_len * 0.7:
            return compressed[:last_period + 1]
        return truncated + "..."

    def _expand_description(self, skill: Skill) -> str:
        """Generate a better description for a skill with missing/inadequate metadata."""
        body_lower = skill.body.lower()

        patterns = {
            "deploy": "Deploy and manage",
            "test": "Run tests for",
            "build": "Build and compile",
            "config": "Configure and manage",
            "monitor": "Monitor and observe",
            "analyze": "Analyze and report on",
            "migrate": "Migrate and transform",
            "backup": "Backup and restore",
            "debug": "Debug and troubleshoot",
        }

        action = "Manage and operate"
        for keyword, phrase in patterns.items():
            if keyword in body_lower:
                action = phrase
                break

        # Use name + tags instead of body excerpt to prevent info leak
        domain = skill.name.replace("-", " ").replace("_", " ").title()
        tags_str = ", ".join(skill.tags[:3]) if skill.tags else ""
        if tags_str:
            return f"{action} {domain} [{tags_str}]"
        return f"{action} {domain}"

    def _infer_action_prefix(self, skill: Skill) -> str:
        """Prepend an action verb for better semantic matching."""
        body_lower = skill.body.lower()

        action_map = {
            "deploy": "Deploy",
            "kubernetes": "Deploy",
            "docker": "Build & deploy",
            "test": "Test & validate",
            "pytest": "Test & validate",
            "build": "Build & compile",
            "compile": "Build & compile",
            "migrate": "Migrate",
            "monitor": "Monitor",
            "debug": "Debug",
            "troubleshoot": "Debug",
            "backup": "Backup",
            "restore": "Backup",
            "create": "Create",
            "generate": "Create",
            "analyze": "Analyze",
            "report": "Analyze",
            "search": "Search",
            "query": "Search",
            "configure": "Configure",
            "setup": "Configure",
            "install": "Configure",
        }

        for keyword, action in action_map.items():
            if keyword in body_lower:
                return f"{action}: {skill.description}"
        return f"Use: {skill.description}"

    def _write_optimized(self, skill_file: Path, skill: Skill):
        """Write optimized metadata back to SKILL.md with backup."""
        content = skill_file.read_text(encoding="utf-8")

        # Build new content
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                frontmatter = parts[1]
                body = parts[2]

                new_fm_lines = []
                updated_desc = False
                for line in frontmatter.strip().split("\n"):
                    if line.startswith("description:"):
                        new_fm_lines.append(f"description: {skill.description}")
                        updated_desc = True
                    elif line.startswith("tags:"):
                        tags_str = ", ".join(skill.tags)
                        new_fm_lines.append(f"tags: [{tags_str}]")
                    else:
                        new_fm_lines.append(line)

                if not updated_desc:
                    new_fm_lines.append(f"description: {skill.description}")

                new_content = "---\n" + "\n".join(new_fm_lines) + f"\n---\n{body}"
        else:
            tags_str = ", ".join(skill.tags)
            new_content = (
                f"---\nname: {skill.name}\ndescription: {skill.description}\n"
                f"tags: [{tags_str}]\n---\n\n{content}"
            )

        # Write atomically: write to temp, replace atomically. If anything
        # fails, the original file is untouched.
        tmp = skill_file.with_name(f".{skill_file.name}.tmp")
        try:
            tmp.write_text(new_content, encoding="utf-8")
            os.replace(tmp, skill_file)
        except OSError:
            # Clean up dangling temp file on failure
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
            raise
