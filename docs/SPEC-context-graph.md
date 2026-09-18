# Spec: Context Graph Memory (CGM)

> Status: **Draft v1**, normative for M1–M4.
>
> This is the core of Mogestrator and the alternative to context compaction.
> Rationale and rejected alternatives: [ADR-0002](./adr/0002-graph-memory-over-summarization.md),
> [ADR-0003](./adr/0003-content-anchors-and-staleness.md),
> [ADR-0004](./adr/0004-eviction-with-recall.md).

## Implemented subset in 0.2.0a1

Experimental exact/FTS plus optional local semantic retrieval, bounded graph
expansion, rank fusion, conservative budget packing, current-source L0–L3 views,
impact/neighbors, explicit memories, pins, and context handles are implemented.
MCP supports stdio and loopback HTTP with optional polling. See ADR-0009 for
limits: vector ranking is an exact scan; no L3 blame/diffs, automatic eviction,
rename continuity, or completed M2 quality benchmark. This draft continues to
describe the target design beyond the implemented subset.

---

## 1. Node types

Every node has: `id` (ULID), `kind`, `labels` (trust/taint, see SPEC-policy),
`created_at`, `updated_at`, `embedding_id?`, `anchor?`, `state ∈ {fresh, stale,
superseded, retracted}`.

### Structural nodes — derived from the repo, rebuildable
| Kind | Content | Source |
|------|---------|--------|
| `Repo` | root, remote, default branch | git |
| `Module` | package/directory, purpose summary | tree-sitter + inference |
| `File` | path, language, size, content hash | filesystem |
| `Symbol` | fn/class/method/const: name, signature, docstring, span, body hash | tree-sitter |
| `Test` | test symbol + what it exercises | tree-sitter + naming/import heuristics |
| `Config` | env vars, feature flags, settings keys | parsers per format |

### Episodic nodes — learned, not rebuildable, the irreplaceable half
| Kind | Content | Why it exists |
|------|---------|---------------|
| `Decision` | what was chosen, rationale, alternatives rejected | Survives the reset that kills F1 |
| `Failure` | approach tried, exact error, why it can't work | Answers F4 — stops retry loops |
| `Constraint` | an invariant discovered or stated ("CI has no network") | Never re-derivable from code |
| `Convention` | a project norm ("errors wrap, never bare-raise") | Cheap to state, expensive to infer |
| `Correction` | a user correcting the agent | Highest-value node type; never auto-evicted |
| `Task` | a unit of work, its status and outcome | Threads episodes together |
| `Episode` | one session/run, with participants and time range | Provenance root |

**Design note:** structural nodes are a cache — delete and rebuild in minutes.
Episodic nodes are the actual asset. Back up accordingly; they are what makes
session #40 cheaper than session #1.

## 2. Edge types

Directed, typed, weighted. Weights drive graph expansion (§5).

| Edge | Meaning | Default weight |
|------|---------|----------------|
| `defines` | File → Symbol | 0.9 |
| `calls` | Symbol → Symbol | 0.8 |
| `imports` | File → File/Module | 0.6 |
| `tested_by` | Symbol → Test | 0.85 |
| `configures` | Config → Symbol | 0.7 |
| `co_changed` | Symbol ↔ Symbol (git history correlation) | 0.5 |
| `about` | Episodic node → structural node | 0.95 |
| `caused_by` | Failure → Symbol/Config | 0.9 |
| `supersedes` | Decision → Decision | 1.0 |
| `contradicts` | any ↔ any (flagged for review) | — |
| `derived_from` | fact → source episode | provenance only |

`co_changed` is worth calling out: two symbols that change in the same commit 8
times out of 10 are coupled in a way no import graph shows. It is mined from git
log and is often the highest-signal edge for "what else do I need to touch?"

## 3. Anchors and staleness

The mechanism that makes memory falsifiable (F2).

```
anchor := {
  path:       "src/auth/middleware.py",
  symbol:     "verify_token",
  span_hash:  "sha256:9f3c…",   # hash of the normalized symbol body
  file_hash:  "sha256:1a77…",
  commit:     "b4ef335",
  captured_at: "2026-08-28T10:14:02Z"
}
```

Rules:

1. **Never store line numbers as identity.** They shift on every edit above the
   span. `span_hash` over the normalized body (whitespace- and comment-stripped
   for structural nodes) is stable across reformatting and unrelated edits.
2. On re-index, recompute `span_hash`. Mismatch → every fact anchored to it
   flips to `stale` and is **labelled, not deleted**.
3. A `stale` fact is still served when relevant, always with its staleness and a
   diff hint: `⚠ stale — verify_token changed 3 commits ago`. Silent staleness is
   worse than absence; labelled staleness is often still useful.
4. Symbol renames are matched by body-hash continuity, so a rename does not
   orphan history.
5. `mog verify` re-checks every anchor and reports the drift rate — a direct,
   measurable answer to "how much does the agent think it knows that is wrong?"

## 4. Zoom levels

Progressive disclosure. Retrieval returns the **lowest zoom that answers the
query**; the agent escalates explicitly.

| Zoom | Content | Typical cost |
|------|---------|--------------|
| **L0** | Repo map: modules, responsibilities, entry points, key configs | 2–4k tokens for a mid-size repo |
| **L1** | File skeleton: imports, signatures, docstrings, no bodies | ~5–10% of the file |
| **L2** | One symbol body + its immediate callers/callees as L1 | 100–600 tokens |
| **L3** | Full file, plus blame and recent diffs | full cost |

The default read path for an agent becomes L0 → L2, not "read the file". Design
target: the common "where is X handled and what calls it" question answered at
L2 for ~1–2k tokens against ~15–40k for reading the two or three candidate files
in full. **These are targets, not measurements** — [EVALUATION.md](./EVALUATION.md)
defines how they get validated, and they may not hold.

## 5. Seed-and-spread retrieval

```
query ──► embed ──► k-NN over node embeddings ──► SEEDS (5–10)
                                                    │
                                    ┌───────────────┘
                                    ▼
                        graph expansion, budgeted walk
                        frontier score = parent_score × edge_weight × decay^hop
                        stop when: budget exhausted | score < θ | hops > 3
                                    │
                                    ▼
                        candidate set (30–80 nodes)
                                    │
                    ┌───────────────┼───────────────┐
                    ▼               ▼               ▼
              semantic sim    structural rank   recency/pin
                    └───────────────┼───────────────┘
                                    ▼
                          fuse (weighted RRF)
                                    ▼
                    pack to token budget at chosen zoom ──► CONTEXT PACK
```

Why this beats chunk-RAG for code:

- **Vectors find the door; edges walk the building.** "What breaks if I change
  `parse_config`?" is answered *exactly* by inverting `calls` + `co_changed`.
  Cosine similarity guesses at it and returns files that merely *talk* like
  config code.
- **One embedding call, then indexed SQL.** Expansion is B-tree lookups on an
  adjacency table — microseconds, no additional model calls.
- **Deduplicated by construction.** A symbol is one node however many times it
  was read. Chunking re-embeds and re-returns the same code repeatedly.
- **Explainable.** Every returned node carries its path from a seed
  (`parse_config ──calls──> load_env ──defines──> settings.py`). An agent can
  tell *why* something was included, and a human can debug bad retrieval.

**Query types** the API supports directly: `search` (semantic+structural),
`impact(symbol)` (reverse dependency closure), `why(topic)` (episodic ledger —
decisions, failures, constraints), `neighbors(node, edge_types)`, and
`recall(id)` (pull back an evicted item).

## 6. The working set — eviction, not compaction

The live context is a scored, budgeted cache.

```
score = w_rel·relevance          # similarity to current task
      × w_rec·exp(−λ·age)        # recency decay
      × w_pin·pin_multiplier     # user/agent pins, Corrections = ∞
      × (1 − staleness_penalty)
      × w_freq·log(1 + hits)     # re-referenced items are load-bearing
```

Defaults: `budget = 60%` of the model's window, leaving room for the live
conversation. When the budget is exceeded, evict lowest-first **until 45%** —
hysteresis, so eviction isn't running every turn.

**Eviction leaves a stub.** This is the whole difference from compaction:

```
[evicted ctx_8f2a · "auth middleware token-refresh analysis" · 3.2k tokens
 · recall: mog recall ctx_8f2a]
```

The agent still knows the memory exists, what it covers, and how to get it back
verbatim — 40 tokens holding the place of 3.2k. Compaction, by contrast, rewrites
those 3.2k tokens into 300 tokens of prose from which the original is
unrecoverable, and does so without telling the agent what was lost.

**Never evictable:** `Correction`, `Constraint`, active `Task`, anything pinned,
and anything referenced in the last N turns.

**Summarization still exists** — but only as the *last* tier, applied only to
conversational filler (tool chatter, superseded intermediate output), never to
facts, decisions, or errors, and always with the original retained in the graph.

## 7. Storage

Single SQLite file at `.mog/graph.db` (Q5 open on content storage).

```sql
nodes(id, kind, state, labels, anchor_json, content, meta_json, created_at, updated_at)
edges(src, dst, kind, weight, meta_json)            -- idx on (src,kind), (dst,kind)
vectors(node_id, model_id, dim, embedding)          -- sqlite-vec virtual table
fts(node_id, text)                                  -- FTS5, exact-symbol fallback
episodes(id, started_at, ended_at, agent, meta_json)
working_set(session_id, node_id, score, pinned, evicted_at, stub)
egress_log(...)  policy_log(...)                    -- see SPEC-policy
```

Choices: SQLite because a single portable file with zero setup is the difference
between "I tried it" and "I didn't". `sqlite-vec` keeps vectors in the same file
(Q2 must confirm recall at 10⁶ vectors). **FTS5 alongside vectors is not
optional** — exact symbol lookup must never depend on an embedding being good.

## 8. Indexing pipeline

```
git ls-files ─► filter (.gitignore, .mogignore, size/binary caps)
             ─► tree-sitter parse ─► Symbol/File/Test/Config nodes
             ─► edge extraction (defines, calls, imports, tested_by)
             ─► git log mining ─► co_changed edges
             ─► embed new/changed nodes only (content-hash keyed cache)
             ─► write batch
```

- **Incremental by default.** Re-index touches only files whose hash changed.
- **Triggers:** `mog index`, a git `post-commit` hook, file-watch in
  `mog serve --watch`, or CI.
- **Budget:** design target of a full index of 50k LOC in under 60s on a laptop
  with local embeddings; incremental under 2s for a typical commit. Unvalidated
  until M1.
- Languages at M1: Python, TypeScript/JavaScript, Go, Rust. Others degrade to
  file-level nodes plus FTS — degraded, not broken.

## 9. MCP tool surface

What Claude Code actually sees:

| Tool | Signature | Returns |
|------|-----------|---------|
| `search_context` | `(query, budget_tokens=4000, zoom="auto")` | ranked pack with anchors + provenance paths |
| `expand` | `(node_id, zoom)` | deeper zoom on one node |
| `impact` | `(symbol, depth=2)` | reverse-dependency closure + affected tests |
| `why` | `(topic)` | decisions, failures, constraints — "have we tried this?" |
| `remember` | `(kind, content, anchor?)` | writes an episodic node |
| `recall` | `(id)` | restores an evicted item verbatim |
| `pin` / `unpin` | `(node_id)` | protect from eviction |
| `verify` | `()` | anchor drift report |

Every returned item carries `state`, `anchor`, `provenance`, and `trust_label`.
Per [SPEC-policy](./SPEC-policy.md), all of it is framed as untrusted data.
