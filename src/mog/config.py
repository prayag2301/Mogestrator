"""Configuration shared by the CLI and the repository-bound MCP service."""

from pathlib import Path

import yaml

from mog.index.walker import DEFAULT_EXCLUDES, MAX_FILE_BYTES


def index_options(root: Path) -> dict:
    cfg = root / "mogestrator.yaml"
    if not cfg.exists():
        return {}
    try:
        data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError) as exc:
        raise ValueError("cannot parse mogestrator.yaml") from exc
    if not isinstance(data, dict) or type(data.get("version")) is not int or data["version"] != 1:
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
        if not isinstance(patterns, list) or any(not isinstance(p, str) or not p for p in patterns):
            raise ValueError(f"index.{key} must be a list of nonempty strings")
        result[key] = tuple(patterns)
    result["exclude"] = (*DEFAULT_EXCLUDES, *result.get("exclude", ()))
    maximum = options.get("max_file_bytes", MAX_FILE_BYTES)
    if type(maximum) is not int or maximum <= 0:
        raise ValueError("index.max_file_bytes must be a positive integer")
    result["max_bytes"] = maximum
    return result


def ensure_gitignored(root: Path, entry: str = ".mog/") -> bool:
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
            f"{prefix}# mogestrator index — describes the whole tree, keep it local\n{entry}\n"
        )
    return True
