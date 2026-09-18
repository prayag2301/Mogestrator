"""One repository per service; each operation owns its SQLite connection."""

from contextlib import contextmanager
from pathlib import Path
from threading import RLock

from mog.config import ensure_gitignored, index_options
from mog.graph.store import Store
from mog.index.embed import LocalEmbedder, update_embeddings
from mog.index.indexer import Indexer
from mog.memory.ledger import Ledger
from mog.retrieve.engine import Retriever
from mog.retrieve.render import SourceView


class RepositoryService:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.lock = RLock()
        self.embedder = None

    @contextmanager
    def session(self):
        options = index_options(self.root)
        database = self.root / ".mog/graph.db"
        if not database.exists():
            raise FileNotFoundError("no index; run mog index or start mog serve --watch")
        with self.lock:
            store = Store(database)
            try:
                view = SourceView(
                    self.root,
                    store,
                    allow=options.get("allow_secret_content", ()),
                    **({"max_bytes": options["max_bytes"]} if "max_bytes" in options else {}),
                )
                yield store, view
            finally:
                store.close()

    def reindex(self):
        options = index_options(self.root)
        if not self.root.is_dir():
            raise ValueError("repository directory does not exist")
        with self.lock:
            ensure_gitignored(self.root)
            store = Store(self.root / ".mog/graph.db")
            try:
                return Indexer(self.root, store, **options).run()
            finally:
                store.close()

    def call(self, operation: str, **kwargs):
        with self.session() as (store, view):
            if operation == "verify":
                return view.verify()
            if operation in {"create_context", "load_context"}:
                from mog.memory.handles import create, materialize

                function = create if operation == "create_context" else materialize
                return function(store, view, **kwargs)
            if operation == "embed":
                self.embedder = LocalEmbedder(kwargs.pop("model"))
                return update_embeddings(store, self.embedder, view)
            if operation in {"remember", "recall", "pin"}:
                return getattr(Ledger(store, view), operation)(**kwargs)
            semantic = kwargs.pop("semantic", False)
            if semantic and self.embedder is None:
                model = store.get_meta("embedding_model")
                if model:
                    self.embedder = LocalEmbedder(model)
            retriever = Retriever(store, view, embedder=self.embedder if semantic else None)
            if operation == "why":
                kwargs["kind"] = "memory"
                operation = "search"
            result = getattr(retriever, operation)(**kwargs)
            if semantic and self.embedder is None:
                result["warnings"].append("no semantic index; run mog embed; using FTS and graph")
                # Keep the response within its budget after adding a warning.
                from mog.retrieve.engine import pack

                result = pack(
                    result["items"],
                    result["budget_tokens"],
                    mode=result["mode"],
                    warnings=result["warnings"],
                )
            return result
