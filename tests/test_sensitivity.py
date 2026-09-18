"""The ingest gate (ADR-0008).

Every test here is a regression test for something that was reproduced against
the M1 index before the gate existed.
"""

import subprocess
from pathlib import Path

import pytest

from mog.graph.models import Node, NodeKind
from mog.graph.store import SECRET_LABEL, Store
from mog.index.indexer import Indexer
from mog.index.sensitivity import PRIVATE, SECRET, classify
from mog.index.walker import discover

AWS_KEY = "AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
RSA_KEY = (
    "-----BEGIN RSA PRIVATE KEY-----\n"
    "MIIEowIBAAKCAQEA0Zx8Kg9pQ7fakefakefakefakefakefake\n"
    "-----END RSA PRIVATE KEY-----\n"
)


def _index(root: Path, **kw) -> tuple[Store, object]:
    store = Store(root / ".mog" / "graph.db")
    return store, Indexer(root, store, **kw).run()


def _db_bytes(root: Path) -> bytes:
    return b"".join(
        (root / ".mog" / name).read_bytes()
        for name in ("graph.db", "graph.db-wal")
        if (root / ".mog" / name).exists()
    )


# ---- classification ------------------------------------------------------


@pytest.mark.parametrize(
    "rel",
    [".env", ".env.production", "deploy/prod.env", "certs/deploy_key.pem",
     "config/credentials.json", ".ssh/id_rsa", "infra/terraform.tfstate", ".npmrc"],
)
def test_credential_paths_are_gated(rel):
    assert classify(rel, b"nothing interesting here\n") == [SECRET]


@pytest.mark.parametrize(
    "body",
    [RSA_KEY, AWS_KEY, "token = ghp_aB3dE5fG7hJ9kL1mN3pQ5rS7tU9vW1xY3zA5",
     'slack = "xoxb-1234567890-abcdefghijkl"'],
)
def test_credential_content_is_gated_whatever_the_path(body):
    assert classify("src/config.py", body.encode()) == [SECRET]


@pytest.mark.parametrize(
    "body",
    [
        "# set your API_KEY=your_api_key_here before running\n",
        'password = "changeme_placeholder"\n',
        "SECRET_KEY = os.environ['SECRET_KEY']\n",
        "def verify_token(token):\n    return token\n",
    ],
)
def test_ordinary_code_and_docs_are_not_gated(body):
    """A false positive costs real content, so the generic rule stays conservative."""
    assert classify("src/settings.py", body.encode()) == [PRIVATE]


def test_allowlist_is_per_pattern(tmp_path):
    assert classify("tests/fixtures/fake.env", AWS_KEY.encode()) == [SECRET]
    assert classify(
        "tests/fixtures/fake.env", AWS_KEY.encode(), allow=("tests/fixtures/*",)
    ) == [PRIVATE]


# ---- the reproductions ---------------------------------------------------


def test_secret_bytes_never_reach_the_database(tmp_path):
    (tmp_path / ".env.production").write_text(AWS_KEY + "\n")
    (tmp_path / "deploy_key.pem").write_text(RSA_KEY)
    (tmp_path / "a.py").write_text("def f():\n    return 1\n")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=False)

    store, stats = _index(tmp_path)
    raw = _db_bytes(tmp_path)
    assert b"wJalrX" not in raw
    assert b"BEGIN RSA PRIVATE KEY" not in raw
    assert stats.files_gated == 2
    store.close()


def test_symlink_out_of_the_tree_is_not_followed(tmp_path):
    outside = tmp_path / "outside.env"
    outside.write_text("TOKEN=beyond-the-repo\n")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("def f():\n    return 1\n")
    (repo / "link.txt").symlink_to(outside)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=False)

    assert "link.txt" not in {rel for rel, _, _ in discover(repo)}
    store, _ = _index(repo)
    assert b"beyond-the-repo" not in _db_bytes(repo)
    store.close()


def test_symlink_confinement_holds_without_git(tmp_path):
    """The non-git walk uses rglob, which follows links just as happily."""
    outside = tmp_path / "outside.env"
    outside.write_text("TOKEN=beyond-the-repo\n")
    repo = tmp_path / "plain"
    repo.mkdir()
    (repo / "a.py").write_text("def f():\n    return 1\n")
    (repo / "link.txt").symlink_to(outside)

    assert "link.txt" not in {rel for rel, _, _ in discover(repo)}


# ---- what the gate keeps -------------------------------------------------


def test_gated_file_keeps_its_node_but_loses_its_bytes(tmp_path):
    (tmp_path / ".env").write_text(AWS_KEY + "\n")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=False)
    store, _ = _index(tmp_path)

    nodes = [n for n in store.find_nodes(kind=NodeKind.FILE, limit=50) if n.name == ".env"]
    assert nodes, "the graph must still say the file exists"
    assert nodes[0].labels == [SECRET]
    assert nodes[0].content == ""
    assert "start_byte" not in nodes[0].meta
    store.close()


def test_gated_content_is_not_searchable(tmp_path):
    (tmp_path / ".env").write_text(AWS_KEY + "\n")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=False)
    store, _ = _index(tmp_path)
    assert store.search_text("wJalrXUtnFEMI") == []
    assert store.count_labeled(SECRET_LABEL) == 1
    store.close()


def test_allowlist_lets_a_fixture_through(tmp_path):
    fixtures = tmp_path / "tests" / "fixtures"
    fixtures.mkdir(parents=True)
    (fixtures / "sample.env").write_text(AWS_KEY + "\n")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=False)
    store, stats = _index(tmp_path, allow_secret_content=("tests/fixtures/*",))
    assert stats.files_gated == 0
    store.close()


# ---- the enforcement -----------------------------------------------------


def test_store_refuses_secret_content_from_any_writer(store):
    node = Node(kind=NodeKind.FILE, name=".env", content=AWS_KEY, labels=[SECRET])
    with pytest.raises(ValueError, match="refusing to store content"):
        store.upsert_nodes([node])


def test_store_refuses_byte_offsets_on_a_secret_node(store):
    node = Node(
        kind=NodeKind.SYMBOL, name="key", labels=[SECRET], meta={"start_byte": 0, "end_byte": 9}
    )
    with pytest.raises(ValueError, match="refusing to store content"):
        store.upsert_nodes([node])


def test_credentials_after_first_4kb_are_gated(tmp_path):
    (tmp_path / "late.py").write_text("# padding\n" * 600 + f"key = '{AWS_KEY}'\n")
    store, stats = _index(tmp_path)
    assert stats.files_gated == 1
    assert b"wJalrX" not in _db_bytes(tmp_path)
    store.close()


def test_removing_allowlist_reclassifies_unchanged_files(tmp_path):
    (tmp_path / "fixture.env").write_text(AWS_KEY)
    store, _ = _index(tmp_path, allow_secret_content=("fixture.env",))
    assert store.count_labeled(SECRET_LABEL) == 0
    stats = Indexer(tmp_path, store).run()
    assert stats.files_indexed == 1
    assert store.count_labeled(SECRET_LABEL) == 1
    assert store.search_text("AWS_SECRET_ACCESS_KEY") == []
    store.close()
