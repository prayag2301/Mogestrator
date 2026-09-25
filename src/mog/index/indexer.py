"""Builds the graph from a working tree (SPEC-context-graph §8).

Incremental by default: a file whose content hash is unchanged is skipped
entirely, so re-indexing after a commit touches only what moved.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from mog.graph.anchors import body_hash, file_hash, span_hash
from mog.graph.models import Anchor, Edge, EdgeKind, Node, NodeKind, State
from mog.graph.store import Store
from mog.index.parsers.base import LangSpec, Symbol, extract, spec_for_path
from mog.index.parsers.languages import get_parser
from mog.index.relationships import co_change_edges, git_history_marker, import_edges
from mog.index.sensitivity import SECRET, classify
from mog.index.walker import DEFAULT_EXCLUDES, MAX_FILE_BYTES, discover

#: A callee name resolving to more than this many definitions carries almost no
#: information — `__init__`, `get`, `save` match hundreds of unrelated symbols,
#: and linking them all produces a hairball that buries real structure (the same
#: reasoning as IDF: a term in every document discriminates nothing). Measured
#: on Django: this cap removes 98.3% of call edges, all of them ambiguous.
MAX_CALL_FANOUT = 8

#: Resolves Q5: we store an L1 preview (signature + docstring) and byte offsets,
#: not whole bodies. The working tree is the source of truth for code; the anchor
#: already tells us whether it has moved. Storing bodies made the Django index
#: 25% of source size — mostly a second copy of Django.
PREVIEW_CHARS = 600


@dataclass(slots=True)
class IndexStats:
    files_seen: int = 0
    files_indexed: int = 0
    files_skipped: int = 0
    files_removed: int = 0
    symbols: int = 0
    edges: int = 0
    stale_marked: int = 0
    files_gated: int = 0
    languages: dict[str, int] = field(default_factory=dict)
    duration: float = 0.0

    def summary(self) -> str:
        return (
            f"{self.files_indexed} indexed, {self.files_skipped} unchanged, "
            f"{self.files_removed} removed · {self.symbols} symbols · {self.edges} edges"
            + (f" · {self.files_gated} gated as secret" if self.files_gated else "")
        )


class Indexer:
    def __init__(
        self, root: Path, store: Store, *, exclude=DEFAULT_EXCLUDES, allow_secret_content=(),
        include=("**",), max_bytes=MAX_FILE_BYTES,
    ) -> None:
        self.root = Path(root).resolve()
        self.store = store
        self.exclude = exclude
        self.include = include
        self.max_bytes = max_bytes
        self.allow_secret_content = tuple(allow_secret_content)
        self._parsers: dict[str, object] = {}

    def _parser(self, lang: str):
        if lang not in self._parsers:
            self._parsers[lang] = get_parser(lang)
        return self._parsers[lang]

    def run(self, *, full: bool = False) -> IndexStats:
        started = time.time()
        stats = IndexStats()
        # Older indexes did not persist call sites; rebuild once on upgrade or
        # when the ingest policy changes, even if file bytes are unchanged.
        policy = {"format": 3, "allow_secret_content": list(self.allow_secret_content)}
        refresh = self.store.get_meta("index_policy") != policy
        known = {} if full else self.store.known_files()
        old_symbols: list[Node] | None = None

        def remember_symbols() -> None:
            nonlocal old_symbols
            if old_symbols is None:
                old_symbols = (
                    [n for n in self.store.find_nodes(limit=-1)
                     if n.kind in (NodeKind.SYMBOL, NodeKind.TEST) and n.meta.get("body_hash")]
                    if not full else []
                )
        history_marker = git_history_marker(self.root)
        history_changed = history_marker != self.store.get_meta("git_history_marker")
        seen: set[str] = set()
        # (qualname, path) -> node id, for cross-file call resolution.
        symbol_index: dict[str, list[str]] = {}
        pending_calls: list[tuple[str, str]] = []
        pending_tests: list[tuple[str, str]] = []

        with self.store.transaction():
            if full:
                for path in list(self.store.known_files()):
                    self.store.forget_file(path)

            for rel, abs_path, size in discover(
                self.root, include=self.include, exclude=self.exclude, max_bytes=self.max_bytes,
            ):
                stats.files_seen += 1
                seen.add(rel)
                try:
                    source = abs_path.read_bytes()
                except OSError:
                    continue
                fhash = file_hash(source)
                if not refresh and known.get(rel) == fhash:
                    stats.files_skipped += 1
                    continue

                remember_symbols()
                self.store.delete_file_nodes(rel)
                labels = classify(rel, source, self.allow_secret_content)
                spec = None if SECRET in labels else spec_for_path(rel)
                lang = spec.name if spec else None
                nodes, edges, syms = self._index_file(rel, source, spec, fhash, labels)
                if SECRET in labels:
                    stats.files_gated += 1
                self.store.upsert_nodes(nodes)
                self.store.upsert_edges(edges)
                self.store.record_file(rel, fhash, lang, size, time.time())

                stats.files_indexed += 1
                stats.symbols += len(syms)
                stats.edges += len(edges)
                if lang:
                    stats.languages[lang] = stats.languages.get(lang, 0) + 1

            for path in set(known) - seen:
                remember_symbols()
                self.store.forget_file(path)
                stats.files_removed += 1

            # Re-resolve all call sites when definitions change. Otherwise an
            # edited callee loses incoming edges from unchanged callers/tests.
            if stats.files_indexed or stats.files_removed or full:
                self.store.db.execute("DELETE FROM edges WHERE kind IN ('calls','tested_by')")
                for node in self.store.find_nodes(limit=-1):
                    if node.kind not in (NodeKind.SYMBOL, NodeKind.TEST):
                        continue
                    for name in {node.name, node.meta.get("qualname", node.name)}:
                        symbol_index.setdefault(name, []).append(node.id)
                    pending_calls.extend((node.id, callee) for callee in node.meta.get("calls", []))
                    if node.kind is NodeKind.TEST:
                        pending_tests.append((node.id, node.name))
                stats.edges += self._resolve_calls(pending_calls, symbol_index)
                stats.edges += self._link_tests(pending_tests, symbol_index)
                stats.edges += self._link_imports()
                self._continue_renames(old_symbols or [])
            if stats.files_indexed or stats.files_removed or full or history_changed:
                self.store.db.execute("DELETE FROM edges WHERE kind='co_changed'")
                symbols = [n for n in self.store.find_nodes(limit=-1)
                           if n.kind in (NodeKind.SYMBOL, NodeKind.TEST)]
                stats.edges += self.store.upsert_edges(co_change_edges(self.root, symbols))
            stats.stale_marked = self._mark_stale_facts()
            self.store.set_meta("last_indexed_at", time.time())
            self.store.set_meta("root", str(self.root))
            self.store.set_meta("index_policy", policy)
            self.store.set_meta("git_history_marker", history_marker)

        stats.duration = time.time() - started
        return stats

    def _index_file(
        self,
        rel: str,
        source: bytes,
        spec: LangSpec | None,
        fhash: str,
        labels: list[str] | None = None,
    ) -> tuple[list[Node], list[Edge], list[tuple[Node, Symbol]]]:
        labels = labels or classify(rel, source, self.allow_secret_content)
        secret = SECRET in labels
        text = "" if secret else source.decode("utf-8", "replace")
        file_node = Node(
            kind=NodeKind.FILE,
            name=rel.rsplit("/", 1)[-1],
            # A gated file keeps its node — "config/prod.env exists and holds a
            # secret" is the useful fact — and loses its bytes (ADR-0008).
            content=text[:PREVIEW_CHARS],
            labels=labels,
            meta={"lines": source.count(b"\n") + 1},
            anchor=Anchor(path=rel, span_hash=fhash, file_hash=fhash),
        )
        nodes: list[Node] = [file_node]
        edges: list[Edge] = []
        syms: list[tuple[Node, Symbol]] = []

        if secret or spec is None:
            # Gated: no parse, so no symbol bodies and no byte offsets that
            # would let a reader fetch them from disk. Otherwise degraded:
            # file-level node + FTS only.
            return nodes, edges, syms

        try:
            tree = self._parser(spec.name).parse(source)
        except Exception:
            return nodes, edges, syms

        symbols, imports = extract(tree.root_node, source, spec, rel)
        for sym in symbols:
            start, end = sym.node.start_byte, sym.node.end_byte
            body = source[start:end].decode("utf-8", "replace")
            node = Node(
                kind=NodeKind.TEST if sym.is_test else NodeKind.SYMBOL,
                name=sym.name,
                content=body[:PREVIEW_CHARS],
                labels=labels,
                anchor=Anchor(
                    path=rel,
                    symbol=sym.qualname,
                    span_hash=span_hash(sym.node, source),
                    start_line=sym.node.start_point[0] + 1,
                ),
                meta={
                    "symbol_kind": sym.kind,
                    "qualname": sym.qualname,
                    "body_hash": body_hash(sym.node, source),
                    "calls": sorted(set(sym.calls)),
                    "end_line": sym.node.end_point[0] + 1,
                    "signature": body.split("\n", 1)[0][:200],
                    # Byte offsets let L2 read the exact body from disk without
                    # a second copy in the database.
                    "start_byte": start,
                    "end_byte": end,
                },
            )
            nodes.append(node)
            edges.append(Edge(file_node.id, node.id, EdgeKind.DEFINES))
            syms.append((node, sym))

        for imp in imports:
            file_node.meta.setdefault("imports", []).append(imp)
        return nodes, edges, syms

    def _resolve_calls(self, pending: list[tuple[str, str]], index: dict[str, list[str]]) -> int:
        """Link call sites to definitions by name.

        Ambiguity is recorded rather than hidden: when a name resolves to
        several definitions we link all of them and mark the edge ambiguous, so
        retrieval can down-weight it instead of trusting a coin flip.
        """
        edges: list[Edge] = []
        self.dropped_ambiguous = 0
        for caller_id, callee in pending:
            targets = index.get(callee)
            if not targets:
                continue
            if len(targets) > MAX_CALL_FANOUT:
                # Too common to be informative. Dropped rather than down-weighted:
                # a low weight still costs a row and still pollutes expansion.
                self.dropped_ambiguous += 1
                continue
            ambiguous = len(targets) > 1
            for target in dict.fromkeys(targets):
                if target == caller_id:
                    continue
                edges.append(
                    Edge(
                        caller_id, target, EdgeKind.CALLS,
                        weight=0.4 if ambiguous else None,
                        meta={"ambiguous": ambiguous, "name": callee},
                    )
                )
        return self.store.upsert_edges(edges)

    def _link_imports(self) -> int:
        self.store.db.execute("DELETE FROM edges WHERE kind='imports'")
        files = self.store.find_nodes(kind=NodeKind.FILE, limit=-1)
        languages = {r["path"]: r["language"] for r in self.store.db.execute(
            "SELECT path, language FROM files"
        )}
        return self.store.upsert_edges(import_edges(files, languages))

    def _continue_renames(self, previous: list[Node]) -> None:
        from collections import defaultdict

        old_by_hash: dict[str, list[Node]] = defaultdict(list)
        new_by_hash: dict[str, list[Node]] = defaultdict(list)
        for node in previous:
            old_by_hash[node.meta["body_hash"]].append(node)
        for node in self.store.find_nodes(limit=-1):
            if node.kind in (NodeKind.SYMBOL, NodeKind.TEST) and node.meta.get("body_hash"):
                new_by_hash[node.meta["body_hash"]].append(node)
        current_locations = {(n.path, n.meta.get("qualname"))
                             for nodes in new_by_hash.values() for n in nodes}
        for digest, old_nodes in old_by_hash.items():
            new_nodes = new_by_hash.get(digest, [])
            if len(old_nodes) != 1 or len(new_nodes) != 1:
                continue
            old, new = old_nodes[0], new_nodes[0]
            if (old.path, old.meta.get("qualname")) == (new.path, new.meta.get("qualname")):
                continue
            if (old.path, old.meta.get("qualname")) in current_locations:
                continue  # copied declaration, not a rename
            if old.anchor and new.anchor:
                self.store.move_anchored_facts(old.anchor, new.anchor)

    def _link_tests(self, tests: list[tuple[str, str]], index: dict[str, list[str]]) -> int:
        """``test_verify_token`` -> ``verify_token``, when that symbol exists."""
        edges: list[Edge] = []
        for test_id, test_name in tests:
            stem = test_name
            for prefix in ("test_", "Test", "test"):
                if stem.startswith(prefix):
                    stem = stem[len(prefix) :]
                    break
            for candidate in {stem, stem.lstrip("_")}:
                if not candidate:
                    continue
                targets = index.get(candidate, [])
                if len(targets) > MAX_CALL_FANOUT:
                    continue
                for target in dict.fromkeys(targets):
                    if target != test_id:
                        edges.append(Edge(target, test_id, EdgeKind.TESTED_BY))
        return self.store.upsert_edges(edges)

    def _mark_stale_facts(self) -> int:
        """Flip episodic facts whose anchored span no longer matches.

        This is the whole point of anchoring: memory that can be wrong and
        knows it (ADR-0003). Stale facts are labelled, never deleted.
        """
        current: dict[tuple[str, str], str] = {}
        for row in self.store.db.execute(
            "SELECT path, anchor, span_hash FROM nodes "
            "WHERE kind IN ('symbol','test') AND anchor IS NOT NULL"
        ):
            import json as _json

            anchor = _json.loads(row["anchor"])
            if anchor.get("symbol"):
                current[(row["path"], anchor["symbol"])] = row["span_hash"]

        stale: list[str] = []
        for row in self.store.db.execute(
            "SELECT id, anchor, state FROM nodes WHERE anchor IS NOT NULL "
            "AND kind NOT IN ('symbol','test','file','module') AND state='fresh'"
        ):
            import json as _json

            anchor = _json.loads(row["anchor"])
            key = (anchor.get("path"), anchor.get("symbol"))
            if not key[1]:
                continue
            if current.get(key) != anchor.get("span_hash"):
                stale.append(row["id"])
        return self.store.set_state(stale, State.STALE)
