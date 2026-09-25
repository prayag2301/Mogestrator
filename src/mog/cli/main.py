"""The ``mog`` command line. Surface defined in docs/SPEC-cli.md.

M1 implements indexing, inspection, and anchored full-text search.
Graph retrieval commands (impact, why) arrive in M2.
"""

from __future__ import annotations

import json as jsonlib
import re
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from mog import __version__
from mog.graph.anchors import drift, file_hash, span_hash
from mog.graph.models import EdgeKind
from mog.graph.store import SECRET_LABEL, Store
from mog.index.indexer import Indexer
from mog.index.parsers.base import spec_for_path
from mog.index.walker import DEFAULT_EXCLUDES, MAX_FILE_BYTES

app = typer.Typer(
    name="mog", help="Mogestrator — a context substrate for coding agents.",
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


def _ensure_gitignored(root: Path, entry: str = ".mog/") -> bool:
    """Add ``.mog/`` to .gitignore. Returns True if it was written.

    Printing a reminder is not a safe default: the index describes the whole
    tree, and committing it publishes that description (ADR-0008).
    """
    ignore = root / ".gitignore"
    lines = ignore.read_text(encoding="utf-8").splitlines() if ignore.exists() else []
    if any(line.strip().rstrip("/") == entry.rstrip("/") for line in lines):
        return False
    prefix = "" if not lines or lines[-1] == "" else "\n"
    with ignore.open("a", encoding="utf-8") as fh:
        fh.write(
            f"{prefix}# mogestrator index — describes the whole tree, keep it local\n"
            f"{entry}\n"
        )
    return True


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
    """Validate supported index settings before changing the store."""
    cfg = root / "mogestrator.yaml"
    if not cfg.exists():
        return {}
    try:
        import yaml

        data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
        if (
            not isinstance(data, dict)
            or type(data.get("version")) is not int
            or data["version"] != 1
        ):
            raise ValueError("version must be 1")
        options = data.get("index", {})
        if not isinstance(options, dict):
            raise ValueError("index must be a mapping")
        supported = {"include", "exclude", "max_file_bytes", "allow_secret_content"}
        if unknown := options.keys() - supported:
            raise ValueError(f"unsupported index settings: {', '.join(sorted(unknown))}")
        result = {}
        for key in ("include", "exclude", "allow_secret_content"):
            if key not in options:
                continue
            patterns = options[key]
            if not isinstance(patterns, list) or any(
                not isinstance(p, str) or not p for p in patterns
            ):
                raise ValueError(f"index.{key} must be a list of nonempty strings")
            result[key] = tuple(patterns)
        result["exclude"] = (*DEFAULT_EXCLUDES, *result.get("exclude", ()))
        maximum = options.get("max_file_bytes", MAX_FILE_BYTES)
        if type(maximum) is not int or maximum <= 0:
            raise ValueError("index.max_file_bytes must be a positive integer")
        result["max_bytes"] = maximum
        return result
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
                f"[yellow]{len(gated)} files gated as secret[/] "
                f"[dim](content not stored)[/]"
            )
            for n in gated:
                console.print(f"  {n.path}")
        store.close()
        return
    last = store.get_meta("last_indexed_at")
    age = time.time() - last if last else None
    payload = {
        "root": str(root), "counts": counts, "age_seconds": age,
        "vectors": {"available": store.vec.available, "version": store.vec.version,
                    "reason": store.vec.reason},
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
        f"[green]sqlite-vec {store.vec.version}[/]" if store.vec.available
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
    from mog.index.parsers.languages import get_parser

    root = _root(directory)
    store = _open(root)
    checked = fresh = 0
    problems: list[dict[str, str]] = []
    cache: dict[str, dict[str, str]] = {}

    for node in store.find_nodes(limit=-1):
        if not node.anchor:
            continue
        checked += 1
        path = node.anchor.path
        if path not in cache:
            cache[path] = _current_spans(root / path, path, get_parser, root)
        current = cache[path].get(node.anchor.symbol or "")
        reason = drift(node.anchor.span_hash, current)
        if reason is None:
            fresh += 1
        else:
            problems.append({"id": node.id, "location": node.display(), "reason": reason})

    rate = (len(problems) / checked * 100) if checked else 0.0
    if json_out:
        _emit(jsonlib.dumps({"checked": checked, "fresh": fresh,
                             "drifted": len(problems), "drift_rate_pct": round(rate, 2),
                             "problems": problems[:200]}))
    else:
        console.print(
            f"checked [bold]{checked}[/] anchors · [green]{fresh} hold[/] · "
            f"[yellow]{len(problems)} drifted[/] ([bold]{rate:.1f}%[/] drift rate)"
        )
        for p in problems[:20]:
            console.print(f"  [yellow]{p['reason']:14}[/] {p['location']}")
        if len(problems) > 20:
            console.print(f"  [dim]…and {len(problems) - 20} more[/]")
        if problems:
            console.print("\n[dim]drift is expected after edits — run 'mog index' to refresh[/]")
    store.close()
    if strict and problems:
        raise typer.Exit(EX_STALE)


def _resolve(store: Store, target: str):
    """Resolve `id`, `name`, `Class.method`, or `path::symbol` to one node."""
    path, _, rest = target.rpartition("::")
    matches = store.find_by_qualname(rest, limit=20) or store.find_nodes(name=rest, limit=20)
    if path:
        matches = [n for n in matches if n.path == path]
    return matches[0] if matches else None


def _current_spans(abs_path: Path, rel: str, get_parser, root: Path) -> dict[str, str]:
    """Symbol -> span hash for the file as it exists right now."""
    spec = spec_for_path(rel)
    try:
        abs_path.resolve(strict=True).relative_to(root.resolve())
        source = abs_path.read_bytes()
    except (OSError, ValueError, RuntimeError):
        return {}
    spans = {"": file_hash(source)}
    if spec is None:
        return spans
    try:
        tree = get_parser(spec.name).parse(source)
    except Exception:
        return spans
    from mog.index.parsers.base import extract

    symbols, _ = extract(tree.root_node, source, spec, rel)
    spans.update({s.qualname: span_hash(s.node, source) for s in symbols})
    return spans


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
def search(
    query: Annotated[str, typer.Argument(help="Words to find in indexed names and previews.")],
    directory: Annotated[Path | None, typer.Option("--repo")] = None,
    limit: Annotated[int, typer.Option("--limit", min=1, max=100)] = 20,
    json_out: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Find anchored nodes with local full-text search."""
    terms = re.findall(r"\w+", query, flags=re.UNICODE)
    if not terms:
        err.print("[red]query must contain a word[/]")
        raise typer.Exit(EX_USAGE)
    root = _root(directory)
    store = _open(root)
    # Quote each term so punctuation cannot be interpreted as FTS syntax.
    matches = store.search_text(" OR ".join(f'"{term}"' for term in terms), limit)
    rows = [
        {"id": node.id, "location": node.display(), "kind": node.kind.value,
         "state": node.state.value, "anchor": asdict(node.anchor) if node.anchor else None,
         "score": score}
        for node, score in matches
    ]
    if json_out:
        _emit(jsonlib.dumps({"query": query, "results": rows}))
    else:
        for row in rows:
            console.print(f"{row['location']}  [dim]{row['state']} · {row['id']}[/]")
        if not rows:
            err.print("[yellow]no matches[/]")
    store.close()
    if not rows:
        raise typer.Exit(EX_NOT_FOUND)


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
        "GROUP BY path ORDER BY c DESC LIMIT ?", (limit,)
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
            n["name"] for n in store.db.execute(
                "SELECT name FROM nodes WHERE path=? AND kind='symbol' "
                "AND json_extract(meta,'$.qualname') NOT LIKE '%.%' LIMIT 5", (r["path"],)
            )
        ]
        table.add_row(r["path"], str(r["c"]), ", ".join(names))
    console.print(table)
    store.close()


class _null:
    def __enter__(self): return self
    def __exit__(self, *a): return False


if __name__ == "__main__":
    sys.exit(app())
