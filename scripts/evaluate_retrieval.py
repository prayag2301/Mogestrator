"""Run a versioned localization dataset; this is not the full M2 acceptance benchmark.

Dataset JSON: {"cases": [{"query": "...", "expected": ["path::symbol", ...]}]}.
Indexes the supplied repository locally and reports top-5 recall, bytes, and latency.
"""

import argparse
import json
import statistics
import subprocess
import time
from pathlib import Path

from mog.config import ensure_gitignored, index_options
from mog.graph.store import Store
from mog.index.embed import DEFAULT_MODEL
from mog.index.indexer import Indexer
from mog.serve.service import RepositoryService


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--semantic", action="store_true")
    args = parser.parse_args()
    service = RepositoryService(args.repo)
    root = args.repo.resolve()
    options = index_options(root)
    exclusions = list(options.get("exclude", ()))
    # Answers and previous reports must not become retrieval candidates.
    for excluded in (args.dataset.resolve(), args.output.parent.resolve()):
        try:
            relative = excluded.relative_to(root).as_posix()
        except ValueError:
            continue
        exclusions.append(relative + "/**" if excluded.is_dir() else relative)
    options["exclude"] = tuple(exclusions)
    ensure_gitignored(root)
    store = Store(root / ".mog/graph.db")
    try:
        Indexer(root, store, **options).run()
    finally:
        store.close()
    embedding_stats = service.call("embed", model=DEFAULT_MODEL) if args.semantic else None
    cases = json.loads(args.dataset.read_text())["cases"]
    rows = []
    for case in cases:
        start = time.perf_counter()
        result = service.call("search", query=case["query"], budget=16000, semantic=args.semantic)
        elapsed = (time.perf_counter() - start) * 1000
        expected = set(case["expected"])
        locations = [item["location"] for item in result["items"][:5]]
        rows.append(
            {
                "query": case["query"],
                "expected": sorted(expected),
                "top5": locations,
                "recall_at_5": len(expected.intersection(locations)) / len(expected),
                "latency_ms": round(elapsed, 2),
                "context_bytes": result["token_upper_bound"],
                "mode": result["mode"],
            }
        )
    commit = subprocess.run(
        ["git", "-C", str(args.repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    report = {
        "scope": "Authored localization smoke cases; not M2 acceptance or B0/B1 comparison",
        "repository_commit": commit,
        "working_tree_dirty": bool(
            subprocess.run(
                ["git", "-C", str(args.repo), "status", "--porcelain"],
                capture_output=True,
                text=True,
                check=False,
            ).stdout.strip()
        ),
        "recall_at_5": statistics.mean(r["recall_at_5"] for r in rows),
        "median_latency_ms": statistics.median(r["latency_ms"] for r in rows),
        "embedding_setup": embedding_stats,
        "cases": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "cases"}, indent=2))


if __name__ == "__main__":
    main()
