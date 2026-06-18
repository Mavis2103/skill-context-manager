#!/usr/bin/env bash
# SCM Install Script
# curl -fsSL https://raw.githubusercontent.com/your-org/skill-context-manager/main/scripts/install.sh | bash

set -euo pipefail

SCM_DIR="${SCM_DIR:-$HOME/Workspaces/skill-context-manager}"
SCM_BIN="${SCM_BIN:-$HOME/.local/bin}"

echo "🚀 Installing Skill Context Manager..."

# Check python
if ! command -v python3 &>/dev/null; then
    echo "❌ Python 3.11+ required"
    exit 1
fi

# Check uv
if ! command -v uv &>/dev/null; then
    echo "📦 Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | bash
    export PATH="$HOME/.cargo/bin:$PATH"
fi

# Clone or update
if [ -d "$SCM_DIR" ]; then
    echo "📂 Updating existing installation..."
    cd "$SCM_DIR"
    git pull --ff-only 2>/dev/null || echo "   (not a git repo, skipping pull)"
else
    echo "📂 Cloning..."
    git clone https://github.com/your-org/skill-context-manager.git "$SCM_DIR"
    cd "$SCM_DIR"
fi

# Create venv & install
echo "📦 Installing Python dependencies..."
uv venv --python 3.11
source .venv/bin/activate

echo "   Core: BM25 search + session tracking (lightweight)"
uv pip install sentence-transformers transformers torch 2>/dev/null || {
    echo "   ℹ️  AI models optional. Install later with: uv pip install sentence-transformers transformers torch"
}

# Create symlink
mkdir -p "$SCM_BIN"
ln -sf "$SCM_DIR/.venv/bin/scm" "$SCM_BIN/scm" 2>/dev/null || true

# Add to PATH if needed
if [[ ":$PATH:" != *":$SCM_BIN:"* ]]; then
    echo "export PATH=\"\$PATH:$SCM_BIN\"" >> "$HOME/.bashrc"
    echo "export PATH=\"\$PATH:$SCM_BIN\"" >> "$HOME/.zshrc" 2>/dev/null || true
    echo "   Added $SCM_BIN to PATH (restart shell or source .bashrc/.zshrc)"
fi

# Post-install: index common skill directories
echo ""
echo "🔍 Indexing common skill directories..."
scm index --dir "$HOME/.hermes/skills/" 2>/dev/null || echo "   No Hermes skills found"
scm index --dir "$HOME/.claude/skills/" 2>/dev/null || echo "   No Claude skills found"
scm index --dir ".cursor/skills/" 2>/dev/null || echo "   No Cursor skills found"

echo ""
echo "✅ Skill Context Manager installed!"
echo ""
echo "Quick start:"
echo "  scm query \"deploy to kubernetes\""
echo "  scm session start --id my-session"
echo "  scm session context --id my-session"
echo "  scm stats"
echo ""
echo "See README.md for full documentation"
