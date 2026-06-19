"""Tests for the multi-agent MCP setup registry (scm.mcp_setup)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scm import mcp_setup as ms


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """Point Path.home() (and HOME/APPDATA) at a temp dir."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path


# Platforms grouped by the on-disk shape we expect to assert against.
JSON_MCPSERVERS = ["claude-code", "claude-desktop", "cursor", "windsurf", "cline", "gemini"]


def test_registry_covers_expected_agents():
    expected = {
        "claude-code", "claude-desktop", "cursor", "windsurf", "cline", "gemini",
        "vscode", "zed", "codex", "goose", "continue", "opencode", "hermes",
    }
    assert expected.issubset(set(ms.PLATFORMS))
    # Every platform has a writer registered for its format.
    for plat in ms.PLATFORMS.values():
        assert plat.fmt in ms._WRITERS


def test_server_command_uses_current_interpreter():
    cmd, args = ms.server_command()
    assert cmd  # non-empty path to python
    assert args == ["-m", "scm.mcp_server"]


@pytest.mark.parametrize("key", ms.ALL_KEYS)
def test_add_then_status_then_remove(fake_home, key):
    # Add
    r = ms.configure(key)
    assert r["status"] in (ms.ADDED, ms.UPDATED), r
    assert Path(r["path"]).exists()

    # Status reflects configured
    st = ms.status(key)
    assert st["configured"] is True

    # Idempotent re-add → exists or updated, never error
    r2 = ms.configure(key)
    assert r2["status"] in (ms.ADDED, ms.UPDATED, ms.EXISTS), r2

    # Remove
    r3 = ms.configure(key, uninstall=True)
    assert r3["status"] == ms.REMOVED, r3
    assert ms.status(key)["configured"] is False


def test_remove_when_absent_is_not_found(fake_home):
    r = ms.configure("cursor", uninstall=True)
    assert r["status"] == ms.NOT_FOUND


def test_json_merge_preserves_siblings(fake_home):
    p = ms.PLATFORMS["cursor"].path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}, "extra": 1}))

    ms.configure("cursor")
    data = json.loads(p.read_text())
    assert "other" in data["mcpServers"]
    assert "scm" in data["mcpServers"]
    assert data["extra"] == 1

    ms.configure("cursor", uninstall=True)
    data = json.loads(p.read_text())
    assert "other" in data["mcpServers"]
    assert "scm" not in data["mcpServers"]
    assert data["extra"] == 1


def test_opencode_shape(fake_home):
    ms.configure("opencode")
    data = json.loads(ms.PLATFORMS["opencode"].path.read_text())
    entry = data["mcp"]["scm"]
    assert entry["type"] == "local"
    assert entry["enabled"] is True
    assert entry["command"][1:] == ["-m", "scm.mcp_server"]


def test_vscode_uses_servers_key_with_stdio(fake_home):
    ms.configure("vscode")
    data = json.loads(ms.PLATFORMS["vscode"].path.read_text())
    assert "servers" in data and "mcpServers" not in data
    assert data["servers"]["scm"]["type"] == "stdio"


def test_zed_uses_context_servers(fake_home):
    ms.configure("zed")
    data = json.loads(ms.PLATFORMS["zed"].path.read_text())
    assert "scm" in data["context_servers"]
    entry = data["context_servers"]["scm"]
    # command is a plain string per https://zed.dev/docs/ai/mcp
    assert isinstance(entry["command"], str)
    assert entry["args"] == ["-m", "scm.mcp_server"]


def test_codex_toml_block(fake_home):
    ms.configure("codex")
    text = ms.PLATFORMS["codex"].path.read_text()
    assert "[mcp_servers.scm]" in text
    assert "args = [" in text
    # idempotent
    assert ms.configure("codex")["status"] == ms.EXISTS
    assert ms.configure("codex", uninstall=True)["status"] == ms.REMOVED
    assert "[mcp_servers.scm]" not in ms.PLATFORMS["codex"].path.read_text()


def test_hermes_yaml_block(fake_home):
    ms.configure("hermes")
    text = ms.PLATFORMS["hermes"].path.read_text()
    assert "mcp_servers:" in text
    assert "scm:" in text
    assert "allowed_tools:" in text
    assert ms.configure("hermes")["status"] == ms.EXISTS
    assert ms.configure("hermes", uninstall=True)["status"] == ms.REMOVED


def test_goose_extensions(fake_home):
    import yaml
    ms.configure("goose")
    data = yaml.safe_load(ms.PLATFORMS["goose"].path.read_text())
    assert data["extensions"]["scm"]["type"] == "stdio"
    ms.configure("goose", uninstall=True)
    data = yaml.safe_load(ms.PLATFORMS["goose"].path.read_text())
    assert "scm" not in data.get("extensions", {})


def test_continue_mcpservers_list(fake_home):
    import yaml
    ms.configure("continue")
    data = yaml.safe_load(ms.PLATFORMS["continue"].path.read_text())
    names = [s.get("name") for s in data["mcpServers"]]
    assert "scm" in names
    # no duplicate after re-add
    ms.configure("continue")
    data = yaml.safe_load(ms.PLATFORMS["continue"].path.read_text())
    assert [s.get("name") for s in data["mcpServers"]].count("scm") == 1


def test_configure_many_and_unknown_platform(fake_home):
    results = ms.configure_many(["cursor", "zed"])
    assert all(r["status"] == ms.ADDED for r in results)
    bad = ms.configure("does-not-exist")
    assert bad["status"] == ms.ERROR


def test_status_all_returns_every_platform(fake_home):
    rows = ms.status_all()
    assert len(rows) == len(ms.ALL_KEYS)
