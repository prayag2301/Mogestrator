"""Anchored, untrusted episodic records; no automatic capture or eviction yet."""

import time

from mog.graph.models import Edge, EdgeKind, Node, NodeKind
from mog.graph.store import SECRET_LABEL, Store
from mog.index.sensitivity import classify
from mog.retrieve.engine import MEMORY_KINDS, resolve
from mog.retrieve.render import SourceView


class Ledger:
    def __init__(self, store: Store, view: SourceView):
        self.store, self.view = store, view

    def remember(self, kind: str, content: str, *, anchor=None, origin="user") -> dict:
        kind = NodeKind(kind)
        if kind not in MEMORY_KINDS:
            raise ValueError(
                "kind must be decision, failure, constraint, convention, correction, or task"
            )
        if origin != "user" and kind not in {NodeKind.DECISION, NodeKind.FAILURE, NodeKind.TASK}:
            raise PermissionError("agent-origin memories cannot create constraints or corrections")
        if not content.strip() or len(content.encode()) > 16_000:
            raise ValueError("memory must contain 1 to 16000 UTF-8 bytes")
        if SECRET_LABEL in classify("memory.txt", content.encode()):
            raise PermissionError("refusing credential-shaped memory content")
        subject = resolve(self.store, anchor) if anchor else None
        if subject and not self.view.allowed(subject):
            raise PermissionError("cannot attach memory to gated content")
        if subject and self.view.state(subject)[0] != "fresh":
            raise ValueError("anchor is stale; run mog index before remembering")
        episode = Node(
            kind=NodeKind.EPISODE, name="memory write", labels=["derived"], meta={"origin": origin}
        )
        node = Node(
            kind=kind,
            name=content.splitlines()[0][:120],
            content=content,
            anchor=subject.anchor if subject else None,
            labels=sorted({"derived", "repo:private", *(subject.labels if subject else [])}),
            meta={"provenance": {"source": "memory", "origin": origin, "episode": episode.id}},
        )
        with self.store.transaction():
            self.store.upsert_nodes([episode, node])
            edges = [Edge(node.id, episode.id, EdgeKind.DERIVED_FROM)]
            if subject:
                edges.append(Edge(node.id, subject.id, EdgeKind.ABOUT))
            self.store.upsert_edges(edges)
        return self.view.render(node, "L2")

    def recall(self, node_id: str) -> dict:
        node = resolve(self.store, node_id)
        if node.kind not in MEMORY_KINDS:
            raise ValueError("recall requires a memory ID; use expand for source nodes")
        return self.view.render(node, "L2")

    def pin(self, node_id: str, value: bool) -> dict:
        node = resolve(self.store, node_id)
        if node.kind not in MEMORY_KINDS:
            raise ValueError("pin requires a persistent memory ID")
        if not self.view.allowed(node):
            raise PermissionError("content unavailable under the ingest policy")
        node.meta["pinned"] = value
        node.updated_at = time.time()
        with self.store.transaction():
            self.store.upsert_nodes([node])
        return {"id": node.id, "pinned": value, "trust_label": "untrusted"}
