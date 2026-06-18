#!/usr/bin/env bash
# Benchmark SCM against baseline (all skills loaded)
# Usage: bash scripts/benchmark.sh

set -euo pipefail

SCM="$(cd "$(dirname "$0")/.." && pwd)/.venv/bin/scm"

if [ ! -f "$SCM" ]; then
    echo "❌ SCM not installed. Run scripts/install.sh first."
    exit 1
fi

echo "═══════════════════════════════════════════"
echo "  SCM Benchmark — Skill Selection Accuracy"
echo "═══════════════════════════════════════════"
echo ""

# Test queries covering different domains
QUERIES=(
    "deploy application to kubernetes cluster"
    "write unit tests for python project"
    "analyze trading data and generate report"
    "configure database connection"
    "build docker image and push to registry"
    "debug memory leak in production"
    "create api documentation"
    "monitor system performance"
    "backup postgresql database"
    "setup continuous integration pipeline"
)

TOTAL=0
BEST_METHOD=""

echo "Testing ${#QUERIES[@]} queries across different methods..."
echo ""

for query in "${QUERIES[@]}"; do
    echo "▶  \"$query\""
    
    # BM25
    BM25_TIME=$( (time $SCM query "$query" --method bm25 --top 1 --format json) 2>&1 | grep real | awk '{print $2}')
    echo "   BM25:      ${BM25_TIME}"
    
    # Embedding (if available)
    EMB_TIME=$($SCM query "$query" --method embedding --top 1 --format json 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(f\"{d['latency_ms']:.0f}ms\")" 2>/dev/null || echo "N/A")
    echo "   Embedding: ${EMB_TIME}"
    
    # Hybrid
    HYB_TIME=$($SCM query "$query" --method hybrid --top 1 --format json 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(f\"{d['latency_ms']:.0f}ms\")" 2>/dev/null || echo "N/A")
    echo "   Hybrid:    ${HYB_TIME}"
    
    echo ""
    TOTAL=$((TOTAL + 1))
done

echo "═══════════════════════════════════════════"
echo "  Token Savings Estimate"
echo "═══════════════════════════════════════════"

$SCM insights 2>/dev/null || echo "   Run 'scm insights' after some usage"

echo ""
echo "Done! Tested $TOTAL queries."
