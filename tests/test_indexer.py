"""End-to-end indexing behaviour, including the properties that justify the design."""

from mog.graph.models import Anchor, EdgeKind, Node, NodeKind, State
from mog.index.indexer import Indexer


def test_indexes_symbols_and_files(indexed):
    _, store, _stats = indexed
    counts = store.counts()
    assert counts["kind:symbol"] >= 4
    assert counts["kind:file"] >= 3
    names = {n.name for n in store.find_nodes(kind=NodeKind.SYMBOL, limit=100)}
    assert {"verify_token", "load_key", "TokenStore"} <= names


def test_calls_are_resolved_across_files(indexed):
    _, store, _ = indexed
    vt = store.find_by_qualname("verify_token")[0]
    called = {n.name for n, _, _ in store.neighbors(vt.id, [EdgeKind.CALLS])}
    assert "load_key" in called, called
    assert "TokenStore" in called or "refresh" in called


def test_tested_by_edge_links_test_to_subject(indexed):
    _, store, _ = indexed
    vt = store.find_by_qualname("verify_token")[0]
    tests = {n.name for n, _, _ in store.neighbors(vt.id, [EdgeKind.TESTED_BY])}
    assert "test_verify_token" in tests


def test_non_code_files_still_indexed_at_file_level(indexed):
    """Unsupported types degrade to a file node — degraded, not broken."""
    _, store, _ = indexed
    assert any(n.name == "README.md" for n in store.find_nodes(kind=NodeKind.FILE, limit=100))


def test_reindex_is_incremental(indexed):
    repo, store, _ = indexed
    second = Indexer(repo, store).run()
    assert second.files_indexed == 0
    assert second.files_skipped > 0


def test_edit_reindexes_only_that_file(indexed):
    repo, store, _ = indexed
    (repo / "src/util.py").write_text("def helper(x):\n    return x * 3\n", encoding="utf-8")
    stats = Indexer(repo, store).run()
    assert stats.files_indexed == 1
    assert stats.files_skipped >= 2


def test_deleted_file_is_removed_from_graph(indexed):
    repo, store, _ = indexed
    (repo / "src/util.py").unlink()
    stats = Indexer(repo, store).run()
    assert stats.files_removed == 1
    assert not store.find_nodes(name="helper")


def test_anchor_survives_unrelated_edit_in_same_file(indexed):
    """The core claim: editing elsewhere must not invalidate a fact."""
    repo, store, _ = indexed
    before = store.find_by_qualname("verify_token")[0].anchor.span_hash
    src = (repo / "src/auth.py").read_text()
    (repo / "src/auth.py").write_text(src + "\n\ndef appended():\n    return 1\n", encoding="utf-8")
    Indexer(repo, store).run()
    after = store.find_by_qualname("verify_token")[0].anchor.span_hash
    assert before == after


def test_editing_a_symbol_marks_dependent_memory_stale(indexed):
    """Memory that can be wrong, and knows it (ADR-0003)."""
    repo, store, _ = indexed
    sym = store.find_by_qualname("verify_token")[0]
    fact = Node(
        kind=NodeKind.DECISION, name="hs256", content="verify_token uses HS256",
        anchor=Anchor(path="src/auth.py", symbol="verify_token",
                      span_hash=sym.anchor.span_hash, file_hash=sym.anchor.file_hash),
    )
    with store.transaction():
        store.upsert_nodes([fact])
    assert store.get_node(fact.id).state is State.FRESH

    src = (repo / "src/auth.py").read_text().replace('token + "!"', 'token + "?"')
    src = src.replace("return store.refresh(token) and key", "return key and store.refresh(token)")
    (repo / "src/auth.py").write_text(src, encoding="utf-8")
    stats = Indexer(repo, store).run()

    assert stats.stale_marked >= 1
    assert store.get_node(fact.id).state is State.STALE


def test_full_rebuild_matches_incremental(indexed):
    repo, store, first = indexed
    rebuilt = Indexer(repo, store).run(full=True)
    assert rebuilt.symbols == first.symbols


def test_ambiguous_call_names_are_dropped_not_linked(tmp_path):
    """A name matching many definitions must not produce a hairball."""
    from mog.graph.store import Store

    for i in range(12):
        p = tmp_path / f"m{i}.py"
        p.write_text(f"class C{i}:\n    def __init__(self):\n        pass\n", encoding="utf-8")
    (tmp_path / "caller.py").write_text("def go():\n    C0()\n    x.__init__()\n", encoding="utf-8")
    store = Store(tmp_path / ".mog" / "graph.db")
    ix = Indexer(tmp_path, store)
    ix.run()
    calls = store.db.execute(
        "SELECT count(*) c FROM edges WHERE kind='calls' AND json_extract(meta,'$.name')='__init__'"
    ).fetchone()["c"]
    assert calls == 0, f"__init__ fan-out should be dropped, got {calls} edges"
    assert ix.dropped_ambiguous >= 1


def _relationships(store):
    return {
        (r["source"], r["target"], r["kind"], r["weight"], r["meta"])
        for r in store.db.execute(
            "SELECT s.path || '::' || s.name AS source, "
            "d.path || '::' || d.name AS target, e.kind, e.weight, e.meta "
            "FROM edges e JOIN nodes s ON s.id=e.src JOIN nodes d ON d.id=e.dst"
        )
    }


def test_incremental_edges_match_full_rebuild_after_callee_edit(indexed):
    repo, store, _ = indexed
    path = repo / "src/auth.py"
    path.write_text(path.read_text() + "\n# edit callee file only\n")
    Indexer(repo, store).run()
    incremental = _relationships(store)
    Indexer(repo, store).run(full=True)
    assert incremental == _relationships(store)


def test_unchanged_callers_find_new_definitions_and_lose_removed_ones(tmp_path):
    from mog.graph.store import Store

    (tmp_path / "caller.py").write_text("def caller():\n    target()\n")
    store = Store(tmp_path / ".mog/graph.db")
    Indexer(tmp_path, store).run()
    target = tmp_path / "target.py"
    target.write_text("def target():\n    pass\n")
    Indexer(tmp_path, store).run()
    caller = store.find_by_qualname("caller")[0]
    assert len(store.neighbors(caller.id, [EdgeKind.CALLS])) == 1
    target.unlink()
    Indexer(tmp_path, store).run()
    assert store.neighbors(caller.id, [EdgeKind.CALLS]) == []
    store.close()


def test_unique_top_level_callee_is_not_ambiguous(indexed):
    _, store, _ = indexed
    row = store.db.execute(
        "SELECT weight, json_extract(meta, '$.ambiguous') AS ambiguous FROM edges "
        "WHERE kind='calls' AND json_extract(meta, '$.name')='load_key'"
    ).fetchone()
    assert row["ambiguous"] == 0
    assert row["weight"] == 0.8
