import json

import pytest
from typer.testing import CliRunner

from mog.cli.main import app
from mog.graph.models import Anchor, Node, NodeKind
from mog.graph.store import Store
from mog.index.indexer import Indexer
from mog.retrieve.engine import Retriever, pack, resolve, serialize, token_bound
from mog.retrieve.render import SourceView
from mog.serve.service import RepositoryService


def engine(indexed):
    root, store, _ = indexed
    return Retriever(store, SourceView(root, store))


def test_exact_symbol_ranks_first_with_provenance(indexed):
    result = engine(indexed).search("verify_token", budget=8000)
    first = result["items"][0]
    assert first["location"] == "src/auth.py::verify_token"
    assert first["provenance"]["path"][0]["via"] == "exact"
    assert first["anchor"]["span_hash"]
    assert first["trust_label"] == "untrusted"
    assert "return store.refresh" in first["content"]


def test_search_spreads_to_related_symbols(indexed):
    result = engine(indexed).search("verify_token", budget=20000)
    assert any(item["location"] == "src/auth.py::load_key" for item in result["items"])
    assert any(len(item["provenance"]["path"]) > 1 for item in result["items"])


@pytest.mark.parametrize("query", ['" OR NOT * (', "TokenStore.refresh", "a:b AND [x]", "你好"])
def test_arbitrary_query_is_not_raw_fts_syntax(indexed, query):
    assert "items" in engine(indexed).search(query)


@pytest.mark.parametrize("budget", [512, 700, 1200, 4000])
def test_budget_includes_metadata_and_unicode(budget):
    result = pack([{"id": "x", "content": '🐍 quoted " text\n' * 3000}], budget, mode="test")
    assert token_bound(result) <= budget
    assert result["token_upper_bound"] == token_bound(result)
    assert isinstance(json.loads(serialize(result)), dict)


def test_oversize_metadata_is_omitted():
    result = pack([{"id": "x" * 3000, "content": ""}], 512, mode="test")
    assert result["items"] == []
    assert result["omitted"] == 1


def test_empty_search(indexed):
    assert engine(indexed).search("absolutely_nonexistent_zzzz")["items"] == []


def test_depth_bounded_impact_includes_tests(indexed):
    retriever = engine(indexed)
    assert retriever.impact("load_key", depth=0)["items"] == []
    result = retriever.impact("load_key", depth=2, budget=10000)
    locations = {item["location"] for item in result["items"]}
    assert "src/auth.py::verify_token" in locations
    assert "tests/test_auth.py::test_verify_token" in locations
    result = retriever.impact("load_key", depth=2, tests_only=True, budget=10000)
    assert result["items"] and all(item["kind"] == "test" for item in result["items"])


def test_zoom_and_shifted_offsets(indexed):
    root, store, _ = indexed
    original = (root / "src/auth.py").read_text()
    (root / "src/auth.py").write_text("# moved down\n" * 100 + original)
    retriever = Retriever(store, SourceView(root, store))
    result = retriever.expand("verify_token", zoom="L2")["items"][0]
    assert result["state"] == "fresh"
    assert result["content"].startswith("def verify_token")
    assert "# moved down" not in result["content"]
    signature = retriever.expand("verify_token", zoom="L1")["items"][0]["content"]
    assert "\n" not in signature
    full = retriever.expand("verify_token", zoom="L3", budget=20000)["items"][0]["content"]
    assert full.startswith("# moved down")


def test_changed_source_is_returned_with_stale_warning(indexed):
    root, store, _ = indexed
    path = root / "src/util.py"
    path.write_text("def helper(x):\n    return x * 99\n")
    result = Retriever(store, SourceView(root, store)).expand("helper")["items"][0]
    assert result["state"] == "stale"
    assert "99" in result["content"]
    assert "warning" in result


def test_deleted_source_does_not_return_old_body(indexed):
    root, store, _ = indexed
    (root / "src/util.py").unlink()
    item = Retriever(store, SourceView(root, store)).expand("helper")["items"][0]
    assert item["state"] == "stale" and item["content"] == ""


def test_secret_added_since_index_is_not_returned(indexed):
    root, store, _ = indexed
    (root / "src/util.py").write_text("-----BEGIN PRIVATE KEY-----\nprivate-material\n")
    retriever = Retriever(store, SourceView(root, store))
    with pytest.raises(PermissionError):
        retriever.expand("helper", zoom="L3")
    assert all(
        item["location"] != "src/util.py::helper" for item in retriever.search("helper")["items"]
    )


def test_external_symlink_is_not_read_on_expand(indexed, tmp_path):
    root, store, _ = indexed
    outside = tmp_path.parent / f"{tmp_path.name}-outside.py"
    outside.write_text("def helper(x):\n    return 'outside-only'\n")
    path = root / "src/util.py"
    path.unlink()
    path.symlink_to(outside)
    result = Retriever(store, SourceView(root, store)).expand("helper", zoom="L3")
    assert "outside-only" not in serialize(result)
    assert result["items"][0]["state"] == "stale"


def test_duplicate_symbols_require_path_and_match_beyond_twenty(store):
    nodes = [
        Node(
            kind=NodeKind.SYMBOL,
            name="common",
            anchor=Anchor(
                path=f"{i}.py",
                symbol="common",
                span_hash="hash",
            ),
        )
        for i in range(30)
    ]
    with store.transaction():
        store.upsert_nodes(nodes)
    with pytest.raises(ValueError, match="ambiguous"):
        resolve(store, "common")
    assert resolve(store, "29.py::common").path == "29.py"
    with pytest.raises(LookupError):
        resolve(store, "missing.py::common")


def test_search_cli_explain_and_json(repo):
    runner = CliRunner()
    assert runner.invoke(app, ["index", "--repo", str(repo)]).exit_code == 0
    result = runner.invoke(app, ["search", "verify_token", "--repo", str(repo), "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["items"]
    result = runner.invoke(app, ["search", "verify_token", "--repo", str(repo), "--explain"])
    assert result.exit_code == 0, result.output
    assert '"via":"exact"' in result.stdout


def test_semantic_request_without_index_reports_fallback(indexed):
    root, _, _ = indexed
    result = RepositoryService(root).call("search", query="verify_token", semantic=True)
    assert result["items"] and result["warnings"]
    assert token_bound(result) <= result["budget_tokens"]


def test_schema_v1_upgrade_preserves_nodes(tmp_path):
    import sqlite3

    from mog.graph.store import _SCHEMA

    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.executescript(_SCHEMA)
    db.execute("PRAGMA user_version=1")
    db.execute(
        "INSERT INTO nodes (id,kind,name,content,created_at,updated_at) VALUES (?,?,?,?,?,?)",
        ("legacy", "decision", "legacy fact", "keep this memory", 0, 0),
    )
    db.commit()
    db.close()
    store = Store(path)
    assert store.db.execute("PRAGMA user_version").fetchone()[0] == 3
    assert store.db.execute("SELECT count(*) FROM embedding_cache").fetchone()[0] == 0
    assert store.get_node("legacy").content == "keep this memory"
    store.close()


def test_full_reindex_preserves_memory(indexed):
    root, store, _ = indexed
    service = RepositoryService(root)
    memory = service.call(
        "remember", kind="decision", content="Prefer explicit config", anchor="verify_token"
    )
    Indexer(root, store).run(full=True)
    assert service.call("recall", node_id=memory["id"])["content"] == "Prefer explicit config"


def test_gated_file_verification_checks_hash_without_serving_content(tmp_path):
    (tmp_path / "private.env").write_text("password=fixture\n")
    service = RepositoryService(tmp_path)
    service.reindex()
    assert service.call("verify")["drifted"] == 0
    assert service.call("search", query="private")["items"] == []
    (tmp_path / "private.env").write_text("password=changed\n")
    assert service.call("verify")["drifted"] == 1
