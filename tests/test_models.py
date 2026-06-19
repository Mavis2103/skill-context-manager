"""Tests for Skill Context Manager — core data models."""

import json
import tempfile
from pathlib import Path


from scm.models import Skill, QueryResult, SessionState, FeedbackRecord


class TestSkill:
    def test_from_skill_file_with_frontmatter(self):
        """Parse a SKILL.md with full YAML frontmatter."""
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = Path(tmp) / "my-test-skill"
            skill_dir.mkdir()
            skill_file = skill_dir / "SKILL.md"
            skill_file.write_text("""\
---
name: my-test-skill
description: Test skill description
category: testing
tags: [python, test, ci]
---

# My Skill
This is the body content.
""")

            skill = Skill.from_skill_file(skill_file)
            assert skill.name == "my-test-skill"
            assert skill.description == "Test skill description"
            assert skill.category == "testing"
            assert skill.tags == ["python", "test", "ci"]
            assert "body content" in skill.body
            assert skill.token_cost_metadata > 0
            assert skill.token_cost_body > 0

    def test_from_skill_file_no_frontmatter(self):
        """Parse a SKILL.md without frontmatter — uses directory name."""
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = Path(tmp) / "no-fm-skill"
            skill_dir.mkdir()
            skill_file = skill_dir / "SKILL.md"
            skill_file.write_text("Just body text with no frontmatter.")

            skill = Skill.from_skill_file(skill_file)
            assert skill.name == "no-fm-skill"
            assert skill.description == ""
            assert skill.category == "uncategorized"
            assert skill.tags == []

    def test_from_skill_file_empty_description(self):
        """Handle frontmatter with missing description field."""
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = Path(tmp) / "empty-desc"
            skill_dir.mkdir()
            skill_file = skill_dir / "SKILL.md"
            skill_file.write_text("""\
---
name: empty-desc
tags: [test]
---

Body text
""")

            skill = Skill.from_skill_file(skill_file)
            assert skill.name == "empty-desc"
            assert skill.description == ""

    def test_from_skill_file_yaml_edge_cases(self):
        """Handle YAML with special chars, multi-line."""
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = Path(tmp) / "yaml-edge"
            skill_dir.mkdir()
            skill_file = skill_dir / "SKILL.md"
            skill_file.write_text("""\
---
name: yaml-edge
description: "Description with: colons and #hashes"
tags: [tag:one, tag-two, tag_three]
---

Body
""")
            skill = Skill.from_skill_file(skill_file)
            assert skill.name == "yaml-edge"
            assert "colons" in skill.description
            assert len(skill.tags) == 3

    def test_from_skill_file_unquoted_colon_in_description(self):
        """Fall back to naive parser when YAML fails on unquoted colon."""
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = Path(tmp) / "confluence-gen"
            skill_dir.mkdir()
            skill_file = skill_dir / "SKILL.md"
            skill_file.write_text("""\
---
name: confluence-gen
description: a skill whenever the user wants to: create a Confluence page, export to PDF
---

Body text
""")
            skill = Skill.from_skill_file(skill_file)
            assert skill.name == "confluence-gen"
            assert "create a Confluence page" in skill.description
            assert "export to PDF" in skill.description

    def test_metadata_str(self):
        """metadata_str produces token-efficient output."""
        skill = Skill(
            name="test-skill",
            description="Does X and Y",
            tags=["tag1", "tag2", "tag3"],
        )
        result = skill.metadata_str
        assert "test-skill" in result
        assert "Does X and Y" in result
        assert "tag1" in result

    def test_metadata_str_no_tags(self):
        skill = Skill(name="test", description="test desc")
        assert skill.metadata_str == "test: test desc"

    def test_to_dict_excludes_embedding(self):
        skill = Skill(name="test", description="test", embedding=[0.1, 0.2])
        d = skill.to_dict()
        assert "embedding" not in d
        assert d["name"] == "test"


class TestQueryResult:
    def test_create(self):
        skill = Skill(name="test", description="test")
        qr = QueryResult(skill=skill, score=0.85, retrieval_method="bm25")
        assert qr.skill.name == "test"
        assert qr.score == 0.85
        assert qr.retrieval_method == "bm25"


class TestSessionState:
    def test_record_skill_use(self):
        s = SessionState(session_id="sess-1", started_at="2025-01-01")
        s.record_skill_use("skill-a", "query x", True)
        s.record_skill_use("skill-b", "query y", False)
        assert len(s.skills_used) == 2
        assert s.skills_used[0]["skill"] == "skill-a"
        assert s.skills_used[1]["success"] is False

    def test_get_recent_skills(self):
        s = SessionState(session_id="sess-1")
        for i in range(10):
            s.record_skill_use(f"skill-{i}")
        recent = s.get_recent_skills(n=3)
        assert recent == ["skill-7", "skill-8", "skill-9"]

    def test_to_json(self):
        s = SessionState(session_id="sess-1")
        s.record_skill_use("skill-a")
        j = json.loads(s.to_json())
        assert j["session_id"] == "sess-1"
        assert len(j["skills_used"]) == 1


class TestFeedbackRecord:
    def test_create(self):
        fr = FeedbackRecord(query="deploy", skill_name="k8s", success=True)
        assert fr.query == "deploy"
        assert fr.skill_name == "k8s"
        assert fr.success is True
        assert fr.timestamp is not None

    def test_create_with_rating(self):
        fr = FeedbackRecord(query="test", skill_name="pytest", success=True, user_rating=4)
        assert fr.user_rating == 4
        assert fr.latency_ms == 0  # default
