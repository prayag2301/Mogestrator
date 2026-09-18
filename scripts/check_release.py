"""Reject a tag that would publish a different version than its release name."""

import ast
import os
import tomllib
from pathlib import Path

project = tomllib.loads(Path("pyproject.toml").read_text())["project"]
module = ast.parse(Path("src/mog/__init__.py").read_text())
version = next(
    ast.literal_eval(node.value)
    for node in module.body
    if isinstance(node, ast.Assign)
    and any(isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets)
)
assert version == project["version"], "CLI and package versions differ"
if os.environ.get("GITHUB_EVENT_NAME") == "release":
    assert os.environ["GITHUB_REF_NAME"] == f"v{version}", "Release tag and package version differ"
print(f"Version verified: {version}")
