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
                cwd=root, capture_output=True, text=True,
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
        (root / "sample.py").write_text("def greet(name):\n    return 'hi ' + name\n")
        assert json.loads(run("verify", "--strict", "--json", expected=4))["drifted"] > 0
        run("index", "--json")
        assert json.loads(run("verify", "--strict", "--json"))["drifted"] == 0
    print("Installed-package smoke test passed")


if __name__ == "__main__":
    main()
