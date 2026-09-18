"""The ``mog`` command line. Surface defined in docs/SPEC-cli.md.

Index/inspect commands plus experimental retrieval, memory, and MCP serving.
"""

from __future__ import annotations

import json as jsonlib
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from mog import __version__
from mog.config import ensure_gitignored as _ensure_gitignored
from mog.config import index_options
from mog.graph.models import EdgeKind
from mog.graph.store import SECRET_LABEL, Store
from mog.index.indexer import Indexer

app = typer.Typer(
    name="mog",
    help="Mogestrator — a context substrate for coding agents.",
    add_completion=False,
)
console = Console()
err = Console(stderr=True)

# Exit codes are a public contract (SPEC-cli.md).
EX_NOT_FOUND, EX_USAGE, EX_CONFIG, EX_STALE = 1, 2, 3, 4

DEFAULT_CONFIG = """\
version: 1
project: {project}

index:
  exclude: ["**/node_modules/**", "**/.venv/**", "**/dist/**", "**/target/**"]
  max_file_bytes: 400000
"""

DEFAULT_MOGIGNORE = "# Paths mog should not index (same syntax as .gitignore)\n*.min.js\n*.lock\n"


def _root(explicit: Path | None = None) -> Path:
    """Nearest ancestor holding mogestrator.yaml or .mog/, else cwd."""
    if explicit:
        return explicit.resolve()
    here = Path.cwd().resolve()
    for candidate in (here, *here.parents):
        if (candidate / "mogestrator.yaml").exists() or (candidate / ".mog").is_dir():
            return candidate
    return here


def _index_options(root: Path) -> dict:
    try:
        return index_options(root)
    except Exception as exc:
        err.print(f"[red]invalid mogestrator.yaml:[/] {exc}")
        raise typer.Exit(EX_CONFIG) from exc


def _open(root: Path, *, require_index: bool = True) -> Store:
    db = root / ".mog" / "graph.db"
    if require_index and not db.exists():
        err.print(f"[red]no index at[/] {db}\n[dim]run:[/] mog index")
        raise typer.Exit(EX_STALE)
    return Store(db)


@app.callback(invoke_without_command=True)
def _main(
    ctx: typer.Context,
    version: Annotated[bool, typer.Option("--version", help="Show version and exit.")] = False,
) -> None:
    if version:
        _emit(__version__)
        raise typer.Exit(0)
    if ctx.invoked_subcommand is None:
        console.print(ctx.get_help())
        raise typer.Exit(0)


def _emit(text: str) -> None:
    """Write machine-readable output unwrapped.

    rich hard-wraps at the terminal width, which corrupts JSON for anything
    downstream — `mog ... --json | jq` is a documented contract (SPEC-cli.md).
    """
    sys.stdout.write(text + "\n")


@app.command()
def init(
    directory: Annotated[Path | None, typer.Argument(help="Project root.")] = None,
) -> None:
    """Scaffold mogestrator.yaml, .mogignore and .mog/."""
    root = (directory or Path.cwd()).resolve()
    root.mkdir(parents=True, exist_ok=True)
    (root / ".mog").mkdir(exist_ok=True)
    cfg = root / "mogestrator.yaml"
    if cfg.exists():
        err.print(f"[yellow]exists, not overwritten:[/] {cfg}")
    else:
        cfg.write_text(DEFAULT_CONFIG.format(project=root.name), encoding="utf-8")
        console.print(f"[green]created[/] {cfg.relative_to(root)}")
    ignore = root / ".mogignore"
    if not ignore.exists():
        ignore.write_text(DEFAULT_MOGIGNORE, encoding="utf-8")
        console.print("[green]created[/] .mogignore")
    console.print("[green]created[/] .mog/")
    if _ensure_gitignored(root):
        console.print("[green]added[/] .mog/ to .gitignore")
    console.print("\n[dim]next:[/] mog index")


@app.command()
def index(
    directory: Annotated[Path | None, typer.Option("--repo", help="Project root.")] = None,
    full: Annotated[bool, typer.Option("--full", help="Rebuild from scratch.")] = False,
    json_out: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Build or update the context graph. Incremental unless --full."""
    root = _root(directory)
    options = _index_options(root)
    store = _open(root, require_index=False)
    _ensure_gitignored(root)
    with console.status("indexing…") if not json_out else _null():
        indexer = Indexer(root, store, **options)
        stats = indexer.run(full=full)
    counts = store.counts()
    if json_out:
        _emit(jsonlib.dumps({**asdict(stats), "totals": counts}, default=str))
    else:
        console.print(f"[green]indexed[/] {root}")
        console.print(
            f"  {counts['files']} files · {counts.get('kind:symbol', 0)} symbols · "
            f"{counts.get('kind:test', 0)} tests · {counts['edges']} edges"
        )
        console.print(
            f"  [dim]{stats.files_indexed} written, {stats.files_skipped} unchanged, "
            f"{stats.files_removed} removed · {stats.duration:.2f}s[/]"
        )
        if stats.stale_marked:
            console.print(f"  [yellow]{stats.stale_marked} facts marked stale[/]")
        if stats.files_gated:
            console.print(
                f"  [yellow]{stats.files_gated} files gated as secret[/] "
                f"[dim](listed, never stored — mog status --secrets)[/]"
            )
        if not store.vec.available:
            console.print(f"  [yellow]vector search unavailable[/] [dim]({store.vec.reason})[/]")
    store.close()


@app.command()
def status(
    directory: Annotated[Path | None, typer.Option("--repo")] = None,
    json_out: Annotated[bool, typer.Option("--json")] = False,
    secrets: Annotated[bool, typer.Option("--secrets", help="List gated files.")] = False,
) -> None:
    """Index size, freshness and capability report."""
    import time

    root = _root(directory)
    store = _open(root)
    counts = store.counts()
    if secrets:
        gated = store.find_labeled(SECRET_LABEL, limit=-1)
        if json_out:
            _emit(jsonlib.dumps({"gated": [n.path for n in gated]}))
        elif not gated:
            console.print("[green]no files gated[/]")
        else:
            console.print(
                f"[yellow]{len(gated)} files gated as secret[/] [dim](content not stored)[/]"
            )
            for n in gated:
                console.print(f"  {n.path}")
        store.close()
        return
    last = store.get_meta("last_indexed_at")
    age = time.time() - last if last else None
    payload = {
        "root": str(root),
        "counts": counts,
        "age_seconds": age,
        "vectors": {
            "available": store.vec.available,
            "version": store.vec.version,
            "reason": store.vec.reason,
        },
    }
    if json_out:
        _emit(jsonlib.dumps(payload, default=str))
        store.close()
        return

    table = Table(show_header=False, box=None)
    table.add_row("root", str(root))
    table.add_row("files", str(counts["files"]))
    table.add_row("symbols", str(counts.get("kind:symbol", 0)))
    table.add_row("tests", str(counts.get("kind:test", 0)))
    table.add_row("edges", str(counts["edges"]))
    stale = counts.get("state:stale", 0)
    table.add_row("stale facts", f"[yellow]{stale}[/]" if stale else "0")
    gated = counts.get("gated", 0)
    table.add_row("gated as secret", f"[yellow]{gated}[/]" if gated else "0")
    table.add_row("last indexed", f"{age / 60:.1f} min ago" if age else "[yellow]never[/]")
    table.add_row(
        "vector search",
        f"[green]sqlite-vec {store.vec.version}[/]"
        if store.vec.available
        else f"[yellow]unavailable[/] [dim]({store.vec.reason})[/]",
    )
    console.print(table)
    store.close()


@app.command()
def verify(
    directory: Annotated[Path | None, typer.Option("--repo")] = None,
    json_out: Annotated[bool, typer.Option("--json")] = False,
    strict: Annotated[bool, typer.Option("--strict", help="Exit 4 if any drift.")] = False,
) -> None:
    """Re-check every anchor against the working tree and report drift."""
    from mog.serve.service import RepositoryService

    root = _root(directory)
    if not (root / ".mog/graph.db").exists():
        err.print("no index; run mog index")
        raise typer.Exit(EX_STALE)
    try:
        payload = RepositoryService(root).call("verify")
    except ValueError as exc:
        err.print(str(exc), markup=False)
        raise typer.Exit(EX_CONFIG) from exc
    if json_out:
        _emit(jsonlib.dumps(payload))
    else:
        console.print(
            f"checked {payload['checked']} anchors · {payload['fresh']} hold · "
            f"{payload['drifted']} drifted ({payload['drift_rate_pct']}% drift rate)"
        )
        for problem in payload["problems"][:20]:
            console.print(f"  {problem['location']}: {problem['reason']}", markup=False)
    if strict and payload["drifted"]:
        raise typer.Exit(EX_STALE)


def _resolve(store: Store, target: str):
    """Resolve `id`, `name`, `Class.method`, or `path::symbol` to one node."""
    path, _, rest = target.rpartition("::")
    matches = store.find_by_qualname(rest, limit=20) or store.find_nodes(name=rest, limit=20)
    if path:
        matches = [n for n in matches if n.path == path]
    return matches[0] if matches else None


@app.command()
def show(
    target: Annotated[str, typer.Argument(help="Node id, or path::symbol.")],
    directory: Annotated[Path | None, typer.Option("--repo")] = None,
    edges: Annotated[bool, typer.Option("--edges/--no-edges")] = True,
) -> None:
    """Print one node with its anchor and neighbours."""
    root = _root(directory)
    store = _open(root)
    node = store.get_node(target)
    if node is None:
        node = _resolve(store, target)
    if node is None:
        err.print(f"[red]not found:[/] {target}")
        store.close()
        raise typer.Exit(EX_NOT_FOUND)

    colour = {"fresh": "green", "stale": "yellow"}.get(node.state.value, "red")
    console.print(f"[bold]{node.display()}[/]  [{colour}]{node.state.value}[/]  [dim]{node.id}[/]")
    if node.anchor:
        console.print(
            f"[dim]anchor  {node.anchor.span_hash[:23]}…  line {node.anchor.start_line}[/]"
        )
    if sig := node.meta.get("signature"):
        console.print(f"[dim]{sig}[/]")
    if edges:
        for label, rev in (("calls", False), ("called by", True)):
            rows = store.neighbors(node.id, [EdgeKind.CALLS], reverse=rev)
            if rows:
                names = ", ".join(sorted({n.name for n, _, _ in rows})[:12])
                console.print(f"  [cyan]{label:10}[/] {names}")
        tests = store.neighbors(node.id, [EdgeKind.TESTED_BY])
        if tests:
            console.print(f"  [cyan]{'tested by':10}[/] {', '.join(n.name for n, _, _ in tests)}")
    store.close()


@app.command()
def map(
    directory: Annotated[Path | None, typer.Option("--repo")] = None,
    limit: Annotated[int, typer.Option("--limit")] = 25,
) -> None:
    """Print the L0 repo map: the largest modules and their entry points."""
    root = _root(directory)
    store = _open(root)
    rows = store.db.execute(
        "SELECT path, count(*) c FROM nodes WHERE kind IN ('symbol','test') "
        "GROUP BY path ORDER BY c DESC LIMIT ?",
        (limit,),
    ).fetchall()
    if not rows:
        err.print("[yellow]index is empty[/]")
        store.close()
        raise typer.Exit(EX_NOT_FOUND)
    table = Table(box=None)
    table.add_column("file", style="cyan")
    table.add_column("symbols", justify="right")
    table.add_column("top-level", style="dim")
    for r in rows:
        names = [
            n["name"]
            for n in store.db.execute(
                "SELECT name FROM nodes WHERE path=? AND kind='symbol' "
                "AND json_extract(meta,'$.qualname') NOT LIKE '%.%' LIMIT 5",
                (r["path"],),
            )
        ]
        table.add_row(r["path"], str(r["c"]), ", ".join(names))
    console.print(table)
    store.close()


def _query(operation: str, directory: Path | None, json_out: bool, **kwargs) -> None:
    from mog.retrieve.engine import serialize
    from mog.serve.service import RepositoryService

    try:
        result = RepositoryService(_root(directory)).call(
            operation,
            **{k: v for k, v in kwargs.items() if k != "explain"},
        )
    except FileNotFoundError as exc:
        err.print(str(exc), markup=False)
        raise typer.Exit(EX_STALE) from exc
    except LookupError as exc:
        err.print(str(exc), markup=False)
        raise typer.Exit(EX_NOT_FOUND) from exc
    except PermissionError as exc:
        err.print(str(exc), markup=False)
        raise typer.Exit(6) from exc
    except (ValueError, ImportError, RuntimeError) as exc:
        err.print(str(exc), markup=False)
        raise typer.Exit(EX_CONFIG) from exc
    if json_out:
        _emit(serialize(result))
    elif "items" in result:
        for item in result["items"]:
            console.print(f"{item['location']} [{item['state']}] {item['zoom']}", markup=False)
            console.print(item["content"], markup=False)
            if item.get("warning"):
                console.print(item["warning"], markup=False)
            if kwargs.get("explain"):
                console.print(serialize(item["provenance"]), markup=False)
        for warning in result["warnings"]:
            err.print(warning, markup=False)
        console.print(
            f"{len(result['items'])} results · {result['token_upper_bound']} token upper bound"
        )
    else:
        console.print(serialize(result), markup=False)


@app.command()
def search(
    query: str,
    directory: Annotated[Path | None, typer.Option("--repo")] = None,
    budget: Annotated[int, typer.Option(min=512, max=100_000)] = 4000,
    zoom: str = "auto",
    kind: str | None = None,
    semantic: bool = False,
    explain: bool = False,
    json_out: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Retrieve ranked anchored context with exact/FTS seeds and graph expansion."""
    _query(
        "search",
        directory,
        json_out,
        query=query,
        budget=budget,
        zoom=zoom,
        kind=kind,
        semantic=semantic,
        explain=explain,
    )


@app.command()
def expand(
    target: str,
    directory: Annotated[Path | None, typer.Option("--repo")] = None,
    zoom: str = "L2",
    budget: Annotated[int, typer.Option(min=512, max=100_000)] = 4000,
    json_out: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Expand a node at L0 (map), L1 (signature), L2 (body), or L3 (file)."""
    _query("expand", directory, json_out, target=target, zoom=zoom, budget=budget)


@app.command()
def impact(
    symbol: str,
    directory: Annotated[Path | None, typer.Option("--repo")] = None,
    depth: Annotated[int, typer.Option(min=0, max=8)] = 2,
    tests: bool = False,
    budget: Annotated[int, typer.Option(min=512, max=100_000)] = 4000,
    json_out: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Find reverse callers and affected tests, with bounded dependency traversal."""
    _query(
        "impact", directory, json_out, target=symbol, depth=depth, tests_only=tests, budget=budget
    )


@app.command()
def neighbors(
    target: str,
    directory: Annotated[Path | None, typer.Option("--repo")] = None,
    edge: str | None = None,
    reverse: bool = False,
    budget: Annotated[int, typer.Option(min=512, max=100_000)] = 4000,
    json_out: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Inspect typed graph relationships."""
    _query(
        "neighbors", directory, json_out, target=target, edge=edge, reverse=reverse, budget=budget
    )


@app.command()
def why(
    topic: str,
    directory: Annotated[Path | None, typer.Option("--repo")] = None,
    budget: Annotated[int, typer.Option(min=512, max=100_000)] = 4000,
    json_out: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Search recorded decisions, failures, and constraints."""
    _query("why", directory, json_out, query=topic, budget=budget)


@app.command()
def remember(
    kind: str,
    content: str,
    directory: Annotated[Path | None, typer.Option("--repo")] = None,
    anchor: str | None = None,
    json_out: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Record an episodic memory with an optional node ID or path::symbol anchor."""
    _query(
        "remember", directory, json_out, kind=kind, content=content, anchor=anchor, origin="user"
    )


@app.command()
def recall(
    node_id: str,
    directory: Annotated[Path | None, typer.Option("--repo")] = None,
    json_out: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Restore a recorded memory verbatim."""
    _query("recall", directory, json_out, node_id=node_id)


@app.command()
def pin(
    node_id: str,
    directory: Annotated[Path | None, typer.Option("--repo")] = None,
) -> None:
    """Pin a persistent memory."""
    _query("pin", directory, True, node_id=node_id, value=True)


@app.command()
def unpin(
    node_id: str,
    directory: Annotated[Path | None, typer.Option("--repo")] = None,
) -> None:
    """Unpin a persistent memory."""
    _query("pin", directory, True, node_id=node_id, value=False)


@app.command()
def embed(
    directory: Annotated[Path | None, typer.Option("--repo")] = None,
    model: str = "BAAI/bge-small-en-v1.5",
) -> None:
    """Build cached local embeddings (optional extra; first run downloads model weights)."""
    _query("embed", directory, True, model=model)


@app.command()
def serve(
    directory: Annotated[Path | None, typer.Option("--repo")] = None,
    mcp: Annotated[bool, typer.Option("--mcp")] = True,
    transport: str = "stdio",
    port: Annotated[int, typer.Option(min=1, max=65535)] = 8765,
    watch: bool = False,
    allow_memory_writes: bool = False,
) -> None:
    """Serve repository-bound MCP tools over stdio or loopback HTTP (/mcp)."""
    if transport not in {"stdio", "http"}:
        raise typer.BadParameter("transport must be stdio or http")
    try:
        from mog.serve.mcp import create_server
    except ImportError as exc:
        err.print('Install MCP support: pip install "mogestrator[mcp]"', markup=False)
        raise typer.Exit(EX_CONFIG) from exc
    server = create_server(
        _root(directory), watch=watch, allow_memory_writes=allow_memory_writes, port=port
    )
    server.run(transport="stdio" if transport == "stdio" else "streamable-http")


class _null:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


if __name__ == "__main__":
    sys.exit(app())
