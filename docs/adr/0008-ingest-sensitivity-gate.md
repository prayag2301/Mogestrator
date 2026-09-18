# ADR-0008 — Taint labels at ingest, not at egress

**Status:** Accepted · **Date:** 2026-09-02

## Context

The policy plane ([SPEC-policy.md](../SPEC-policy.md)) reasons about `secret` and
`repo:private` taint and blocks their flow to egress. It is specified for **M5**.
The store landed in **M1**. For four milestones, therefore, the design has the
index accumulating content it has no labels for, and only starts caring at the
far end of the pipe.

That is not theoretical. Measured against M1 as shipped:

```console
$ mog index            # repo containing .env.production and deploy_key.pem
$ strings .mog/graph.db | grep -i wJalrX
file_…  .env.production  fresh  AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/…
wjalrxutnfemi          ← the FTS5 token: indexed, lowercased, queryable
```

Two ingest paths fed it. `Indexer._index_file` stored the first 600 characters of
every discovered file as `nodes.content` and 1200 into FTS5, with no notion of
what the file was. And `discover()` followed symlinks out of the tree — in the
git walk as much as the fallback one, since `git ls-files --others` lists a
symlink like any other file — so a link to `~/.ssh` pulled content from outside
the repo into an index that claims to describe it.

`mog init` compounded both by *printing* `add to .gitignore` rather than doing
it. The default outcome was a single portable SQLite file concentrating every
credential in the tree, sitting in the repo root, one `git add -A` from
publication — and, per M3, about to be exposed over MCP to an agent with network
access.

## Decision

**Assign taint where provenance is known, which is the indexer**, and make the
storage layer enforce it.

1. **Ingest is confined to the tree.** `discover()` resolves each path and skips
   anything landing outside the root. Both walk modes.
2. **Every file is classified** (`mog.index.sensitivity`) into `secret` or
   `repo:private` from its path shape and all of its bytes (up to the configured file-size limit). Strong
   markers (PEM headers, `AKIA…`, `ghp_…`, `xoxb-…`) decide on their own; the
   generic `KEY = value` shape additionally requires the value to look random
   (Shannon entropy ≥ 3.9 bits/char, placeholders rejected), because a false
   positive here costs real content.
3. **A `secret` file keeps its node and loses its bytes.** No content preview,
   no FTS row, no parse, and no `start_byte`/`end_byte` — the offsets would let
   a reader fetch from disk what the store declined to hold. *"`config/prod.env`
   exists and holds a secret"* is the useful fact, and it survives.
4. **`Store.upsert_nodes` refuses** to write content on a `secret`-labelled node,
   for every writer, now and later. Gating is policy; this is enforcement.
5. `mog init` and `mog index` write `.mog/` to `.gitignore`. `mog index` reports
   the gated count; `mog status --secrets` lists what was gated.

Escape hatch: `index.allow_secret_content` in `mogestrator.yaml` — per-pattern,
opt-in, for the fixtures that legitimately contain fake keys. There is no global
off switch. This repo uses it for `tests/test_sensitivity.py`.

The property this buys is stronger than any downstream rule: **secret content
never enters the store, so no future bug in retrieval, MCP or egress can leak
it.** It costs one check at one boundary.

## Alternatives considered

- **Wait for M5 and handle it in the flow rules.** The store would hold four
  milestones of unlabelled secrets by then, and every M5 rule would be one bug
  away from being bypassed. Filtering at egress protects the paths you thought
  of; not storing protects the ones you didn't.
- **Add the patterns to `DEFAULT_EXCLUDES`.** Cheaper, and it makes the graph
  lie: the file exists, and an agent that cannot see it exists will propose
  creating it. Excluded files are also invisible to `mog status`, so nobody
  learns the gate is working.
- **Encrypt `.mog/graph.db` at rest.** Solves the committed-index problem and
  none of the others — retrieval decrypts by definition, so MCP still serves the
  bytes. Wrong layer.
- **Redact matched spans, keep the rest of the file.** Tempting for `.env` files
  with a mix of secrets and settings. Redaction is a string operation on content
  we have already decided we cannot classify reliably; partial success here is
  indistinguishable from failure.
- **Entropy scanning alone, no path rules.** Misses a `.pem` whose base64 body
  scores below threshold, and gates every minified asset and checksum file.
- **A vendored detect-secrets / gitleaks ruleset.** Hundreds of vendor patterns,
  a dependency, and a maintenance stream, for a boundary whose job is to be
  conservative and obvious. Revisit if the gate proves leaky in practice.

## Consequences

+ The `secret` label exists at the only point that can assign it truthfully, and
  M5's flow rules inherit a store that is already labelled — `repo:private` is
  populated too, not just `secret`.
+ The `labels` column, plumbed since M1 and written by nothing, now carries the
  taint it was designed for.
+ A committed `.mog/` no longer publishes credentials, and is unlikely to be
  committed at all.
− False positives cost content: a gated file is a file the agent cannot read
  through mog. `mog status --secrets` exists so this is visible rather than
  mysterious, and the allowlist so it is fixable.
− The classifier is a heuristic and will miss things — a low-entropy password, a format nobody has seen. It raises the floor; it is not
  a proof.
− Removing an allowlist entry removes matching content from active nodes and
  search results on the next index. This does not securely erase historical
  bytes from SQLite pages, WAL files, or backups created while explicitly
  allowing that content.
− Two rule sets now describe sensitive paths (`.mogignore` excludes, sensitivity
  patterns). They answer different questions — *don't index this* versus *index
  this without its bytes* — but the overlap will invite confusion.
