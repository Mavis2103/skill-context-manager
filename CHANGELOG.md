# Changelog

All notable changes to **Skill Context Manager (SCM)** are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.7.0] - 2026-06-19 — MiniLM Embedding + Adaptive Retrieval + Knowledge Graph

### Added
- **all-MiniLM-L6-v2 embedding model** — replaces BGE-base-en-v1.5 as default. 22x faster cold start (10s vs 64s), 384-dim vs 768-dim, with **higher accuracy**: 100% Recall@5, 0.931 MRR, 88% Precision@1. Falls back to BM25 gracefully when sentence-transformers unavailable.
- **RRF fusion** (`--method rrf`, now default) — Reciprocal Rank Fusion replaces weighted hybrid. No score normalization needed; combines BM25 + embedding ranks directly. SIGIR-recommended `k=60` parameter.
- **Adaptive retrieval** (`skill_query_adaptive` MCP tool) — elbow detection (`detect_elbow()`) auto-selects `k` instead of hardcoded top-5. DBSCAN clustering (`SkillClusterer`) for diverse mode that avoids variant pollution.
- **Knowledge graph** (`SkillGraph`) — 3 edge types: co-occurrence (skills used in same session), transition (sequential usage), content similarity (embedding cosine > 0.8). Personalized PageRank for session-aware boosting.
- **LambdaMART LTR scaffolding** (`src/scm/ltr.py`, `scripts/train-ltr.py`) — 25 features extracted (retrieval scores, rank, text stats, feedback signals). Ready for training with ~100+ feedback records.

### Changed
- `src/scm/retriever.py` — default embedding model switched from BGE-base-en-v1.5 (768-dim) to all-MiniLM-L6-v2 (384-dim) for 22x faster cold start and higher accuracy; local model path auto-resolves from model name instead of hardcoded `bge-base`; ONNX auto-detects with `file_name="model_quantized.onnx"`; `_embedding_search_inner` SELECT includes `body` column (fixes `IndexError: No item with that key`).
- `scripts/download-embedding-model.py` — uses `datasets.Dataset` for calibration, `quantizer.fit()` before `quantize()` for proper ONNX export pipeline.
- Default search method: `hybrid` → `rrf` (RRF fusion).
- Test suite: 136 → **168 tests** (all passing).
- Version bumped to **0.7.0**.

### Fixed
- **Embedding search crash** (`IndexError: No item with that key`) — `_embedding_search_inner` SELECT query was missing the `body` column; `Skill.row_to_skill` crashed accessing `row["body"]`.
- **Model cache miss** — `_load_embedding_model` now searches `~/.scm/models/bge-base` before falling back to HF hub download.
- **ONNX export** — `AutoCalibrationConfig.minmax` now passes proper `Dataset` (not raw tensors); `quantize()` uses `save_dir` + `calibration_tensors_range` (correct optimal API).

---

## [0.6.2] - 2026-06-19 — Graceful YAML Frontmatter Parsing
- **Unquoted colons in YAML frontmatter** — `yaml.safe_load` now falls back to the naive parser when it fails (previously crashed and skipped the skill). Skills with descriptions like `"a skill to: create a page"` no longer error out during `scm index`.

### Added
- **`test_from_skill_file_unquoted_colon_in_description`** — regression test for unquoted colons in frontmatter.

### Changed
- `Skill.from_skill_file()` — YAML parsing now catches `Exception` instead of just `ImportError`, falling back to the naive parser on any parse failure.
- Test suite: 135 → **136 tests** (all passing).
- Version bumped to **0.6.2**.

---

## [0.6.1] - 2026-06-19 — Global Skills Auto-Detect

### Added
- **`.agents/skills/` to auto-detect** — `scm index` now also detects the global multi-agent skill directory (`~/.agents/skills/`) when it exists.

### Changed
- `SkillIndexer.AGENT_SKILL_DIRS` — `.agents/skills` added to the list of known agent skill paths.
- Version bumped to **0.6.1**.

---

## [0.6.0] - 2026-06-19 — Index Safety + Auto-Detect

### Added
- **Skip patterns during index** — `scm index` now skips hidden dirs (`.` prefix) and common noise dirs (`.git`, `node_modules`, `__pycache__`, `.venv`, `dist`, etc.) during recursive scanning. Prevents accidental full-home scans.
- **Auto-detect agent skill dirs** — `scm index` without `--dir` (or with `--all`) auto-detects existing agent skill directories (`~/.agents/skills/`, `~/.hermes/skills/`, `~/.claude/skills/`, `~/.cursor/skills/`, etc.) and indexes all of them.
- **Progress callback** — Shows `... scanned N/M` during long index operations.
- **8 new tests** — skip patterns (hidden, noise, custom, non-recursive), auto-detect (found, empty), progress callback (with and without).
- **`.agents/skills/` added to auto-detect** — global multi-agent skill directory now detected by default.

### Changed
- `SkillIndexer.index_directory()` now accepts `exclude` (extra skip patterns) and `progress_callback` params (backward-compatible).
- `SkillIndexer.DEFAULT_EXCLUDE` — class-level set of directory names to skip.
- `SkillIndexer.detect_skill_dirs()` — static method returning existing agent skill dirs.
- `SkillIndexer.AGENT_SKILL_DIRS` — list of known agent skill paths (relative to `$HOME`), including `.agents/skills/`.
- Version bumped to **0.6.0**.
- Test suite: 127 → **135 tests** (all passing).

---

## [0.5.0] - 2026-06-19 — Install Dir Relocation + Package Rename

### Changed
- **Default install directory** moved from `~/Workspaces/skill-context-manager` to `~/.scm/` — cleaner, hidden dotfile layout.
- **Database directory** moved from `~/.scm/` to `~/.scm/db/` — avoids conflict with source code when both live under `~/.scm/`.
- **Package name** renamed from `skill-context-manager` to `scm` — `uv tool upgrade scm` and `uv tool uninstall scm` now work.
- **Install method** switched to `uv tool install — no clone, no venv, no symlink. One command, ~3 seconds. `--dev` flag available for editable clone install.
- **`src/scm/db.py`** — `SCM_DB_DIR` updated to `~/.scm/db/`.
- **`scripts/install.sh`** — rewritten to use `uv tool install` as default; `--dev` for editable clone install.
- Version bumped to **0.5.0**.

### Migration
> If you have an existing database at `~/.scm/scm.db`, move it to `~/.scm/db/scm.db`:
> ```bash
> mkdir -p ~/.scm/db && mv ~/.scm/scm.db ~/.scm/db/scm.db
> ```

---

## [0.4.0] - 2026-06-19 — Agent Auto-Detection + Install Fixes

### Added
- **Agent auto-detection** (`src/scm/mcp_setup.py`) — `scm mcp setup --all`
  now auto-detects which agents are installed on the system (checks config dir
  and/or `PATH` binary for all 13 platforms).
- **`scm mcp setup --force-all`** — configure all 13 agents regardless of
  detection (use when the agent is installed but auto-detection heuristics
  don't recognise it).
- **`scm mcp status`** — now shows a detection marker (`✓`/`·`) per agent
  and reports `configured/detected_total` instead of `configured/total`.
- **`scm mcp setup --list`** — shows detection status for each platform
  (e.g. `✓ --claude-code` vs `· --cursor`) with a summary line.

### Fixed
- **`install.sh` uninstall order** (CRITICAL) — MCP config cleanup now runs
  **before** the venv and source are removed. Previously it ran after
  `rm -rf $SCM_DIR`, which made `scm mcp setup --uninstall` impossible.
- **`install.sh` uninstall flag** — `--all` → `--force-all` so that
  uninstall cleans all 13 agents regardless of detection state.
- **`install.sh` fallback path** — when `scm` CLI is gone but the venv
  Python3 still exists, the installer calls `mcp_setup.configure_many()`
  directly to clean all agents.
- **`install.sh` cleanup helpers removed** — `_clean_mcp_hermes()` and
  `_clean_mcp_opencode()` replaced by the universal `scm mcp setup` path.

### Changed
- **`scm mcp setup --all` semantics** — now configures **detected only**
  (smart default). Pass `--force-all` for the old "all 13" behaviour.
- **13 detection heuristics** — each agent key has a `DETECTORS` entry
  that checks either the agent's config directory or its binary on PATH.
  Unknown keys default to "assume present".
- Version bumped to **0.4.0** (feature: auto-detection).
- Test suite: **127 tests** (all passing, ruff clean).

---

## [0.3.1] - 2026-06-19 — Bug Fix Patch

### Fixed
- **Zed config structure** — `mcp_setup.py` was generating nested `{"path": ..., "args": ...}` under `command`, but Zed expects flat `command`/`args` fields per [Zed MCP docs](https://zed.dev/docs/ai/mcp). Fixed to emit correct flat structure.
- **Zed config test** — Updated `test_mcp_setup.py` assertion to match the flat `command`/`args` format.
- **Ruff lint issues** — Fixed minor lint warnings across `cli.py`, `mcp_server.py`, `feedback.py`, `optimizer.py`, `reranker.py`, `retriever.py` (unused imports, naming convention).
- **`scripts/install.sh` skill dirs** — Expanded available skill directories for Hermes Agent integration.

### Changed
- Version bumped to **0.3.1** (patch release).
- **README.md** — Updated SkillRouter reference to arXiv preprint with permanent link, improved Anthropic eval accuracy numbers, corrected SKillRouter table label from "CVPR" to "arXiv".

---

## [0.3.0] - 2026-06-19 — Multi-Agent MCP Setup Registry

### Added
- **Multi-agent MCP setup registry** (`src/scm/mcp_setup.py`) — single command
  to configure SCM as an MCP server for **13 agent platforms**:
  `claude-code`, `claude-desktop`, `cursor`, `windsurf`, `cline`, `gemini`,
  `vscode`, `zed`, `codex`, `goose`, `continue`, `opencode`, `hermes`.
- **`scm mcp setup --all`** — configure all 13 platforms at once.
- **`scm mcp setup --<agent>`** — per-agent flags (e.g. `--claude-code`).
- **`scm mcp setup --list`** — show all supported agents with config paths.
- **`scm mcp status`** — enhanced to check config across all platforms.
- **8 config writers** — JSON (`mcpServers`, `servers`, `context_servers`,
  `mcp`), YAML (`mcp_servers`, `extensions`, `mcpServers` list), and
  TOML (`[mcp_servers.scm]`).
- **`tests/test_mcp_setup.py`** — 26 tests covering every platform, add/
  remove/status/idempotency/merge safety/unknown platform.
- `mcp>=1.0` added to core dependencies (headline MCP feature now works
  out of the box).

### Fixed
- **`mcp` package missing from dependencies** — `scm.mcp_server` would crash
  with `ModuleNotFoundError` on a fresh install. Now a core dependency.
- **Bash scripts with Python docstrings** — `install-mcp-hermes.sh`,
  `install-mcp-opencode.sh`, `scm-mcp.sh` opened with Python `"""` blocks
  that bash would try to execute as commands.
- **`datetime.utcnow()` deprecation (Python 3.13+)** — `db.py`, `models.py`,
  `session.py`, `tracker.py` now use timezone-aware UTC via `datetime.now(timezone.utc)`.
- **MCP test crash when `mcp` not installed** — `test_regression.py` test
  `test_all_tools_return_dict` now skip-safe with `pytest.importorskip`.
- **README version badge mismatch** — badge said 0.2.2, footer said 0.2.1.

### Changed
- CLI refactored: `scm mcp setup` now registry-driven instead of hardcoded
  per-agent branches. Adding a new agent = one entry in the registry dict.
- Version bumped to **0.3.0** (major feature: multi-agent registry).
- Test suite: 101 → **127 tests** (all passing).

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

|| Version | Date       | Type   | Highlights |
||---------|-----------|--------|------------|
|| 0.6.2   | 2026-06-19 | Patch  | Graceful YAML parsing (unquoted colons in frontmatter) |
|| 0.6.1   | 2026-06-19 | Patch  | Auto-detect `.agents/skills/` global skills dir |
|| 0.6.0   | 2026-06-19 | Minor  | Index safety (skip hidden/noise dirs), auto-detect skill dirs, progress |
|| 0.5.0   | 2026-06-19 | Minor  | Install dir ~/.scm/, DB under ~/.scm/db/, package rename to scm |
|| 0.4.0   | 2026-06-19 | Minor  | Agent auto-detection, install.sh fixes, `--all`/`--force-all` split |
|| 0.3.1   | 2026-06-19 | Patch  | Zed config fix, ruff lint fixes, expanded skill dirs |
|| 0.3.0   | 2026-06-19 | Minor  | Multi-agent MCP setup registry, 13 platforms, bug fixes |
|| 0.2.2   | 2026-06-18 | Minor  | scm mcp setup CLI, uv-first install, README restructured |
|| 0.2.1   | 2026-06-18 | Patch  | 16 bug fixes, 24 regression tests |
|| 0.2.0   | 2026-06-18 | Minor  | Initial public release, MCP server |

[Unreleased]: https://github.com/Mavis2103/skill-context-manager/compare/v0.6.2...HEAD
[0.6.2]: https://github.com/Mavis2103/skill-context-manager/compare/v0.6.1...v0.6.2
[0.6.1]: https://github.com/Mavis2103/skill-context-manager/compare/v0.6.0...v0.6.1
[0.6.0]: https://github.com/Mavis2103/skill-context-manager/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/Mavis2103/skill-context-manager/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/Mavis2103/skill-context-manager/compare/v0.3.1...v0.4.0
[0.3.1]: https://github.com/Mavis2103/skill-context-manager/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/Mavis2103/skill-context-manager/compare/v0.2.2...v0.3.0
[0.2.1]: https://github.com/Mavis2103/skill-context-manager/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/Mavis2103/skill-context-manager/releases/tag/v0.2.0
