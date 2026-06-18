#!/usr/bin/env bash
# Auto-install SCM MCP server into OpenCode config.
# Usage: bash scripts/install-mcp-opencode.sh [--dry-run]

set -euo pipefail

DRY_RUN="${1:-}"
if [ "$DRY_RUN" = "--dry-run" ]; then
    DRY_RUN=true
else
    DRY_RUN=false
fi

OPENCODE_CONFIG="${HOME}/.config/opencode/opencode.json"
SCM_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SCM_WRAPPER="${SCM_DIR}/scripts/scm-mcp.sh"

echo "📦 Installing SCM MCP into OpenCode..."
echo "   Config: $OPENCODE_CONFIG"
echo "   Wrapper: $SCM_WRAPPER"
echo ""

# Find/create config
if [ ! -f "$OPENCODE_CONFIG" ]; then
    echo "📝 OpenCode config not found, will create..."
    mkdir -p "$(dirname "$OPENCODE_CONFIG")"
fi

# Check if existing config has mcp section
HAS_MCP=false
if [ -f "$OPENCODE_CONFIG" ]; then
    if grep -q '"mcp"' "$OPENCODE_CONFIG" 2>/dev/null; then
        HAS_MCP=true
    fi
fi

MCP_ENTRY=$(cat <<EOF
  "scm": {
    "type": "local",
    "command": ["bash", "${SCM_WRAPPER}"],
    "description": "Skill Context Manager — smart skill selection",
    "enabled": true
  }
EOF
)

if $DRY_RUN; then
    echo "📋 Would add to $OPENCODE_CONFIG:"
    echo "$MCP_ENTRY"
    echo ""
    echo "   Use without --dry-run to apply."
    exit 0
fi

if $HAS_MCP; then
    # Insert into existing mcp block
    python3 -c "
import json, sys
config_path = '${OPENCODE_CONFIG}'
with open(config_path) as f:
    config = json.load(f)
if 'mcp' not in config:
    config['mcp'] = {}
config['mcp']['scm'] = {
    'type': 'local',
    'command': ['bash', '${SCM_WRAPPER}'],
    'description': 'Skill Context Manager — smart skill selection',
    'enabled': True
}
with open(config_path, 'w') as f:
    json.dump(config, f, indent=2)
print('✅ SCM MCP added to existing OpenCode config')
" 2>&1
else
    # Create new config
    python3 -c "
import json
config = {
    '\$schema': 'https://opencode.ai/config.json',
    'mcp': {
        'scm': {
            'type': 'local',
            'command': ['bash', '${SCM_WRAPPER}'],
            'description': 'Skill Context Manager — smart skill selection',
            'enabled': True
        }
    }
}
with open('${OPENCODE_CONFIG}', 'w') as f:
    json.dump(config, f, indent=2)
print('✅ Created OpenCode config with SCM MCP')
" 2>&1
fi

echo ""
echo "Next steps:"
echo "   1. Restart OpenCode (or reload config)"
echo "   2. Try: opencode run \"use scm skill_query to find deploy skills\""
echo ""
