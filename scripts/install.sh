#!/usr/bin/env bash
# =============================================================================
# SCM (Skill Context Manager) — One-Click Install
# Uses `uv tool install` — no clone, no venv management needed.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/Mavis2103/skill-context-manager/main/scripts/install.sh | bash
#   curl -fsSL ... | bash -s -- --with-mcp          # auto-configure all 13 agent platforms
#   curl -fsSL ... | bash -s -- --dev               # clone repo + editable install (for contributors)
#   curl -fsSL ... | bash -s -- --uninstall         # remove everything
#   curl -fsSL ... | bash -s -- --scm-dir ~/custom/path  # custom clone dir (only with --dev)
# =============================================================================

set -euo pipefail

# ---- Config ----------------------------------------------------------------
REPO="Mavis2103/skill-context-manager"
REPO_URL="https://github.com/${REPO}.git"
SCM_DIR="${SCM_DIR:-${HOME}/.scm}"
SCM_BIN="${SCM_BIN:-${HOME}/.local/bin}"
SCM_DB_DIR="${HOME}/.scm/db"
WITH_MCP=false
DEV_MODE=false

# ---- Style helpers ---------------------------------------------------------
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'
info()  { printf "${CYAN}ℹ️${NC}  %s\n" "$*"; }
ok()    { printf "${GREEN}✅${NC}  %s\n" "$*"; }
warn()  { printf "${YELLOW}⚠️${NC}  %s\n" "$*"; }
err()   { printf "${RED}❌${NC}  %s\n" "$*"; exit 1; }
header(){ printf "\n${BOLD}━━━ %s ━━━${NC}\n" "$*"; }

# ---- Pre-flight checks ----------------------------------------------------
preflight() {
  header "Pre-flight"

  # Parse args
  for arg in "$@"; do
    case "$arg" in
      --with-mcp)    WITH_MCP=true ;;
      --dev)         DEV_MODE=true ;;
      --uninstall)   action_uninstall; exit 0 ;;
      --scm-dir=*)   SCM_DIR="${arg#*=}" ;;
    esac
  done

  if $DEV_MODE; then
    info "Dev mode — will clone repo to ${SCM_DIR}"
    # git check
    if ! command -v git &>/dev/null; then
      err "git is required in dev mode but not found."
    fi
    ok "git detected"
  else
    info "Quick install — no clone needed"
  fi

  # Python check
  if ! command -v python3 &>/dev/null; then
    err "Python 3.11+ is required but not found."
  fi
  if python3 -c "import sys; exit(0 if sys.version_info >= (3,11) else 1)"; then
    ok "Python $(python3 --version | cut -d' ' -f2) meets minimum (3.11)"
  else
    err "Python $(python3 --version | cut -d' ' -f2) is too old. Need 3.11+"
  fi

  # uv check / install
  if command -v uv &>/dev/null; then
    ok "uv already installed ($(uv --version | head -1))"
  else
    warn "uv not found — installing via Astral installer..."
    curl -LsSf https://astral.sh/uv/install.sh | bash
    # Find uv binary after install
    for uv_candidate in "${HOME}/.local/bin/uv" "${HOME}/.cargo/bin/uv" "/usr/local/bin/uv"; do
      if [[ -f "$uv_candidate" ]]; then
        export PATH="$(dirname "$uv_candidate"):${PATH}"
        break
      fi
    done
    command -v uv &>/dev/null || err "uv installed but not in PATH. Re-run after restarting shell."
    ok "uv installed ($(uv --version | head -1))"
  fi
}

# ---- Install via uv tool ---------------------------------------------------
quick_install() {
  header "Installing SCM via uv tool"

  info "Running: uv tool install git+${REPO_URL}"
  uv tool install "git+${REPO_URL}" --quiet
  ok "SCM installed (v$(scm --version 2>/dev/null || true))"

  # Ensure ~/.local/bin is on PATH for this session
  if ! command -v scm &>/dev/null; then
    export PATH="${PATH}:${SCM_BIN}"
  fi
}

# ---- Dev install (clone + editable) -----------------------------------------
dev_install() {
  header "Dev Install"

  # Clone / update repo
  if [[ -d "$SCM_DIR" ]]; then
    if git -C "$SCM_DIR" rev-parse --git-dir &>/dev/null 2>&1; then
      remote_url=$(git -C "$SCM_DIR" remote get-url origin 2>/dev/null || echo "")
      if [[ "$remote_url" == *"${REPO}"* ]]; then
        info "Updating existing installation..."
        git -C "$SCM_DIR" pull --ff-only
        ok "Updated to $(git -C "$SCM_DIR" rev-parse --short HEAD)"
      else
        warn "Directory exists with different remote. Keeping it."
      fi
    else
      warn "Directory exists but not a git repo. Keeping it."
    fi
  else
    info "Cloning ${REPO_URL}..."
    git clone --depth 1 "$REPO_URL" "$SCM_DIR"
    ok "Cloned: $(git -C "$SCM_DIR" rev-parse --short HEAD)"
  fi

  # Install editable
  cd "$SCM_DIR"
  info "Creating virtualenv + installing SCM in editable mode..."
  if [[ -d ".venv" ]]; then
    rm -rf .venv
  fi
  uv venv --quiet
  source .venv/bin/activate
  uv pip install -e . --quiet
  ok "SCM core installed (v$(python3 -c 'from scm import __version__; print(__version__)'))"

  # Symlink
  mkdir -p "$SCM_BIN"
  ln -sf "${SCM_DIR}/.venv/bin/scm" "${SCM_BIN}/scm"
  ok "Symlinked: ${SCM_BIN}/scm → ${SCM_DIR}/.venv/bin/scm"

  export PATH="${PATH}:${SCM_BIN}"

  # Shell-agnostic PATH via profile.d (Linux)
  if [[ "$(uname -s)" == "Linux" && -d "/etc/profile.d" && -w "/etc/profile.d" ]]; then
    local profile_script="/etc/profile.d/scm-path.sh"
    if [[ ! -f "$profile_script" ]]; then
      printf 'export PATH="${PATH}:%s"\n' "$SCM_BIN" | sudo tee "$profile_script" >/dev/null 2>&1 || true
    fi
  fi

  # GPU notice
  if command -v nvidia-smi &>/dev/null || python3 -c "import torch" &>/dev/null 2>&1; then
    warn "GPU detected — AI models recommended: uv pip install scm[full]"
  fi
}

# ---- MCP Auto-Setup (optional) ---------------------------------------------
setup_mcp() {
  header "MCP Integration"

  if command -v scm &>/dev/null; then
    if scm mcp setup --all 2>&1; then
      ok "MCP configured for all 13 agents"
    else
      warn "MCP setup incomplete — configure manually: scm mcp setup --all"
    fi
  else
    warn "scm not in PATH — skip MCP setup. Run later: scm mcp setup --all"
  fi
}

# ---- Index common directories ----------------------------------------------
index_skills() {
  header "Indexing"

  local dirs=(
    "${HOME}/.hermes/skills"
    "${HOME}/.claude/skills"
    "${HOME}/.cursor/skills"
    "${HOME}/.codeium/windsurf/skills"
    "${HOME}/.codex/skills"
    "${HOME}/.config/goose/skills"
    "${HOME}/.continue/skills"
    "${HOME}/.local/share/agent-skills"
  )
  local found=false

  for dir in "${dirs[@]}"; do
    if [[ -d "$dir" ]]; then
      scm index --dir "$dir" 2>/dev/null && { ok "Indexed: ${dir}"; found=true; } || {
        warn "Index ${dir}: no valid skills found"
      }
    fi
  done

  if ! $found; then
    warn "No skill directories found."
    warn "  Run later: scm index --dir /path/to/skills"
  fi
}

# ---- Uninstall -------------------------------------------------------------
action_uninstall() {
  header "Uninstalling SCM"

  local confirm=""
  printf "${RED}Remove SCM entirely?${NC} This will delete:"
  echo "  • uv tool scm          (binary + venv)"
  echo "  • ${SCM_DB_DIR}       (database + feedback data)"
  echo ""
  printf "Type ${BOLD}yes${NC} to proceed: "
  read -r confirm
  if [[ "$confirm" != "yes" ]]; then
    info "Uninstall cancelled."
    exit 0
  fi

  # Step 1: Clean MCP configs
  if command -v scm &>/dev/null; then
    scm mcp setup --force-all --uninstall 2>/dev/null || true
    ok "Cleaned MCP configs"
  else
    warn "Could not clean MCP configs (scm not found). Clean manually if needed."
  fi

  # Step 2: Uninstall uv tool
  if uv tool list 2>/dev/null | grep -q "^scm "; then
    uv tool uninstall scm --quiet
    ok "Uninstalled uv tool scm"
  fi

  # Step 3: Remove dev-mode clone if exists
  if [[ -d "$SCM_DIR" && -f "$SCM_DIR/.venv/bin/scm" ]]; then
    rm -rf "$SCM_DIR"
    ok "Removed dev clone: ${SCM_DIR}"
  fi

  # Step 4: Remove database
  if [[ -d "$SCM_DB_DIR" ]]; then
    rm -rf "$(dirname "$SCM_DB_DIR")"
    ok "Removed database"
  fi

  # Step 5: Clean profile.d PATH script
  if [[ -f "/etc/profile.d/scm-path.sh" ]]; then
    sudo rm -f "/etc/profile.d/scm-path.sh" 2>/dev/null || true
  fi

  echo ""
  ok "SCM has been uninstalled."
  echo "  To clean PATH from current shell: exec \$SHELL"
}

# ---- Sanity check ----------------------------------------------------------
sanity_check() {
  header "Sanity Check"

  if command -v scm &>/dev/null; then
    ok "scm CLI is accessible ($(command -v scm))"
  else
    warn "scm not in PATH yet. Run: source ~/.bashrc (or restart shell)"
  fi

  scm stats 2>/dev/null && ok "Database responding" || {
    warn "Initialise DB by running: scm index --dir ~/.hermes/skills/"
  }
}

# ---- Summary ---------------------------------------------------------------
print_summary() {
  header "Installation Complete"
  echo ""

  local ver="?"
  command -v scm &>/dev/null && ver=$(scm --version 2>/dev/null || echo "?")
  ok "SCM v${ver} installed"

  echo ""
  info "${BOLD}Quick start:${NC}"
  echo "  scm index --dir ~/.hermes/skills/        # index your skills"
  echo "  scm query \"deploy to kubernetes\"          # find the right skill"
  echo "  scm session start --id my-session         # track a session"
  echo "  scm --help                                # full usage"
  echo ""
  info "${BOLD}Documentation:${NC}"
  echo "  https://github.com/Mavis2103/skill-context-manager"
  echo ""
  info "${BOLD}Update:${NC}"
  echo "  uv tool upgrade scm"
  echo ""
  info "${BOLD}Uninstall:${NC}"
  echo "  curl -fsSL https://raw.githubusercontent.com/Mavis2103/skill-context-manager/main/scripts/install.sh | bash -s -- --uninstall"
  echo ""

  if $WITH_MCP; then
    info "${GREEN}MCP configured for all 13 agents 🎉${NC}"
    echo "  Restart your agent to start using SCM tools."
  fi
}

# =============================================================================
# Main
# =============================================================================
main() {
  echo ""
  printf "${CYAN}${BOLD}"
  echo "  ╔══════════════════════════════════════════════╗"
  echo "  ║       Skill Context Manager Installer        ║"
  echo "  ║     🚀  uv tool • zero-clone • global PATH   ║"
  echo "  ╚══════════════════════════════════════════════╝"
  printf "${NC}\n"

  preflight "$@"

  if $DEV_MODE; then
    dev_install
  else
    quick_install
  fi

  if $WITH_MCP; then
    setup_mcp
  fi

  if ! $DEV_MODE; then
    # Only index in quick mode (dev mode already has skills/SCM skills to index)
    index_skills
  fi

  sanity_check
  print_summary
}

main "$@"
