from mog.graph.models import Anchor, Edge, EdgeKind, Node, NodeKind, State


def _sym(name="bar", path="a.py", h="sha256:1"):
    return Node(kind=NodeKind.SYMBOL, name=name, content=f"def {name}(): pass",
                anchor=Anchor(path=path, symbol=name, span_hash=h, file_hash="sha256:f"))


def test_roundtrip_preserves_anchor(store):
    n = _sym()
    with store.transaction():
        store.upsert_nodes([n])
    got = store.get_node(n.id)
    assert got is not None
    assert got.anchor.span_hash == "sha256:1"
    assert got.anchor.symbol == "bar"
    assert got.kind is NodeKind.SYMBOL


def test_upsert_is_idempotent(store):
    n = _sym()
    with store.transaction():
        store.upsert_nodes([n])
        store.upsert_nodes([n])
    assert store.counts()["nodes"] == 1


def test_edges_deduplicate_on_conflict(store):
    a, b = _sym("a"), _sym("b")
    with store.transaction():
        store.upsert_nodes([a, b])
        store.upsert_edges([Edge(a.id, b.id, EdgeKind.CALLS)])
        store.upsert_edges([Edge(a.id, b.id, EdgeKind.CALLS)])
    assert store.counts()["edges"] == 1


def test_neighbors_both_directions(store):
    a, b = _sym("a"), _sym("b")
    with store.transaction():
        store.upsert_nodes([a, b])
        store.upsert_edges([Edge(a.id, b.id, EdgeKind.CALLS)])
    assert [n.name for n, _, _ in store.neighbors(a.id, [EdgeKind.CALLS])] == ["b"]
    assert [n.name for n, _, _ in store.neighbors(b.id, [EdgeKind.CALLS], reverse=True)] == ["a"]


def test_fts_finds_by_content(store):
    with store.transaction():
        store.upsert_nodes([_sym("verify_token")])
    assert any(n.name == "verify_token" for n, _ in store.search_text("verify_token"))


def test_delete_file_nodes_spares_episodic_memory(store):
    """Structural nodes are a cache; episodic nodes are the irreplaceable asset."""
    sym = _sym(path="a.py")
    decision = Node(kind=NodeKind.DECISION, name="use HS256", content="because…",
                    anchor=Anchor(path="a.py", symbol="bar", span_hash="sha256:1",
                                  file_hash="sha256:f"))
    with store.transaction():
        store.upsert_nodes([sym, decision])
        store.delete_file_nodes("a.py")
    assert store.get_node(sym.id) is None
    assert store.get_node(decision.id) is not None


def test_set_state_marks_stale(store):
    n = _sym()
    with store.transaction():
        store.upsert_nodes([n])
        store.set_state([n.id], State.STALE)
    assert store.get_node(n.id).state is State.STALE


def test_schema_version_recorded(store):
    assert store.db.execute("PRAGMA user_version").fetchone()[0] == 3


def test_fts_does_not_duplicate_on_reupsert(store):
    """FTS5 has no upsert; a naive insert grows the index on every re-index."""
    n = _sym("verify_token")
    with store.transaction():
        store.upsert_nodes([n])
        store.upsert_nodes([n])
    rows = store.db.execute(
        "SELECT count(*) c FROM fts WHERE rowid=(SELECT rowid FROM nodes WHERE id=?)", (n.id,)
    ).fetchone()["c"]
    assert rows == 1


def test_contentless_fts_removes_old_terms_on_update(store):
    n = _sym("verify_token")
    with store.transaction():
        store.upsert_nodes([n])
        n.content = "def verify_token(): return changed_term"
        store.upsert_nodes([n])
    assert store.search_text("changed_term")[0][0].id == n.id
    assert store.search_text("pass") == []


def test_v2_fts_migration_preserves_search_and_memory(tmp_path):
    import sqlite3

    from mog.graph.store import Store

    db = tmp_path / "old.db"
    old = Store(db)
    symbol = _sym("verify_token")
    memory = Node(kind=NodeKind.DECISION, name="chosen", content="keep this fact")
    with old.transaction():
        old.upsert_nodes([symbol, memory])
    old.close()
    conn = sqlite3.connect(db)
    conn.execute("DROP TABLE fts")
    conn.execute("CREATE VIRTUAL TABLE fts USING fts5(node_id UNINDEXED,name,content)")
    conn.execute("PRAGMA user_version=2")
    conn.commit()
    conn.close()

    upgraded = Store(db)
    assert upgraded.get_node(memory.id).content == "keep this fact"
    assert upgraded.search_text("verify_token")[0][0].id == symbol.id
    assert upgraded.search_text("chosen")[0][0].id == memory.id
    upgraded.close()
