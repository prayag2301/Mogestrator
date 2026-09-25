# Mogestrator

**A context substrate and policy gateway for coding agents.**

Claude Code already orchestrates well — subagents, hooks, skills, MCP, plan mode.
What it doesn't have is memory that can be *checked*. Mogestrator keeps a live,
anchored, self-invalidating graph of your codebase and of what agents have
learned about it — memory that knows when it has gone out of date. The design
extends to serving precisely-scoped context on demand and enforcing prompt
integrity and network policy on tool-using agents; see the status note below for
what is built versus specified.

```bash
git clone https://github.com/prayag2301/Mogestrator.git && cd Mogestrator
uv venv && uv pip install -e .

mog index      # build the graph
mog search "token" # find anchored matches
mog verify     # re-check every anchor: how much of what it knows is now wrong?
```

> **Status: M1 complete.** Stable release: **0.1.2**.
>
> **Built and tested:** the context graph, content-hash anchors with automatic
> staleness detection, incremental indexing for Python/TypeScript/Go/Rust, the
> ingest sensitivity gate (credentials are labelled and never stored), and the
> `init · index · status · verify · show · map · search` commands. Search uses
> local full-text matching and returns content anchors. Import, test, call, and
> bounded Git co-change relationships are indexed.
>
> **Not built:** graph retrieval (`impact`, `why`, seed-and-spread),
> embeddings, the working set, the MCP server, and the rest of the policy plane
> (its ingest half — taint labels at the indexer — ships with M1, ADR-0008).
> Those sections below describe the design, not shipped behaviour, and are
> marked *(planned)*.
>
> Claims about *quality* — that this beats ripgrep or chunk-RAG — remain
> untested hypotheses; the harness that decides them is specified in
> [EVALUATION.md](docs/EVALUATION.md) and gates M2. Progress:
> [ROADMAP.md](docs/ROADMAP.md).

---

## The problem: compaction is a compression answer to a memory problem

Five failures show up in every long agentic session:

| | Failure | Why it happens |
|---|---|---|
| **F1** | After a compact, the agent forgets which file held the flag, the exact test error, the constraint you stated | Summarization keeps prose and drops precise anchors |
| **F2** | The summary says "auth lives in `middleware.py`". Forty edits later it doesn't, and nothing notices | A prose summary has no link to the code it describes, so it can't be invalidated |
| **F3** | Every new session — and every subagent spawn — re-derives the same repo facts from zero | No shared substrate; context is copied as text or re-grepped |
| **F4** | The agent retries the approach it ruled out 30 turns ago, hitting the same wall | The rejection and its reason were the first thing compaction dropped |
| **F5** | The same file gets read six times in a session to answer questions that needed one signature | Retrieval granularity is "the file" |

Compaction treats context as a **compression problem**. Mogestrator treats it as
a **memory-hierarchy problem** — and memory hierarchies are a solved discipline.
You don't compress a cache. You index it, score it, evict it, and fetch it back
on a miss.

## The approach

### 1. A typed graph, not a pile of chunks

Nodes are `Symbol`, `File`, `Module`, `Test`, `Config` — plus the half that
actually matters and that no re-index can rebuild: `Decision`, `Failure`,
`Constraint`, `Convention`, `Correction`. Edges are real relationships:
`calls`, `imports`, `tested_by`, `caused_by`, `supersedes`, and `co_changed`
(mined from git history — two symbols that change together in 8 of 10 commits
are coupled in a way no import graph shows).

### 2. Anchors make memory falsifiable

Every fact carries a content-hash anchor, **never line numbers** — those shift on
every edit above them:

```
anchor: { path: src/auth/middleware.py, symbol: verify_token,
          span_hash: sha256:9f3c…, commit: b4ef335 }
```

Re-index, the hash mismatches, the fact flips to `stale` — labelled and still
served with a warning, never silently wrong. `mog verify` gives you a drift rate:
a number for *how much of what the agent believes is no longer true*. A summary
can't do this, and that's the point.

### 3. Seed-and-spread retrieval

```
query → embed → k-NN → 8 SEEDS → budgeted graph walk (weighted, hop-decayed)
      → fuse structural + semantic + recency → pack to token budget
```

**Vectors find the door; edges walk the building.** "What breaks if I change
`parse_config`?" is answered *exactly* by inverting `calls` + `co_changed`.
Cosine similarity guesses at it and returns files that merely talk like config
code. Cost: one embedding call, then indexed SQL.

Every result carries its path from a seed, so bad retrieval is debuggable:

```console
$ mog search "how are refresh tokens validated" --explain --budget 2000
1. verify_token         src/auth/middleware.py::verify_token   L2  fresh
   seed (cosine 0.83)
2. TokenStore.refresh   src/auth/store.py::refresh             L2  fresh
   verify_token ──calls──> refresh
3. test_refresh_expiry  tests/test_auth.py::test_refresh_expiry L1 fresh
   verify_token ──tested_by──> test_refresh_expiry
⚠ 1 related fact is stale: decision d_71 ("use HS256") — config.py changed 4 commits ago
1,840 tokens · 32ms
```

### 4. Zoom levels — never read a whole file again

**L0** repo map (2–4k tokens) → **L1** file skeleton, signatures only → **L2** one
symbol body + its callers → **L3** full file with blame. Retrieval returns the
lowest zoom that answers the question.

### 5. Eviction with recall, instead of compaction

Live context is a scored cache with an explicit budget:

```
score = relevance × recency_decay × pin × (1 − staleness) × log(1 + hits)
```

Over budget, evict lowest-first — and **leave a stub**:

```
[evicted ctx_8f2a · "auth middleware token-refresh analysis" · 3.2k tokens
 · recall: mog recall ctx_8f2a]
```

40 tokens holding the place of 3,200, and the original is recoverable
**verbatim**. Compaction rewrites those 3.2k tokens into 300 tokens of prose from
which nothing is recoverable — and doesn't tell the agent what it lost.
`Correction`, `Constraint`, and pinned items are never evictable.

### 6. Warm subagents

A subagent gets a **context handle** — `ctx://run_7a1/task_3`, a query plus
pinned nodes — not a 20k-token transcript copy. It materializes exactly what it
needs at the zoom it needs. Cold starts (F3) stop being structural.

> Approaches considered and rejected — RAPTOR-style summarization trees, pure
> chunk RAG, LLMLingua-style token pruning, scratchpad markdown, KV-cache
> offload — are documented with their reasons in
> [ADR-0002](docs/adr/0002-graph-memory-over-summarization.md).

---

## The policy plane — for server-based chats

A hosted agent with tools has two exposures a local CLI mostly doesn't: a prompt
assembled from sources of mixed trust, and a network stack that will fetch
anything. And there's a sharper problem specific to us: **the context graph is
itself an injection vector.** Better memory without flow control is a
better-targeted vulnerability.

**Taint at ingest, not at egress** *(the one part that is built)*. Labels are
assigned where provenance is known — the indexer. A file matching a credential
shape keeps its node and loses its bytes: no preview, no full-text row, no byte
offsets. Secret content never enters the store, so no downstream bug in
retrieval, MCP or egress can leak it — a stronger property than any flow rule,
and one that had to precede the store rather than follow it four milestones
later ([ADR-0008](docs/adr/0008-ingest-sensitivity-gate.md)).

```console
$ mog index
  4 files · 1 symbols · 0 tests · 1 edges
  2 files gated as secret (listed, never stored — mog status --secrets)
```

**Layered, signed prompts.** `identity → capability → policy → context → task`.
L0–L2 are digest-pinned and immutable at run time — retrieved content is
*structurally incapable* of landing there. Not "the model refuses to be
overridden": it can't reach the layer. Every layer's digest goes in the audit
record, so "what exactly was this agent told on Tuesday?" has an exact answer.

**Taint labels, not regex DLP.** Content carries `secret`, `repo:private`,
`web:untrusted`, `user:pii`, `derived`, `verified`, and labels **propagate
through derivation**. That catches the secret that was base64'd, paraphrased, or
split across two fields — because provenance is tracked, not strings. Rules are
flow constraints:

```yaml
flows:
  - deny: { taint: secret, to: egress }
  - deny: { taint: web:untrusted, to: prompt_layer, at_or_above: policy }
  - deny: { from: untrusted_instruction, to: write_action }
```

**Default-deny egress firewall.** Per-agent host allowlists; private and
link-local CIDRs blocked (`169.254.169.254` — the cloud-metadata SSRF path);
DNS **resolved once and pinned** for the request's lifetime, defeating rebinding;
off-allowlist redirects refused; body-size and rate caps bounding any successful
exfiltration. The ledger records **which rule allowed each request**, not just
that it happened.

**Capability tokens** — short-lived, run-scoped, per-user, so the model never
sees a key and can't be talked into leaking one, and an agent acting for user B
can't use user A's permissions. **Canary tripwire** — a random token in the
policy layer; if it ever appears in an outbound payload, the run dies and the
session is quarantined.

In **proxy mode** the security property comes from network topology, not from the
agent's cooperation. A policy the agent could route around is documentation.

Full threat model (T1–T6) and specification: [SPEC-policy.md](docs/SPEC-policy.md).

---

## Installation

### From source

```bash
git clone https://github.com/prayag2301/Mogestrator.git && cd Mogestrator
uv venv && source .venv/bin/activate
uv pip install -e .
mog --version
```

Requires Python 3.11+. Install the stable M1 release from
[PyPI](https://pypi.org/project/mogestrator/):

```bash
pip install "mogestrator>=0.1.2"
# or, in an isolated tool environment:
uv tool install "mogestrator>=0.1.2"
# run without a persistent installation:
uvx --from "mogestrator>=0.1.2" mog index
```

The release pipeline tests macOS, Linux, and Windows before publishing. The
four supported grammar wheels are installed with the package; indexing does
not download parsers at runtime.

> On macOS, the *system* Python (`/usr/bin/python3`) is built without SQLite
> extension support, so `sqlite-vec` cannot load there. `mog` detects this and
> falls back to full-text search; `mog status` tells you which mode you are in.
> Use a uv/homebrew/python.org interpreter to get vector support.

### Planned: additional channels *(M6)*

Full channel matrix and build/signing process: [DISTRIBUTION.md](docs/DISTRIBUTION.md).
**None of the commands below work yet.**

```bash
# no Python opinion
brew install prayag2301/tap/mog
curl -fsSL https://get.mogestrator.dev | sh      # signed binary, checksum-verified
irm https://get.mogestrator.dev/ps1 | iex        # Windows

# JS teams
npx @mogestrator/cli index

# containers
docker run --rm -v "$PWD:/w" -w /w ghcr.io/prayag2301/mogestrator index
```

**In Claude Code** — the primary integration, and the point of the whole project
*(M3, not yet implemented)*:

```bash
claude mcp add mog -- mog serve --mcp --watch
```

Claude Code would gain `search_context`, `expand`, `impact`, `why`, `remember`,
`recall`, `pin`, and `verify` as tools. Also planned: a plugin marketplace entry
(`/mog-search`, `/mog-why`), `mise`, Nix, Scoop, and a VS Code extension.

## Usage

### Working today

```bash
mog init                     # scaffold mogestrator.yaml, .mogignore, .mog/
mog index [--full]           # build the graph; incremental by default
mog status [--secrets]       # counts, index age, drift, vectors; gated files
mog verify [--strict]        # re-check every anchor; --strict exits 4 on drift
mog show Store.upsert_nodes  # one symbol: anchor, callers, callees, tests
mog map                      # the files with the most symbols
```

Every command takes `--repo PATH`; `index`, `status` and `verify` take `--json`
for scripting. Exit codes are a contract — see [SPEC-cli.md](docs/SPEC-cli.md).

`verify` checks file, symbol, test, and stored fact anchors. A missing or
changed file can therefore report drift even when its symbol bodies match.

`mog verify` is the one worth trying first. It answers a question nothing else
does: *how much of what the index believes is no longer true?*

```console
$ mog verify
checked 93 anchors · 93 hold · 0 drifted (0.0% drift rate)
```

### Planned *(M2–M5, not implemented)*

```bash
mog search "where are refresh tokens validated" --explain --budget 2000
mog impact verify_token --tests          # what breaks, and which tests cover it
mog why "why not RS256"                  # decisions, failures, constraints
mog remember failure "RS256 rejected: key rotation needs infra we lack"
mog recall ctx_8f2a                      # restore an evicted item verbatim
mog ws show                              # working set, scores, budget usage

mog serve --mcp --watch                  # expose to Claude Code / any MCP client
mog gateway --policy policy/ --port 8080 # policy plane for server-based chats
mog policy explain api.github.com        # which rule allows this, and why
```

Full command reference: [SPEC-cli.md](docs/SPEC-cli.md).
Config: [SPEC-config.md](docs/SPEC-config.md).

## What this is not

Not an agent framework or workflow engine — Claude Code orchestrates, we remember
and govern. Not a model or router. Not a RAG-over-chunks library (that's the
baseline we must beat). Not a hosted service in v1. Not a code-search UI for
humans — the consumer is an agent.

## Does it actually work?

**Speed: measured for 0.1.2** on django/django at pinned commit
`a3d71038401b9340f550f2fdf1caaad66465ba88` (4,852 indexed files, 45.1 MB
of source), on an Apple Silicon laptop:

| | Target | Measured | |
|---|---|---|---|
| Full index | < 60s for 50k LOC | **25.4s** | ✅ |
| Incremental re-index | < 2s | **1.6s** | ✅ |
| Index size | < 400% of source | **125.6 MB vs 45.1 MB — 279%** | ✅ |

The original 15% size target proved unrealistic for symbol-level anchors and
full-text lookup. [ADR-0007](docs/adr/0007-index-scale-findings.md) records the
revised 400% limit; [ADR-0010](docs/adr/0010-m1-relationship-continuity-and-fts.md)
records the contentless FTS change.

Measuring on a real repo instead of fixtures also caught a design flaw:
naive call resolution produced **1,991,411 edges, 98.3% of them ambiguous**
(`__init__` alone: 534,473). Capping fan-out cut the index 4× in both time and
size.

**Quality: unknown, and we refuse to pretend otherwise.** Every claim that this
beats the alternatives is a hypothesis until
[EVALUATION.md](docs/EVALUATION.md) says otherwise. The harness measures
against four baselines — `rg` + full file reads (**B0**), chunk RAG (**B1**),
no management (**B2**), and summarization compaction (**B3**) — on localization,
impact, multi-session continuity, repeat-failure avoidance, staleness handling,
and cold subagent start.

Hard gates: **M2 doesn't ship** unless seed-and-spread beats both `rg` and chunk
RAG on tokens-to-correct-answer. **M4 doesn't ship** unless eviction-and-recall
beats compaction on continuity. **M5 doesn't ship** unless zero injected
memories reach a high-trust prompt layer.

If the graph loses to ripgrep, we publish that and change course. Negative
results go in `docs/results/` too.

## Documentation

| Document | Contents |
|---|---|
| [PLAN.md](docs/PLAN.md) | The five failures, the three planes, scope, milestones, risks, open questions |
| [SPEC-context-graph.md](docs/SPEC-context-graph.md) | **The core** — nodes, edges, anchors, zoom, seed-and-spread, working set, storage, MCP tools |
| [SPEC-policy.md](docs/SPEC-policy.md) | Threat model, prompt layers, taint flow, egress firewall, capabilities, audit |
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | Module layout, protocols, request paths, degradation, perf targets |
| [EVALUATION.md](docs/EVALUATION.md) | Baselines, suites, metrics, ship gates |
| [SPEC-cli.md](docs/SPEC-cli.md) · [SPEC-config.md](docs/SPEC-config.md) | `mog` commands · `mogestrator.yaml` |
| [DISTRIBUTION.md](docs/DISTRIBUTION.md) | 16 install channels, build and signing |
| [ROADMAP.md](docs/ROADMAP.md) | M1–M6 checklists — the work queue |
| [adr/](docs/adr/) | Architecture decisions and measured tradeoffs |

## Requirements

Python 3.11+ · Git · a tree-sitter-supported language for symbol-level indexing
(Python, TypeScript, Go, Rust; anything else degrades to file-level nodes plus
full-text search — degraded, not broken).

No API key, no account, no network. The graph is one SQLite file in `.mog/`, and
your code never leaves the machine. Embeddings, when they land in M2, run locally
by default (ADR-0005).

## Contributing

M1 is complete. [CONTRIBUTING.md](CONTRIBUTING.md) — specs lead code,
decisions get an ADR, and context claims need numbers.

## License

MIT.
