# Evaluation harness

> Status: **Draft v1**. Gates M2 and M4.
>
> A context system with no benchmark is faith. Everything in
> [SPEC-context-graph.md](./SPEC-context-graph.md) is a *hypothesis* until this
> harness says otherwise. The honest outcome "the graph lost to ripgrep" must be
> discoverable, and if it happens we publish it and change course.

## Baselines we must beat

| ID | Baseline | Why it's the bar |
|----|----------|------------------|
| **B0** | `rg` + read the whole file | What a competent agent does today. Cheap, dumb, surprisingly strong. |
| **B1** | Chunk-and-embed RAG (512-token chunks, top-k=10) | The standard answer we claim to improve on. |
| **B2** | Full-transcript, no management (fits or fails) | Upper bound on quality, unusable at scale. |
| **B3** | Summarization compaction at 90% | What Claude Code does now — the direct comparison for §6 of the graph spec. |

## Task suites

1. **Localization** — "where is X handled?" Metric: is the correct symbol in the
   returned pack? `recall@k`, plus tokens spent.
2. **Impact** — "what breaks if I change Y?" Ground truth from a real refactor
   commit's touched-set. Metric: precision/recall against the actual diff.
3. **Multi-session continuity** — task split across three sessions with a hard
   reset between. Metric: does session 3 repeat work, or re-ask a settled
   question? This is where B3 should lose and we should win.
4. **Repeat-failure avoidance** — the ledger's whole purpose. Seed a `Failure`,
   then pose a task whose obvious approach is the failed one. Metric: does the
   agent retry it?
5. **Staleness** — mutate the repo after indexing. Metric: does the system serve
   the stale fact silently (fail), labelled (pass), or refuse (over-strict)?
6. **Cold subagent** — spawn a subagent with a context handle vs. a copied blob
   vs. nothing. Metric: tokens to first correct action.

## Metrics

- **Answer quality** — task success, human- or judge-scored, per suite.
- **Token cost** — total in/out to reach a correct answer. The headline number.
- **Latency** — p50/p95 for retrieval; must stay well under a file read.
- **Staleness rate** — share of served facts whose anchors no longer match.
- **Poisoning resistance** — of N injected malicious memories, how many reach a
  high-trust prompt layer? Target: zero, and it is a release blocker.

## Corpora

Three real OSS repos of increasing size (~5k / ~50k / ~500k LOC) across Python,
TypeScript, and Go, pinned to specific commits so results are reproducible.
Ground truth is mined from real commits and issues, not hand-written, so we
cannot accidentally author a benchmark we are guaranteed to pass.

## Gates

- **M2 ships only if** seed-and-spread beats B1 on localization recall *and*
  beats B0 on tokens-to-correct-answer by a margin we state publicly.
- **M4 ships only if** eviction-and-recall beats B3 on multi-session continuity
  and repeat-failure avoidance.
- **M5 ships only if** poisoning resistance is zero-through and the scripted
  exfiltration suite is fully blocked.

Results, including negative ones, are published in `docs/results/`.

## Implemented smoke harness (0.2.0a1)

`scripts/evaluate_retrieval.py` accepts a repository and JSON query/expected-location
cases, indexes it, and reports recall@5, latency, and conservative context bytes.
The authored cases in `tests/fixtures/retrieval_cases.json` are smoke tests only;
`docs/results/retrieval-smoke-0.2.0a1.json` includes their failures as well as
successes. No claim of passing B0/B1 or the M2 gate follows from these results.
The prerelease exposes the implementation for evaluation while M2 remains open.
