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
