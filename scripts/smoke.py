"""Exercise an installed distribution outside the checkout, without API keys."""

import json
import subprocess
import sys
import tempfile
from pathlib import Path


def main():
    with tempfile.TemporaryDirectory(prefix="mog-smoke-") as directory:
        root = Path(directory)
        (root / "sample.py").write_text("def greet(name):\n    return 'hello ' + name\n")

        def run(*args, expected=0):
            result = subprocess.run(
                [sys.executable, "-m", "mog.cli.main", *args],
                cwd=root,
                capture_output=True,
                text=True,
            )
            assert result.returncode == expected, (args, result.stdout, result.stderr)
            return result.stdout

        run("--version")
        run("init")
        first = json.loads(run("index", "--json"))
        assert first["totals"]["kind:symbol"] == 1
        assert json.loads(run("index", "--json"))["files_indexed"] == 0
        assert json.loads(run("status", "--json"))["counts"]["kind:symbol"] == 1
        assert json.loads(run("verify", "--strict", "--json"))["drifted"] == 0
        assert "greet" in run("show", "sample.py::greet")
        assert "sample.py" in run("map")
        search = json.loads(run("search", "greet", "--json"))
        assert search["items"][0]["location"] == "sample.py::greet"
        memory = json.loads(
            run("remember", "decision", "Keep greetings simple", "--anchor", "greet", "--json")
        )
        assert (
            json.loads(run("recall", memory["id"], "--json"))["content"] == "Keep greetings simple"
        )
        assert json.loads(run("why", "greetings", "--json"))["items"]
        (root / "sample.py").write_text("def greet(name):\n    return 'hi ' + name\n")
        assert json.loads(run("verify", "--strict", "--json", expected=4))["drifted"] > 0
        run("index", "--json")
        report = json.loads(run("verify", "--strict", "--json", expected=4))
        assert report["drifted"] == 1  # the remembered decision keeps its original anchor
        assert report["problems"][0]["id"] == memory["id"]
        assert json.loads(run("recall", memory["id"], "--json"))["state"] == "stale"
    print("Installed-package smoke test passed")


if __name__ == "__main__":
    main()
