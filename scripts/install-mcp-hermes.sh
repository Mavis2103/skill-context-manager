#!/usr/bin/env bash
"""
Auto-install SCM MCP server into Hermes Agent config.
Usage: bash scripts/install-mcp-hermes.sh [--dry-run]
"""

set -euo pipefail

DRY_RUN="${1:-}"
if [ "$DRY_RUN" = "--dry-run" ]; then
    DRY_RUN=true
else
    DRY_RUN=false
fi

HERMES_CONFIG="${HOME}/.hermes/config.yaml"
SCM_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SCM_WRAPPER="${SCM_DIR}/scripts/scm-mcp.sh"

echo "📦 Installing SCM MCP into Hermes Agent..."
echo "   Config: $HERMES_CONFIG"
echo "   Wrapper: $SCM_WRAPPER"
echo ""

if [ ! -f "$HERMES_CONFIG" ]; then
    echo "❌ Hermes config not found at $HERMES_CONFIG"
    echo "   Create one with: hermes init"
    exit 1
fi

# Check if already installed
if grep -q "scm:" "$HERMES_CONFIG" 2>/dev/null; then
    echo "✅ SCM MCP already configured in Hermes config."
    echo "   Run \`hermes mcp test scm\` to verify, or edit $HERMES_CONFIG to update."
    exit 0
fi

# Append config block
MCP_BLOCK=$(cat <<EOF

# SCM — Skill Context Manager (auto-installed)
# Smart skill selection for AI agents
mcp_servers:
  scm:
    command: bash
    args:
    - ${SCM_WRAPPER}
    allowed_tools:
      - skill_query
      - skill_session_start
      - skill_session_use
      - skill_session_context
      - skill_session_end
      - skill_feedback
      - skill_feedback_stats
      - skill_stats
      - skill_insights
    env:
      SCM_DB_DIR: ${HOME}/.scm
EOF
)

if $DRY_RUN; then
    echo "📋 Would add to $HERMES_CONFIG:"
    echo "$MCP_BLOCK"
    echo ""
    echo "   Use without --dry-run to apply."
else
    echo "$MCP_BLOCK" >> "$HERMES_CONFIG"
    echo "✅ Config added to $HERMES_CONFIG"
    echo ""
    echo "Next steps:"
    echo "   1. hermes mcp test scm    # Test connection"
    echo "   2. hermes chat             # Start session"
    echo "   3. /reload-mcp             # Load tools (in-session)"
    echo ""
    echo "Or start fresh:"
    echo "   hermes new --skill native-mcp"
fi
