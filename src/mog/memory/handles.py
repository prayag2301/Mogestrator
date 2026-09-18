"""Durable query handles for starting another agent with scoped context."""

from mog.graph.models import new_id
from mog.graph.store import SECRET_LABEL
from mog.index.sensitivity import classify
from mog.retrieve.engine import Retriever, pack, resolve


def create(store, view, query: str, pinned: list[str]) -> dict:
    if not query.strip() or len(query) > 2000 or len(pinned) > 32:
        raise ValueError("context requires a query of 1-2000 characters and at most 32 pins")
    if SECRET_LABEL in classify("context.txt", query.encode()):
        raise PermissionError("refusing credential-shaped context query")
    refs = []
    for target in pinned:
        node = resolve(store, target)
        if not view.allowed(node):
            raise PermissionError("cannot include gated content in a context handle")
        refs.append(node.display() if node.kind.is_structural else node.id)
    handle = new_id("ctx")
    with store.transaction():
        store.set_meta(handle, {"query": query, "pinned": refs})
    return {"handle": f"ctx://{handle}", "trust_label": "untrusted"}


def materialize(store, view, handle: str, budget: int) -> dict:
    key = handle.removeprefix("ctx://")
    if not key.startswith("ctx_") or len(key) > 100:
        raise ValueError("invalid context handle")
    saved = store.get_meta(key)
    if not isinstance(saved, dict):
        raise LookupError("context handle not found")
    result = Retriever(store, view).search(saved["query"], budget=100_000)
    pinned = []
    warnings = []
    for ref in saved["pinned"]:
        try:
            pinned.append(view.render(resolve(store, ref)))
        except (LookupError, PermissionError, ValueError):
            warnings.append("a pinned node is missing, ambiguous, or gated")
    ids = {item["id"] for item in pinned}
    items = [*pinned, *(item for item in result["items"] if item["id"] not in ids)]
    return pack(items, budget, mode="context-handle", warnings=list(dict.fromkeys(warnings)))
