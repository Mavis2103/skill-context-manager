"""Multi-agent MCP setup — configure the SCM MCP server for any MCP client.

A single registry (:data:`PLATFORMS`) describes every supported agent: where its
config lives and which on-disk format it uses. ``scm mcp setup`` reads this
registry, so adding a new agent is a one-line entry, not new branching code.

The launch command is always the *current* interpreter running ``scm`` (i.e. the
project venv) plus ``-m scm.mcp_server``. Using ``sys.executable`` makes the
generated config self-contained and PATH-independent, so it works regardless of
how the agent process resolves ``python3``.
"""

from __future__ import annotations

import json
import os
import platform as _platform
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

# The tools we expose to allow-list–based clients (Hermes). Other clients
# auto-discover every tool, so this list is only used where required.
ALLOWED_TOOLS = [
    "skill_query",
    "skill_index",
    "skill_dedup",
    "skill_stats",
    "skill_session_start",
    "skill_session_use",
    "skill_session_context",
    "skill_session_end",
    "skill_optimize",
    "skill_feedback",
    "skill_feedback_stats",
    "skill_insights",
]

# Result status strings returned by configure()/remove().
ADDED = "added"
UPDATED = "updated"
EXISTS = "exists"
REMOVED = "removed"
NOT_FOUND = "not_found"
ERROR = "error"


def server_command() -> tuple[str, list[str]]:
    """Return (command, args) that launch the SCM MCP server (stdio)."""
    return sys.executable, ["-m", "scm.mcp_server"]


# ── Path helpers ──────────────────────────────────────────────────────

def _home() -> Path:
    return Path.home()


def _claude_desktop_path() -> Path:
    """Claude Desktop config path is OS-specific."""
    system = _platform.system()
    if system == "Darwin":
        return _home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    if system == "Windows":
        base = os.environ.get("APPDATA") or str(_home() / "AppData" / "Roaming")
        return Path(base) / "Claude" / "claude_desktop_config.json"
    # Linux / other
    return _home() / ".config" / "Claude" / "claude_desktop_config.json"


def _vscode_user_dir() -> Path:
    system = _platform.system()
    if system == "Darwin":
        return _home() / "Library" / "Application Support" / "Code" / "User"
    if system == "Windows":
        base = os.environ.get("APPDATA") or str(_home() / "AppData" / "Roaming")
        return Path(base) / "Code" / "User"
    return _home() / ".config" / "Code" / "User"


# ── Platform registry ─────────────────────────────────────────────────

@dataclass(frozen=True)
class Platform:
    key: str
    display: str
    fmt: str
    path_fn: Callable[[], Path]
    note: str = ""

    @property
    def path(self) -> Path:
        return self.path_fn()


PLATFORMS: dict[str, Platform] = {
    # ── Anthropic ──
    "claude-code": Platform(
        "claude-code", "Claude Code", "json_mcpServers",
        lambda: _home() / ".claude.json",
        note="user-scope MCP servers; project scope lives in ./.mcp.json",
    ),
    "claude-desktop": Platform(
        "claude-desktop", "Claude Desktop", "json_mcpServers",
        _claude_desktop_path,
    ),
    # ── JSON mcpServers family ──
    "cursor": Platform(
        "cursor", "Cursor", "json_mcpServers",
        lambda: _home() / ".cursor" / "mcp.json",
    ),
    "windsurf": Platform(
        "windsurf", "Windsurf", "json_mcpServers",
        lambda: _home() / ".codeium" / "windsurf" / "mcp_config.json",
    ),
    "cline": Platform(
        "cline", "Cline", "json_mcpServers",
        lambda: _vscode_user_dir() / "globalStorage" / "saoudrizwan.claude-dev"
        / "settings" / "cline_mcp_settings.json",
    ),
    "gemini": Platform(
        "gemini", "Gemini CLI", "json_mcpServers",
        lambda: _home() / ".gemini" / "settings.json",
    ),
    # ── Distinct shapes ──
    "vscode": Platform(
        "vscode", "VS Code (Copilot)", "json_vscode",
        lambda: _vscode_user_dir() / "mcp.json",
        note="uses the `servers` key with type=stdio",
    ),
    "zed": Platform(
        "zed", "Zed", "json_zed",
        lambda: _home() / ".config" / "zed" / "settings.json",
        note="uses the `context_servers` key",
    ),
    "codex": Platform(
        "codex", "Codex CLI", "toml_codex",
        lambda: _home() / ".codex" / "config.toml",
    ),
    "goose": Platform(
        "goose", "Goose", "yaml_goose",
        lambda: _home() / ".config" / "goose" / "config.yaml",
        note="uses the `extensions` key",
    ),
    "continue": Platform(
        "continue", "Continue.dev", "yaml_continue",
        lambda: _home() / ".continue" / "config.yaml",
    ),
    "opencode": Platform(
        "opencode", "OpenCode", "json_opencode",
        lambda: _home() / ".config" / "opencode" / "opencode.json",
    ),
    "hermes": Platform(
        "hermes", "Hermes Agent", "yaml_hermes",
        lambda: _home() / ".hermes" / "config.yaml",
    ),
}

# Agents enabled by `--all`. Order is display order.
ALL_KEYS = list(PLATFORMS.keys())


# ── Agent detection ───────────────────────────────────────────────────
# Each entry returns True if the agent appears installed on this system.
# Heuristic: check either the agent's config dir or binary on PATH.

def _has_bin(*names: str) -> bool:
    return any(shutil.which(n) for n in names)


DETECTORS: dict[str, Callable[[], bool]] = {
    "claude-code":    lambda: _home().joinpath(".claude").is_dir() or _has_bin("claude"),
    "claude-desktop": lambda: _claude_desktop_path().parent.is_dir(),
    "cursor":         lambda: _home().joinpath(".cursor").is_dir() or _has_bin("cursor"),
    "windsurf":       lambda: _home().joinpath(".codeium", "windsurf").is_dir() or _has_bin("windsurf"),
    "cline":          lambda: (_vscode_user_dir() / "globalStorage" / "saoudrizwan.claude-dev").is_dir(),
    "gemini":         lambda: _home().joinpath(".gemini").is_dir() or _has_bin("gemini"),
    "vscode":         lambda: _vscode_user_dir().is_dir() or _has_bin("code", "code-insiders"),
    "zed":            lambda: _home().joinpath(".config", "zed").is_dir() or _has_bin("zed"),
    "codex":          lambda: _home().joinpath(".codex").is_dir() or _has_bin("codex"),
    "goose":          lambda: _home().joinpath(".config", "goose").is_dir() or _has_bin("goose"),
    "continue":       lambda: _home().joinpath(".continue").is_dir(),
    "opencode":       lambda: _home().joinpath(".config", "opencode").is_dir() or _has_bin("opencode"),
    "hermes":         lambda: _home().joinpath(".hermes").is_dir() or _has_bin("hermes"),
}


def is_detected(key: str) -> bool:
    """Return True if agent *key* appears to be installed on this system."""
    fn = DETECTORS.get(key)
    if fn is None:
        return True  # unknown key → assume present
    try:
        return bool(fn())
    except Exception:
        return False


def detected_keys() -> list[str]:
    """Return platform keys whose agent appears installed."""
    return [k for k in ALL_KEYS if is_detected(k)]


# ── JSON helpers ──────────────────────────────────────────────────────

def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return {}
    return json.loads(text)


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


# ── Per-format writers ────────────────────────────────────────────────
# Each returns one of the status constants.

def _cfg_json_mcpServers(p: Platform, uninstall: bool) -> str:
    cmd, args = server_command()
    entry = {"command": cmd, "args": args}
    if uninstall:
        if not p.path.exists():
            return NOT_FOUND
        data = _read_json(p.path)
        servers = data.get("mcpServers", {})
        if "scm" not in servers:
            return NOT_FOUND
        del servers["scm"]
        data["mcpServers"] = servers
        _write_json(p.path, data)
        return REMOVED
    data = _read_json(p.path)
    servers = data.setdefault("mcpServers", {})
    status = UPDATED if "scm" in servers else ADDED
    servers["scm"] = entry
    _write_json(p.path, data)
    return status


def _cfg_json_vscode(p: Platform, uninstall: bool) -> str:
    cmd, args = server_command()
    entry = {"type": "stdio", "command": cmd, "args": args}
    if uninstall:
        if not p.path.exists():
            return NOT_FOUND
        data = _read_json(p.path)
        servers = data.get("servers", {})
        if "scm" not in servers:
            return NOT_FOUND
        del servers["scm"]
        data["servers"] = servers
        _write_json(p.path, data)
        return REMOVED
    data = _read_json(p.path)
    servers = data.setdefault("servers", {})
    status = UPDATED if "scm" in servers else ADDED
    servers["scm"] = entry
    _write_json(p.path, data)
    return status


def _cfg_json_zed(p: Platform, uninstall: bool) -> str:
    cmd, args = server_command()
    # Zed uses flat command/args, NOT a nested {"path": ..., "args": ...} object.
    # Ref: https://zed.dev/docs/ai/mcp
    entry = {"command": cmd, "args": args}
    if uninstall:
        if not p.path.exists():
            return NOT_FOUND
        data = _read_json(p.path)
        servers = data.get("context_servers", {})
        if "scm" not in servers:
            return NOT_FOUND
        del servers["scm"]
        data["context_servers"] = servers
        _write_json(p.path, data)
        return REMOVED
    data = _read_json(p.path)
    servers = data.setdefault("context_servers", {})
    status = UPDATED if "scm" in servers else ADDED
    servers["scm"] = entry
    _write_json(p.path, data)
    return status


def _cfg_json_opencode(p: Platform, uninstall: bool) -> str:
    cmd, args = server_command()
    entry = {"type": "local", "command": [cmd, *args], "enabled": True}
    if uninstall:
        if not p.path.exists():
            return NOT_FOUND
        data = _read_json(p.path)
        mcp = data.get("mcp", {})
        if "scm" not in mcp:
            return NOT_FOUND
        del mcp["scm"]
        data["mcp"] = mcp
        _write_json(p.path, data)
        return REMOVED
    data = _read_json(p.path)
    mcp = data.setdefault("mcp", {})
    status = UPDATED if "scm" in mcp else ADDED
    mcp["scm"] = entry
    _write_json(p.path, data)
    return status


def _cfg_yaml_hermes(p: Platform, uninstall: bool) -> str:
    cmd, args = server_command()
    if uninstall:
        if not p.path.exists():
            return NOT_FOUND
        content = p.path.read_text(encoding="utf-8")
        # Match the mcp_servers block (with optional leading SCM comment),
        # at start-of-file or after a newline, until the next top-level key or EOF.
        cleaned = re.sub(
            r"(?:^|\n)(?:#[^\n]*\n)?mcp_servers:\n  scm:.*?(?=\n\S|\Z)",
            "",
            content,
            flags=re.DOTALL,
        )
        if cleaned == content:
            return NOT_FOUND
        p.path.write_text(cleaned, encoding="utf-8")
        return REMOVED
    args_yaml = ", ".join(f'"{a}"' for a in args)
    tools_yaml = "\n".join(f"      - {t}" for t in ALLOWED_TOOLS)
    block = (
        "\n"
        "mcp_servers:\n"
        "  scm:\n"
        f"    command: {cmd}\n"
        f"    args: [{args_yaml}]\n"
        "    allowed_tools:\n"
        f"{tools_yaml}\n"
    )
    if p.path.exists():
        content = p.path.read_text(encoding="utf-8")
        if "mcp_servers" in content and re.search(r"\n  scm:", content):
            return EXISTS
        with open(p.path, "a", encoding="utf-8") as f:
            f.write(block)
        return ADDED
    p.path.parent.mkdir(parents=True, exist_ok=True)
    p.path.write_text(block.lstrip("\n"), encoding="utf-8")
    return ADDED


def _cfg_yaml_goose(p: Platform, uninstall: bool) -> str:
    import yaml
    cmd, args = server_command()
    data = {}
    if p.path.exists():
        loaded = yaml.safe_load(p.path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            data = loaded
    exts = data.get("extensions", {})
    if uninstall:
        if "scm" not in exts:
            return NOT_FOUND
        del exts["scm"]
        data["extensions"] = exts
        p.path.parent.mkdir(parents=True, exist_ok=True)
        p.path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        return REMOVED
    status = UPDATED if "scm" in exts else ADDED
    exts["scm"] = {
        "name": "scm",
        "type": "stdio",
        "enabled": True,
        "cmd": cmd,
        "args": args,
    }
    data["extensions"] = exts
    p.path.parent.mkdir(parents=True, exist_ok=True)
    p.path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return status


def _cfg_yaml_continue(p: Platform, uninstall: bool) -> str:
    import yaml
    cmd, args = server_command()
    data = {}
    if p.path.exists():
        loaded = yaml.safe_load(p.path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            data = loaded
    servers = data.get("mcpServers", [])
    if not isinstance(servers, list):
        servers = []
    existing = next((s for s in servers if isinstance(s, dict) and s.get("name") == "scm"), None)
    if uninstall:
        if existing is None:
            return NOT_FOUND
        servers = [s for s in servers if not (isinstance(s, dict) and s.get("name") == "scm")]
        data["mcpServers"] = servers
        p.path.parent.mkdir(parents=True, exist_ok=True)
        p.path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        return REMOVED
    status = UPDATED if existing is not None else ADDED
    servers = [s for s in servers if not (isinstance(s, dict) and s.get("name") == "scm")]
    servers.append({"name": "scm", "command": cmd, "args": args})
    data["mcpServers"] = servers
    p.path.parent.mkdir(parents=True, exist_ok=True)
    p.path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return status


def _cfg_toml_codex(p: Platform, uninstall: bool) -> str:
    cmd, args = server_command()
    marker = "[mcp_servers.scm]"
    if uninstall:
        if not p.path.exists():
            return NOT_FOUND
        content = p.path.read_text(encoding="utf-8")
        if marker not in content:
            return NOT_FOUND
        # Strip the [mcp_servers.scm] table (until next table header or EOF)
        cleaned = re.sub(
            r"\n*\[mcp_servers\.scm\][^\[]*",
            "\n",
            content,
            flags=re.DOTALL,
        )
        p.path.write_text(cleaned.rstrip("\n") + "\n", encoding="utf-8")
        return REMOVED
    args_toml = ", ".join(f'"{a}"' for a in args)
    block = f'\n[mcp_servers.scm]\ncommand = "{cmd}"\nargs = [{args_toml}]\n'
    if p.path.exists():
        content = p.path.read_text(encoding="utf-8")
        if marker in content:
            return EXISTS
        with open(p.path, "a", encoding="utf-8") as f:
            f.write(block)
        return ADDED
    p.path.parent.mkdir(parents=True, exist_ok=True)
    p.path.write_text(block.lstrip("\n"), encoding="utf-8")
    return ADDED


_WRITERS: dict[str, Callable[[Platform, bool], str]] = {
    "json_mcpServers": _cfg_json_mcpServers,
    "json_vscode": _cfg_json_vscode,
    "json_zed": _cfg_json_zed,
    "json_opencode": _cfg_json_opencode,
    "yaml_hermes": _cfg_yaml_hermes,
    "yaml_goose": _cfg_yaml_goose,
    "yaml_continue": _cfg_yaml_continue,
    "toml_codex": _cfg_toml_codex,
}


# ── Public API ────────────────────────────────────────────────────────

def configure(key: str, uninstall: bool = False) -> dict:
    """Add or remove the SCM MCP server for a single platform.

    Returns {"platform", "display", "path", "status"} where status is one of the
    module's status constants. Never raises — file/parse errors become ERROR.
    """
    p = PLATFORMS.get(key)
    if p is None:
        return {"platform": key, "display": key, "path": "", "status": ERROR,
                "error": f"unknown platform: {key}"}
    writer = _WRITERS[p.fmt]
    try:
        status = writer(p, uninstall)
    except Exception as e:  # noqa: BLE001 — surface as a clean status, don't crash setup
        return {"platform": key, "display": p.display, "path": str(p.path),
                "status": ERROR, "error": str(e)}
    return {"platform": key, "display": p.display, "path": str(p.path), "status": status}


def configure_many(keys: list[str], uninstall: bool = False) -> list[dict]:
    return [configure(k, uninstall=uninstall) for k in keys]


def status(key: str) -> dict:
    """Report whether SCM is currently configured for a platform."""
    p = PLATFORMS.get(key)
    if p is None:
        return {"platform": key, "display": key, "path": "", "configured": False}
    configured = False
    try:
        if p.path.exists():
            text = p.path.read_text(encoding="utf-8")
            if p.fmt == "toml_codex":
                configured = "[mcp_servers.scm]" in text
            elif p.fmt in ("yaml_hermes",):
                configured = "mcp_servers" in text and bool(re.search(r"\n  scm:", text))
            elif p.fmt in ("yaml_goose", "yaml_continue"):
                configured = "scm" in text
            else:
                data = _read_json(p.path)
                if p.fmt == "json_opencode":
                    configured = "scm" in data.get("mcp", {})
                elif p.fmt == "json_zed":
                    configured = "scm" in data.get("context_servers", {})
                elif p.fmt == "json_vscode":
                    configured = "scm" in data.get("servers", {})
                else:
                    configured = "scm" in data.get("mcpServers", {})
    except Exception:  # noqa: BLE001
        configured = False
    return {"platform": key, "display": p.display, "path": str(p.path),
            "configured": configured, "exists": p.path.exists(),
            "detected": is_detected(key)}


def status_all() -> list[dict]:
    return [status(k) for k in ALL_KEYS]
