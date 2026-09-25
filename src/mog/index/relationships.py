"""Conservative file imports and Git co-change relationships."""

from __future__ import annotations

import ast
import itertools
import posixpath
import re
import subprocess
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath

from mog.graph.models import Edge, EdgeKind, Node


def _import_targets(path: str, language: str, statement: str) -> list[str]:
    """Return possible repo-relative module paths, without guessing package roots."""
    parent = PurePosixPath(path).parent
    stems: list[str] = []
    if language == "python":
        try:
            tree = ast.parse(statement)
        except SyntaxError:
            return []
        for item in tree.body:
            if isinstance(item, ast.Import):
                stems.extend(alias.name.replace(".", "/") for alias in item.names)
            elif isinstance(item, ast.ImportFrom):
                base = item.module.replace(".", "/") if item.module else ""
                if item.level:
                    root = parent
                    for _ in range(item.level - 1):
                        root = root.parent
                    base = str(root / base)
                stems.append(base)
                stems.extend(f"{base}/{alias.name}" for alias in item.names if alias.name != "*")
    elif language == "typescript":
        match = re.search(r"(?:from\s*|import\s*)['\"]([^'\"]+)['\"]", statement)
        if match and match.group(1).startswith("."):
            stems.append(str(parent / match.group(1)))
    elif language == "go":
        stems.extend(re.findall(r'"([^"\s]+)"', statement))
    elif language == "rust":
        match = re.match(r"use\s+(?:crate::|self::|super::)?([\w:]+)", statement)
        if match:
            stem = match.group(1).replace("::", "/")
            stems.extend((stem, str(parent / stem)))
    return stems


def import_edges(files: list[Node], languages: dict[str, str | None]) -> list[Edge]:
    by_path = {n.path: n for n in files if n.path}
    edges: list[Edge] = []
    for source in files:
        if source.path is None or "secret" in source.labels:
            continue
        lang = languages.get(source.path)
        if lang is None:
            continue
        for statement in source.meta.get("imports", []):
            matches: set[str] = set()
            for stem in _import_targets(source.path, lang, statement):
                normalized = posixpath.normpath(stem)
                if normalized == ".." or normalized.startswith("../"):
                    continue
                extensions = (".py", "/__init__.py") if lang == "python" else (
                    (".ts", ".tsx", ".js", ".jsx", "/index.ts", "/index.js")
                    if lang == "typescript" else (".go",) if lang == "go" else (".rs", "/mod.rs")
                )
                candidates = (normalized, *(normalized + ext for ext in extensions))
                stem_matches = {p for p in candidates if p in by_path and p != source.path}
                if lang in ("python", "go") and not stem_matches:
                    suffixes = tuple("/" + candidate for candidate in candidates)
                    stem_matches.update(
                        p for p in by_path if p != source.path and p.endswith(suffixes)
                    )
                matches.update(stem_matches)
            # A statement resolving to multiple indexed files is ambiguous.
            if len(matches) == 1:
                target = by_path[next(iter(matches))]
                edges.append(Edge(source.id, target.id, EdgeKind.IMPORTS))
    return edges


def git_history_marker(root: Path) -> str | None:
    """Change when HEAD moves or a shallow checkout gains history."""
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-list", "--max-count=200", "HEAD"],
            capture_output=True, text=True, timeout=5, check=True,
        )
        commits = result.stdout.splitlines()
        return f"hunk-v1:{commits[0]}:{len(commits)}:{commits[-1]}" if commits else None
    except (OSError, subprocess.SubprocessError):
        return None


def co_change_edges(root: Path, symbols: list[Node], *, history: int = 200) -> list[Edge]:
    """Mine repeated symbol pairs from Git's changed line ranges.

    Hunks are mapped onto current symbol spans, so old line positions are an
    approximation. Repeated co-changes and a bounded fan-out limit noise.
    """
    if git_history_marker(root) is None:
        return []
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "log", f"-{history}", "--format=COMMIT:%H",
             "-p", "-U0", "--no-ext-diff", "--no-color", "--no-renames"],
            capture_output=True, text=True, errors="replace", timeout=30, check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    by_file: dict[str, list[Node]] = defaultdict(list)
    for node in symbols:
        if node.path and node.anchor and node.anchor.start_line and "secret" not in node.labels:
            by_file[node.path].append(node)
    symbol_by_id = {node.id: node for node in symbols}
    counts: Counter[str] = Counter()
    pair_counts: Counter[tuple[str, str]] = Counter()
    changed: set[str] = set()
    path: str | None = None
    hunk = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")

    def consume() -> None:
        ids = sorted(changed)
        if not 1 <= len(ids) <= 20:
            return
        counts.update(ids)
        if len(ids) >= 2:
            pair_counts.update(
                (a, b) for a, b in itertools.combinations(ids, 2)
                if symbol_by_id[a].path != symbol_by_id[b].path
            )

    for line in result.stdout.splitlines():
        if line.startswith("COMMIT:"):
            consume()
            changed.clear()
            path = None
        elif line.startswith("+++ b/"):
            path = line[6:]
        elif line.startswith("+++ /dev/null"):
            path = None
        elif path and (match := hunk.match(line)):
            start = int(match.group(1))
            end = start + max(1, int(match.group(2) or "1")) - 1
            overlapping = [
                n for n in by_file.get(path, [])
                if n.anchor.start_line <= end and n.meta.get("end_line", 0) >= start
            ]
            # A nested method is more specific than its enclosing class.
            for point in (start, end):
                covering = [n for n in overlapping
                            if n.anchor.start_line <= point <= n.meta.get("end_line", 0)]
                if covering:
                    narrowest = min(
                        covering,
                        key=lambda n: n.meta["end_line"] - n.anchor.start_line,
                    )
                    changed.add(narrowest.id)
    consume()
    edges: list[Edge] = []
    degree: Counter[str] = Counter()
    for (left, right), count in pair_counts.most_common():
        support = count / max(counts[left], counts[right])
        if count < 2 or support < 0.5 or degree[left] >= 8 or degree[right] >= 8:
            continue
        a, b = symbol_by_id[left], symbol_by_id[right]
        metadata = {"commits": count, "support": round(support, 3), "method": "diff_hunk"}
        edges.extend((Edge(a.id, b.id, EdgeKind.CO_CHANGED, meta=metadata),
                      Edge(b.id, a.id, EdgeKind.CO_CHANGED, meta=metadata)))
        degree[left] += 1
        degree[right] += 1
    return edges
