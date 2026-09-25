# Architecture

> Status: **Design (pre-code)**. Contract for M1–M6.

## 1. Shape

`mogestrator` ships three runtime surfaces over one core:

- **`mog` CLI** — indexing, query, inspection, eval.
- **MCP server** (`mog serve --mcp`) — the primary integration; Claude Code and
  any MCP client consume CGM as tools.
- **Gateway** (`mog gateway`) — the policy plane for server-based chats.

State: one SQLite file (`.mog/graph.db`) plus policy layer files. No daemon
required, no external services, no account.

```
        ┌──────────── clients ────────────┐
        │ Claude Code · Cursor · your app │
        └────────┬─────────────┬──────────┘
             MCP │             │ HTTP (gateway mode)
        ┌────────▼─────────────▼──────────┐
        │           mogestrator            │
        │  ┌───────────────────────────┐   │
        │  │ POLICY PLANE              │   │ prompt assembly · taint tracker
        │  │                           │   │ egress firewall · capabilities
        │  └────────────┬──────────────┘   │ audit ledger
        │  ┌────────────▼──────────────┐   │
        │  │ RETRIEVAL                 │   │ seed → spread → fuse → pack
        │  └────────────┬──────────────┘   │ zoom renderer · working set
        │  ┌────────────▼──────────────┐   │
        │  │ GRAPH CORE                │   │ nodes · edges · anchors · staleness
        │  └────────────┬──────────────┘   │
        │  ┌────────────▼──────────────┐   │
        │  │ INDEXERS                  │   │ tree-sitter · git miner · embedder
        │  └───────────────────────────┘   │
        └────────────────┬─────────────────┘
                 SQLite (+ sqlite-vec, FTS5)
```

## 2. Package layout (target)

```
src/mog/
├── cli/                    commands only, no logic
├── graph/
│   ├── models.py           node/edge/anchor types
│   ├── store.py            SQLite DAL, migrations
│   ├── anchors.py          span hashing, drift detection      ← ADR-0003
│   └── query.py            neighbors, impact, closure
├── index/
│   ├── walker.py           file discovery, ignore rules
│   ├── parsers/            tree-sitter per language
│   ├── edges.py            calls/imports/tested_by extraction
│   ├── gitminer.py         co_changed edges from history
│   └── embed.py            embedding providers + content-hash cache
├── retrieve/
│   ├── seed.py             k-NN + FTS5 exact fallback
│   ├── spread.py           budgeted weighted graph walk         ← core
│   ├── fuse.py             weighted reciprocal-rank fusion
│   ├── zoom.py             L0–L3 renderers
│   └── pack.py             token budgeting, provenance stamping
├── memory/
│   ├── ledger.py           episodic append-only log
│   ├── workingset.py       scoring, eviction, stubs, recall     ← ADR-0004
│   └── conflict.py         contradicts detection
├── policy/
│   ├── layers.py           signed layer loading + digest pinning ← SPEC-policy §2
│   ├── taint.py            label propagation
│   ├── flows.py            flow-rule evaluation
│   ├── egress.py           resolve-pin-connect firewall
│   ├── capability.py       short-lived scoped tokens
│   └── audit.py            append-only audit records
├── serve/
│   ├── mcp.py              MCP tool surface
│   └── gateway.py          HTTP policy plane
└── eval/                   harness, suites, baselines
```

## 3. Protocols (the public API, SemVer'd)

```python
class Parser(Protocol):        # per language
    def parse(self, path, src) -> tuple[list[Node], list[Edge]]: ...

class Embedder(Protocol):
    model_id: str; dim: int
    def embed(self, texts: list[str]) -> list[Vector]: ...

class Retriever(Protocol):
    def retrieve(self, q: Query, budget: int) -> ContextPack: ...

class PolicyEngine(Protocol):
    def assemble(self, layers, ctx, task) -> Prompt: ...
    def check_egress(self, req: EgressRequest) -> Decision: ...
    def propagate(self, inputs: list[Labeled]) -> Labels: ...
```

Third parties register via entry points: `mog.parsers`, `mog.embedders`,
`mog.retrievers`, `mog.policies`.

## 4. Request paths

**Retrieval** (`search_context`): parse query → embed (1 call) → k-NN seeds +
FTS5 exact matches → budgeted spread → fuse → choose zoom → render → stamp
provenance and trust labels → return. Everything after the embedding is indexed
SQL, so p95 is dominated by one embedding call.

**Indexing**: walk → hash → skip unchanged → parse → extract edges → mine git →
embed only new content → batch write → recompute anchor drift. Incremental
throughout; a full rebuild is always safe because structural nodes are a cache.

**Gateway request**: authenticate user → mint capability → assemble prompt from
signed layers + labelled context → run → intercept every tool egress through the
firewall → propagate taint on results → write the audit record.

## 5. Failure and degradation

The system degrades rather than breaking:

| Condition | Behaviour |
|-----------|-----------|
| Index stale | Serve with a freshness warning; exit 4 in `--strict`/CI |
| Embeddings unavailable | Fall back to FTS5 + graph expansion — worse recall, still useful |
| Unsupported language | File-level nodes + FTS5; no symbol edges |
| Graph DB corrupt | Rebuild structural nodes from source; **episodic nodes restore from the WAL-backed ledger**, which is the only irreplaceable data |
| Policy layer digest mismatch | **Hard fail.** Never run with unverified instructions. |
| Egress rule ambiguous | Deny, and log the ambiguity |

## 6. Performance — measured at M1

Fresh index on django/django `a3d71038401b9340f550f2fdf1caaad66465ba88`
(4,852 indexed files, 34.0 MB of source, ~1.05M indexed lines), Apple M1,
Python 3.11, no embeddings. [Reproduction and full results](./results/m1-index-2026-09-25.md).

| Operation | Target | Measured | |
|-----------|--------|----------|---|
| Full index, 50k LOC | < 60s | **18.23s for ~1.05M indexed lines** | ✅ |
| Incremental re-index, no changes | < 2s | **0.78s** | ✅ |
| Nodes / edges produced | — | 45,817 / 200,773 | |
| Anchored search | Result returned | `Model.save` at `django/db/models/base.py::Model.save` | ✅ |
| **DB size** | **< 400% of source** (ADR-0007) | **125.55 MB / 34.01 MB = 369%** | ✅ |

The original <15% size guess was retired by [ADR-0007](./adr/0007-index-scale-findings.md).
Contentless FTS in schema v3 removes a duplicate copy of previews while retaining
the token index. These M1 measurements do not include optional embedding vectors
or establish the M2 retrieval quality and latency gates.

## 7. Cross-platform

macOS, Linux, Windows are first-class: `pathlib` throughout, no POSIX-only
shelling from core, tree-sitter grammars shipped as prebuilt wheels, SQLite WAL
mode with a documented fallback for network filesystems where WAL is unsafe.
