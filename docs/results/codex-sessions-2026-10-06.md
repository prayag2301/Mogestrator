# Codex agent-session A/B — 2026-10-06

**Measured duration: 25 min 33 s; target: about 20 minutes for the whole experiment.**
One baseline run and one Mogestrator run completed 10 prompts each.
Both arms passed every applicable hidden acceptance check. On the original ten prompts, Mogestrator processed
+49.2% input tokens and took +17.0% wall time.
This is a single pair, not evidence of a statistically established effect.

## Method

- Same private Python repository as the [Claude Code v2 evaluation](agent-sessions-v2-2026-09-30.md), approximately 1.7k LOC; name and code withheld.
- Mogestrator `0.2.0a2`, repository commit `0a559e7c77ca36fdab69ca4e10db800dfcd35b28`.
- `codex-cli 0.160.0`, `gpt-6.1-sol`, reasoning effort `high`.
- Separate fresh clones, with one baseline/Mogestrator pair run sequentially for each prompt. Three persisted sessions per arm, with fresh threads at the two session boundaries.
- The ten substantive prompts and two teammate changes were recovered from the prior evaluator's session log. No new Claude Code benchmark was run.
- Baseline used native tools and a repository handoff text file. Mogestrator used its existing skill, installed under `.agents/skills/mogestrator`, and anchored memories in its local graph. Both were told to avoid subagents and other test checkouts.
- Codex used its `workspace-write` sandbox with command network access disabled. User configuration and execution rules were excluded; the CLI used the existing account login for inference.
- [Non-interactive Codex execution](https://learn.chatgpt.com/docs/non-interactive-mode) supplied JSONL traces and session resumption. Hidden acceptance checks were recovered separately and run by the evaluator after both arms finished; agents were not given those checks.
- Time measures actual prompt execution, CLI startup, and transitions; initial clone/index setup, authentication probes, and final evaluator scoring are excluded. There was no idle padding. The original ten-prompt sequence was finished even though it exceeded the target.

| Session | Tasks |
|---|---|
| 1 | Localization, rename impact, validated threshold flag, new module and catalogue entry, handoff |
| Teammate change 1 | Exclude undecided outcomes from resilience denominators |
| 2 | Category thresholds and teammate-change report, second module, handoff update |
| Teammate change 2 | Rename the per-result explanation key in JSON reports |
| 3 | Report summarizer and integration test, audit and correct persisted notes |

## Measurements

| Original ten prompts | Baseline | Mogestrator |
|---|---:|---:|
| Input tokens, including cache hits | 1,733,252 | 2,586,158 |
| Cached input tokens (subset) | 1,562,496 | 2,370,048 |
| Output tokens, including reasoning | 25,053 | 27,425 |
| Model-reported reasoning tokens (subset) | 5,003 | 5,110 |
| Wall time (seconds) | 706.1 | 826.0 |
| Shell command calls | 56 | 161 |
| File-change calls | 10 | 9 |
| Shell command calls mentioning `mog` | 0 | 104 |
| Hidden acceptance checks | 20/20 | 19/19 |
| Additional gate-failure checks | 4/4 | 4/4 |
| Dollar cost | Not reported | Not reported |

Codex reports cumulative token usage when resuming a thread. The evaluator
subtracts the previous result for that thread to obtain each prompt's usage,
then sums those differences across all three sessions. Cached input is already
included in input tokens; reasoning is already included in output tokens.
Neither subset is added again. The saved session trace confirmed this accounting.
The JSON artifact retains the reported cumulative values beside the differences.

## Checks and observations

- Both arms correctly answered all three localization questions and listed the 18 files affected by the field rename, including the JSON consumer contract.
- Both fresh second sessions identified the changed denominator and adapted their tests to it.
- Final suites: baseline **107 passed in 0.27s**; Mogestrator **80 passed in 0.29s**. The acceptance checks also independently exercised threshold validation, repeated category gates, module registration/classification, catalogue updates, and summarizer output from a real report.
- Recoverable `mog` lookup failures: baseline 0, Mogestrator 11. Login shells reset the supplied PATH; the Mogestrator agent recovered with non-login shells. These retries are included in command counts and time, and confound attribution of overhead to retrieval alone.

Failed acceptance checks: none. The baseline skips `P3_below_exits_1`;
Mogestrator skips `P3_below_exits_1` and `P6_below_exits_1`.
The legacy grader skips below-threshold checks when the echo adapter scores 100%.
Four additional checks per arm use the compromised adapter to verify both failure
exit codes and report preservation, so those branches still receive coverage.

## Comparison limits

The historical Claude experiment used another model/client, four runs per arm,
and Claude Code auto-memory. This Codex experiment has one run per arm and a
repository handoff instead of auto-memory. Compare the Mogestrator effect within
each client; the values do not rank the two clients. Claude's 2×2 seeded-stale-note
experiment was not repeated here. Command counts from Codex also differ from
Claude's count of all tool calls.

The result is a small-repository measurement. It does not satisfy the pinned
corpora, chunk-RAG comparison, or M2 evaluation gate. Detailed per-prompt usage and
acceptance outcomes are in [the JSON artifact](codex-sessions-2026-10-06.json).
Private source and raw transcripts remain outside this repository.
