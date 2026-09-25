"""CLI contract: exit codes and output shape are public API (SPEC-cli.md)."""

import json

from typer.testing import CliRunner

from mog.cli.main import app

runner = CliRunner()


def test_version():
    r = runner.invoke(app, ["--version"])
    assert r.exit_code == 0
    assert r.stdout.strip()


def test_init_scaffolds(tmp_path):
    r = runner.invoke(app, ["init", str(tmp_path)])
    assert r.exit_code == 0
    assert (tmp_path / "mogestrator.yaml").exists()
    assert (tmp_path / ".mogignore").exists()
    assert (tmp_path / ".mog").is_dir()


def test_init_does_not_clobber_existing_config(tmp_path):
    (tmp_path / "mogestrator.yaml").write_text("version: 1\nproject: mine\n")
    runner.invoke(app, ["init", str(tmp_path)])
    assert "mine" in (tmp_path / "mogestrator.yaml").read_text()


def test_status_without_index_exits_4(tmp_path):
    r = runner.invoke(app, ["status", "--repo", str(tmp_path)])
    assert r.exit_code == 4


def test_index_then_status_json(repo):
    assert runner.invoke(app, ["index", "--repo", str(repo)]).exit_code == 0
    r = runner.invoke(app, ["status", "--repo", str(repo), "--json"])
    assert r.exit_code == 0
    payload = json.loads(r.stdout)
    assert payload["counts"]["files"] >= 3
    assert "vectors" in payload


def test_verify_clean_after_index(repo):
    runner.invoke(app, ["index", "--repo", str(repo)])
    r = runner.invoke(app, ["verify", "--repo", str(repo), "--json"])
    assert r.exit_code == 0
    assert json.loads(r.stdout)["drifted"] == 0


def test_verify_strict_exits_4_on_drift(repo):
    runner.invoke(app, ["index", "--repo", str(repo)])
    src = (repo / "src/auth.py").read_text().replace("return token", "return str(token)")
    (repo / "src/auth.py").write_text(src)
    r = runner.invoke(app, ["verify", "--repo", str(repo), "--strict"])
    assert r.exit_code == 4


def test_show_missing_node_exits_1(repo):
    runner.invoke(app, ["index", "--repo", str(repo)])
    r = runner.invoke(app, ["show", "no_such_symbol", "--repo", str(repo)])
    assert r.exit_code == 1


def test_show_resolves_qualname(repo):
    runner.invoke(app, ["index", "--repo", str(repo)])
    r = runner.invoke(app, ["show", "TokenStore.refresh", "--repo", str(repo)])
    assert r.exit_code == 0
    assert "refresh" in r.stdout


def test_search_returns_anchored_result(repo):
    assert runner.invoke(app, ["index", "--repo", str(repo)]).exit_code == 0
    r = runner.invoke(app, ["search", "refresh", "--repo", str(repo), "--json"])
    assert r.exit_code == 0, r.output
    rows = json.loads(r.stdout)["results"]
    assert any(row["location"].endswith("::TokenStore.refresh") and row["anchor"]
               for row in rows)


def test_search_treats_fts_syntax_as_text(repo):
    assert runner.invoke(app, ["index", "--repo", str(repo)]).exit_code == 0
    r = runner.invoke(app, ["search", 'refresh OR "', "--repo", str(repo), "--json"])
    assert r.exit_code == 0, r.output
    assert json.loads(r.stdout)["results"]


def test_map_lists_files(repo):
    runner.invoke(app, ["index", "--repo", str(repo)])
    r = runner.invoke(app, ["map", "--repo", str(repo)])
    assert r.exit_code == 0
    assert "auth.py" in r.stdout


def test_init_writes_mog_to_gitignore(tmp_path):
    """Printing a reminder was not a safe default (ADR-0008)."""
    from mog.cli.main import app

    result = CliRunner().invoke(app, ["init", str(tmp_path)])
    assert result.exit_code == 0
    assert ".mog/" in (tmp_path / ".gitignore").read_text()


def test_init_does_not_duplicate_an_existing_gitignore_entry(tmp_path):
    from mog.cli.main import app

    (tmp_path / ".gitignore").write_text("__pycache__/\n.mog/\n")
    CliRunner().invoke(app, ["init", str(tmp_path)])
    assert (tmp_path / ".gitignore").read_text().count(".mog/") == 1


def test_index_json_is_parseable_and_incremental(repo):
    first = runner.invoke(app, ["index", "--repo", str(repo), "--json"])
    assert first.exit_code == 0, first.output
    assert json.loads(first.stdout)["files_indexed"] > 0
    second = runner.invoke(app, ["index", "--repo", str(repo), "--json"])
    assert second.exit_code == 0, second.output
    assert json.loads(second.stdout)["files_indexed"] == 0


def test_verify_includes_tests_and_non_code_files(repo):
    runner.invoke(app, ["index", "--repo", str(repo)])
    (repo / "tests/test_auth.py").write_text("def test_verify_token():\n    assert False\n")
    (repo / "README.md").write_text("changed\n")
    result = runner.invoke(app, ["verify", "--repo", str(repo), "--strict", "--json"])
    assert result.exit_code == 4
    locations = {p["location"] for p in json.loads(result.stdout)["problems"]}
    assert "tests/test_auth.py::test_verify_token" in locations
    assert "README.md" in locations


def test_show_does_not_fall_back_to_a_different_file(repo):
    runner.invoke(app, ["index", "--repo", str(repo)])
    result = runner.invoke(app, ["show", "missing.py::verify_token", "--repo", str(repo)])
    assert result.exit_code == 1


def test_index_honors_config_filters_and_size(repo):
    (repo / "mogestrator.yaml").write_text(
        'version: 1\nindex:\n  include: ["src/**"]\n'
        '  exclude: ["src/auth.py"]\n  max_file_bytes: 100\n'
    )
    (repo / "src/large.py").write_text("# padding\n" * 100)
    result = runner.invoke(app, ["index", "--repo", str(repo), "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["totals"]["files"] == 1


def test_invalid_config_fails_before_creating_index(repo):
    for config in (
        "[invalid", "version: 2\n", "version: true\n",
        "version: 1\nindex: []\n",
        "version: 1\nindex:\n  allow_secret_content: '*.env'\n",
        "version: 1\nindex:\n  max_file_bytes: -1\n",
        "version: 1\nindex:\n  unknown: true\n",
    ):
        (repo / "mogestrator.yaml").write_text(config)
        result = runner.invoke(app, ["index", "--repo", str(repo)])
        assert result.exit_code == 3, result.output
        assert not (repo / ".mog/graph.db").exists()


def test_verify_does_not_follow_replaced_symlink(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    source = root / "a.py"
    source.write_text("def hello():\n    return 1\n")
    runner.invoke(app, ["index", "--repo", str(root)])
    outside = tmp_path / "outside.py"
    outside.write_text(source.read_text())
    source.unlink()
    source.symlink_to(outside)
    result = runner.invoke(app, ["verify", "--repo", str(root), "--strict", "--json"])
    assert result.exit_code == 4
    assert json.loads(result.stdout)["fresh"] == 1  # only .gitignore
