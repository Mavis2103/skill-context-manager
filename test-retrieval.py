"""Comprehensive SCM retrieval test suite — complex prompts against all methods."""
import subprocess
import time
import sys
from pathlib import Path

SCM_DIR = Path.home() / "Workspaces" / "skill-context-manager"

# ── 15 complex test prompts covering all domains ──────────────────────────
TEST_PROMPTS = [
    # BLOCKCHAIN (3)
    (
        "Cardano node",
        "how to set up a Cardano node with Dolos and Oura for mainnet data pipeline",
        ["cardano-data-infrastructure", "oura-v2-build-pipeline"],
    ),
    (
        "DeFi lending",
        "monitor Cardano DeFi lending positions and get alerts when liquidation threshold is near",
        ["dano-loan-monitor", "cardano-data-infrastructure"],
    ),
    (
        "Whale tracking",
        "build a Cardano transaction monitoring pipeline that detects large whale movements",
        ["cardano-data-infrastructure", "oura-v2-build-pipeline"],
    ),
    # DEVOPS (3)
    (
        "Docker + Postgres",
        "deploy a Docker container with PostgreSQL and set up automated daily backups",
        ["docker-setup", "container-networking", "cron-monitoring"],
    ),
    (
        "Monitoring stack",
        "set up Prometheus and Grafana monitoring for services with alertmanager and pagerduty",
        ["cron-monitoring", "docker-setup"],
    ),
    (
        "CI/CD pipeline",
        "configure a CI/CD pipeline with GitHub Actions for Python package build, test and deploy to PyPI",
        ["python-package-release", "software-release"],
    ),
    # SOFTWARE DEV (3)
    (
        "Code review",
        "review a pull request for security vulnerabilities, code quality, and add meaningful comments",
        ["code-review-workflow"],
    ),
    (
        "TDD feature",
        "implement a new feature following test-driven development with pytest fixtures",
        ["test-driven-development"],
    ),
    (
        "Debug C extension",
        "debug a segmentation fault in a Python C extension module using gdb and valgrind",
        ["systematic-debugging"],
    ),
    # ML (3)
    (
        "Fine-tune LLM",
        "fine-tune Llama 3.2 3B model on custom instruction dataset using QLoRA on Google Colab T4 GPU",
        ["llm-fine-tuning", "llm-finetuning"],
    ),
    (
        "LLM benchmark",
        "benchmark a language model on MMLU and GSM8K datasets using lm-eval-harness",
        ["evaluating-llms-harness"],
    ),
    (
        "vLLM deploy",
        "deploy a production vLLM server with OpenAI-compatible API and quantization",
        ["serving-llms-vllm"],
    ),
    # EDGE CASES (3)
    (
        "Random chars",
        "x y z q w e r t a b c d f g h i j k l m n o p",
        [],
    ),
    (
        "Single letter",
        "a",
        [],
    ),
    (
        "Stop words only",
        "how do i what is the best way to",
        [],
    ),
]


def run_query(query: str, method: str = "rrf", top: int = 5):
    """Run SCM query and return parsed results."""
    cmd = [
        "uv", "run", "scm", "query", query,
        "--top", str(top),
        "--method", method,
        "--no-rerank",
        "--format", "json",
    ]
    start = time.time()
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=300,
        cwd=str(SCM_DIR),
    )
    elapsed = time.time() - start
    if result.returncode != 0:
        return {"error": result.stderr[:500], "elapsed": elapsed}

    import json as j
    try:
        data = j.loads(result.stdout)
        if isinstance(data, dict):
            return {"results": data.get("results", []), "elapsed": elapsed}
        return {"results": data if isinstance(data, list) else [], "elapsed": elapsed}
    except (j.JSONDecodeError, KeyError) as e:
        return {"error": f"Parse failed: {e}\n{result.stdout[:500]}", "elapsed": elapsed}


def score_results(results: list[dict], expected: list[str]) -> dict:
    """Score how well the results match expected skills."""
    found_names = [r.get("name", "") for r in results]
    matches = [e for e in expected if e in found_names]
    top1_match = expected and found_names and found_names[0] in expected
    return {
        "found_count": len(found_names),
        "expected_in_top": matches,
        "top1_correct": top1_match,
        "precision": len(matches) / max(len(found_names), 1),
        "recall": len(matches) / max(len(expected), 1),
    }


def print_bar(pct: float, width: int = 15) -> str:
    filled = int(pct * width)
    return "█" * filled + "░" * (width - filled)


def main():
    print("=" * 72)
    print("  SCM RETRIEVAL TEST SUITE — v0.7.0 (BGE + RRF)")
    print("=" * 72)
    print(f"\n  {len(TEST_PROMPTS)} prompts x 3 methods (rrf, embedding, bm25) = {len(TEST_PROMPTS) * 3} queries")
    print()

    # Index first
    print("  Indexing skills...")
    subprocess.run(
        ["uv", "run", "scm", "index", "--all"],
        capture_output=True, cwd=str(SCM_DIR), timeout=120,
    )

    total_queries = 0
    passed = 0
    failed = 0

    for prompt_id, (domain, query, expected) in enumerate(TEST_PROMPTS, 1):
        print(f"\n{'─' * 72}")
        print(f"  [{prompt_id}/{len(TEST_PROMPTS)}] {domain}")
        print(f"  Q: {query}")
        print(f"  Expected: {expected or '(any — edge case)'}")
        print()

        for method in ["rrf", "embedding", "bm25"]:
            total_queries += 1
            result = run_query(query, method=method)

            if "error" in result:
                print(f"    ❌ {method:10s} ERROR: {result['error'][:80]}")
                failed += 1
                continue

            results = result.get("results", [])
            elapsed = result.get("elapsed", 0)
            scoring = score_results(results, expected)

            # Build status
            if expected:
                status = "✅" if scoring["top1_correct"] else "⚠️"
                if not scoring["expected_in_top"]:
                    status = "❌"
            else:
                status = "💡"

            # Show top 3 results
            names = [r.get("name", "?") for r in results[:3]]
            scores = [r.get("score", 0) for r in results[:3]]

            result_lines = []
            for n, s in zip(names, scores):
                bar = print_bar(min(abs(s) / 5.0, 1.0))
                result_lines.append(f"  {n:35s} {bar} {s:.3f}")

            top1_str = ""
            if scoring["expected_in_top"]:
                top1_str = f" ✓ found {', '.join(scoring['expected_in_top'])}"

            print(f"    {status} {method:10s} {elapsed:5.1f}s  {top1_str}")
            for line in result_lines:
                print(f"           {line}")

            if status in ("✅", "💡"):
                passed += 1

        # Score summary per prompt
        if expected:
            all_methods = []
            for method in ["rrf", "embedding", "bm25"]:
                result = run_query(query, method=method)
                if "error" not in result:
                    scoring = score_results(result.get("results", []), expected)
                    all_methods.append(f"{method}={scoring['expected_in_top'] or '∅'}")
            print(f"   └─ Results: {' | '.join(all_methods)}")

    print()
    print("=" * 72)
    print(f"  SUMMARY: {total_queries} queries | {passed} passed | {failed} failed")
    print(f"  Success rate: {passed}/{total_queries} ({passed*100//max(total_queries,1)}%)")
    print("=" * 72)


if __name__ == "__main__":
    main()
