# Agent-session A/B, v2 — 2026-09-30

A follow-up to the [v1 pilot](agent-sessions-2026-09-30.md) (n=2, 7 prompts):
- more runs per arm and more prompts;
- a controlled test of whether out-of-date notes change agent performance, with and without Mogestrator.

The task project is the same private Python repository (~1.7k LOC); its name, domain and code are withheld.
Aggregates are in [`agent-sessions-v2-2026-09-30.json`](agent-sessions-v2-2026-09-30.json).

## Summary

| Question | Answer |
|---|---|
| Does Mogestrator change task success? | **No.** All 20 runs, in every arm, passed all 303 hidden checks. |
| Is it cheaper over a long multi-session sequence? | **Not measurably.** Mean cost was 8% lower, but the per-run ranges overlap. Tokens were 4% higher. |
| Do out-of-date notes hurt? | **Not correctness, but cost.** Stale notes never produced wrong code, but they made a baseline agent 59% more expensive, because it re-verified every note. |
| Does Mogestrator help with out-of-date notes? | **Yes, here.** With stale notes, Mogestrator runs were 15% cheaper than baseline, and cheaper than Mogestrator with no notes. Its drift labels told the agent which notes to distrust. |
| Where does it lose? | With no notes to manage, Mogestrator was 55% more expensive on short tasks. |

## Method

- **Agent:** `claude -p` (Claude Code 2.1.285, `claude-opus-5-5`), one headless process per prompt.
  - Follow-up prompts resume the same session.
  - A "new session" is a new session ID with no conversation history.
- **Isolation:** the OS sandbox (writes confined to the clone, no network), and only the project's own settings loaded.
- **Arms:**
  - **Baseline:** Claude Code's native tools. `CLAUDE.md` and Claude Code's auto-memory load as they normally would.
  - **Mogestrator:** the same, plus the project skill from v1 and `mog` on `PATH`.
- **Scoring:**
  - Hidden acceptance checks the agents never saw.
  - Cost is Claude Code's reported `total_cost_usd` per prompt; tokens processed = input + cache-read + cache-write.
  - Ranges are min–max across runs.
- **Interruptions:** two usage-limit interruptions happened during the runs.
  - Runs cut off before they started any work were rebuilt and re-run from scratch.
  - Exp A runs interrupted mid-task were reset to the start of that session, then re-run from there. Two Mogestrator memories written by an interrupted attempt were deleted, so they couldn't leak into the re-run.
  - One prompt per Exp A run (Task 7) was interrupted mid-session and continued in the same session. Its cost includes both attempts.
- **Total spend:** $68.

### Exp A: long sequence (4 runs per arm, 10 prompts, 3 sessions)

| Session | Prompts |
|---|---|
| 1 | Localization question, rename-impact question, add a validated CLI gate flag, add a plugin module + docs catalogue entry, persist handoff |
| *(teammate commit 1: scoring formula changed, descriptive commit message)* | |
| 2 | Per-category variant of the gate flag + report what the teammate changed, add a second plugin module, update notes |
| *(teammate commit 2: JSON report key renamed, vague commit message)* | |
| 3 | Write a report-summarizer script (depends on the renamed key), audit which notes are now wrong |

### Exp B: stale notes (2×2, 3 runs per cell, 4 tasks in one session)

The repository starts with seven notes from a "previous session". They are identical in wording and anchored to the same code in both arms:
- **Baseline:** stored in `CLAUDE.md`.
- **Mogestrator:** stored as `mog remember` memories.

Four teammate commits land after the notes, so four of the notes are wrong. Each task is built so that trusting one wrong note gives silently wrong output:

| Task | Stale note it tempts |
|---|---|
| Add a gate flag that exits "like the other gates" | Gate failures exit 1 (now 3) |
| Write a report summarizer | The explanation JSON key is `evidence` (now renamed) |
| Document how scoring works | Undecided results count in the denominator (no longer) |
| Add a plugin using the shared helpers | Helpers live in module X (moved) |

Control cells have the same code and history but no notes.

## Exp A results

| Mean of 4 runs | Baseline | Mogestrator | Δ |
|---|---|---|---|
| Hidden checks passed | 80/80 | 79/79 (1 n/a) | — |
| Wrong notes found in final audit | 4/4 | 4/4 | — |
| Cost, all 10 prompts | $5.87 (5.75–5.96) | $5.38 (5.04–5.92) | −8% |
| Tokens processed | 1.95M | 2.03M | +4% |
| Tool calls | 70.5 | 57.8 | −18% |
| Wall time | 370 s | 366 s | −1% |
| Session 1 cost | $2.24 | $2.01 | −10% |
| Session 2 cost | $3.02 | $2.71 | −10% |
| Session 3 cost | $0.61 | $0.66 | +8% |

- **Baseline memory was strong.** All four baseline runs kept their handoff notes in Claude Code's auto-memory, which loads into every new session. Mogestrator runs kept project facts in `mog` memories.
- **The vague second teammate commit didn't fool anyone.** Every run in both arms wrote a summarizer that reads the renamed key, and every final audit flagged the old-key note.
- **Verdict:** the 8% cost gap isn't meaningful at n=4, since the ranges overlap. Mogestrator used fewer tool calls: it batched `mog` commands, and grepped and read files less.

## Exp B results

| Mean of 3 runs | Baseline, no notes | Baseline, stale notes | Mog, no notes | Mog, stale notes |
|---|---|---|---|---|
| Hidden checks passed | 36/36 | 36/36 | 36/36 | 36/36 |
| Outputs that followed a stale note | — | 0 | — | 0 |
| **Cost, 4 tasks** | **$1.40** (1.25–1.70) | **$2.22** (2.05–2.36) | **$2.17** (2.00–2.35) | **$1.89** (1.84–1.96) |
| Tokens processed | 507k | 869k | 1.04M | 764k |
| Tool calls | 13.7 | 33.0 | 31.0 | 24.0 |
| Wall time | 97 s | 130 s | 145 s | 123 s |

- **Effect of stale notes:**
  - Baseline: +59% cost and 2.4× the tool calls.
  - Mogestrator: −13% cost.
- **Mogestrator vs baseline:**
  - With no notes, Mogestrator is 55% more expensive.
  - With stale notes, Mogestrator is 15% cheaper.
- **Range overlap:** no ranges overlap between cells, except baseline-stale and Mogestrator-with-no-notes.

**How the agents behaved:**
- **Baseline with stale `CLAUDE.md`:** the model didn't follow wrong notes blindly. It read the recent commits (`git show`) and re-verified each note against the code before relying on it. That verification is where the extra ~20 tool calls went. Two of three runs also corrected `CLAUDE.md`.
- **Mogestrator with stale memories:**
  - The session-start `mog verify` / `why` labelled 3 of the 4 wrong memories `[stale]` up front, so agents re-checked only those and trusted the rest.
  - With no memories, Mogestrator runs made more search and read calls (31 vs 24), so stale-but-labelled memories came out cheaper than none.
- **The fourth wrong memory was a blind spot.** It named a module that a teammate commit had moved. Mogestrator's rename tracking followed the move and re-anchored the memory, so `why` showed it as **`[fresh]`**, even though its text still named the old path. Two runs were shown it. Both checked the code and didn't follow it.
- **Mogestrator memories are append-only.** Agents recorded corrections next to the wrong memories instead of fixing them.

## What changed since v1, and limits

- **v1 vs v2.** v1 (n=2, in-session subagents, no auto-memory) measured Mogestrator at +29% tokens. v2 with n=4 and headless sessions puts the long-sequence difference within noise. The cost of the Mogestrator ritual shows up clearly only on short, note-free tasks.
- **Where Mogestrator helped.** The measurable benefit is narrow: when persisted notes have drifted, drift labels cut the verification cost that stale notes impose.
- **Limits:**
  - One small repository.
  - One model; a model that trusted notes blindly might show correctness effects, and this one didn't.
  - Only 3–4 runs per cell.
  - The Exp B notes were seeded by hand with correct anchors. v1 showed agents sometimes anchor facts to the wrong symbol, which defeats the drift labels.
- **Product follow-ups, confirmed by v2:**
  - Re-validate memory *text* after rename tracking moves an anchor.
  - Let a correction supersede a memory.
  - Make the session-start ritual cheaper when there is nothing to recall.
