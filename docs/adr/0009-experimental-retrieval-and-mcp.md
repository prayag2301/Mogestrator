# ADR-0009 — Experimental local retrieval and repository-bound MCP

Status: Accepted for 0.2.0a1 · Date: 2026-09-18

## Decision

Ship retrieval and MCP as a prerelease while retaining the M2 evaluation gate.
Exact names and quoted FTS tokens seed a bounded weighted graph traversal. Fuse
lexical, semantic (when requested), and structural ranks, then apply small recency,
pin, and staleness preferences. This makes offline retrieval useful without
implicitly downloading model weights or calling an API.

Resolve Q1 provisionally with optional FastEmbed and BAAI/bge-small-en-v1.5.
SQLite schema v2 stores vectors and model/content cache entries. Inference is
batched; cached content is reused across structural rebuilds. Ranking is an exact
cosine scan, not the planned scalable sqlite-vec k-NN index. Model/dimension
mismatches fail into explicit lexical fallback. Existing v1 databases migrate
without losing episodic nodes.

Use conservative UTF-8 byte accounting for context budgets, including compact
JSON metadata. Unlike a characters/4 estimate, this bounds byte-level tokenizer
cost without a tokenizer-data download. It underfills many model windows;
model-specific tokenizers remain a future optimization. MCP wrappers are outside
the context budget.

Source views reparse current files and verify hashes instead of trusting stale
byte offsets. Ingest labels are checked again on retrieval, including changed
source and resolved symlink targets. Gated content is excluded from search and
expansion. Results are structured untrusted data with anchors and provenance.

Use the supported MCP 1.x SDK, constrained to >=1.30,<2, with stdio and loopback
Streamable HTTP. Each tool call owns its SQLite connection, and a service lock
serializes operations with the optional polling watcher. The server is bound to
one repository, does not accept client-supplied roots, and cannot be exposed on
an arbitrary host through its CLI.

Bring forward only the memory operations needed for useful MCP sessions:
explicit episodic writes, verbatim recall, persistent pins, why queries, and
query-plus-reference context handles. Every write records its origin/episode.
MCP memory writes require a startup flag and cannot create corrections,
constraints, or conventions. No tool accepts a caller-supplied trusted origin.
This does not implement automatic capture, eviction, or the M5 policy plane.

## Alternatives

- Requiring embeddings would break the offline first-run behavior and add model
  download and runtime costs for exact-symbol lookup.
- Implementing a custom JSON-RPC server would duplicate protocol negotiation,
  transport security, schemas, cancellation, and SDK interoperability.
- Returning stored offsets after edits can expose the wrong source span; the
  additional parse is preferable to silently incorrect context.
- Calling this a completed M2 would bypass the evidence gate. Publish authored
  smoke results, including misses, without treating them as B0/B1 evidence.
