"""Load installed grammar wheels; indexing never downloads executable parsers."""

from importlib import import_module

from tree_sitter import Language, Parser


def get_parser(name: str) -> Parser:
    if name not in {"python", "typescript", "go", "rust"}:
        raise ValueError(f"unsupported language: {name}")
    module = import_module(f"tree_sitter_{name}")
    factory = module.language_typescript if name == "typescript" else module.language
    return Parser(Language(factory()))
