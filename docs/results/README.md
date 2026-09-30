# Retrieval smoke results — 0.2.0a1

These six cases were authored against this repository. They test plumbing and
record misses; they are not independent development tasks or the required M2
B0/B1 comparison. The repository was a dirty working tree at the recorded base
commit. The harness excludes its answer dataset and results directory from
indexing, so they cannot supply retrieval hits.

`retrieval-smoke-0.2.0a1.json` uses exact/FTS plus graph retrieval.
`retrieval-semantic-smoke-0.2.0a1.json` additionally uses local
BAAI/bge-small-en-v1.5. Context cost is a conservative byte upper bound, not
actual model tokens. Latency excludes index/model setup, which occurs before the measured queries. Do not generalize these small-sample measurements.

The FastEmbed smoke test also confirmed a second embedding pass made no new
embeddings for unchanged content. Unit tests cover dimension/model mismatch,
cache reuse, and explicit lexical fallback. M2 acceptance remains open.

## Agent-session A/B — 2026-09-30

[`agent-sessions-2026-09-30.md`](agent-sessions-2026-09-30.md) compares baseline
agents with Mogestrator-skill agents on a private project across a multi-session
task sequence, with raw per-prompt metrics in the matching JSON file. The result is
negative: equal task success, and about 29% more tokens with Mogestrator.

## Agent-session A/B v2 — 2026-09-30

[`agent-sessions-v2-2026-09-30.md`](agent-sessions-v2-2026-09-30.md) covers two experiments,
using headless Claude Code runs with hidden checks:
- a 10-prompt, 3-session sequence with n=4 per arm;
- a 2×2 stale-notes experiment with n=3 per cell.

Success was equal in every arm. Over the long sequence there was no measurable cost
difference. Stale notes raised baseline cost by 59%, while Mogestrator with stale
memories was 15% cheaper than baseline.
