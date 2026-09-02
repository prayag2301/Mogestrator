"""File discovery. Honours git, .gitignore and .mogignore."""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from fnmatch import fnmatch
from pathlib import Path

DEFAULT_EXCLUDES = (
    "**/node_modules/**", "**/.venv/**", "**/venv/**", "**/__pycache__/**",
    "**/dist/**", "**/build/**", "**/target/**", "**/.git/**", "**/vendor/**",
    "**/*.min.js", "**/*.lock", "**/.mog/**",
)
MAX_FILE_BYTES = 400_000


def _git_files(root: Path) -> list[str] | None:
    """Tracked + untracked-but-not-ignored files, or None outside a repo.

    Using git means .gitignore is honoured for free — and it is what kept the
    api_token file out of the published sdist.
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "ls-files",
             "--cached", "--others", "--exclude-standard", "-z"],
            capture_output=True, text=True, timeout=30, check=True,
        ).stdout
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return None
    return [p for p in out.split("\0") if p]


def _walk_files(root: Path) -> list[str]:
    return [
        str(p.relative_to(root)).replace("\\", "/")
        for p in root.rglob("*")
        if p.is_file()
    ]


def is_binary(path: Path, probe: int = 4096) -> bool:
    try:
        chunk = path.open("rb").read(probe)
    except OSError:
        return True
    return b"\0" in chunk


def load_mogignore(root: Path) -> list[str]:
    f = root / ".mogignore"
    if not f.exists():
        return []
    return [
        line.strip()
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines()
        if line.strip() and not line.startswith("#")
    ]


def excluded(rel: str, patterns: tuple[str, ...] | list[str]) -> bool:
    return any(fnmatch(rel, pat) or fnmatch(f"/{rel}", pat) for pat in patterns)


def discover(
    root: Path,
    include: tuple[str, ...] | list[str] = ("**",),
    exclude: tuple[str, ...] | list[str] = DEFAULT_EXCLUDES,
    max_bytes: int = MAX_FILE_BYTES,
) -> Iterator[tuple[str, Path, int]]:
    """Yield ``(relative_path, absolute_path, size)`` for indexable files."""
    root = root.resolve()
    rels = _git_files(root)
    if rels is None:
        rels = _walk_files(root)
    patterns = list(exclude) + load_mogignore(root)

    for rel in sorted(rels):
        if excluded(rel, patterns):
            continue
        if include != ("**",) and not any(fnmatch(rel, pat) for pat in include):
            continue
        abs_path = root / rel
        # Ingest is confined to the tree: a symlink out of it would pull
        # ~/.ssh or /etc into an index that is meant to describe this repo
        # (ADR-0008). git lists symlinks like any other file, so both walk
        # modes need this.
        try:
            real = abs_path.resolve(strict=True)
            real.relative_to(root)
        except (OSError, ValueError, RuntimeError):
            continue
        try:
            size = real.stat().st_size
        except OSError:
            continue
        if size == 0 or size > max_bytes or is_binary(abs_path):
            continue
        yield rel, abs_path, size
