#!/usr/bin/env bash
"""
SCM MCP Wrapper — Launches the Skill Context Manager as an MCP server.

Usage:
    scm-mcp                    # stdio mode (default, for Hermes Agent)
    scm-mcp --http             # HTTP/SSE mode (for OpenCode remote)
    scm-mcp --http --port 8321 # Custom port

Environment:
    SCM_DB_DIR: Override database directory (default: ~/.scm/)
    SCM_VERBOSE: Set to 1 for debug output
"""

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# Activate venv
if [ -f "$PROJECT_DIR/.venv/bin/activate" ]; then
    source "$PROJECT_DIR/.venv/bin/activate"
elif [ -f "$PROJECT_DIR/.venv/bin/python3" ]; then
    PYTHON="$PROJECT_DIR/.venv/bin/python3"
else
    # Try to find scm command
    PYTHON="$(command -v scm 2>/dev/null && which scm 2>/dev/null || echo python3)"
    PYTHON="$(dirname "$PYTHON")/python3"
fi

PYTHON="${PYTHON:-python3}"

export SCM_DB_DIR="${SCM_DB_DIR:-$HOME/.scm}"

if [ "${SCM_VERBOSE:-}" = "1" ]; then
    echo "🚀 SCM MCP Server starting..." >&2
    echo "   Python: $PYTHON" >&2
    echo "   DB: $SCM_DB_DIR" >&2
    echo "   Mode: $([ $# -gt 0 ] && echo \"$*\" || echo \"stdio\")" >&2
fi

exec "$PYTHON" -m scm.mcp_server "$@"
