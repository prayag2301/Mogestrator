# Distribution & installation channels

> Status: **0.1.1** is stable; **0.2.0a2** completes M1 and includes experimental retrieval/MCP. Other
> channels remain planned for **M6**.
>
> Principle: *adoption dies at the install step*. A developer must be able to
> try `mog` in under 30 seconds without touching their global Python, and a
> team must be able to pin an exact version in CI.

## 0. Naming (must be settled before first publish)

| Artifact | Proposed name | Note |
|----------|---------------|------|
| CLI binary | `mog` | short, memorable, typed dozens of times a day |
| PyPI package | `mogestrator` | 0.0.1 placeholder; functional releases start at 0.1.1 |
| npm package | `@mogestrator/cli` | scoped, wraps the binary |
| Homebrew | `prayag2301/tap/mog` | own tap first; core tap only after traction |
| Docker | `ghcr.io/prayag2301/mogestrator` | GHCR, free for public repos |
| GitHub Action | `prayag2301/mogestrator-action@v1` | thin wrapper over the binary |

## 1. Channel matrix

| # | Channel | Command | Audience | Priority |
|---|---------|---------|----------|----------|
| 1 | **uv (zero-install)** | `uvx --from mogestrator mog index` | try it now, no commitment | **P0** |
| 2 | **uv tool** | `uv tool install mogestrator` | daily driver, isolated | **P0** |
| 3 | **pip** | `pip install mogestrator` | inside an existing venv / CI | **P0** |
| 4 | **pipx** | `pipx install mogestrator` | Python devs who keep globals clean | P1 |
| 5 | **Homebrew** | `brew install prayag2301/tap/mog` | macOS/Linux, no Python opinion | P1 |
| 6 | **Install script** | `curl -fsSL https://get.mogestrator.dev \| sh` | servers, containers, CI images | P1 |
| 7 | **PowerShell script** | `irm https://get.mogestrator.dev/ps1 \| iex` | Windows | P1 |
| 8 | **npm/npx** | `npx @mogestrator/cli init` | JS/TS teams with no Python | P1 |
| 9 | **Docker** | `docker run --rm -v $PWD:/w ghcr.io/…/mog run x` | hermetic CI, no host install | P1 |
| 10 | **GitHub Action** | `uses: prayag2301/mogestrator-action@v1` | CI workflows | P1 |
| 11 | **Claude Code plugin** | `/plugin marketplace add prayag2301/Mogestrator` | Claude Code users — slash commands wrapping `mog` | P2 |
| 12 | **MCP server** | `mog serve --mcp` | expose the context graph as tools to any MCP client | P2 |
| 13 | **mise / asdf** | `mise use mogestrator@latest` | polyglot version-manager users | P2 |
| 14 | **Nix flake** | `nix run github:prayag2301/Mogestrator` | Nix users | P3 |
| 15 | **VS Code extension** | Marketplace: *Mogestrator* | GUI run/sync surface | P3 |
| 16 | **Scoop / WinGet** | `scoop install mog` | Windows package managers | P3 |

Python release tooling is implemented; publication requires the PyPI account
configuration below. Other channels remain planned.

## 2. How each is built

### Python wheel (channels 1–4, 10)
- `pyproject.toml`, PEP 621 metadata, hatchling backend.
- Entry point: `[project.scripts] mog = "mog.cli.main:app"`.
- Mogestrator ships one pure-Python wheel + sdist. tree-sitter, grammar wheels,
  and sqlite-vec are native dependencies installed alongside it.
- Extras keep the base install lean:
  `mogestrator[api-embeddings]`, `[mcp]`, `[gateway]`, `[all]`.
- Published by CI on a **published GitHub release**, via PyPI Trusted Publishing
  (OIDC). Manual workflow runs test the pipeline without publishing.
- The tag must equal `v` plus the version in both `pyproject.toml` and
  `src/mog/__init__.py`. Tests on three operating systems, metadata checks,
  and an installed-wheel smoke test must pass before upload.

### PyPI account setup and release procedure

At <https://pypi.org/manage/project/mogestrator/settings/publishing/>, configure:

| Field | Value |
|-------|-------|
| Owner | `prayag2301` |
| Repository | `Mogestrator` |
| Workflow filename | `release.yml` |
| Environment | `pypi` |

The old `v0.1.0` release points to placeholder code and failed with
`invalid-publisher`; it must not be retried as a functional release. Use the new
`v0.1.1` tag on the verified main commit. Do not move the old tag.

After merge, create a GitHub release for the new version. Mark alpha versions
such as `0.2.0a1` as prereleases; stable installs should continue to select 0.1.1. Watch the Release
workflow, then verify the version on PyPI and run `scripts/smoke.py` using a
fresh environment installed from PyPI. A GitHub release alone does not prove
that publishing succeeded. If OIDC fails, correct the matching publisher in
PyPI and rerun the failed job; never put a long-lived token into the repository.

### Standalone binary (channels 5–9, 13, 14)
- Built with PyInstaller (one-file) for: macOS arm64/x64, Linux x64/arm64
  (built on `manylinux` for glibc compatibility), Windows x64.
- Every release publishes `SHA256SUMS` plus a Sigstore/cosign signature and an
  SBOM (CycloneDX).
- The install scripts detect OS/arch, download the matching asset, **verify the
  checksum**, and install to `~/.local/bin` (or `%LOCALAPPDATA%\mog\bin`).
- `curl | sh` always has a documented manual alternative — some teams forbid it,
  and rightly.

### npm wrapper (channel 8)
- `@mogestrator/cli` ships a `postinstall` that fetches the matching binary
  from the GitHub release and verifies its checksum, with per-platform
  `optionalDependencies` so `npx` gets the right one.
- Exists so a JS team never has to think about Python.

### Docker (channel 9)
- Multi-stage, distroless-based final image, non-root, multi-arch
  (`linux/amd64`, `linux/arm64`).
- Tags: `:1.4.2`, `:1.4`, `:1`, `:latest`.
- Documented run form mounts the repo read-write and passes secrets by env, not
  by baking them into the image.

### GitHub Action (channel 10)
- Composite action: installs a pinned `mog`, caches the graph, runs `mog index`
  on push and `mog verify --strict` to fail CI when anchors have drifted.
- Inputs: `version`, `command` (`index` / `verify` / `search`), `strict`.

### Claude Code plugin (channel 11)
- A marketplace entry providing slash commands that shell out to `mog`
  (`/mog-search`, `/mog-why`) plus the `mog` MCP server, so Claude Code can
  query the context graph as tools.

## 3. Versioning and support

- **SemVer.** The public contract is: `mogestrator.yaml` schema, CLI flags,
  exit codes, and the plugin protocols in ARCHITECTURE §3.
- Config `version: 1` is supported for the life of major 1.x; a schema change
  that breaks existing files requires `version: 2` plus `mog migrate`.
- Release channels: `latest` (stable) and `next` (pre-release, `1.5.0rc1`).
- Deprecations warn for one minor release before removal.

## 4. Release pipeline (M3 definition of done)

```
tag v1.x.y
   ├─ build sdist + wheel                → PyPI (OIDC trusted publishing)
   ├─ build 5 binaries                   → GitHub Release + SHA256SUMS + cosign sig
   ├─ build multi-arch image             → ghcr.io
   ├─ bump formula                       → homebrew-tap repo (automated PR)
   ├─ publish npm wrapper                → npmjs (provenance enabled)
   ├─ publish action tag                 → v1 moving tag
   └─ smoke test every channel           → install matrix job, must pass
```

The smoke-test matrix is the gate: a release is not announced until a fresh
runner on each OS can install via each P0/P1 channel and run
`mog init && mog index && mog search "auth"`.

## 5. Uninstall

Every channel documents its removal command. `mog` writes only to
`~/.mog/` (cache, credentials) and the project's `.mog/`; `mog uninstall
--purge` removes both and any installed git hooks.
