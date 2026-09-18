import pytest

from mog.index.parsers.base import SPECS, extract, spec_for_path
from mog.index.parsers.languages import get_parser


def parse(lang, code, path="f.py"):
    src = code.encode()
    return extract(get_parser(lang).parse(src).root_node, src, SPECS[lang], path)


def test_python_qualnames_include_class():
    syms, _ = parse("python", "class Foo:\n    def bar(self):\n        pass\n")
    assert {s.qualname for s in syms} == {"Foo", "Foo.bar"}


def test_nested_functions_do_not_collide():
    """Two inner functions with the same name must stay distinguishable."""
    syms, _ = parse("python", (
        "def outer_a():\n    def visit():\n        pass\n\n"
        "def outer_b():\n    def visit():\n        pass\n"
    ))
    quals = {s.qualname for s in syms}
    assert "outer_a.visit" in quals and "outer_b.visit" in quals


def test_calls_attributed_to_enclosing_symbol_only():
    syms, _ = parse("python", (
        "def outer():\n    a()\n    def inner():\n        b()\n"
    ))
    by_name = {s.qualname: s.calls for s in syms}
    assert "a" in by_name["outer"] and "b" not in by_name["outer"]
    assert "b" in by_name["outer.inner"]


def test_attribute_call_resolves_to_final_name():
    syms, _ = parse("python", "def f():\n    store.refresh(1)\n")
    assert "refresh" in syms[0].calls


def test_imports_captured():
    _, imports = parse("python", "import os\nfrom a.b import c\n")
    assert len(imports) == 2


def test_test_detection_by_name_and_path():
    syms, _ = parse("python", "def test_thing():\n    pass\n", path="tests/test_x.py")
    assert syms[0].is_test
    syms, _ = parse("python", "def thing():\n    pass\n", path="src/x.py")
    assert not syms[0].is_test


@pytest.mark.parametrize(
    "lang,code,expected",
    [
        ("go", "func Add(a int) int { return a }", "Add"),
        ("rust", "fn add(a: i32) -> i32 { a }", "add"),
        ("typescript", "function add(a: number) { return a; }", "add"),
    ],
)
def test_other_languages_extract_symbols(lang, code, expected):
    syms, _ = parse(lang, code, path=f"f.{lang}")
    assert expected in {s.name for s in syms}


def test_spec_lookup_by_extension():
    assert spec_for_path("a/b.py").name == "python"
    assert spec_for_path("a/b.go").name == "go"
    assert spec_for_path("a/b.unknown") is None
