"""M1 graph links and rename continuity, including conservative failure cases."""

import subprocess

import pytest

from mog.graph.models import Anchor, EdgeKind, Node, NodeKind, State
from mog.graph.store import Store
from mog.index.indexer import Indexer


def _store(root):
    return Store(root / ".mog" / "graph.db")


def _git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True,
    ).stdout.strip()


def test_import_edges_rebuild_after_target_deletion(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("from . import b\ndef use():\n    return b.go()\n")
    target = tmp_path / "pkg" / "b.py"
    target.write_text("def go():\n    return 1\n")
    store = _store(tmp_path)
    Indexer(tmp_path, store).run()
    source = store.find_nodes(path="pkg/a.py", kind=NodeKind.FILE)[0]
    linked = store.neighbors(source.id, [EdgeKind.IMPORTS])
    assert [n.path for n, _, _ in linked] == ["pkg/b.py"]
    target.unlink()
    Indexer(tmp_path, store).run()
    assert store.neighbors(source.id, [EdgeKind.IMPORTS]) == []
    store.close()


def test_typescript_relative_import_edge(tmp_path):
    (tmp_path / "a.ts").write_text("import { go } from './b';\nexport function use() { go(); }\n")
    (tmp_path / "b.ts").write_text("export function go() {}\n")
    store = _store(tmp_path)
    Indexer(tmp_path, store).run()
    source = store.find_nodes(path="a.ts", kind=NodeKind.FILE)[0]
    assert [n.path for n, _, _ in store.neighbors(source.id, [EdgeKind.IMPORTS])] == ["b.ts"]
    store.close()


@pytest.mark.parametrize(
    ("source_name", "source_text", "target_name", "target_text"),
    [
        ("main.go", 'package main\nimport "local/util"\nfunc main() {}\n',
         "local/util.go", "package util\nfunc Help() {}\n"),
        ("main.rs", "use crate::helper;\nfn main() {}\n",
         "helper.rs", "pub fn help() {}\n"),
    ],
)
def test_go_and_rust_local_import_edges(
    tmp_path, source_name, source_text, target_name, target_text,
):
    (tmp_path / target_name).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / source_name).write_text(source_text)
    (tmp_path / target_name).write_text(target_text)
    store = _store(tmp_path)
    Indexer(tmp_path, store).run()
    source = store.find_nodes(path=source_name, kind=NodeKind.FILE)[0]
    assert [n.path for n, _, _ in store.neighbors(source.id, [EdgeKind.IMPORTS])] == [
        target_name
    ]
    store.close()


def test_unique_rename_moves_fact_anchor_and_keeps_it_fresh(tmp_path):
    source = tmp_path / "a.py"
    source.write_text("def before(x):\n    return x + 1\n")
    store = _store(tmp_path)
    Indexer(tmp_path, store).run()
    old = store.find_by_qualname("before")[0]
    fact = Node(
        kind=NodeKind.DECISION, name="increment", content="Increment once",
        anchor=Anchor(path="a.py", symbol="before", span_hash=old.anchor.span_hash),
    )
    with store.transaction():
        store.upsert_nodes([fact])
    source.write_text("def after(x):\n    return x + 1\n")
    Indexer(tmp_path, store).run()
    moved = store.get_node(fact.id)
    assert moved.anchor.symbol == "after"
    assert moved.anchor.span_hash == store.find_by_qualname("after")[0].anchor.span_hash
    assert moved.state is State.FRESH
    store.close()


def test_body_edit_is_not_mistaken_for_rename(tmp_path):
    source = tmp_path / "a.py"
    source.write_text("def before(x):\n    return x + 1\n")
    store = _store(tmp_path)
    Indexer(tmp_path, store).run()
    old = store.find_by_qualname("before")[0]
    fact = Node(
        kind=NodeKind.DECISION, name="increment", content="Increment once",
        anchor=Anchor(path="a.py", symbol="before", span_hash=old.anchor.span_hash),
    )
    with store.transaction():
        store.upsert_nodes([fact])
    source.write_text("def after(x):\n    return x + 2\n")
    Indexer(tmp_path, store).run()
    assert store.get_node(fact.id).state is State.STALE
    store.close()


def test_file_move_keeps_unique_symbol_fact_fresh(tmp_path):
    original = tmp_path / "old.py"
    original.write_text("def answer():\n    return 42\n")
    store = _store(tmp_path)
    Indexer(tmp_path, store).run()
    old = store.find_by_qualname("answer")[0]
    fact = Node(
        kind=NodeKind.DECISION, name="answer", content="Stable answer",
        anchor=Anchor(path="old.py", symbol="answer", span_hash=old.anchor.span_hash),
    )
    with store.transaction():
        store.upsert_nodes([fact])
    original.rename(tmp_path / "new.py")
    Indexer(tmp_path, store).run()
    assert store.get_node(fact.id).anchor.path == "new.py"
    assert store.get_node(fact.id).state is State.FRESH
    store.close()


def test_copy_does_not_move_fact_anchor(tmp_path):
    original = tmp_path / "old.py"
    original.write_text("def answer():\n    return 42\n")
    store = _store(tmp_path)
    Indexer(tmp_path, store).run()
    old = store.find_by_qualname("answer")[0]
    fact = Node(
        kind=NodeKind.DECISION, name="answer", content="Stable answer",
        anchor=Anchor(path="old.py", symbol="answer", span_hash=old.anchor.span_hash),
    )
    with store.transaction():
        store.upsert_nodes([fact])
    (tmp_path / "copy.py").write_text(original.read_text().replace("answer", "copied"))
    Indexer(tmp_path, store).run()
    assert store.get_node(fact.id).anchor.path == "old.py"
    assert store.get_node(fact.id).anchor.symbol == "answer"
    store.close()


def test_git_miner_links_only_recurring_single_symbol_file_pairs(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    a, b = tmp_path / "a.py", tmp_path / "b.py"
    for number in (1, 2):
        a.write_text(f"def a():\n    return {number}\n")
        b.write_text(f"def b():\n    return {number}\n")
        _git(tmp_path, "add", "a.py", "b.py")
        _git(tmp_path, "commit", "-qm", f"pair {number}")
    store = _store(tmp_path)
    Indexer(tmp_path, store).run()
    symbol = store.find_by_qualname("a")[0]
    linked = store.neighbors(symbol.id, [EdgeKind.CO_CHANGED])
    assert [n.name for n, _, _ in linked] == ["b"]
    assert store.db.execute(
        "SELECT json_extract(meta, '$.commits') FROM edges WHERE src=? AND kind='co_changed'",
        (symbol.id,),
    ).fetchone()[0] == 2
    store.close()
