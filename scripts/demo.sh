#!/usr/bin/env bash
# Interactive demo of Skill Context Manager
set -euo pipefail

SCM="$(cd "$(dirname "$0")/.." && pwd)/.venv/bin/scm"

if [ ! -f "$SCM" ]; then
    echo "❌ SCM not installed. Run: bash scripts/install.sh"
    exit 1
fi

echo "╔══════════════════════════════════════════════╗"
echo "║  Skill Context Manager — Interactive Demo    ║"
echo "╚══════════════════════════════════════════════╝"
echo ""

echo "STEP 1: Index skills"
echo "────────────────────"
echo "Let's index some skills first..."
echo ""

# Try indexing various skill directories
for dir in "$HOME/.hermes/skills" "$HOME/.claude/skills" ".cursor/skills"; do
    if [ -d "$dir" ]; then
        echo "📂 Found skills in: $dir"
    fi
done
echo ""

echo "Run: scm index --dir ~/.hermes/skills/"
$SCM index --dir "$HOME/.hermes/skills/" 2>/dev/null || echo "   ℹ️  No Hermes skills dir yet"
echo ""

echo "STEP 2: Check stats"
echo "───────────────────"
$SCM stats 2>/dev/null || echo "   No stats yet"
echo ""

echo "STEP 3: Query skills"
echo "────────────────────"
echo "Try some queries:"
echo ""

QUERIES=(
    "deploy application to kubernetes"
    "write and run unit tests"
    "configure postgresql database"
    "build docker container"
    "monitor system health"
)

for q in "${QUERIES[@]}"; do
    echo "▶ scm query \"$q\" --top 2"
    $SCM query "$q" --top 2 2>/dev/null || true
    echo ""
done

echo "STEP 4: Session tracking"
echo "───────────────────────"
echo "▶ scm session start --id demo-1"
$SCM session start --id demo-1 2>/dev/null || echo "   (session system ready)"
echo ""
echo "▶ scm session context --id demo-1"
$SCM session context --id demo-1 2>/dev/null || echo "   (context system ready)"
echo ""

echo "STEP 5: Optimize metadata"
echo "─────────────────────────"
echo "▶ scm optimize --dir ~/.hermes/skills/ --dry-run"
$SCM optimize --dir "$HOME/.hermes/skills/" --dry-run 2>/dev/null || echo "   ℹ️  No skills to optimize"
echo ""

echo "══════════════════════════════════════════════"
echo "  Demo complete!"
echo ""
echo "  Key commands:"
echo "    scm query \"your task\" --top 3"
echo "    scm session start --id my-session"
echo "    scm optimize --dir ./skills/ --dry-run"
echo "    scm insights"
echo "══════════════════════════════════════════════"
