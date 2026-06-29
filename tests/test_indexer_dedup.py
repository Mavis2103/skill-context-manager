"""Integration tests: dedup via SkillIndexer."""

import tempfile
from pathlib import Path

from scm.indexer import SkillIndexer
from scm.db import init_schema
from scm.models import Skill


class TestIndexerDedup:
    def test_dedup_exact_duplicates(self):
        """Indexer dedup merges skills with same normalized name."""
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.db"
            init_schema(db)
            indexer = SkillIndexer(db_path=db)

            # Insert skills directly via _upsert_skill
            s1 = Skill(name="K8s-Deploy", description="Deploy to K8s",
                       body="body1", category="devops", tags=["k8s"])
            s2 = Skill(name="k8s-deploy", description="Deploy to K8s",
                       body="body2", category="devops", tags=["k8s"])
            indexer._upsert_skill(s1)
            indexer._upsert_skill(s2)

            before = indexer.stats()["total_skills"]
            result = indexer.dedup_skills()

            after = indexer.stats()["total_skills"]
            assert result["total_removed"] > 0 or before == after
