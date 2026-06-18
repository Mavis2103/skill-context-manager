# Changelog

All notable changes to **Skill Context Manager (SCM)** are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.2] - 2026-06-18 — MCP Setup CLI + uv-first Install

### Added
- **`scm mcp setup`** subcommand — auto-configure SCM as MCP server for
  Hermes Agent (`--hermes`) and/or OpenCode (`--opencode`), or both (`--all`).
- **`scm mcp status`** — check MCP configuration status across platforms.
- **`scm mcp start`** — launch the MCP server from CLI (`--http` for SSE mode).
- **`scm mcp setup --uninstall`** — remove SCM MCP config from all platforms.
- **`scripts/install.sh` upgraded** — uv-first, auto `uv venv`, profile.d PATH,
  `scm mcp setup --all` integration, `--uninstall` flag, idempotent re-runs,
  sanity check (`scm stats`), color-coded output, Python version auto-detect.

### Changed
- README restructured: Installation → Features → … → Development (Quick Start removed).
- README MCP section simplified: `scm mcp setup` is the primary path.
- Install script no longer does raw YAML/JSON manipulation — delegates to
  `scm mcp setup --all` (and fallback helpers if CLI unavailable).

### Planned
- GUI dashboard
- Multi-agent session sharing
- Configurable stopwords list (currently hardcoded in retriever)

---

## [0.2.1] - 2026-06-18 — Patch Release

**Stability release — 16 bug fixes + 24 regression tests (101/101 passing).**

### Fixed
- **FTS5 query injection** (CRITICAL) — `SkillRetriever._build_fts_queries` now
  sanitizes user input by quoting all FTS5 terms, preventing injection of
  operators like `OR`, `NEAR`, `AND`, `NOT` and special characters.
- **`daily_stats.avg_latency_ms` running-average drift** (CRITICAL) — Replaced
  inline `ON CONFLICT` formula with read-then-write logic to prevent mathematical
  drift over multiple events on the same day.
- **Missing `FeedbackRecord` import in CLI** (CRITICAL) — `scm feedback record`
  crashed with `NameError`; import added to `cli.py`.
- **Cross-process session resolution** (CRITICAL) — `SessionTracker` now exposes
  `get_or_resolve_session()` which falls back to the most recently started
  session in the DB when no `--id` is provided and no in-memory active session
  exists. Fixes `scm session use/context` in fresh CLI processes.
- **`FeedbackEngine.apply_weights` input mutation** — Now returns a new list of
  new `QueryResult` objects instead of mutating caller's input in place.
- **`SkillOptimizer._write_optimized` non-atomic** — Replaced unsafe
  rename-to-.bak + rename-back sequence with `os.replace()` (POSIX-atomic),
  and added cleanup of dangling `.tmp` files on failure.
- **Empty `skill_name` accepted in `SessionTracker.record_skill_use`** — Now
  silently rejected instead of inserting invalid empty rows.
- **Library code using `print()`** — `indexer.py` and `reranker.py` replaced
  `print()` with `logger.warning/info` to avoid corrupting `scm query
  --format json` output.
- **MCP tool return type hints** — All 11 tools in `mcp_server.py` now declare
  `-> dict` (the prompt still declares `-> str`).

### Added
- `tests/test_regression.py` — 24 regression tests covering all 16 fixes:
  FTS5 injection safety, daily_stats math, `apply_weights` immutability,
  session edge cases, optimizer atomic write, library no-print, MCP type hints,
  full end-to-end CLI workflow.
- `SessionTracker.get_or_resolve_session()` — Public method for cross-process
  session resolution.

### Changed
- `pyproject.toml` version: `0.1.0` → `0.2.1` (was 0.2.0 in README, now aligned).
- Test suite: 77 → **101 tests** (all passing).

### Security
- FTS5 query injection vector closed. Crafted queries containing
  `"OR"1"="1` and similar patterns no longer reach FTS5 as raw syntax.

---

## [0.2.0] - 2026-06-18 — Minor Release

**Initial public release — MCP server + Hermes Agent / OpenCode integration.**

### Added
- **Two-stage retrieval** (SkillRouter CVPR 2026 architecture): BM25 (SQLite FTS5)
  → Embedding search (optional `sentence-transformers`) → Cross-encoder
  reranking (optional `transformers` + `torch`).
- **Session tracking** — `start/use/end/context` with persistence in shared DB.
- **Feedback loop** — Bayesian weight update from `success/failure` data.
- **Metadata optimizer** — Compress long descriptions, expand short ones, infer
  action prefixes.
- **Usage analytics** — `insights` command with daily trend, top skills,
  unused skills.
- **MCP server** — 11 tools via stdio and HTTP/SSE transports.
- **Hermes Agent integration** — Tested with `hermes mcp test scm` (11 tools
  discovered in 738ms).
- **OpenCode integration** — `opencode.json` config snippet.
- **Single shared SQLite database** (`~/.scm/scm.db`) with WAL mode — eliminates
  cross-DB bugs and duplicated WAL/connection code.
- **77 tests** across 7 test modules (models, indexer, retriever, session,
  feedback, optimizer, tracker, reranker).
- `docs/ARCHITECTURE.md` and `docs/MCP-INTEGRATION.md`.
- `scripts/install.sh`, `scripts/demo.sh`, `scripts/benchmark.sh`.

### Changed
- Migrated all documentation (README, docs/*) to English (from Vietnamese).
- All source code comments in English.

### Notes
- v0.1.0 was the internal alpha (never released to GitHub).
- v0.2.0 was the first published release.

---

## Version History (Reference)

| Version | Date       | Type   | Highlights |
|---------|-----------|--------|------------|
| 0.2.2   | 2026-06-18 | Minor  | scm mcp setup CLI, uv-first install, README restructured |
| 0.2.1   | 2026-06-18 | Patch  | 16 bug fixes, 24 regression tests |
| 0.2.0   | 2026-06-18 | Minor  | Initial public release, MCP server |

[Unreleased]: https://github.com/Mavis2103/skill-context-manager/compare/v0.2.2...HEAD
[0.2.2]: https://github.com/Mavis2103/skill-context-manager/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/Mavis2103/skill-context-manager/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/Mavis2103/skill-context-manager/releases/tag/v0.2.0
