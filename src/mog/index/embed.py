"""Opt-in local embeddings, cached by model and content digest in SQLite."""

import math
import os
import struct
from pathlib import Path

from platformdirs import user_cache_dir

from mog.graph.anchors import sha256
from mog.graph.models import State
from mog.graph.store import SECRET_LABEL, Store

DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"


class LocalEmbedder:
    def __init__(self, model_id=DEFAULT_MODEL):
        self.model_id = model_id
        self._model = None

    def embed(self, texts: list[str]) -> list[list[float]]:
        if self._model is None:
            try:
                from fastembed import TextEmbedding
            except ImportError as exc:
                raise ImportError(
                    "install mogestrator[embeddings] to enable semantic search"
                ) from exc
            self._model = TextEmbedding(
                model_name=self.model_id,
                cache_dir=os.environ.get(
                    "MOG_MODEL_CACHE",
                    str(Path(user_cache_dir("mogestrator")) / "models"),
                ),
            )
        return [list(map(float, row)) for row in self._model.embed(texts)]


def vector_bytes(vector):
    if not vector or not all(math.isfinite(v) for v in vector):
        raise ValueError("embedding must be a nonempty finite vector")
    return struct.pack(f"<{len(vector)}f", *vector)


def embedding_text(node):
    return f"{node.display()}\n{node.content}"


def update_embeddings(store: Store, embedder, view) -> dict:
    written = cached = 0
    nodes = [n for n in store.find_nodes(limit=-1) if view.allowed(n)]
    # Model work happens outside transactions. No partial model switch on error.
    records = []
    pending = []
    for node in nodes:
        text = embedding_text(node)
        digest = sha256(text)
        row = store.db.execute(
            "SELECT dim, vector FROM embedding_cache WHERE model=? AND digest=?",
            (embedder.model_id, digest),
        ).fetchone()
        if row is None:
            pending.append((node.id, digest, text))
        else:
            records.append((node.id, digest, row["dim"], row["vector"]))
            cached += 1
    for offset in range(0, len(pending), 64):
        batch = pending[offset : offset + 64]
        vectors = embedder.embed([r[2] for r in batch])
        if len(vectors) != len(batch):
            raise ValueError("embedder returned an unexpected number of vectors")
        for (node_id, digest, _), vector in zip(batch, vectors, strict=True):
            records.append((node_id, digest, len(vector), vector_bytes(vector)))
            written += 1
    dims = {r[2] for r in records}
    if len(dims) > 1:
        raise ValueError("embedding dimensions changed for the same model; use a new model ID")
    with store.transaction():
        store.db.execute("DELETE FROM node_embeddings")
        for node_id, digest, dim, vector in records:
            store.db.execute(
                "INSERT OR IGNORE INTO embedding_cache VALUES (?,?,?,?)",
                (embedder.model_id, digest, dim, vector),
            )
            store.db.execute(
                "INSERT INTO node_embeddings VALUES (?,?,?)",
                (node_id, embedder.model_id, digest),
            )
        store.set_meta("embedding_model", embedder.model_id)
        store.set_meta("embedding_dim", next(iter(dims), None))
    return {"model": embedder.model_id, "embedded": written, "cached": cached}


def nearest(store: Store, query: str, embedder, limit=20):
    if store.get_meta("embedding_model") != embedder.model_id:
        raise ValueError("no embeddings for the requested model; run mog embed")
    vector = embedder.embed([query])[0]
    if len(vector) != store.get_meta("embedding_dim"):
        raise ValueError("query embedding dimension does not match the index")
    vector_bytes(vector)  # validate before passing values to SQLite/math
    query_norm = math.sqrt(sum(v * v for v in vector))
    if not query_norm:
        raise ValueError("query embedding has zero norm")
    rows = store.db.execute(
        "SELECT e.node_id, c.dim, c.vector FROM node_embeddings e "
        "JOIN embedding_cache c ON c.model=e.model AND c.digest=e.digest "
        "WHERE e.model=?",
        (embedder.model_id,),
    )
    ranked = []
    for row in rows:
        if row["dim"] != len(vector):
            continue
        stored = struct.unpack(f"<{row['dim']}f", row["vector"])
        norm = math.sqrt(sum(v * v for v in stored))
        similarity = (
            sum(a * b for a, b in zip(vector, stored, strict=True)) / (norm * query_norm)
            if norm
            else 0
        )
        ranked.append((similarity, row["node_id"]))
    result = []
    for _, node_id in sorted(ranked, reverse=True)[:limit]:
        node = store.get_node(node_id)
        if (
            node
            and SECRET_LABEL not in node.labels
            and node.state
            not in (
                State.RETRACTED,
                State.SUPERSEDED,
            )
        ):
            result.append(node)
    return result
