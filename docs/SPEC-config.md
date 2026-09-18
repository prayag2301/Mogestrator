# Spec: `mogestrator.yaml`

> Status: **Draft v1**. M1 implements `version: 1` and `index.include`,
> `index.exclude`, `index.max_file_bytes`, and `index.allow_secret_content`.
> Invalid values or unsupported index keys exit 3 before modifying the index.
> Custom exclusions extend the built-in runtime/build exclusions. Changes to
> the allowlist reclassify unchanged files on the next index.
> Other sections, precedence layers, and validation rules below are planned;
> currently only `mogestrator.yaml` is loaded.

```yaml
version: 1
project: acme-api

index:
  include: ["src/**", "tests/**"]
  exclude: ["**/node_modules/**", "**/*.min.js", "**/vendor/**"]
  max_file_bytes: 400_000
  languages: [python, typescript, go, rust]
  # Files whose content may be stored despite matching the ingest gate
  # (ADR-0008) — fixtures holding deliberately fake credentials. Per-pattern
  # and opt-in; there is no global off switch.
  allow_secret_content: ["tests/fixtures/*.env"]
  git_history:
    co_change_window: 200        # commits mined for co_changed edges
    min_co_change: 3             # occurrences before an edge is created

embeddings:
  provider: local                # local | anthropic | openai | ollama
  model: bge-small-en-v1.5       # Q1 open
  dim: 384
  batch: 64
  cache: true                    # keyed by content hash; never re-embed unchanged

retrieval:
  seeds: 8
  max_hops: 3
  hop_decay: 0.6
  min_score: 0.15
  default_budget: 4000
  default_zoom: auto
  edge_weights:                  # overrides SPEC-context-graph §2 defaults
    co_changed: 0.65

working_set:
  budget_pct: 60                 # of the model window
  evict_to_pct: 45               # hysteresis
  recency_halflife: 40           # turns
  never_evict: [correction, constraint, pinned, active_task]
  stub_tokens: 40

memory:
  auto_capture:                  # Q3 open — off until explicitly enabled
    enabled: false
    kinds: [decision, failure, correction]
  retention:
    structural: rebuildable      # safe to prune
    episodic: forever

policy:                          # see SPEC-policy.md
  layers_dir: ./policy/layers
  require_signed: true
  flows: ./policy/flows.yaml
  network: ./policy/network.yaml
  canary: true

secrets:                         # declarations only — a value here is an error
  ANTHROPIC_API_KEY: { required: false }
  GITHUB_TOKEN:      { required: false }

serve:
  mcp:
    transport: stdio
    tools: [search_context, expand, impact, why, remember, recall, pin, verify]
    default_budget: 4000
```

## Precedence
built-in defaults → `mogestrator.yaml` → `mogestrator.local.yaml` (git-ignored)
→ `MOG_*` env vars → CLI flags.

## Planned validation
1. `version` must be `1`.
2. `embeddings.dim` must match the stored vectors, or the command fails with a
   `mog reindex --embeddings` hint. Mixed-model vector spaces are never queried.
3. `working_set.evict_to_pct < budget_pct`.
4. Every path in `policy.*` exists and every layer digest verifies when
   `require_signed: true`.
5. Declared-but-unresolvable required secrets fail before any work starts.
