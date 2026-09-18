import pytest

from mog.index.embed import nearest, update_embeddings
from mog.retrieve.engine import Retriever
from mog.retrieve.render import SourceView


class FakeEmbedder:
    model_id = "test-v1"

    def __init__(self):
        self.calls = 0

    def embed(self, texts):
        self.calls += len(texts)
        return [
            [1.0, 0.0] if "helper" in text or "multiplication" in text else [0.0, 1.0]
            for text in texts
        ]


def test_embedding_cache_and_semantic_seed(indexed):
    root, store, _ = indexed
    embedder = FakeEmbedder()
    view = SourceView(root, store)
    first = update_embeddings(store, embedder, view)
    assert first["embedded"] > 0
    calls = embedder.calls
    assert update_embeddings(store, embedder, view)["embedded"] == 0
    assert calls == embedder.calls
    assert any(n.name == "helper" for n in nearest(store, "multiplication", embedder))
    result = Retriever(store, view, embedder=embedder).search("multiplication", budget=10000)
    assert result["mode"] == "semantic+fts+graph"
    assert any(i["location"] == "src/util.py::helper" for i in result["items"])


def test_missing_model_or_dimension_mismatch_fails_closed(indexed):
    root, store, _ = indexed
    embedder = FakeEmbedder()
    with pytest.raises(ValueError, match="no embeddings"):
        nearest(store, "helper", embedder)
    update_embeddings(store, embedder, SourceView(root, store))
    store.set_meta("embedding_dim", 3)
    with pytest.raises(ValueError, match="dimension"):
        nearest(store, "helper", embedder)
    result = Retriever(store, SourceView(root, store), embedder=embedder).search("helper")
    assert result["mode"] == "fts+graph" and result["warnings"]
