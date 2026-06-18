# Skill Context Manager (SCM)

> **Context-aware skill selection for AI agents — Solves the "too many skills" problem.**
>
> Reduces skill context tokens by **85-98%**, improves skill selection accuracy, and learns from feedback.

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![SQLite FTS5](https://img.shields.io/badge/search-BM25%20%2B%20Embedding%20%2B%20Cross--encoder-green)](https://sqlite.org/fts5.html)
[![MCP](https://img.shields.io/badge/MCP-Server%20Ready-purple)](https://modelcontextprotocol.io)
[![Version](https://img.shields.io/badge/version-0.2.2-orange)](CHANGELOG.md)
[![Tests](https://img.shields.io/badge/tests-101%20%E2%9C%94%EF%B8%8F-brightgreen)]()

---

## The Problem

| Problem | Impact | Root Cause |
|---------|--------|------------|
| Users install 50-100+ skills | 30K-60K tokens consumed pre-conversation | Static tool injection doesn't scale |
| Agent loses direction | Accuracy drops from 95% → **<50%** (Anthropic eval, >50 tools) | "Lost in the Middle" + metadata overload |
| Forgets skills after 20-30 messages | Re-reads everything every turn | No session memory |
| Similar skills indistinguishable | Can't decide which to pick | Keyword search is insufficient |
| Wrong skill selected | Wasted tokens + cost from retries | No feedback loop |

### Research References

- **SkillRouter (CVPR 2026)**: 91.7% of cross-encoder attention goes to **skill body**, only 1.0% to description — metadata alone is insufficient.
- **Anthropic Tool Search**: BM25-based deferred loading, 85% token reduction.
- **Anthropic internal eval**: Opus 4 accuracy from 79.5% → 49% with >50 tools.

## Solution

SCM is a **proxy layer** between the agent and the skill directory. Instead of loading all skills into context, SCM performs:

1. **Two-Stage Retrieval (SkillRouter architecture)** — Retrieve → Rerank
2. **Session Memory** — Remembers which skills were used, boosts them when relevant
3. **Feedback Loop** — Bayesian weight updates from success/failure data
4. **Single Shared Database** — Eliminates cross-DB bugs
5. **Graceful Degradation** — Works at every dependency level

### Token Savings

| Scenario | Before | After | Savings |
|----------|--------|-------|---------|
| 100 skills metadata loaded | ~30K tokens | **~300 tokens** | **99%** |
| 50 MCP tools loaded | ~72K tokens | **~8.7K tokens** | **88%** |
| Session tracking (50 messages) | Skills forgotten | **100% recall** | N/A |
| Query latency (77 skills) | — | **~7ms (BM25)** | Instant |

## Installation

### Requirements

- **Python 3.11+**
- **uv** (Astral) — auto-installed if missing
- **git**

### One-Click Install (recommended)

```bash
# Basic install (18 seconds)
curl -fsSL https://raw.githubusercontent.com/Mavis2103/skill-context-manager/main/scripts/install.sh | bash

# With MCP auto-setup (configures Hermes Agent + OpenCode)
curl -fsSL https://raw.githubusercontent.com/Mavis2103/skill-context-manager/main/scripts/install.sh | bash -s -- --with-mcp

# Custom directory
curl -fsSL ... | bash -s -- --scm-dir ~/custom/path
```

The installer will:

| Step | What happens |
|------|-------------|
| ✅ Pre-flight | Check Python 3.11+, install `uv` if needed |
| ✅ Clone | `git clone --depth 1` to `~/Workspaces/skill-context-manager` |
| ✅ Venv | `uv venv` + `uv pip install -e .` — zero-dependency core |
| ✅ Symlink | `~/.local/bin/scm` — auto-PATH via profile.d + shell rc |
| ✅ Index | Auto-index `~/.hermes/skills/`, `~/.claude/skills/`, `~/.cursor/skills/` |
| ✅ Sanity | Smoke test + version check |

### Manual Install

```bash
# Requirements: Python 3.11+, uv, git
git clone https://github.com/Mavis2103/skill-context-manager.git
cd skill-context-manager
uv venv
source .venv/bin/activate
uv pip install -e .

# Optional: AI models for embedding search and reranking
uv pip install scm[full]

# Index common skill directories
scm index --dir ~/.hermes/skills/

# Add to PATH
echo 'export PATH="$PATH:'$(pwd)'/.venv/bin"' >> ~/.bashrc
source ~/.bashrc
```

### Uninstall

```bash
curl -fsSL https://raw.githubusercontent.com/Mavis2103/skill-context-manager/main/scripts/install.sh | bash -s -- --uninstall
```

Removes: source, venv, database, symlink, PATH config, MCP config.

## Quick Start

```bash
# 1. Index your skills (10 seconds)
scm index --dir ~/.hermes/skills/
# ✅ Indexed 24 skills
# 📊 Total: 24 skills | 1,847 meta tokens | 12,430 body tokens
# 📁 Categories: devops, software-development, mlops, creative, data-science

# 2. Query — find the most relevant skill
scm query "deploy application to kubernetes" --top 3
# 🔍 Top 3 skills for: "deploy application to kubernetes"
#   1. kubernetes-deploy
#      Deploy and manage Kubernetes clusters
#      [██████████] 92% | hybrid | ~15t meta / ~420t body

# 3. Start session tracking
scm session start --id my-session-1
scm session use --skill kubernetes-deploy --query "deploy app"

# 4. Get token-optimized context (only ~30 tokens)
scm session context --id my-session-1 --query "current task"
```

## Features

### 1. Semantic Skill Retrieval

Find skills using **hybrid BM25 + embedding**, zero-dependency fallback.

```bash
# BM25 (FTS5) — stdlib only, fast, precise for keywords
scm query "kubernetes deploy helm" --method bm25

# Embedding — semantic search (requires sentence-transformers)
scm query "orchestrate container cluster management" --method embedding

# Hybrid (default) — best of both worlds
scm query "deploy app to production" --method hybrid
```

### 2. Session Tracking

Remembers which skills were used in a session — no more forgetting:

```bash
scm session start --id "chat-abc-123"
scm session use --skill k8s-deploy --query "deploy"
scm session use --skill docker-build --query "build image"

# Export context for the agent — only ~30 tokens
scm session context --id "chat-abc-123"

# Output:
# {
#   "active_skills": ["k8s-deploy", "docker-build"],
#   "context_size_tokens": 42,
#   "matching_skills": [...]
# }
```

### 3. Feedback Loop — Self-Learning

SCM improves over time:

```bash
# Record feedback
scm feedback record --query "deploy app" --skill k8s-deploy --success true --rating 5
scm feedback record --query "deploy app" --skill helm --success false

# View statistics
scm feedback stats
# 📊 Feedback Statistics
#    Total feedback:     47
#    Success rate:       87%
#    Query patterns:     12
#    Skills with data:   8
#    Top skills by success rate:
#      • k8s-deploy: 15/16 (94%)
#      • docker-build: 8/10 (80%)
```

### 4. Metadata Optimization

Compress descriptions to save tokens:

```bash
# Preview
scm optimize --dir ~/.hermes/skills/ --dry-run
# 📊 Potential savings:
#    Before: 1,847 meta tokens
#    After:  1,240 meta tokens
#    Saved:  607 tokens per load (33%)

# Apply
scm optimize --dir ~/.hermes/skills/ --no-dry-run
```

### 5. Usage Analytics

```bash
scm insights
# 📈 Usage Insights (last 30 days)
#    Total queries:     142
#    Tokens saved:      ~28,400
#    Retrieval methods: bm25: 89, hybrid: 42, embedding: 11
#    Top skills used:
#      • k8s-deploy: 23 times
#      • pytest-run: 18 times
#      • docker-build: 15 times

scm stats
# 📊 Skill Index Statistics
#    Total skills:     24
#    Categories:       5
#    Metadata tokens:  1,847
#    Body tokens:      12,430
```

## Architecture

```
User Request
    │
    ▼
┌─────────────────────────────────────────────────┐
│ 1. Query Analysis                               │
│    - Extract key terms                          │
│    - Embed query (if embedding enabled)         │
└─────────────────────┬───────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────┐
│ 2. Stage 1: Retrieval (top 20)                   │
│    ┌──────────┐   ┌──────────┐   ┌──────────┐   │
│    │  BM25    │ + │Embedding │ = │  Hybrid  │   │
│    │ (FTS5)   │   │ (cosine) │   │ (0.3+0.7)│   │
│    └──────────┘   └──────────┘   └──────────┘   │
└─────────────────────┬───────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────┐
│ 3. Context Injection                             │
│    + Session boost (recently used +0.5)          │
│    + Feedback weights (Bayesian prior)           │
└─────────────────────┬───────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────┐
│ 4. Stage 2: Rerank (top 5)                       │
│    Cross-encoder: query × skill body             │
│    "cross-encoder/ms-marco-MiniLM-L6-v2"         │
│    ~50ms on CPU for 20 candidates                │
└─────────────────────┬───────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────┐
│ 5. Output                                        │
│    - Top 5 skill names + descriptions (~300 t)   │
│    - Session context (~30 tokens)                │
│    - Agent loads only the 1 skill body it needs  │
└─────────────────────────────────────────────────┘
```

### Token Flow

```
Without SCM:
  Session start: load 50 skills metadata = 50 × 60 tokens = 3,000 tokens
  Agent picks 1, but ALL 50 stay in context
  Session grows → agent forgets → re-load all: +3,000 tokens
  Total waste: ~6,000+ tokens per session

With SCM:
  Session start: active skills only = 3 × 15 tokens = 45 tokens
  Query → top 5 metadata = 5 × 40 tokens = 200 tokens
  Session tracker: ~30 tokens
  Total: ~275 tokens per query
  Savings: 85-98%
```

## MCP Server

SCM runs as an **MCP server** with **11 tools**, compatible with any MCP-compatible agent (Hermes Agent, OpenCode, Claude Code, Cline, etc.).

### Quick Start

```bash
# Auto-configure for Hermes Agent + OpenCode (idempotent)
scm mcp setup --all

# Check configuration status
scm mcp status

# Start server in stdio mode (default — for Hermes/OpenCode)
python3 -m scm.mcp_server

# Start server in HTTP/SSE mode
python3 -m scm.mcp_server --http --port 8321
```

### Available Tools

| Tool | Layer | Description |
|------|-------|-------------|
| `skill_query` | Retrieve | Find the most relevant skills for a task |
| `skill_index` | Index | Index skills from a directory |
| `skill_stats` | Index | Get database statistics |
| `skill_session_start` | Session | Start a tracking session |
| `skill_session_use` | Session | Record skill usage |
| `skill_session_context` | Session | Export session context (~30 tokens) |
| `skill_session_end` | Session | End a session |
| `skill_optimize` | Optimize | Compress metadata to save tokens |
| `skill_feedback` | Feedback | Record usage feedback |
| `skill_feedback_stats` | Feedback | View feedback statistics |
| `skill_insights` | Analytics | Usage analytics dashboard |

### Hermes Agent Integration

```bash
# Auto-configure
scm mcp setup --hermes

# Or manual config: add to ~/.hermes/config.yaml
```

This adds to `~/.hermes/config.yaml`:

```yaml
mcp_servers:
  scm:
    command: python3
    args: ["-m", "scm.mcp_server"]
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
```

Test connection:

```bash
hermes mcp test scm
# ✓ Connected (738ms)
# ✓ Tools discovered: 11
```

After that, Hermes Agent automatically discovers and can call the MCP tools.

### OpenCode Integration

```bash
# Auto-configure
scm mcp setup --opencode

# Or manual config: add to ~/.config/opencode/opencode.json
```

This adds to `~/.config/opencode/opencode.json`:

```jsonc
{
  "mcp": {
    "scm": {
      "type": "local",
      "command": ["python3", "-m", "scm.mcp_server"],
      "enabled": true
    }
  }
}
```

After restarting OpenCode, MCP tools are auto-discovered. You can then prompt:

```
"use scm skill_query to find the best skill for deploying to kubernetes"
```

### Claude Code / Cline / Cursor / Continue.dev

```bash
scm mcp start --http --port 8321

# Then configure:
# Claude Desktop: mcpServers → scm → url: http://localhost:8321/sse
# Cline:          mcpServers → scm → command: ["python3", "-m", "scm.mcp_server"]
# Cursor:         MCP → Add → command: python3 -m scm.mcp_server
# Continue.dev:   experimental.mcpServers → scm → command: python3 -m scm.mcp_server
```

### Remote Mode (HTTP/SSE)

```bash
# Start server
python3 -m scm.mcp_server --http --port 8321

# Client config
{
  "mcpServers": {
    "scm": {
      "url": "http://localhost:8321/sse"
    }
  }
}
```

### Agent Skill Template (for Hermes Agent skills)

Create a `skill-router/SKILL.md`:

```markdown
---
name: skill-router
description: Select and load the most relevant agent skills using semantic search
---

When a skill needs to be selected for a task, use:
  scm query "<user_task>" --top 3 --format json
Then load the SKILL.md body of the top-matching skill.
```

## Graceful Degradation

| Dependencies | Features Available |
|-------------|-------------------|
| **Python stdlib only** | BM25 (FTS5) + Session tracking + Feedback |
| + `sentence-transformers` | Semantic embedding search |
| + `transformers` + `torch` | Cross-encoder reranking |
| + feedback data | Self-improving Bayesian weights |

The zero-dependency core works immediately without installing anything extra. AI models are optional.

## Comparison with Alternatives

| Solution | Progressive Discovery | Semantic Search | Session Memory | Feedback Loop | Token Cost | Zero-Dep |
|----------|---------------------|-----------------|----------------|---------------|------------|----------|
| **Claude Code Skills** | ✅ Load on-demand | ❌ Keyword | ❌ No | ❌ No | ~500 tokens | ❌ |
| **MCP Tool Search** | ✅ Deferred load | ✅ BM25 | ❌ No | ❌ No | ~500 tokens | ❌ |
| **SkillRouter (CVPR)** | ❌ All at once | ✅ Cross-encoder | ❌ No | ✅ Yes | Training needed | ❌ GPU |
| **Hermes Skills** | ✅ Metadata only | ❌ Keyword | ❌ No | ❌ No | ~3K tokens | ✅ |
| **Lunar MCPX** | ✅ Tool groups | ✅ Custom | ❌ No | ❌ No | ~8.7K tokens | ❌ |
| **✨ SCM (This)** | ✅ Metadata only | ✅ BM25 + Embedding + Cross-encoder | ✅ Full session tracking | ✅ Bayesian | **~275 tokens** | ✅ |

## Project Structure

```
skill-context-manager/
├── src/scm/
│   ├── __init__.py          # Version + schema init
│   ├── cli.py               # CLI interface (argparse, 9 subcommands)
│   ├── db.py                # Shared database connection (single DB, WAL)
│   ├── indexer.py           # Skill indexing engine (FTS5)
│   ├── retriever.py         # BM25 + embedding retrieval
│   ├── reranker.py          # Cross-encoder reranking
│   ├── session.py           # Session state tracker
│   ├── optimizer.py         # Skill metadata optimizer
│   ├── feedback.py          # Feedback collection + Bayesian learning
│   ├── tracker.py           # Usage analytics
│   ├── models.py            # Data models (Skill, QueryResult, SessionState, FeedbackRecord)
│   └── mcp_server.py        # MCP server (11 tools)
├── tests/
│   ├── test_models.py       # 13 tests — data models + YAML parsing
│   ├── test_indexer.py      # 11 tests — index/reindex/empty/WAL
│   ├── test_retriever.py    # 9 tests — BM25/hybrid/session boost/empty
│   ├── test_session_feedback.py  # 21 tests — session lifecycle + feedback
│   ├── test_optimizer.py    # 9 tests — compression/expansion/info-leak
│   ├── test_tracker.py      # 8 tests — recording/insights/daily-trend
│   ├── test_reranker.py     # 6 tests — fallback/empty/top-k/custom model
│   └── test_regression.py   # 24 tests — bug regression coverage
├── scripts/
│   ├── install.sh           # One-click install
│   ├── benchmark.sh         # Performance benchmark
│   └── demo.sh              # Interactive demo
├── configs/
│   └── default.yaml         # Default configuration
├── docs/
│   ├── ARCHITECTURE.md      # Detailed architecture docs
│   └── MCP-INTEGRATION.md   # MCP integration guide
├── pyproject.toml
├── LICENSE
└── README.md
```

### Storage

Single SQLite database (`~/.scm/scm.db`) with WAL mode:

| Table | Purpose |
|-------|---------|
| `skills` + `skills_fts` (FTS5) | Skill index + full-text search |
| `sessions` + `session_skills` | Session tracking |
| `feedback` + `skill_weights` + `query_patterns` | Feedback & learning |
| `usage_events` + `daily_stats` | Usage analytics |

## Workflow Example

### 1. Agent receives a new task

```
User: "Deploy app to production"

Agent internally calls:
  → skill_query(query="deploy app to production", top_k=3)
  → Returns: [kubernetes-deploy (0.92), docker-build (0.78), monitoring (0.45)]
  → Agent loads kubernetes-deploy SKILL.md, executes deploy steps
  → skill_session_use(session_id="...", skill_name="kubernetes-deploy", success=true)
```

### 2. Agent needs context injection

```
Agent generates system prompt block:
  "Session active skills: [kubernetes-deploy]
   Related skills: [docker-build, helm-chart]
   Estimated context: 15 tokens"
```

### 3. Agent encounters a similar task later

```bash
# This query doesn't need to scan all skills again.
# Session tracker knows kubernetes-deploy was used and boosts it.
# Saves 50-200 tokens per query.
scm session context --id "..." --query "scale deployment"
```

## Development

### Run Tests

```bash
# All 101 tests (77 original + 24 regression)
uv run pytest -v

# Specific module
uv run pytest tests/test_indexer.py -v

# Just regression tests
uv run pytest tests/test_regression.py -v

# Coverage (optional)
uv run pytest --cov=src/scm/ tests/
```

### Supported Skill Formats

- SKILL.md with YAML frontmatter (Hermes Agent, Claude Code)
- Plain text files (directory name = skill name)

### Database Migration

```bash
# SCM auto-migrates schema on version changes (CREATE TABLE IF NOT EXISTS)
# No manual migration needed
```

## Roadmap

- [x] Research & Architecture (SkillRouter, Anthropic, MCP scalability)
- [x] Core indexing engine (FTS5 + BM25)
- [x] Semantic retrieval (embedding + hybrid)
- [x] Session tracker with persistence
- [x] Metadata optimizer (compress + expand)
- [x] Cross-encoder reranker (miniLM)
- [x] Feedback loop with Bayesian weights
- [x] Usage analytics and insights
- [x] **MCP Server** (11 tools)
- [x] **Hermes Agent integration**
- [x] **OpenCode integration**
- [x] **Single shared DB** (eliminates cross-DB bugs)
- [x] **77 tests across all modules**
- [x] **101 tests + 16 bug fixes** (v0.2.1)
- [ ] GUI dashboard
- [ ] Multi-agent session sharing

## References

1. **SkillRouter: Retrieval-Augmented Skill Selection for LLM Agents at Scale** — Zheng et al., CVPR 2026. [arXiv:2603.22455](https://arxiv.org/abs/2603.22455)
2. **Advanced Tool Use & Tool Search** — Anthropic Engineering Blog. [Link](https://www.anthropic.com/engineering/advanced-tool-use)
3. **MCP Tool Scalability Problem** — Jenova AI. [Link](https://www.jenova.ai/en/resources/mcp-tool-scalability-problem)
4. **Skills Over MCPs: Context-Efficient Agent Capabilities** — Agentic Engineer. [Link](https://www.agentic-engineer.com/blog/2025-12-04-skills-over-mcps-context-efficiency)
5. **Beyond the Prompt: Agent Skills as Dynamic Context Management** — Dev.to. [Link](https://dev.to/peng_r_8a73c977039dac3b9c/beyond-the-prompt-understanding-agent-skills-as-dynamic-context-management-e5o)

## License

MIT — Copyright (c) 2026 Mavis2103

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for version history. Current: **v0.2.1**.
