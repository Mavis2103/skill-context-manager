#!/usr/bin/env bash
# =============================================================================
# SCM (Skill Context Manager) — One-Click Install
# Prioritises uv (Astral) for package management.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/Mavis2103/skill-context-manager/main/scripts/install.sh | bash
#   curl -fsSL ... | bash -s -- --with-mcp          # auto-configure Hermes + OpenCode
#   curl -fsSL ... | bash -s -- --uninstall         # remove everything
#   curl -fsSL ... | bash -s -- --scm-dir ~/custom/path
# =============================================================================

set -euo pipefail

# ---- Config ----------------------------------------------------------------
REPO="Mavis2103/skill-context-manager"
REPO_URL="https://github.com/${REPO}.git"
SCM_DIR="${SCM_DIR:-${HOME}/Workspaces/skill-context-manager}"
SCM_BIN="${SCM_BIN:-${HOME}/.local/bin}"
SCM_DB_DIR="${HOME}/.scm"
PROFILE_D="/etc/profile.d"
WITH_MCP=false

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
      --uninstall)   action_uninstall; exit 0 ;;
      --scm-dir=*)   SCM_DIR="${arg#*=}" ;;
    esac
  done

  info "Target: ${SCM_DIR}"

  # OS check
  case "$(uname -s)" in
    Linux|Darwin) ;;
    *) err "Unsupported OS: $(uname -s)" ;;
  esac

  # Python check
  if ! command -v python3 &>/dev/null; then
    err "Python 3.11+ is required but not found."
  fi
  py_ver=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
  info "Python ${py_ver} detected"
  if python3 -c "import sys; exit(0 if sys.version_info >= (3,11) else 1)"; then
    ok "Python ${py_ver} meets minimum (3.11)"
  else
    err "Python ${py_ver} is too old. Need 3.11+"
  fi

  # uv check / install
  if command -v uv &>/dev/null; then
    uv_ver=$(uv --version 2>/dev/null || echo "unknown")
    ok "uv already installed (${uv_ver})"
  else
    warn "uv not found — installing via Astral installer..."
    curl -LsSf https://astral.sh/uv/install.sh | bash
    # uv installs to $HOME/.local/bin, cargo, or ~/.cargo/bin depending on platform
    if [[ -f "${HOME}/.local/bin/uv" ]]; then
      export PATH="${HOME}/.local/bin:${PATH}"
    elif [[ -f "${HOME}/.cargo/bin/uv" ]]; then
      export PATH="${HOME}/.cargo/bin:${PATH}"
    elif [[ -f "/usr/local/bin/uv" ]]; then
      : # system install, already in PATH
    else
      # Try to find it
      uv_path=$(command -v uv 2>/dev/null || true)
      if [[ -n "$uv_path" ]]; then
        export PATH="$(dirname "$uv_path"):${PATH}"
      else
        err "uv installed but not in PATH. Please add it manually and re-run."
      fi
    fi
    ok "uv installed (v$(uv --version | head -1))"
  fi

  # git check
  if ! command -v git &>/dev/null; then
    err "git is required but not found."
  fi
  ok "git detected"
}

# ---- Clone / Update repo --------------------------------------------------
clone_repo() {
  header "Repo"

  if [[ -d "$SCM_DIR" ]]; then
    # Check if it's a git repo
    if git -C "$SCM_DIR" rev-parse --git-dir &>/dev/null 2>&1; then
      # Check remote
      remote_url=$(git -C "$SCM_DIR" remote get-url origin 2>/dev/null || echo "")
      if [[ "$remote_url" == *"${REPO}"* ]]; then
        info "Updating existing installation..."
        git -C "$SCM_DIR" pull --ff-only
        ok "Updated to $(git -C "$SCM_DIR" rev-parse --short HEAD)"
      else
        warn "Directory exists with different remote: ${remote_url}"
        warn "Keeping existing directory. Remove it manually if you want a fresh clone."
      fi
    else
      warn "Directory exists but is not a git repo — keeping it."
    fi
  else
    info "Cloning ${REPO_URL}..."
    git clone --depth 1 "$REPO_URL" "$SCM_DIR"
    ok "Cloned: $(git -C "$SCM_DIR" rev-parse --short HEAD)"
  fi
}

# ---- Create venv & install package -----------------------------------------
install_package() {
  header "Python Package"

  cd "$SCM_DIR"

  # Check if already in a venv
  if [[ -n "${VIRTUAL_ENV:-}" ]]; then
    info "Already in a virtualenv (${VIRTUAL_ENV}) — using it"
  else
    if [[ -d ".venv" ]]; then
      info "Virtualenv exists — recreating to ensure consistency"
      rm -rf .venv
    fi
    info "Creating virtualenv with uv..."
    uv venv
    ok "Virtualenv created"
  fi

  # Activate
  source .venv/bin/activate

  # Install core package (no AI deps by default)
  info "Installing SCM core..."
  uv pip install -e . --quiet
  ok "SCM core installed (v$(python3 -c 'from scm import __version__; print(__version__)'))"

  # Check if we can offer optional AI deps
  if command -v nvidia-smi &>/dev/null || python3 -c "import torch" &>/dev/null 2>&1; then
    warn "GPU detected — AI models recommended for best accuracy"
    warn "  uv pip install scm[full]"
    warn "  # or: uv pip install sentence-transformers transformers torch"
  fi
}

# ---- Symlink ---------------------------------------------------------------
setup_symlink() {
  header "PATH Setup"

  mkdir -p "$SCM_BIN"

  local scm_path="${SCM_DIR}/.venv/bin/scm"
  local symlink_target="${SCM_BIN}/scm"

  if [[ ! -f "$scm_path" ]]; then
    err "scm binary not found at ${scm_path}. Something went wrong during install."
  fi

  ln -sf "$scm_path" "$symlink_target"
  ok "Symlinked: ${symlink_target} → ${scm_path}"

  # Shell-agnostic PATH via profile.d (Linux) or shell rc
  local path_line="export PATH=\"\${PATH}:${SCM_BIN}\""

  if [[ "$(uname -s)" == "Linux" && -d "$PROFILE_D" && -w "$PROFILE_D" ]]; then
    # Use profile.d (works for bash, zsh, sh)
    local profile_script="${PROFILE_D}/scm-path.sh"
    if [[ ! -f "$profile_script" ]]; then
      printf '%s\n' "$path_line" | sudo tee "$profile_script" >/dev/null 2>&1 || true
    fi
    if [[ -f "$profile_script" ]]; then
      ok "PATH configured via ${profile_script}"
    fi
  fi

  # Also update current shell rc files as fallback
  for rc in "${HOME}/.bashrc" "${HOME}/.zshrc" "${HOME}/.config/fish/config.fish"; do
    if [[ -f "$rc" ]]; then
      if ! grep -q "SCM_BIN\|scm.*PATH" "$rc" 2>/dev/null; then
        # Check shell type
        case "$rc" in
          *.bashrc) echo "$path_line" >> "$rc" && ok "Added to ${rc}" ;;
          *.zshrc)  echo "$path_line" >> "$rc" && ok "Added to ${rc}" ;;
          *.fish)   echo "fish_add_path ${SCM_BIN}" >> "$rc" && ok "Added to ${rc}" ;;
        esac
      fi
    fi
  done

  # Make bin accessible now for the rest of the script
  export PATH="${PATH}:${SCM_BIN}"
}

# ---- MCP Auto-Setup (optional) ---------------------------------------------
setup_mcp() {
  header "MCP Integration"

  if scm mcp setup --all 2>&1; then
    ok "MCP configured for Hermes Agent + OpenCode"
  else
    warn "MCP setup incomplete — configure manually:"
    warn "  scm mcp setup --all"
  fi
}

# ---- Index common directories ----------------------------------------------
index_skills() {
  header "Indexing"

  local dirs=(
    # Hermes Agent
    "${HOME}/.hermes/skills"
    # Claude Code / Claude Desktop
    "${HOME}/.claude/skills"
    # Cursor
    "${HOME}/.cursor/skills"
    # Windsurf
    "${HOME}/.codeium/windsurf/skills"
    # Codex CLI
    "${HOME}/.codex/skills"
    # Goose
    "${HOME}/.config/goose/skills"
    # Continue.dev
    "${HOME}/.continue/skills"
    # Generic fallback for any agent that uses XDG
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

  # Also scan SCM's own skills as a demo
  if [[ -d "${SCM_DIR}/skills" ]]; then
    scm index --dir "${SCM_DIR}/skills" 2>/dev/null || true
  fi

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
  echo "  • ${SCM_DIR}     (source code)"
  echo "  • ${SCM_BIN}/scm (symlink)"
  echo "  • ${SCM_DB_DIR} (database + feedback data)"
  echo ""
  printf "Type ${BOLD}yes${NC} to proceed: "
  read -r confirm
  if [[ "$confirm" != "yes" ]]; then
    info "Uninstall cancelled."
    exit 0
  fi

  # ── Step 1: Clean MCP configs FIRST (source still on disk) ──────────────
  # Must run before removing the venv/source — fallback needs mcp_setup.py.
  # Use --force-all to clean all 13 agents regardless of what's detected now.
  if command -v scm &>/dev/null; then
    scm mcp setup --force-all --uninstall 2>/dev/null || true
    ok "Cleaned MCP configs via scm mcp"
  elif [[ -f "${SCM_DIR}/.venv/bin/python3" ]]; then
    # Venv python3 still present — call mcp_setup directly
    "${SCM_DIR}/.venv/bin/python3" -c "
import sys
sys.path.insert(0, '${SCM_DIR}/src')
from scm.mcp_setup import ALL_KEYS, configure_many
configure_many(ALL_KEYS, uninstall=True)
" 2>/dev/null && ok "Cleaned MCP configs (all 13 agents)" || \
      warn "MCP config cleanup had errors — check agent configs manually"
  else
    warn "Could not clean MCP configs automatically."
    warn "  Run manually: scm mcp setup --force-all --uninstall"
  fi

  # ── Step 2: Remove symlink ───────────────────────────────────────────────
  rm -f "${SCM_BIN}/scm"
  ok "Removed symlink"

  # ── Step 3: Remove venv ─────────────────────────────────────────────────
  if [[ -d "${SCM_DIR}/.venv" ]]; then
    rm -rf "${SCM_DIR}/.venv"
    ok "Removed venv"
  fi

  # ── Step 4: Remove source ────────────────────────────────────────────────
  if [[ -d "$SCM_DIR" ]]; then
    rm -rf "$SCM_DIR"
    ok "Removed source"
  fi

  # ── Step 5: Remove database ──────────────────────────────────────────────
  if [[ -d "$SCM_DB_DIR" ]]; then
    rm -rf "$SCM_DB_DIR"
    ok "Removed database"
  fi

  # ── Step 6: Remove profile.d PATH script ────────────────────────────────
  if [[ -f "${PROFILE_D}/scm-path.sh" ]]; then
    sudo rm -f "${PROFILE_D}/scm-path.sh" 2>/dev/null || true
    ok "Removed profile.d PATH config"
  fi

  # ── Step 7: Clean shell rc files ────────────────────────────────────────
  for rc in "${HOME}/.bashrc" "${HOME}/.zshrc"; do
    if [[ -f "$rc" ]]; then
      python3 -c "
with open('${rc}') as f:
    lines = [l for l in f if 'SCM_BIN' not in l and '# scm' not in l.lower()]
with open('${rc}', 'w') as f:
    f.writelines(lines)
" 2>/dev/null || true
    fi
  done
  ok "Cleaned shell rc files"

  echo ""
  ok "SCM has been uninstalled."
  echo "  To remove lingering PATH config, restart your shell: exec \$SHELL"
}

# ---- Sanity check ----------------------------------------------------------
sanity_check() {
  header "Sanity Check"

  if command -v scm &>/dev/null; then
    ok "scm CLI is accessible"
  else
    warn "scm not in PATH yet. Run: source ~/.bashrc (or restart shell)"
  fi

  # Quick smoke test
  scm stats 2>/dev/null && ok "Database responding" || {
    warn "Initialise DB by running: scm index --dir ~/.hermes/skills/"
  }
}

# ---- Summary ---------------------------------------------------------------
print_summary() {
  header "Installation Complete"
  echo ""
  ok "Skill Context Manager v$(cd "$SCM_DIR" && python3 -c "from scm import __version__; print(__version__)" 2>/dev/null || echo "?") installed"
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
  info "${BOLD}AI models (optional, for better accuracy):${NC}"
  echo "  cd ${SCM_DIR} && source .venv/bin/activate"
  echo "  uv pip install scm[full]"
  echo ""
  info "${BOLD}Uninstall:${NC}"
  echo "  curl -fsSL https://raw.githubusercontent.com/Mavis2103/skill-context-manager/main/scripts/install.sh | bash -s -- --uninstall"
  echo ""

  if $WITH_MCP; then
    info "${GREEN}MCP configured for Hermes Agent + OpenCode 🎉${NC}"
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
  echo "  ║     Skill Context Manager Installer          ║"
  echo "  ║     🚀  uv-first • zero-dependency core      ║"
  echo "  ╚══════════════════════════════════════════════╝"
  printf "${NC}\n"

  preflight "$@"
  clone_repo
  install_package
  setup_symlink
  if $WITH_MCP; then
    setup_mcp
  fi
  index_skills
  sanity_check
  print_summary
}

main "$@"
