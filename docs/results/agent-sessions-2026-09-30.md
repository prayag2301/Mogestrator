# Agent-session A/B — 2026-09-30

**Verdict: on this project, Mogestrator did not improve agent sessions.** Both arms
reached 100% task success. The Mogestrator arm used about 29% more tokens and about
15% more wall time. Staleness labelling caught a stale memory in one of two runs.

The task project is a private Python repository (~1.7k LOC, ~37 Python files).
Its name, domain, and code are withheld. Raw numbers are in
[`agent-sessions-2026-09-30.json`](agent-sessions-2026-09-30.json).

## Setup

- **Arms.** Four clones of the project at the same commit:
  - **Baseline** (×2): normal agent tools (read, grep/rg, edit, shell). Mogestrator is forbidden.
  - **Mogestrator** (×2): the same tools, plus a project skill
    (`.claude/skills/mogestrator/SKILL.md`, reproduced below). The skill tells the agent to run
    `mog index`, `verify` and `why` at session start, to search and impact-check before
    grepping, and to `mog remember` durable facts. The index was built before session 1.
- **Agent.** `claude-opus-5-5` general-purpose subagents. Every arm got identical
  prompt text, apart from the one tooling line.
- **Sessions.** Session 1 is one continuous agent session with five prompts. Then comes a
  hard reset (a new agent with no conversation history). Between the sessions a
  simulated teammate commit changes a scoring formula that session 1 had learned
  and written into its notes. Session 2 is a fresh agent with two prompts.
- **Baseline memory.** Session 1 persisted its handoff however it chose. One baseline
  wrote `CLAUDE.md` and the other wrote `SESSION_HANDOFF.md`. Baseline session 2 was
  told to read `CLAUDE.md` if present, mirroring Claude Code's auto-load.
  The Mogestrator arm used `mog remember` in both runs.

| # | Suite ([EVALUATION.md](../EVALUATION.md)) | Prompt (generic) |
|---|---|---|
| P1 | Localization | Three "where is X decided?" questions, answered as `path::Symbol` |
| P2 | Impact | Every file touched by renaming a widely used result field, plus external consumers |
| P3 | Edit | Add a validated CLI exit-threshold flag, with a test |
| P4 | Edit | Add a new plugin module following repo conventions, update the docs catalogue |
| P5 | Continuity | Persist whatever the next session needs |
| P6 | Continuity + staleness | Per-category variant of the P3 flag; report what the teammate changed |
| P7 | Continuity + staleness | Recall P4's work; find and correct stale notes |

## Answer quality

Hidden acceptance checks (13, run by the evaluator, never shown to the agents)
cover the CLI behaviour, plugin registration and classification, the docs catalogue and the
full test suite. P1 and P2 were scored against a grep-derived ground truth.

| | Baseline-1 | Baseline-2 | Mog-1 | Mog-2 |
|---|---|---|---|---|
| P1 localization (3 parts) | 3/3 | 3/3 | 3/3 | 3/3 |
| P2 impact (18 files + JSON consumer) | 18/18 ✓ | 18/18 ✓ | 18/18 ✓ | 18/18 ✓ |
| Hidden checks after S1 | 9/9 | 9/9 | 9/9 | 9/9 |
| Hidden checks after S2 | 13/13 | 13/13 | 13/13 | 13/13 |
| Found teammate change (P6) | ✓ via git | ✓ via git | ✓ via git | ✓ via git + stale label |
| Found stale note (P6/P7) | ✓ | ✓ | ✓ (manually) | ✓ (`mog` flagged it) |
| Corrected the note (P7) | declined to edit `CLAUDE.md` | ✓ edited | appended correction | appended correction |

There was no quality difference; both arms hit the ceiling. The project is small
enough that `rg` and whole-file reads are already cheap and complete.

## Cost (mean of two runs per arm)

| Segment | Metric | Baseline | Mogestrator | Δ |
|---|---|---|---|---|
| All 7 prompts | Tokens processed | 1.00M | 1.30M | **+29%** |
| | New input tokens | 80.3k | 89.9k | +12% |
| | Tool calls | 25 | 32.5 | +30% |
| | Wall time | 196 s | 226 s | +15% |
| P1–P2 (localize / impact) | Tokens processed | 202k | 300k | +49% |
| P3–P4 (edits) | Tokens processed | 356k | 389k | +9% |
| S2 (P6–P7, after reset) | Tokens processed | 312k | 498k | **+59%** |
| | Wall time | 70 s | 96 s | +37% |

"Tokens processed" sums input, cache-read and cache-write tokens over every model
call. It is the cost-relevant number. For session 1 the within-arm spread
(634k–749k for baseline) is about the size of the between-arm gap, so treat that
row as noise. The session-2 ranges don't overlap: baseline 252k–373k against
Mogestrator 480k–515k.

**Where the overhead comes from:**
- The session-start ritual (`index` + `verify` + `why`) adds a turn to every session.
- After the reset, agents read the recalled memories (stale and fresh) and then verified them against the code and git anyway.
- In P2, `mog impact` on a dataclass field returned nothing (call links are
  name-based), so the agent fell back to `rg` and paid for both.

## Staleness and memory findings

- **Anchoring worked when the anchor was right (1/2).** Mog-2 anchored the fact "outcome X
  counts in the denominator" to the scoring function the teammate edited, so
  `mog verify` / `why` labelled it stale automatically.
- **Mis-anchored memory stays "fresh" (1/2).** Mog-1 anchored the same fact to the
  runner function that *produces* the value, not the function that computes the ratio.
  The teammate's commit didn't touch that anchor, so the wrong memory kept a `fresh`
  label. The agent caught it only by reading the diff. A drift label is only as good
  as the agent's anchor choice.
- **Memories are append-only.** Neither Mogestrator agent could edit or retire the
  stale memory, so each appended a correction next to it. Future sessions will keep
  retrieving the wrong fact and have to reconcile it.
- **Git already solved the "what changed?" question.** Every arm, including both
  Mogestrator runs, found the teammate commit with `git log`/`diff`.
- **Ingest gate side effect.** One source file contains fake credentials by design
  and was excluded from the index (ADR-0008). Agents had to read it directly.

## Retrieval harness on the same project

`scripts/evaluate_retrieval.py` ran 14 localization cases whose ground truth came
from the session tasks. The index was a clean clone.

| Mode | recall@5 | Median latency | Median context bound |
|---|---|---|---|
| Lexical + graph | 0.64 (9/14) | 150 ms | 16.0 kB |
| Semantic (bge-small) | 0.93 (13/14) | 174 ms | 15.8 kB |

The skill used the default lexical mode. Semantic search would have fixed most
lexical misses. It still would not have beaten reading files: in these sessions
the agents never needed more than a few file reads to localize.

## What this does and does not show

- **n = 2 per arm on one small repository.** Measurements, not a benchmark claim.
- Consistent with the M2 status in the README: **no evidence yet that Mogestrator
  beats `rg` on real development tasks.** On a repository this size it costs more.
- This run did not exercise large repositories where `rg` output floods context,
  sessions longer than two, subagent hand-offs through `ctx://` handles, or MCP
  instead of the CLI. Those are the conditions where the design is supposed to pay off.
- **Suggested product follow-ups:**
  - Let a memory be superseded or retired.
  - Rank or collapse stale memories in `why`.
  - Warn when a memory's anchor doesn't contain the symbols its text names.
  - Make the session-start ritual a single command.

## Skill used by the Mogestrator arm

```markdown
# Mogestrator
CLI: `mog` (run from the repo root; every command takes `--json`).
## Session start
1. `mog index` — incremental refresh (cheap).
2. `mog verify` — anchors that drifted since the last session.
3. `mog why "<topic of the task>"` — decisions/constraints earlier sessions recorded.
## While working
- Locate code: `mog search "<question or symbol>" --budget 4000` before grepping or reading whole files.
- Read one symbol: `mog expand path::Symbol --zoom L2` (L1 signature, L3 whole file).
- Before changing a symbol: `mog impact <symbol> --tests` for callers and affected tests.
- Call links are name-based heuristics. If results look incomplete, confirm with `rg`.
- Files gated as secret (`mog status --secrets`) are not indexed; read them directly.
## Recording memory
`mog remember <decision|constraint|convention|failure|task> "<fact>" --anchor path::symbol`
A memory whose anchor changed is labelled stale. Re-check it before trusting it.
```
