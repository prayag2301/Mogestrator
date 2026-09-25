"""SQLite storage for the context graph (SPEC-context-graph §7, ADR-0005).

One file, no server. Vectors live in the same file via sqlite-vec when the
runtime can load extensions; when it cannot we degrade to FTS5 + graph
expansion rather than failing (ARCHITECTURE §5).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from mog.graph.models import Anchor, Edge, EdgeKind, Node, NodeKind, State

SCHEMA_VERSION = 3

#: Nodes carrying this label hold no content anywhere in the store (ADR-0008).
#: Duplicated from mog.index.sensitivity rather than imported: the storage layer
#: must not depend on the indexer, and this invariant has to hold for every
#: writer, including future ones.
SECRET_LABEL = "secret"

#: Symbol bodies are truncated in the full-text index. Long bodies add index
#: weight without improving lookup — the graph, not FTS, is what finds the rest.
FTS_CONTENT_CHARS = 1200

_SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes (
    id         TEXT PRIMARY KEY,
    kind       TEXT NOT NULL,
    name       TEXT NOT NULL,
    state      TEXT NOT NULL DEFAULT 'fresh',
    content    TEXT NOT NULL DEFAULT '',
    labels     TEXT NOT NULL DEFAULT '[]',
    anchor     TEXT,
    meta       TEXT NOT NULL DEFAULT '{}',
    path       TEXT,
    span_hash  TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_nodes_kind  ON nodes(kind);
CREATE INDEX IF NOT EXISTS ix_nodes_path  ON nodes(path);
CREATE INDEX IF NOT EXISTS ix_nodes_name  ON nodes(name);
CREATE INDEX IF NOT EXISTS ix_nodes_state ON nodes(state);

CREATE TABLE IF NOT EXISTS edges (
    src    TEXT NOT NULL,
    dst    TEXT NOT NULL,
    kind   TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 0.5,
    meta   TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (src, dst, kind)
) WITHOUT ROWID;
-- Expansion walks both directions, so both need an index (SPEC §5).
CREATE INDEX IF NOT EXISTS ix_edges_src ON edges(src, kind);
CREATE INDEX IF NOT EXISTS ix_edges_dst ON edges(dst, kind);

CREATE TABLE IF NOT EXISTS files (
    path      TEXT PRIMARY KEY,
    file_hash TEXT NOT NULL,
    language  TEXT,
    size      INTEGER,
    indexed_at REAL NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(
    name, content, content='', tokenize='unicode61'
);

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

_EMBEDDINGS_SCHEMA = """
CREATE TABLE IF NOT EXISTS embedding_cache (
    model TEXT NOT NULL, digest TEXT NOT NULL, dim INTEGER NOT NULL, vector BLOB NOT NULL,
    PRIMARY KEY (model, digest)
);
CREATE TABLE IF NOT EXISTS node_embeddings (
    node_id TEXT NOT NULL, model TEXT NOT NULL, digest TEXT NOT NULL,
    PRIMARY KEY (node_id, model)
);
"""


class VectorSupport:
    """Whether this runtime can load sqlite-vec, and why not if it cannot."""

    def __init__(self, available: bool, version: str | None, reason: str | None) -> None:
        self.available = available
        self.version = version
        self.reason = reason


def _try_load_vec(db: sqlite3.Connection) -> VectorSupport:
    if not hasattr(db, "enable_load_extension"):
        # e.g. macOS system Python, built without extension support.
        return VectorSupport(False, None, "python sqlite3 built without extension loading")
    try:
        import sqlite_vec
    except ImportError:
        return VectorSupport(False, None, "sqlite-vec not installed")
    try:
        db.enable_load_extension(True)
        sqlite_vec.load(db)
        version = db.execute("select vec_version()").fetchone()[0]
        return VectorSupport(True, version, None)
    except Exception as exc:  # pragma: no cover - platform dependent
        return VectorSupport(False, None, f"{type(exc).__name__}: {exc}")
    finally:
        if hasattr(db, "enable_load_extension"):
            db.enable_load_extension(False)


class Store:
    """Data access layer. All writes go through :meth:`transaction`."""

    def __init__(self, path: Path | str, *, read_only: bool = False) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.vec = _try_load_vec(self.db)
        if not read_only:
            self._migrate()

    def _migrate(self) -> None:
        current = self.db.execute("PRAGMA user_version").fetchone()[0]
        if current == 0:
            self.db.executescript(_SCHEMA)
        elif current > SCHEMA_VERSION:
            raise RuntimeError(
                f"database schema v{current} is newer than this build (v{SCHEMA_VERSION}); "
                "upgrade mogestrator"
            )
        if current < 2:
            self.db.executescript(_EMBEDDINGS_SCHEMA)
        if 0 < current < 3:
            # The former FTS table stored a second copy of each preview. Rebuild
            # postings from nodes while preserving all structural and episodic data.
            with self.transaction():
                self.db.execute("DROP TABLE fts")
                self.db.execute(
                    "CREATE VIRTUAL TABLE fts USING fts5("
                    "name, content, content='', tokenize='unicode61')"
                )
                rows = self.db.execute("SELECT rowid AS rid,kind,name,content,labels FROM nodes")
                self.db.executemany(
                    "INSERT INTO fts(rowid,name,content) VALUES (?,?,?)",
                    (_fts_payload(r) for r in rows if SECRET_LABEL not in json.loads(r["labels"])),
                )
                self.db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            # DROP frees pages internally; VACUUM returns them to the filesystem.
            self.db.execute("VACUUM")
        elif current == 0:
            self.db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        self.db.execute("BEGIN")
        try:
            yield self.db
        except Exception:
            self.db.execute("ROLLBACK")
            raise
        else:
            self.db.execute("COMMIT")

    # ---- writes -------------------------------------------------------

    def upsert_nodes(self, nodes: Iterable[Node]) -> int:
        nodes = list(nodes)
        self._assert_no_secret_content(nodes)
        rows = [
            (
                n.id,
                n.kind.value,
                n.name,
                n.state.value,
                n.content,
                json.dumps(n.labels),
                json.dumps(n.anchor.to_dict()) if n.anchor else None,
                json.dumps(n.meta),
                n.path,
                n.anchor.span_hash if n.anchor else None,
                n.created_at,
                n.updated_at,
            )
            for n in nodes
        ]
        if not rows:
            return 0
        self._delete_fts_by_ids([n.id for n in nodes])
        self.db.executemany(
            "INSERT INTO nodes (id,kind,name,state,content,labels,anchor,meta,path,span_hash,"
            "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET kind=excluded.kind, name=excluded.name, "
            "state=excluded.state, content=excluded.content, labels=excluded.labels, "
            "anchor=excluded.anchor, meta=excluded.meta, path=excluded.path, "
            "span_hash=excluded.span_hash, updated_at=excluded.updated_at",
            rows,
        )
        self._sync_fts(nodes)
        return len(rows)

    @staticmethod
    def _assert_no_secret_content(nodes: list[Node]) -> None:
        """The one invariant that makes the ingest gate a guarantee.

        Gating in the indexer is a policy; this is the enforcement. It fails
        loudly so a future code path cannot quietly regress the property that
        secret bytes never reach disk (ADR-0008).
        """
        for n in nodes:
            if SECRET_LABEL in n.labels and (n.content or n.meta.get("start_byte") is not None):
                raise ValueError(
                    f"refusing to store content for secret-labelled node {n.display()}"
                )

    def _sync_fts(self, nodes: Iterable[Node]) -> None:
        """Add postings after old postings have been removed."""
        node_list = list(nodes)
        if not node_list:
            return
        ids = [n.id for n in node_list]
        marks = ",".join("?" * len(ids))
        rowids = {
            r["id"]: r["rid"]
            for r in self.db.execute(
                f"SELECT rowid AS rid, id FROM nodes WHERE id IN ({marks})", ids
            )
        }
        # A secret-labelled node is not searchable: FTS5 tokenizes and
        # lowercases, so an indexed credential is a *queryable* credential.
        # Its name still reaches search via the nodes table.
        payload = [
            (rowids[n.id], n.name,
             n.content[:FTS_CONTENT_CHARS] if n.kind.is_structural else n.content)
            for n in node_list
            if n.id in rowids and SECRET_LABEL not in n.labels
        ]
        self.db.executemany(
            "INSERT INTO fts (rowid, name, content) VALUES (?,?,?)", payload
        )

    def _delete_fts_by_ids(self, ids: list[str]) -> None:
        if not ids:
            return
        marks = ",".join("?" * len(ids))
        rows = self.db.execute(
            f"SELECT rowid AS rid,kind,name,content,labels FROM nodes WHERE id IN ({marks})",
            ids,
        )
        self.db.executemany(
            "INSERT INTO fts(fts,rowid,name,content) VALUES ('delete',?,?,?)",
            (_fts_payload(r) for r in rows if SECRET_LABEL not in json.loads(r["labels"])),
        )

    def upsert_edges(self, edges: Iterable[Edge]) -> int:
        rows = [(e.src, e.dst, e.kind.value, e.weight, json.dumps(e.meta)) for e in edges]
        if not rows:
            return 0
        self.db.executemany(
            "INSERT INTO edges (src,dst,kind,weight,meta) VALUES (?,?,?,?,?) "
            "ON CONFLICT(src,dst,kind) DO UPDATE SET weight=excluded.weight, meta=excluded.meta",
            rows,
        )
        return len(rows)

    def delete_file_nodes(self, path: str) -> int:
        """Drop a file's structural nodes and their edges before re-indexing it.

        Episodic nodes are never removed here: they are the irreplaceable half
        of the graph (SPEC-context-graph §1) and outlive the code they describe.
        """
        rows = self.db.execute(
            "SELECT rowid AS rid,id,kind,name,content,labels FROM nodes WHERE path=? "
            "AND kind IN ('file','symbol','test','module')",
            (path,),
        ).fetchall()
        if not rows:
            return 0
        ids = [r["id"] for r in rows]
        marks = ",".join("?" * len(ids))
        self.db.execute(f"DELETE FROM edges WHERE src IN ({marks}) OR dst IN ({marks})", ids * 2)
        self._delete_fts_by_ids(ids)
        self.db.execute(f"DELETE FROM nodes WHERE id IN ({marks})", ids)
        self.db.execute(f"DELETE FROM node_embeddings WHERE node_id IN ({marks})", ids)
        return len(ids)

    def record_file(
        self, path: str, fhash: str, language: str | None, size: int, ts: float
    ) -> None:
        self.db.execute(
            "INSERT INTO files (path,file_hash,language,size,indexed_at) VALUES (?,?,?,?,?) "
            "ON CONFLICT(path) DO UPDATE SET file_hash=excluded.file_hash, "
            "language=excluded.language, size=excluded.size, indexed_at=excluded.indexed_at",
            (path, fhash, language, size, ts),
        )

    def forget_file(self, path: str) -> None:
        self.delete_file_nodes(path)
        self.db.execute("DELETE FROM files WHERE path=?", (path,))

    def set_state(self, node_ids: Iterable[str], state: State) -> int:
        ids = list(node_ids)
        if not ids:
            return 0
        marks = ",".join("?" * len(ids))
        cur = self.db.execute(
            f"UPDATE nodes SET state=? WHERE id IN ({marks})", [state.value, *ids]
        )
        return cur.rowcount

    def move_anchored_facts(self, old: Anchor, new: Anchor) -> int:
        """Follow an unambiguous symbol rename without changing a fact's origin."""
        rows = self.db.execute(
            "SELECT id, anchor FROM nodes WHERE path=? AND span_hash=? "
            "AND kind NOT IN ('file','symbol','test','module')",
            (old.path, old.span_hash),
        ).fetchall()
        moved = 0
        for row in rows:
            anchor = json.loads(row["anchor"])
            if anchor.get("symbol") != old.symbol:
                continue
            anchor.update(path=new.path, symbol=new.symbol, span_hash=new.span_hash)
            anchor.pop("file_hash", None)
            self.db.execute(
                "UPDATE nodes SET anchor=?, path=?, span_hash=? WHERE id=?",
                (json.dumps(anchor), new.path, new.span_hash, row["id"]),
            )
            moved += 1
        return moved

    def set_meta(self, key: str, value: Any) -> None:
        self.db.execute(
            "INSERT INTO meta (key,value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )

    def get_meta(self, key: str, default: Any = None) -> Any:
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    # ---- reads --------------------------------------------------------

    def known_files(self) -> dict[str, str]:
        rows = self.db.execute("SELECT path,file_hash FROM files")
        return {r["path"]: r["file_hash"] for r in rows}

    def get_node(self, node_id: str) -> Node | None:
        row = self.db.execute("SELECT * FROM nodes WHERE id=?", (node_id,)).fetchone()
        return _row_to_node(row) if row else None

    def find_nodes(
        self,
        *,
        kind: NodeKind | None = None,
        name: str | None = None,
        path: str | None = None,
        limit: int = 50,
    ) -> list[Node]:
        sql = "SELECT * FROM nodes WHERE 1=1"
        args: list[Any] = []
        criteria = (("kind", kind.value if kind else None), ("name", name), ("path", path))
        for column, value in criteria:
            if value is not None:
                sql += f" AND {column}=?"
                args.append(value)
        sql += " LIMIT ?"
        args.append(limit)
        return [_row_to_node(r) for r in self.db.execute(sql, args)]

    def find_by_qualname(self, qualname: str, limit: int = 20) -> list[Node]:
        rows = self.db.execute(
            "SELECT * FROM nodes WHERE json_extract(meta,'$.qualname')=? LIMIT ?",
            (qualname, limit),
        ).fetchall()
        return [_row_to_node(r) for r in rows]

    def search_text(self, query: str, limit: int = 20) -> list[tuple[Node, float]]:
        """FTS5 lookup. Exact symbol search must never depend on an embedding."""
        rows = self.db.execute(
            "SELECT n.*, bm25(fts) AS score FROM fts JOIN nodes n ON n.rowid = fts.rowid "
            "WHERE fts MATCH ? ORDER BY score LIMIT ?",
            (query, limit),
        ).fetchall()
        return [(_row_to_node(r), -float(r["score"])) for r in rows]

    def neighbors(
        self,
        node_id: str,
        kinds: Iterable[EdgeKind] | None = None,
        *,
        reverse: bool = False,
        limit: int = -1,
    ) -> list[tuple[Node, EdgeKind, float]]:
        col, other = ("dst", "src") if reverse else ("src", "dst")
        sql = (
            f"SELECT n.*, e.kind AS ek, e.weight AS ew FROM edges e "
            f"JOIN nodes n ON n.id=e.{other} WHERE e.{col}=?"
        )
        args: list[Any] = [node_id]
        if kinds:
            ks = [k.value for k in kinds]
            sql += f" AND e.kind IN ({','.join('?' * len(ks))})"
            args += ks
        sql += " ORDER BY e.weight DESC, n.id LIMIT ?"
        args.append(limit)
        return [
            (_row_to_node(r), EdgeKind(r["ek"]), float(r["ew"])) for r in self.db.execute(sql, args)
        ]

    def count_labeled(self, label: str) -> int:
        return self.db.execute(
            "SELECT count(*) c FROM nodes WHERE EXISTS "
            "(SELECT 1 FROM json_each(nodes.labels) WHERE value=?)",
            (label,),
        ).fetchone()["c"]

    def find_labeled(self, label: str, limit: int = 200) -> list[Node]:
        rows = self.db.execute(
            "SELECT * FROM nodes WHERE EXISTS "
            "(SELECT 1 FROM json_each(nodes.labels) WHERE value=?) ORDER BY path LIMIT ?",
            (label, limit),
        ).fetchall()
        return [_row_to_node(r) for r in rows]

    def counts(self) -> dict[str, int]:
        out = {
            "nodes": self.db.execute("SELECT count(*) c FROM nodes").fetchone()["c"],
            "edges": self.db.execute("SELECT count(*) c FROM edges").fetchone()["c"],
            "files": self.db.execute("SELECT count(*) c FROM files").fetchone()["c"],
        }
        for r in self.db.execute("SELECT kind, count(*) c FROM nodes GROUP BY kind"):
            out[f"kind:{r['kind']}"] = r["c"]
        for r in self.db.execute("SELECT state, count(*) c FROM nodes GROUP BY state"):
            out[f"state:{r['state']}"] = r["c"]
        out["gated"] = self.count_labeled(SECRET_LABEL)
        return out

    def close(self) -> None:
        self.db.close()


def _row_to_node(row: sqlite3.Row) -> Node:
    return Node(
        id=row["id"],
        kind=NodeKind(row["kind"]),
        name=row["name"],
        state=State(row["state"]),
        content=row["content"],
        labels=json.loads(row["labels"]),
        anchor=Anchor(**json.loads(row["anchor"])) if row["anchor"] else None,
        meta=json.loads(row["meta"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _fts_payload(row: sqlite3.Row) -> tuple[int, str, str]:
    kind = NodeKind(row["kind"])
    content = row["content"][:FTS_CONTENT_CHARS] if kind.is_structural else row["content"]
    return row["rid"], row["name"], content
