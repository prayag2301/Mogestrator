"""Anchors must be insensitive to formatting and sensitive to behaviour."""

import pytest

from mog.graph.anchors import drift, file_hash, normalize_span, span_hash, span_hash_text
from mog.index.parsers.languages import get_parser


@pytest.fixture
def parser():
    return get_parser("python")


def _fn(parser, src: str):
    source = src.encode()
    tree = parser.parse(source)
    for node in tree.root_node.children:
        if node.type == "function_definition":
            return node, source
    raise AssertionError("no function found")


def test_reformatting_does_not_change_hash(parser):
    a = _fn(parser, "def f(x):\n    return x + 1\n")
    b = _fn(parser, "def f( x ):\n\n        return x  +  1\n")
    assert span_hash(*a) == span_hash(*b)


def test_comment_edits_do_not_change_hash(parser):
    a = _fn(parser, "def f(x):\n    # old note\n    return x + 1\n")
    b = _fn(parser, "def f(x):\n    # a completely different note\n    return x + 1\n")
    assert span_hash(*a) == span_hash(*b)


def test_hash_in_string_literal_is_not_treated_as_comment(parser):
    """Regex-based comment stripping gets this wrong; tree-based must not."""
    a = _fn(parser, 'def f():\n    return "# not a comment"\n')
    b = _fn(parser, 'def f():\n    return "# different string"\n')
    assert span_hash(*a) != span_hash(*b)


def test_behaviour_change_changes_hash(parser):
    a = _fn(parser, "def f(x):\n    return x + 1\n")
    b = _fn(parser, "def f(x):\n    return x + 2\n")
    assert span_hash(*a) != span_hash(*b)


def test_edit_elsewhere_in_file_does_not_change_span_hash(parser):
    """The core property: unrelated edits must not invalidate a fact."""
    a = _fn(parser, "def f(x):\n    return x + 1\n")
    b = _fn(parser, "def f(x):\n    return x + 1\n\ndef unrelated():\n    pass\n")
    assert span_hash(*a) == span_hash(*b)


def test_normalize_drops_comments_keeps_code(parser):
    node, src = _fn(parser, "def f():\n    # note\n    return 1\n")
    out = normalize_span(node, src)
    assert "note" not in out
    assert "return" in out and "1" in out


def test_drift_classification():
    assert drift("sha256:a", "sha256:a") is None
    assert drift("sha256:a", "sha256:b") == "span_changed"
    assert drift("sha256:a", None) == "span_missing"


def test_file_hash_and_text_fallback_are_stable():
    assert file_hash(b"abc") == file_hash(b"abc")
    assert file_hash(b"abc") != file_hash(b"abd")
    assert span_hash_text("a  b\n c") == span_hash_text("a b c")
