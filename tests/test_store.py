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
    assert store.db.execute("PRAGMA user_version").fetchone()[0] == 2


def test_fts_does_not_duplicate_on_reupsert(store):
    """FTS5 has no upsert; a naive insert grows the index on every re-index."""
    n = _sym("verify_token")
    with store.transaction():
        store.upsert_nodes([n])
        store.upsert_nodes([n])
    rows = store.db.execute("SELECT count(*) c FROM fts WHERE node_id=?", (n.id,)).fetchone()["c"]
    assert rows == 1
