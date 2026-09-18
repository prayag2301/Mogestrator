# Spec: `mog` command surface

> Status: **Draft v1**. Package: `mogestrator` · binary: `mog`.

## Implemented in 0.2.0a1

`init`, `index`, `status`, `verify`, `show`, and `map` are available. All except
`init` take `--repo`; `index`, `status`, and `verify` take `--json`. `index`
supports `--full`; `verify --strict` exits 4 on drift. Verification covers file,
symbol, test, and episodic anchors, and never follows a path outside the repo.
`show path::symbol` requires the path to match. Use `mog COMMAND --help` for
the supported options. Experimental `search`, `expand`, `impact`, `neighbors`,
`why`, `remember`, `recall`, `pin`, `unpin`, `embed`, and `serve` are also available.
Retrieval takes `--budget` and `--json`; search supports `--semantic`, `--zoom`,
`--kind`, and `--explain`. `serve --transport http` binds to loopback, and
`--allow-memory-writes` enables agent-origin memory and context-handle writes.
Unlisted commands and flags below remain a design.

## Global flags
```
--repo PATH · --db PATH · --json · --budget TOKENS · -q/-v · --no-color · --version
```

## Exit codes
| 0 ok · 1 not found / empty result · 2 usage · 3 config invalid · 4 index stale
| or missing · 5 provider/embedding error · 6 policy denial · 7 budget exceeded
| · 130 cancelled |

## Index and inspect
```
mog init                          scaffold mogestrator.yaml + .mog/ + .mogignore
mog index [--full] [--watch]      build/update the graph; incremental by default
mog status                        node/edge/vector counts, index age, drift rate
mog status --secrets              list files gated at ingest (ADR-0008)
mog verify [--fix]                re-check anchors; report stale facts
mog map [--depth N]               print the L0 repo map
```

## Query
```
mog search "<query>" [--budget 4000] [--zoom auto|L0..L3] [--kind ...] [--explain]
mog impact <symbol> [--depth 2] [--tests]
mog why "<topic>"                 decisions, failures, constraints from the ledger
mog neighbors <node> [--edge calls,co_changed]
mog show <node-id> [--zoom L2]
```
`--explain` prints each result's path from its seed, so bad retrieval is
debuggable rather than mysterious.

## Memory
```
mog remember decision|failure|constraint|convention "<text>" [--anchor path::symbol]
mog recall <id>                   restore an evicted item verbatim
mog pin <id> / mog unpin <id>
mog forget <id> [--reason]        retract (tombstoned, never hard-deleted)
mog ledger [--since 7d] [--kind failure]
```

## Working set
```
mog ws show                       current items, scores, budget usage
mog ws evict --to 45%             manual eviction pass
mog ws stubs                      what's evicted and recallable
```

## Serving
```
mog serve --mcp [--stdio|--http PORT] [--watch]     expose CGM tools to MCP clients
mog gateway --policy policy/ [--port 8080]          policy plane for server chats
```

## Policy
```
mog policy check [--strict]       validate layer digests, flow rules, egress config
mog policy explain <host>         which rule would allow or deny this host, and why
mog policy test <scenario.yaml>   run the injection/exfiltration suite
mog audit [run-id] [--egress] [--memory] [--since 24h]
```

## Evaluation
```
mog eval run <suite> [--baseline b0|b1|b3]
mog eval report
```

## Example session
```console
$ mog index
indexed 1,204 files · 8,912 symbols · 31,077 edges · 8,912 vectors  (41.3s)

$ mog search "how are refresh tokens validated" --explain --budget 2000
1. verify_token            src/auth/middleware.py::verify_token      L2  fresh
   seed (cosine 0.83)
2. TokenStore.refresh      src/auth/store.py::refresh                L2  fresh
   verify_token ──calls──> refresh
3. test_refresh_expiry     tests/test_auth.py::test_refresh_expiry   L1  fresh
   verify_token ──tested_by──> test_refresh_expiry
⚠ 1 related fact is stale: decision d_71 ("use HS256") — auth/config.py changed
  4 commits ago.  mog show d_71
1,840 tokens · 32ms

$ mog why "why not RS256"
decision d_71 · 2026-07-14 · superseded by d_88
failure  f_12 · "RS256 rejected: key rotation needs infra we don't have (CI has
                 no network — constraint c_03)"
```
