# Mogestrator

A local code knowledge graph for coding agents. Mogestrator indexes a repository,
retrieves scoped context, and detects when anchored code or remembered facts have
changed. Code stays local; no account or API key is required.

**Status:** `0.1.1` is the stable indexing release. `0.2.0a2` completes M1 and
includes experimental retrieval and MCP integration. The broader M2 quality gate remains open: there
is no evidence yet that this beats ripgrep or chunk RAG on real development tasks.

## Install and try

Python 3.11+:

```bash
pip install --pre "mogestrator[mcp]==0.2.0a2"
mog init
mog index
mog search "verify_token" --explain
mog verify
```

Or install this checkout:

```bash
uv venv
uv pip install -e ".[mcp]"
source .venv/bin/activate
```

Version `0.0.1` was a name-only placeholder. Check [PyPI](https://pypi.org/project/mogestrator/)
for available versions. Other distribution channels (Homebrew, standalone binaries,
Docker, npm) remain planned.

## What works

| Area | Available behavior |
|------|--------------------|
| Index | Python, TypeScript/JavaScript, Go, Rust; incremental SQLite graph; file-level fallback |
| Anchors | Content hashes, drift reports, stale memory labels; offsets reparsed before reading bodies |
| Search | Exact symbol and FTS5 seeds, bounded graph expansion, weighted rank fusion, recency/pin preference |
| Semantic search | Optional local BGE embeddings, model/content cache, exact cosine scan, lexical fallback |
| Inspection | Progressive L0–L3 views, neighbors, reverse-call impact and affected tests |
| Memory | Explicit remember/why/recall, provenance, persistent memory pins and context handles |
| MCP | Stdio and loopback Streamable HTTP, optional incremental index polling |
| Ingest gate | Credential-shaped content excluded from storage/retrieval; reads confined to the repository |

## Retrieval and inspection

```bash
mog index [--full]
mog status [--secrets]
mog verify [--strict]
mog show src/auth.py::verify_token
mog map

mog search "where are refresh tokens validated" --budget 4000 --explain
mog search verify_token --kind symbol --json
mog expand src/auth.py::verify_token --zoom L2
mog impact verify_token --depth 2 --tests
mog neighbors verify_token --edge calls --reverse
```

All commands above take `--repo PATH`. `index`, `status`, `verify`, and retrieval
commands support `--json`. Path-qualified lookups must match the file; ambiguous
retrieval targets require a path or node ID.

Search combines exact/FTS ranks with bounded bidirectional graph spread. Every
result includes an anchor, current freshness, trust labels, and a provenance
path. Graph traversal is capped; call resolution is a name-based heuristic,
not a complete static analyzer. Local `imports` links and Git-derived `co_changed`
edges are conservative ranking hints.

Zoom levels: **L0** summarizes the node's file; **L1** shows signatures;
**L2** reads a symbol body; **L3** reads the full file. Automatic zoom falls
back to signatures for large hits so related results can fit the budget. Blame/diff augmentation is
not implemented. Source views are reparsed, so edits above a symbol cannot make
stored offsets return the wrong body. If a source changed, current content is
labelled stale relative to the stored anchor. Missing or out-of-tree source
bodies are not returned.

The context budget includes the compact JSON payload and metadata. Accounting
uses a conservative UTF-8 byte upper bound for byte-level tokenizers, not an
exact model-specific token count. Large bodies are explicitly truncated;
items whose metadata cannot fit are omitted. MCP transport wrappers and client
formatting are outside this budget.

### Optional local semantic search

```bash
pip install --pre "mogestrator[embeddings]==0.2.0a2"
mog index
mog embed                      # first run downloads BAAI/bge-small-en-v1.5
mog search "validate a user's login credentials" --semantic
```

Inference is local. Model weights are cached in the platform's Mogestrator cache;
`MOG_MODEL_CACHE` can override the directory. Embeddings are cached in SQLite by
model and content hash. Run `mog embed` again after indexing changes; watch mode
updates the graph, not embeddings. Without a semantic index, the search explicitly
falls back to FTS and graph retrieval. Vector ranking currently scans the cached
vectors; large-repository k-NN indexing remains future work.

## Memory

```bash
mog remember decision "Use local validation to avoid a network dependency" --anchor verify_token
mog why "local validation"
mog recall deci_ID --json
mog pin deci_ID
mog unpin deci_ID
```

Memories survive re-indexing and process restarts. Anchored facts retain their
original hashes, so editing a source marks them stale instead of silently
rewriting history. Unanchored memories have no source drift check. `recall`
returns the original recorded content. Pinning is persisted for ranking and
future working-set management; automatic eviction, recall stubs, conflict
resolution, and conversation capture are **not implemented**.

## MCP integration

Start the stdio server for a fixed repository:

```bash
mog serve --mcp --repo /absolute/path/to/repo --watch
```

Use this command as the server executable in an MCP client. For example, a
client configuration entry can launch:

```json
{
  "mcpServers": {
    "mog": {
      "command": "mog",
      "args": ["serve", "--mcp", "--repo", "/absolute/path/to/repo", "--watch"]
    }
  }
}
```

`--watch` creates an initial index and polls for changes every second. Protocol
stdout stays clean; diagnostics go to stderr. For Streamable HTTP:

```bash
mog serve --mcp --transport http --port 8765 --repo /absolute/path/to/repo --watch
# endpoint: http://127.0.0.1:8765/mcp
```

HTTP binds only to loopback; remote authenticated/multi-user hosting is not part
of this release.

Tools: `search_context`, `expand`, `impact`, `neighbors`, `why`, `remember`,
`recall`, `pin`, `unpin`, `verify`, `create_context`, `load_context`.
`create_context` saves a query plus optional pinned references as a `ctx://`
handle; another client can materialize it against the current index with
`load_context`. Source references survive ordinary re-indexing.

**Writes are opt-in:** add `--allow-memory-writes` to enable remember, pin/unpin,
and context-handle creation. Agent-origin writes may create decisions, failures,
and tasks; they cannot create higher-trust corrections or constraints. All
returned repository and memory content is untrusted data. Labels are useful
provenance, not a complete prompt-injection defense; the policy gateway remains
planned. Credential detection is heuristic, not a guarantee of detecting every
secret.

## Validation and remaining work

CI tests installed packages on macOS, Linux, and Windows with Python 3.11/3.13.
Tests exercise real MCP clients over both transports, watch refresh, persistence,
stale sources, ingest restrictions, token budgets, and embedding-cache behavior.
The optional local BGE backend also has a manually verified smoke path.

`python scripts/evaluate_retrieval.py --repo . --dataset tests/fixtures/retrieval_cases.json --output /tmp/retrieval.json`
runs authored localization smoke cases. Results are recorded in
[docs/results](docs/results/). These cases are not the pinned real-refactor
corpora or B0/B1 comparisons required by [EVALUATION.md](docs/EVALUATION.md).
M2 remains experimental until those gates pass. Historical M1 performance
figures are in [ARCHITECTURE.md](docs/ARCHITECTURE.md); they do not describe the
latency or index size of this new retrieval implementation.

[ROADMAP.md](docs/ROADMAP.md) tracks the remaining work: evaluation gates,
scalable vectors, complete working-set management, and the policy gateway.
M1 indexing, import/co-change links, rename continuity, and the revised size
target are complete; see the [pinned benchmark](docs/results/m1-index-2026-09-25.md).
See [PLAN.md](docs/PLAN.md),
[SPEC-context-graph.md](docs/SPEC-context-graph.md),
[SPEC-config.md](docs/SPEC-config.md), and [DISTRIBUTION.md](docs/DISTRIBUTION.md).

MIT.
