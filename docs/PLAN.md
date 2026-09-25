# Mogestrator — Master Plan

> Status: **Planning (pre-code)** · Schema/design v2 · Last updated: 2026-08-28
>
> **This plan supersedes the workflow-orchestrator direction** in the repo's
> first planning pass. See [ADR-0001](./adr/0001-pivot-context-substrate.md).

---

## 1. Why not another orchestrator

Claude Code already orchestrates well. It has subagents, hooks, skills, MCP,
background tasks, plan mode, and a permission system. Building a competing
workflow runner would be building the part that already works.

What it does *not* have is a **durable, verifiable substrate for context**. That
is where agentic coding actually degrades, and it degrades in five specific ways:

| # | Failure | What it looks like | Why it happens |
|---|---------|--------------------|----------------|
| **F1** | **Lossy compaction** | After a compact, the agent forgets which file held the flag, what the exact test error was, which constraint the user stated. | Summarization keeps prose and drops precise anchors. |
| **F2** | **Unfalsifiable memory** | The summary says "auth is handled in `middleware.py`". Forty turns of edits later it isn't, and nothing detects this. | A prose summary has no link to the code it describes, so it cannot be invalidated. |
| **F3** | **Cold starts** | Every new session, and *every subagent spawn*, re-derives the same repo facts from zero. Claude Code's own guidance calls spawning "the expensive path" for exactly this reason. | There is no shared substrate; context is copied as text or re-grepped. |
| **F4** | **Rejected-approach amnesia** | The agent retries the approach it already ruled out 30 turns ago, hitting the same wall. | The rejection and its reason were the first things compaction dropped. |
| **F5** | **Re-read churn** | The same file is read 6 times in one session and 40 times in a week, in full, to answer questions that needed one function signature. | Retrieval granularity is "the file", and nothing is cached across turns. |

Compaction treats context as a **compression problem**. We treat it as a
**memory-hierarchy problem** — and memory hierarchies are a solved discipline:
you don't compress a cache, you *index it, score it, evict it, and fetch it back
on miss*.

## 2. What Mogestrator is

> **Mogestrator is a context substrate and policy gateway for coding agents.**
> It keeps a live, queryable, self-invalidating graph of a codebase and of what
> agents have learned about it, serves precisely-scoped context on demand, and
> enforces prompt-integrity and network-egress policy on every tool-using agent.

Three planes, each answering one failure class:

```
┌─ CONTEXT PLANE ───────────────────────────────────────────┐
│  Context Graph Memory (CGM)                               │
│  code graph + episodic ledger + anchored facts            │  F1 F2 F4
│  seed-and-spread hybrid retrieval, zoom levels            │  F5
│  working set with eviction-and-recall (not compaction)    │  F1
└───────────────────────────────────────────────────────────┘
┌─ SHARING PLANE ───────────────────────────────────────────┐
│  Context handles: subagents and new sessions receive a    │  F3
│  *query*, not a copied blob. Warm start by construction.  │
└───────────────────────────────────────────────────────────┘
┌─ POLICY PLANE ────────────────────────────────────────────┐
│  Layered signed system prompts · taint labels ·           │
│  default-deny egress firewall · capability tokens ·       │
│  injection tripwires · full audit ledger                  │
└───────────────────────────────────────────────────────────┘
```

It plugs into what already exists rather than replacing it: Mogestrator is an
**MCP server** first (`mog serve --mcp`), so Claude Code, Cursor, Windsurf, and
any MCP client gain these as tools without changing how they work.

## 3. What it is not

- **Not an agent framework or workflow engine.** No DAGs, no step runner. Claude
  Code orchestrates; we remember and we govern.
- **Not a model or a router.**
- **Not a RAG-over-chunks library.** Chunk-and-embed is the baseline we must beat
  (see [EVALUATION.md](./EVALUATION.md)), not the thing we ship.
- **Not a hosted service in v1.** Local-first, single SQLite file. Gateway mode
  is self-hosted for teams that want it.
- **Not a code index for humans.** No search UI. The consumer is an agent.

## 4. Design principles

1. **Every fact is falsifiable.** A memory that cannot be checked against the
   repo is not stored. Anchoring is mandatory, not decorative.
2. **Evict, don't compress.** Anything dropped from the working set stays in the
   graph and is one hop from returning. Lossy summarization is a last resort,
   applied only to conversational filler, never to facts, decisions, or errors.
3. **Retrieval is structural first, semantic second.** Embeddings find the door;
   the graph walks the building. Vectors alone return plausible neighbours;
   `calls`/`imports`/`tested_by` edges return *correct* ones.
4. **Smallest sufficient zoom.** Never load a file when a signature will do.
5. **Retrieved context is untrusted data.** It is framed, labelled, and can never
   occupy a policy layer of a prompt. Our own memory is an injection vector.
6. **Default-deny egress.** An agent reaches exactly the hosts its policy names.
7. **Local-first.** One SQLite file, local embeddings by default. Code never
   leaves the machine unless the user points at a remote provider.
8. **Prove it or drop it.** Every context claim ships with a benchmark against a
   naive baseline. A context system without an eval is faith.

## 5. The three focus areas in detail

### 5.1 Context Graph Memory — the alternative to compaction

Specified in [SPEC-context-graph.md](./SPEC-context-graph.md). The five
mechanisms, in the order they matter:

**(a) Typed graph with content-hash anchors.** Nodes are `Symbol`, `File`,
`Module`, `Decision`, `Failure`, `Constraint`, `Convention`, `Task`, `Episode`.
Every derived fact carries an **anchor**: `(path, symbol_id, span_hash)` —
hashed content, *not* line numbers, which drift on every edit above them. When
the hash stops matching, the fact flips to `stale` automatically. This is the
direct answer to F2: our memory can be *wrong*, and it knows it.

**(b) Seed-and-spread retrieval.** Embed the query → k-NN to get 5–10 **seed**
nodes → walk the graph outward with per-edge-type weights and a decay budget →
rank by combined structural + semantic score → return. Retrieving "who breaks if
I change `parse_config`?" is a graph query with an exact answer, not a vector
similarity guess. Costs one embedding call plus indexed SQL.

**(c) Zoom levels (progressive disclosure).** L0 repo map (~2–4k tokens for a
mid-size repo) → L1 file skeleton, signatures and docstrings only → L2 the
specific symbol body → L3 full file plus blame. Retrieval returns the lowest
zoom that answers the query, and the agent can zoom in explicitly.

**(d) Episodic decision ledger.** Append-only, highest value per token, and
exactly what compaction destroys first: decisions and their rationale, approaches
tried and the error that killed them, constraints discovered, user corrections.
Answers F4 directly — before an agent tries an approach, `mog why` says whether
it already failed and why.

**(e) Working set with eviction-and-recall.** An explicit token budget with a
scoring function per item:

```
score = w_r·relevance × w_t·recency_decay × w_p·pin × (1 − staleness) × w_c·cost⁻¹
```

Evict the lowest scorer when the budget is hit. Eviction is not deletion: the
item returns to the graph, and a one-line **stub** stays in context (`[evicted:
auth-middleware analysis — mog recall ctx_8f2a]`) so the agent knows the memory
exists and can pull it back in one call. Compaction cannot do this — once
summarized, the detail is unrecoverable.

**Other approaches considered and rejected** (with reasons) in
[ADR-0002](./adr/0002-graph-memory-over-summarization.md): hierarchical
summarization trees (RAPTOR-style), pure vector RAG over chunks, token-level
prompt pruning (LLMLingua-style), plain external scratchpad files, and KV-cache
offloading.

### 5.2 Context handles — warm starts for subagents

A subagent is spawned with a **handle**: a saved query plus a pinned set of node
ids, resolved at spawn time into a budgeted context pack. The parent does not
copy 20k tokens of transcript; it passes `ctx://run_7a1/task_3` and the child
materializes exactly what it needs at the zoom level it needs. Sessions resume
the same way — `mog resume` reconstructs a working set from the ledger and the
graph rather than replaying a transcript.

### 5.3 Policy plane — prompts and network for server-based chats

Specified in [SPEC-policy.md](./SPEC-policy.md). Server-hosted agents with tools
have two exposures that a local CLI mostly doesn't: a prompt assembled from
sources of mixed trust, and a network stack that will happily fetch anything.

**Layered prompt assembly.** System prompts are composed from ordered, versioned,
hash-pinned layers — `identity → capability → policy → context → task` — never
string-concatenated ad hoc. Layers at `policy` and above are immutable at run
time and signed; retrieved context is structurally incapable of landing there.
Each layer's digest goes in the audit record, so "what exactly was this agent
told?" has an exact answer months later.

**Taint labels and flow control.** Content carries labels (`repo:private`,
`secret`, `web:untrusted`, `user:pii`). Rules are flow constraints, not
keyword filters: *tainted `secret` may never appear in an egress payload to a
host not labelled `trusted`.* This is what actually stops exfiltration through a
tool call, which regex-based output filtering does not.

**Default-deny egress firewall.** Per-agent allowlists of host, method, and body
size. Blocks private and link-local CIDRs by default (`169.254.169.254` and
friends — the cloud-metadata SSRF path), pins resolved IPs for the life of a
request to defeat DNS rebinding, refuses off-allowlist redirects, and writes
every request to an egress ledger with the rule that permitted it and a hash of
the body.

**Injection tripwires and capability tokens.** A canary string in the system
prompt; if it ever appears in an outbound payload, the run is killed and the
session quarantined. Tools receive short-lived, run-scoped capability tokens
rather than ambient API keys.

## 6. Interfaces

| Surface | Purpose |
|---------|---------|
| **MCP server** (`mog serve --mcp`) | Primary. Exposes `search_context`, `expand`, `why`, `remember`, `recall`, `impact`, `pin` as tools to Claude Code and any MCP client. |
| **CLI** (`mog`) | Indexing, inspection, budget tuning, debugging, CI checks. |
| **Gateway** (`mog gateway`) | Self-hosted policy plane for server-based chats: prompt assembly, taint, egress firewall, audit. |
| **Python API** | For teams embedding the substrate in their own agents. |

## 7. Milestones

**M0 and M1 are complete.** Retrieval and MCP are available experimentally;
the M2 evaluation gate remains open. [ROADMAP.md](./ROADMAP.md) has the current
implementation checklist and [M1 results](./results/m1-index-2026-09-25.md)
record the index exit demo.

| M | Theme | Exit demo |
|---|-------|-----------|
| **M0** ✅ | Planning | This docs set |
| **M1** ✅ | Graph + index | `mog index` on a 50k-LOC repo in under 60s; `mog search` returns anchored results |
| **M2** | Retrieval + zoom | Seed-and-spread beats the chunk-RAG baseline on the eval harness |
| **M3** | MCP server | Claude Code uses `search_context` and `why` in a real session |
| **M4** | Ledger + working set | Eviction-and-recall demonstrably survives a context reset that compaction does not |
| **M5** | Policy plane | Gateway blocks a scripted exfiltration attempt; audit shows the rule |
| **M6** | Distribution | `uvx mogestrator` on macOS/Linux/Windows, all P0 channels green |

Checklists in [ROADMAP.md](./ROADMAP.md). Success criteria in
[EVALUATION.md](./EVALUATION.md).

## 8. Open questions

| ID | Question | Blocks |
|----|----------|--------|
| Q1 | Default local embedding model — size vs. quality vs. cold-start download. Leaning a small ONNX bi-encoder (~90MB) with an opt-in stronger model. | M1 |
| ~~Q2~~ | **Resolved (ADR-0007):** `sqlite-vec` v0.1.9 loads and gives exact recall; 50k×384-dim k-NN in 4.3 ms, scaling linearly (~86 ms projected at 10⁶). Caveat: macOS *system* Python cannot load SQLite extensions, so a degraded FTS-only mode is required and implemented. | ~~M1~~ |
| Q3 | Does the episodic ledger auto-capture from the Claude Code transcript (via hooks) or require explicit `remember` calls? Auto-capture is far more valuable and far more invasive. | M4 |
| Q4 | Is the gateway a proxy the agent is configured to use, or a library the host embeds? Proxy is stronger (it cannot be bypassed) and harder to deploy. | M5 |
| ~~Q5~~ | **Resolved (ADR-0007):** store an L1 preview (600 chars) plus byte offsets; read full bodies from the working tree at L2. Storing whole bodies duplicated the repo for little gain. | ~~M1~~ |

## 9. Risks

| Risk | Impact | Mitigation |
|------|--------|-----------|
| **The graph is slower than just grepping** | Whole premise fails | Eval harness measures latency and tokens against `rg` + full-file reads from day one (M2 gate). If we lose, we say so and change course. |
| Index staleness on a fast-moving repo | Wrong answers | Incremental re-index on file-watch and git hooks; every result carries a freshness stamp; stale nodes are labelled, never silently served. |
| **Poisoned memory** — a fact written by a malicious file comment is later injected as trusted context | Severe | §5.3 applies to our own store: memories carry provenance and trust labels; only `user`- and `verified`-origin facts enter high-trust prompt layers. ADR-0006. |
| Embedding drift across model upgrades | Silent recall loss | Embedding model id and dimension stored per vector; `mog reindex --embeddings` on change; mixed-model queries refused. |
| Scope creep back into orchestration | Never ships | §3 is a contract. |
| Local embedding quality too weak for code | Poor recall | Q1 evaluates code-specific encoders; API embeddings remain a documented opt-in. |
