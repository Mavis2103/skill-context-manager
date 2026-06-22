# Architecture Overview

## Core Problem

AI agents (Claude Code, Cline, Hermes Agent, OpenCode, and 9+ other platforms) suffer from **skill overload**:
when too many skills/tools are available, the agent's performance degrades due to:

1. **Context window bloat** — Tool definitions consume 30-60K tokens pre-conversation
2. **Selection accuracy decay** — >30-50 tools causes >30% accuracy drop in tool choice
3. **"Lost in the Middle"** — Skills in mid-context get less attention
4. **Session amnesia** — Agent forgets which skills it used earlier
5. **Metadata insufficiency** — SkillRouter (CVPR 2026) shows metadata alone causes 44% accuracy drop

## Design Principles

### 1. Two-Stage Retrieval (Retrieve → Rerank)
- **Stage 1 (Retriever)**: Fast BM25 + embedding hybrid scan of all skills → top 20
- **Stage 2 (Reranker)**: Cross-encoder deep matching on top 20 → top 5
- Reference: SkillRouter (CVPR 2026) achieves 74% Hit@1 on 80K skills

### 2. Progressive Disclosure + Session Awareness
- Only metadata (30-50 tokens/skill) loaded at startup
- Full body loaded only when skill is selected
- Session tracker prevents re-loading and boosts recently-used skills

### 3. Self-Improving via Feedback
- Bayesian update of skill weights based on success/failure
- Query pattern learning (auto-map query keywords → best skill)
- Usage analytics to identify unused skills

### 4. Token-Optimized Metadata
- Compress descriptions to 120 chars max
- Add action prefixes for better semantic matching
- Auto-extract tags from skill body

## Data Flow

```
User Request
    │
    ▼
┌─────────────────────────────────────────────────┐
│ 1. Query Analysis                               │
│    - Strip stopwords                            │
│    - Extract key terms                          │
│    - Embed query (if embedding enabled)         │
└─────────────────────┬───────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────┐
│ 2. Stage 1: Retrieval (top 20)                   │
│    ┌──────────┐   ┌──────────┐   ┌──────────┐   │
│    │  BM25    │ + │Embedding │ = │  RRF     │   │
│    │ (FTS5)   │   │ (cosine) │   │ (k=60)   │   │
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
│    - Session context (~50 tokens)                │
│    - Agent loads only the 1 skill body it needs  │
└─────────────────────────────────────────────────┘
```

## Storage Layer

### SQLite Databases
| Database | Purpose | Tables |
||----------|---------|--------|
|| `scm.db` (shared) | Single shared database | `skills`, `skills_fts` (FTS5), `sessions`, `session_skills`, `feedback`, `skill_weights`, `query_patterns`, `usage_events`, `daily_stats` |

### FTS5 for BM25
- SQLite FTS5 provides zero-dependency full-text search
- BM25 ranking built-in
- Porter stemmer for word normalization

## Token Economics

### Typical session token flow:

```
Without SCM:
┌──────────────────────────────────────────┐
│ Session start: load 50 skills metadata   │
│   = 50 × 60 tokens = 3,000 tokens        │
│ Agent picks 1, but ALL 50 stay           │
│ Session grows: +5K tokens history        │
│ Agent forgets which skill was active     │
│ Agent re-loads all 50: +3,000 tokens     │
│ Total waste: ~6,000+ tokens per session  │
└──────────────────────────────────────────┘

With SCM:
┌──────────────────────────────────────────┐
│ Session start: load active skills only   │
│   = 3 × 15 tokens = 45 tokens            │
│ When needed: query → top 5 metadata      │
│   = 5 × 40 tokens = 200 tokens           │
│ Agent picks 1 → load 1 body              │
│ Session tracker remembers used skill     │
│ Total: ~245 tokens per query             │
│ Savings: 85-98% on skill context         │
└──────────────────────────────────────────┘
```

## Graceful Degradation

SCM works at every dependency level:

| Dependencies | Features Available |
|-------------|-------------------|
| Python stdlib only | BM25 (FTS5) + session tracking |
| + sentence-transformers | Semantic embedding search |
| + transformers + torch | Cross-encoder reranking |
| + feedback data | Self-improving weights |

## Integration Patterns

### 1. Agent-Agnostic (CLI)
```bash
# Any agent can use SCM via shell
TOP_SKILL=$(scm query "$TASK" --top 1 --format json | jq -r '.results[0].name')
echo "Recommended skill: $TOP_SKILL"
```

### 2. Hermes Agent Skill
SCM itself as a skill that other skills register through.

### 3. MCP Server Mode
SCM as an MCP server exposing `skill_query`, `skill_index`, `skill_feedback` tools.

## References

1. **SkillRouter** - Zheng et al., CVPR 2026. Retrieve-and-Rerank at 80K scale.
2. **Anthropic Tool Search** - BM25-based deferred loading, 85% token reduction.
3. **SkillRouter Attention Analysis** - 91.7% attention on skill body, 1.0% on description.
4. **Hermes Agent Skills** - Progressive disclosure with metadata-only indexing.
5. **Claude Code Skills** - Name + description in context, body on demand.
