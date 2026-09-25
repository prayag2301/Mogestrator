# Roadmap

Sequencing, not dates. Every milestone ends in a demo a stranger can reproduce.

| M | Theme | Exit demo |
|---|-------|-----------|
| **M0** ✅ | Planning | This docs set |
| **M1** ✅ | Graph + index | `mog index` a 50k-LOC repo < 60s; `mog search` returns anchored results |
| **M2** | Retrieval + zoom | Seed-and-spread beats chunk-RAG (B1) and `rg` (B0) on the harness |
| **M3** | MCP server | Claude Code uses `search_context` / `why` in a real session |
| **M4** | Ledger + working set | Eviction-and-recall survives a reset that compaction (B3) does not |
| **M5** | Policy plane | Gateway blocks scripted exfiltration; audit names the rule |
| **M6** | Distribution | `uvx mogestrator` green on macOS/Linux/Windows |

## M1 — Graph and index  *(complete; measured on pinned Django checkout)*
- [x] SQLite schema + migrations; FTS5 wired; `sqlite-vec` probed with graceful
      degradation when extensions cannot load (**Q2 resolved**, ADR-0007)
- [x] tree-sitter parsers: Python, TypeScript, Go, Rust — spec-driven, so a new
      language is a `LangSpec`, not a module
- [x] Node/edge extraction: `defines`, `calls`, `tested_by`; call fan-out capped
      (dropping 98.3% ambiguous edges measured on Django)
- [x] Anchors: normalized span hashing, drift detection, automatic staleness
- [x] `mog init/index/status/verify/show/map`
- [x] Perf measured on a 525k-LOC repo; results in ARCHITECTURE §6
- [x] Ingest sensitivity gate: taint labels assigned at the indexer, symlink
      confinement, secret content never stored or indexed (ADR-0008) — moved
      forward from M5, because the store cannot precede its own labelling
- [x] Index, retrieval, memory, embedding-cache, and real MCP protocol tests
- [x] Conservative `imports` file links for local Python/TypeScript/Go/Rust modules
- [x] Bounded Git miner for recurring `co_changed` symbol pairs
- [x] Optional local BGE embedder + model/content-hash cache (ADR-0009)
- [x] Unique body-hash matching carries anchored facts through symbol/file renames
- [x] Close the index-size gap: contentless FTS and v3 migration; 369% of source
      on pinned Django, under ADR-0007's revised 400% target

## M2 — Retrieval *(experimental in 0.2.0a1; quality gate open)*
- [x] Exact/FTS5 and optional exact-cosine semantic seeding
- [ ] Scalable k-NN vector index
- [x] Bounded weighted spread with hop decay
- [x] Weighted rank fusion; `--explain` provenance paths
- [x] L0–L3 source views and automatic default zoom
- [ ] L3 blame/recent diffs and query-specific zoom policy
- [x] Context packing with conservative UTF-8 byte/token upper bound
- [x] `mog search/impact/neighbors/expand/why`
- [ ] **Eval gate:** beat B0 and B1 on localization and token cost

## M3 — MCP
- [x] `mog serve --mcp` (stdio + loopback HTTP), `--watch` incremental reindex
- [x] Core retrieval/memory tools per SPEC-context-graph §9, plus neighbors and handles
- [x] Retrieved items stamped with state, anchor, provenance, trust label
- [x] Durable query/reference handles via create_context/load_context
- [ ] Claude Code plugin wrapping the server

## M4 — Memory
- [x] Explicit episodic records, provenance, why/recall, and persistent memory pins
- [ ] Working set scoring, hysteresis eviction, stubs, `recall`
- [ ] `contradicts` detection and surfacing
- [x] `mog remember/recall/why/pin/unpin`
- [ ] `mog ledger/ws` and automatic working-set lifecycle
- [ ] Auto-capture via Claude Code hooks (resolve **Q3**)
- [ ] **Eval gate:** beat B3 on continuity and repeat-failure avoidance

## M5 — Policy plane
- [ ] Signed layered prompt assembly with digest pinning
- [ ] Taint labels and propagation; declarative flow rules
- [ ] Egress firewall: default-deny, CIDR blocks, resolve-and-pin, no redirects
- [ ] Capability tokens; per-user scoping
- [ ] Canary tripwire; memory-write policy (T2)
- [ ] Audit ledger + `mog audit`, `mog policy explain/test`
- [ ] Proxy deployment mode (resolve **Q4**)
- [ ] **Eval gate:** zero poisoning through, full exfiltration suite blocked

## M6 — Distribution
- [x] Python wheel/sdist build, release version checks, and installed-package
      CI on macOS/Linux/Windows; 0.1.1 published and fresh-install verified
- [x] Verify functional 0.1.1 publication on PyPI
- [ ] Homebrew, binaries, Docker, npm wrapper
- [ ] Signed releases, checksums, SBOM, install smoke matrix

## Deferred
Hosted multi-tenant service · team dashboards · IDE UI · cross-repo federated
graphs · fine-tuned retrievers. Revisit after M6, with evidence.
