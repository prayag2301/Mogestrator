"""Taint classification at the ingest boundary (ADR-0008).

The policy plane (SPEC-policy.md) reasons about `secret` and `repo:private`
labels, but it lands in M5 — four milestones after the store starts holding
content. A label is only sound where provenance is known, and that place is
here: the indexer is the only code that sees a file's path and its bytes at the
same time.

`secret` is not an exclusion. The file still gets a node, so the graph keeps
telling the truth about what exists; what it loses is its bytes. Content that
never enters the store cannot be leaked by a later bug in retrieval, MCP or
egress — a stronger property than any downstream rule, bought with one check.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from fnmatch import fnmatch

#: Applied to the node's `labels`, and understood by SPEC-policy §3 flow rules.
SECRET = "secret"
PRIVATE = "repo:private"

#: Bytes of the file head examined by the content rules. A credential that only
#: appears past 4 KB of a large file is out of scope for a path/content gate;
#: catching it is the job of the M5 flow rules, not of this boundary.
PROBE_BYTES = 4096

#: Path shapes that are credential stores by convention. Matched against the
#: repo-relative path and against its basename, so `deploy/prod.env` and
#: `.env.production` both hit.
SECRET_PATH_PATTERNS: tuple[str, ...] = (
    ".env", ".env.*", "*.env", "env.*.json",
    "*.pem", "*.key", "*.p12", "*.pfx", "*.jks", "*.keystore",
    "id_rsa*", "id_dsa*", "id_ecdsa*", "id_ed25519*",
    ".npmrc", ".pypirc", ".netrc", "_netrc", ".htpasswd",
    "credentials", "credentials.*", "*credentials.json",
    "service-account*.json", "*serviceaccount*.json",
    "*.tfstate", "*.tfstate.backup", "*.tfvars",
    "kubeconfig", "*.kubeconfig", "*_rsa", "*_ed25519",
    "secrets.yaml", "secrets.yml", "secrets.json", "*.secrets.*",
    ".aws/*", ".ssh/*", ".gnupg/*", ".docker/config.json",
)

#: Unambiguous credential material — a hit is decisive, no entropy check needed.
_STRONG_CONTENT = (
    re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(rb"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(rb"\bxox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(rb"\bsk-[A-Za-z0-9_\-]{20,}"),
    re.compile(rb"\bAIza[0-9A-Za-z_\-]{30,}"),
    re.compile(rb'"type"\s*:\s*"service_account"'),
)

#: Generic `KEY = value` assignments. On its own this shape is far too common —
#: every config sample and doc snippet has one — so the value must also look
#: random (see `_entropy`). A false positive here costs real content, which is
#: why this rule is the conservative one.
_ASSIGNMENT = re.compile(
    rb"""(?ix)
    # `\b` is useless here: underscores are word characters, so it never fires
    # between the parts of AWS_SECRET_ACCESS_KEY. Treat alphanumerics as the
    # only thing that continues a word.
    (?<![A-Za-z0-9])
    (?: secret | token | passwd | password | api[_-]?key
      | access[_-]?key | private[_-]?key | client[_-]?secret )
    (?![A-Za-z0-9])
    [^\S\r\n]* ["']? [^\S\r\n]* [:=] [^\S\r\n]* ["']?
    ([A-Za-z0-9+/=_\-]{16,})
    """
)

#: Below this, a 16+ char value is a word or a placeholder, not a credential.
#: `changeme_password` scores ~3.4; a real 32-char key scores ~4.5+.
MIN_ENTROPY_BITS = 3.9

_PLACEHOLDERS = re.compile(
    rb"(?i)^(?:x{4,}|y{4,}|your[_-]|my[_-]|some[_-]|dummy|sample|example|"
    rb"changeme|placeholder|redacted|test[_-]?only|0{6,}|1{6,})"
)


def _entropy(value: bytes) -> float:
    """Shannon entropy in bits per character."""
    if not value:
        return 0.0
    counts = Counter(value)
    total = len(value)
    return -sum((c / total) * math.log2(c / total) for c in counts.values())


def _looks_random(value: bytes) -> bool:
    if _PLACEHOLDERS.match(value):
        return False
    return _entropy(value) >= MIN_ENTROPY_BITS


def path_is_secret(rel: str) -> bool:
    basename = rel.rsplit("/", 1)[-1]
    return any(
        fnmatch(rel, pat) or fnmatch(basename, pat) or fnmatch(rel, f"**/{pat}")
        for pat in SECRET_PATH_PATTERNS
    )


def content_is_secret(head: bytes) -> bool:
    if any(pattern.search(head) for pattern in _STRONG_CONTENT):
        return True
    return any(_looks_random(m.group(1)) for m in _ASSIGNMENT.finditer(head))


def classify(rel: str, source: bytes, allow: tuple[str, ...] | list[str] = ()) -> list[str]:
    """Label one file. Returns ``["secret"]`` or ``["repo:private"]``.

    `allow` names paths whose content may be stored despite matching — the
    fixtures that legitimately hold fake keys. It is per-pattern and opt-in;
    there is deliberately no global off switch.
    """
    if any(fnmatch(rel, pat) for pat in allow):
        return [PRIVATE]
    if path_is_secret(rel) or content_is_secret(source[:PROBE_BYTES]):
        return [SECRET]
    return [PRIVATE]
