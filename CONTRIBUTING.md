# Contributing

> **M1 is complete.** Experimental retrieval and MCP integration are available;
> the M2 evidence gate remains open. Read the roadmap for the current scope.

## Read first

1. [docs/PLAN.md](docs/PLAN.md) — the five failures we target, and what we are not building
2. [docs/SPEC-context-graph.md](docs/SPEC-context-graph.md) — the core: nodes, anchors, retrieval, working set
3. [docs/SPEC-policy.md](docs/SPEC-policy.md) — threat model, prompt layers, taint, egress
4. [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — module layout and protocols
5. [docs/EVALUATION.md](docs/EVALUATION.md) — how we find out if any of this works
6. [docs/adr/](docs/adr/) — decisions made, with alternatives rejected

## Where to start

The open checklist in [docs/ROADMAP.md](docs/ROADMAP.md) is the work queue. Take a
box, open an issue naming it, keep the PR scoped to it.

## Ground rules

- **Specs lead code.** Changing the graph schema, the CLI surface, or a protocol
  means updating its spec in the same PR.
- **Decisions get an ADR** — with the alternatives you rejected and why. Short is fine.
- **Context claims need numbers.** A PR that claims better retrieval must move a
  metric in the eval harness against B0/B1/B3. "Feels better" is not a result,
  and a negative result is a publishable one.
- **Security rules in SPEC-policy are not optional.** Untrusted content stays
  framed and stays out of L0–L2. Memory writes respect origin trust.
- **Anchors are mandatory.** A derived fact without an anchor cannot be
  invalidated, so it does not get stored.
- **Cross-platform from the start:** macOS, Linux, Windows.
- **Tests with behaviour.** Parsers and anchors get unit tests; retrieval gets
  eval-suite coverage; policy gets adversarial tests, not happy-path ones.

## Dev setup

```bash
git clone https://github.com/prayag2301/mogestrator.git
cd mogestrator
uv venv && source .venv/bin/activate
uv pip install -e ".[mcp]" pytest pytest-asyncio ruff
ruff check src tests scripts
pytest
python scripts/smoke.py
```

## Commits and PRs

Conventional commits (`feat:`, `fix:`, `docs:`, `perf:`, `sec:`). One concern per
PR, linked to the roadmap box it closes. State what a reviewer should verify by
hand.

## Open questions

PLAN.md §9 tracks what is still undecided — embedding model, vector store,
auto-capture, gateway deployment mode, content storage. If your work depends on
one, resolve it in an ADR rather than picking silently.
