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

## M1 — Graph and index  *(complete in 0.1.2)*
- [x] SQLite schema + migrations; FTS5 wired; `sqlite-vec` probed with graceful
      degradation when extensions cannot load (**Q2 resolved**, ADR-0007)
- [x] tree-sitter parsers: Python, TypeScript, Go, Rust — spec-driven, so a new
      language is a `LangSpec`, not a module
- [x] Node/edge extraction: `defines`, `calls`, `tested_by`; call fan-out capped
      (dropping 98.3% ambiguous edges measured on Django)
- [x] Anchors: normalized span hashing, drift detection, automatic staleness
- [x] `mog init/index/status/verify/show/map/search` (M1 search is local FTS)
- [x] Perf measured on a 525k-LOC repo; results in ARCHITECTURE §6
- [x] Ingest sensitivity gate: taint labels assigned at the indexer, symlink
      confinement, secret content never stored or indexed (ADR-0008) — moved
      forward from M5, because the store cannot precede its own labelling
- [x] Tests cover anchors, store, parsers, indexing, the ingest gate and CLI
- [x] Conservative `imports` edges between indexed files
- [x] Bounded Git miner for `co_changed`
- [x] Rename continuity via body-hash matching
- [x] Compact contentless FTS; revised size target (ADR-0010)

## M2 — Retrieval
- [ ] Local embedder + content-hash cache (**Q1** still open)
- [ ] k-NN seeding + FTS5 exact fallback
- [ ] Budgeted weighted spread with hop decay
- [ ] Weighted RRF fusion; `--explain` provenance paths
- [ ] L0–L3 zoom renderers; automatic zoom selection
- [ ] Token-budgeted packing
- [ ] Upgrade `mog search` to seed-and-spread; add `impact/neighbors`
- [ ] **Eval gate:** beat B0 and B1 on localization and token cost

## M3 — MCP
- [ ] `mog serve --mcp` (stdio + http), `--watch` incremental reindex
- [ ] Eight tools per SPEC-context-graph §9
- [ ] Every result stamped with state, anchor, provenance, trust label
- [ ] Context handles for subagent warm starts
- [ ] Claude Code plugin wrapping the server

## M4 — Memory
- [ ] Episodic ledger: Decision, Failure, Constraint, Convention, Correction
- [ ] Working set scoring, hysteresis eviction, stubs, `recall`
- [ ] `contradicts` detection and surfacing
- [ ] `mog remember/recall/why/ledger/ws`
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
      CI on macOS/Linux/Windows; 0.1.2 prepared for publication
- [x] Verify functional 0.1.1 publication on PyPI (Trusted Publisher required)
- [ ] Homebrew, binaries, Docker, npm wrapper
- [ ] Signed releases, checksums, SBOM, install smoke matrix

## Deferred
Hosted multi-tenant service · team dashboards · IDE UI · cross-repo federated
graphs · fine-tuned retrievers. Revisit after M6, with evidence.
