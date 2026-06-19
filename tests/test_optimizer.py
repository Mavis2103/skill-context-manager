"""Tests for Skill Context Manager — metadata optimizer."""

import tempfile
from pathlib import Path


from scm.models import Skill
from scm.optimizer import SkillOptimizer


class TestSkillOptimizer:
    def test_optimize_compresses_long_description(self):
        skill = Skill(
            name="test",
            description="This skill is used to deploy applications to a Kubernetes cluster using kubectl and Helm charts for orchestration",
            body="deploy kubernetes content here",
            tags=["k8s"],
        )
        opt = SkillOptimizer(max_description_len=60)
        result = opt.optimize_skill(skill)
        # Should be compressed via phrase replacement and/or truncation
        assert len(result.description) <= 80  # Allow some tolerance
        assert result.token_cost_metadata > 0

    def test_optimize_expands_short_description(self):
        skill = Skill(
            name="k8s-deploy",
            description="",
            body="How to deploy to kubernetes:\n1. kubectl apply\n2. helm install",
            tags=[],
        )
        opt = SkillOptimizer(min_description_len=20)
        result = opt.optimize_skill(skill)
        assert len(result.description) >= 20
        assert "Deploy" in result.description  # Action prefix added

    def test_optimize_keeps_good_description(self):
        skill = Skill(
            name="test-skill",
            description="Deploy applications to Kubernetes",
            body="deploy content",
            tags=[],
        )
        opt = SkillOptimizer()
        result = opt.optimize_skill(skill)
        assert "Deploy" in result.description
        assert result.token_cost_metadata > 0

    def test_compress_description(self):
        """_compress_description should shorten common wordy phrases."""
        opt = SkillOptimizer()
        # Use a description well over default max_len (120) so compression triggers
        desc = "This tool allows you to deploy in order to configure everything. " * 5
        result = opt._compress_description(desc, 500)
        assert len(result) <= len(desc)
        assert "in order to" not in result

    def test_expand_description_no_body_leak(self):
        """Description should NOT contain raw body text (security)."""
        skill = Skill(
            name="secret-skill",
            description="",
            body=("API_KEY=abc123... Secret internal url: "
                  "https://internal.corp SuperSecretToken=xyz789"),
            tags=["secret"],
        )
        opt = SkillOptimizer()
        result = opt.optimize_skill(skill)
        # Should not contain sensitive body excerpts
        assert "API_KEY" not in result.description
        assert "abc123" not in result.description
        assert "SuperSecretToken" not in result.description
        assert "internal.corp" not in result.description

    def test_optimize_directory_dry_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            s1 = base / "test-skill" / "SKILL.md"
            s1.parent.mkdir()
            s1.write_text("---\nname: test-skill\ndescription: A very long description that should be compressed for token efficiency\n---\nBody")
            opt = SkillOptimizer(max_description_len=40)
            results = opt.optimize_directory(base, dry_run=True)
            assert len(results) == 1
            assert results[0]["name"] == "test-skill"
            assert results[0]["changed"] is True
            # Verify file not modified
            content = s1.read_text()
            assert "very long description" in content  # Original preserved

    def test_optimize_directory_apply(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            s1 = base / "test-skill" / "SKILL.md"
            s1.parent.mkdir()
            s1.write_text("---\nname: test-skill\ndescription: Long desc here that needs compression\n---\nBody")
            opt = SkillOptimizer(max_description_len=30)
            results = opt.optimize_directory(base, dry_run=False)
            assert results[0]["changed"] is True
            # Verify file WAS modified
            content = s1.read_text()
            assert "Long desc here that needs compression" not in content

    def test_optimize_directory_with_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            # Create a file that will fail (binary content)
            bad = base / "bad-skill" / "SKILL.md"
            bad.parent.mkdir()
            bad.write_bytes(b"---\nname: bad\n---\n\x80\x81\x82")
            opt = SkillOptimizer()
            results = opt.optimize_directory(base, dry_run=True)
            assert len(results) >= 1

    def test_infer_action_prefix(self):
        opt = SkillOptimizer()
        skill = Skill(name="my-app", description="the application", body="deploy the app to production")
        result = opt._infer_action_prefix(skill)
        assert result.startswith("Deploy")

        skill2 = Skill(name="test", description="code tests", body="run pytest tests")
        result2 = opt._infer_action_prefix(skill2)
        assert "Test" in result2 or "test" in result2
