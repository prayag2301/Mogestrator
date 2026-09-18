"""Freshness-aware source views. Stored offsets are never trusted after edits."""

from pathlib import Path

from mog.graph.anchors import file_hash, span_hash
from mog.graph.models import Node, NodeKind, State
from mog.graph.store import SECRET_LABEL, Store
from mog.index.parsers.base import extract, spec_for_path
from mog.index.parsers.languages import get_parser
from mog.index.sensitivity import classify
from mog.index.walker import MAX_FILE_BYTES


class SourceView:
    def __init__(self, root: Path, store: Store, *, allow=(), max_bytes=MAX_FILE_BYTES):
        self.root = root.resolve()
        self.store = store
        self.allow = allow
        self.max_bytes = max_bytes
        self.cache: dict[str, tuple] = {}
        self.file_hashes: dict[str, str] = {}

    def snapshot(self, path: str):
        if path not in self.cache:
            source = None
            reason = None
            symbols = {}
            try:
                resolved = (self.root / path).resolve(strict=True)
                resolved.relative_to(self.root)
                if not resolved.is_file() or resolved.stat().st_size > self.max_bytes:
                    raise ValueError("file unavailable or exceeds size limit")
                with resolved.open("rb") as stream:
                    source = stream.read(self.max_bytes + 1)
                if len(source) > self.max_bytes:
                    raise ValueError("file exceeds size limit")
                self.file_hashes[path] = file_hash(source)
                if SECRET_LABEL in classify(path, source, self.allow) or SECRET_LABEL in classify(
                    resolved.relative_to(self.root).as_posix(),
                    source,
                    self.allow,
                ):
                    reason = "content gated as secret"
                    source = None
                elif spec := spec_for_path(path):
                    tree = get_parser(spec.name).parse(source)
                    extracted, _ = extract(tree.root_node, source, spec, path)
                    symbols = {s.qualname: s for s in extracted}
            except (OSError, ValueError, RuntimeError):
                source = None
                reason = "source missing, outside repository, or unavailable"
            self.cache[path] = source, symbols, reason
        return self.cache[path]

    def allowed(self, node: Node) -> bool:
        if SECRET_LABEL in node.labels or node.state in (State.RETRACTED, State.SUPERSEDED):
            return False
        if SECRET_LABEL in classify(node.path or "memory.txt", node.content.encode(), self.allow):
            return False
        if node.path:
            _, _, reason = self.snapshot(node.path)
            if reason == "content gated as secret":
                return False
        return True

    def state(self, node: Node) -> tuple[str, str | None]:
        if not node.anchor:
            return node.state.value, None
        source, symbols, reason = self.snapshot(node.anchor.path)
        current = self.file_hashes.get(node.anchor.path) if not node.anchor.symbol else None
        if source is not None:
            if node.anchor.symbol:
                symbol = symbols.get(node.anchor.symbol)
                if symbol:
                    current = span_hash(symbol.node, source)
            else:
                current = file_hash(source)
        if current != node.anchor.span_hash:
            return "stale", reason or "anchored content changed; run mog index"
        return node.state.value, None

    def render(self, node: Node, zoom: str = "auto") -> dict:
        if not self.allowed(node):
            raise PermissionError("content unavailable under the ingest policy")
        if zoom not in {"auto", "L0", "L1", "L2", "L3"}:
            raise ValueError("zoom must be auto, L0, L1, L2, or L3")
        if zoom == "auto":
            zoom = "L2" if node.kind in (NodeKind.SYMBOL, NodeKind.TEST) else "L1"
        state, warning = self.state(node)
        content = node.content
        if node.kind.is_structural and node.path:
            source, symbols, reason = self.snapshot(node.path)
            if source is None:
                content = ""
                warning = reason
            elif zoom == "L0":
                content = f"{node.path}: {', '.join(symbols) or node.name}"
            elif zoom == "L3":
                content = source.decode("utf-8", "replace")
            elif node.anchor and node.anchor.symbol:
                symbol = symbols.get(node.anchor.symbol)
                if symbol:
                    body = source[symbol.node.start_byte : symbol.node.end_byte].decode(
                        "utf-8",
                        "replace",
                    )
                    content = body.splitlines()[0] if zoom == "L1" else body
                else:
                    content = ""
            else:
                content = (
                    "\n".join(
                        source[s.node.start_byte : s.node.end_byte]
                        .decode("utf-8", "replace")
                        .splitlines()[0]
                        for s in symbols.values()
                    )
                    if symbols
                    else source[:600].decode("utf-8", "replace")
                )
        result = {
            "id": node.id,
            "location": node.display(),
            "kind": node.kind.value,
            "state": state,
            "anchor": node.anchor.to_dict() if node.anchor else None,
            "trust_label": "untrusted",
            "labels": node.labels,
            "zoom": zoom,
            "content": content,
            "pinned": bool(node.meta.get("pinned")),
            "provenance": node.meta.get("provenance", {"source": "repository"}),
        }
        if warning:
            result["warning"] = warning
        return result

    def verify(self) -> dict:
        checked = fresh = 0
        problems = []
        for node in self.store.find_nodes(limit=-1):
            if not node.anchor:
                continue
            checked += 1
            state, warning = self.state(node)
            if state == "fresh":
                fresh += 1
            else:
                problems.append(
                    {"id": node.id, "location": node.display(), "reason": warning or state}
                )
        return {
            "checked": checked,
            "fresh": fresh,
            "drifted": checked - fresh,
            "drift_rate_pct": round(100 * (checked - fresh) / checked, 2) if checked else 0,
            "problems": problems[:200],
        }
