"""Structural rules from CLAUDE.md, enforced mechanically.

1. The core (profiler_core/, cleaner_core/, config.py) never imports `mcp`, so
   it stays transport-agnostic.
2. Nothing under src/ calls print(): over stdio, stdout is the JSON-RPC channel.
"""

import ast
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parent.parent / "src" / "data_quality_mcp"
CORE_PATHS = [
    PACKAGE / "profiler_core",
    PACKAGE / "cleaner_core",
    PACKAGE / "config.py",
]


def _python_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    return sorted(path.rglob("*.py")) if path.is_dir() else []


def _core_files() -> list[Path]:
    return [f for p in CORE_PATHS for f in _python_files(p)]


def _imported_modules(tree: ast.AST) -> list[str]:
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.append(node.module)
    return modules


def test_core_files_found():
    # Guard against the boundary test silently passing on an empty file list
    assert len(_core_files()) >= 5


@pytest.mark.parametrize("path", _core_files(), ids=lambda p: p.name)
def test_core_never_imports_mcp(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    offending = [
        m for m in _imported_modules(tree) if m == "mcp" or m.startswith("mcp.")
    ]
    assert not offending, (
        f"{path.relative_to(PACKAGE)} imports {offending}. The core must stay "
        "transport-agnostic: only tools/ and server.py may import mcp."
    )


@pytest.mark.parametrize("path", _python_files(PACKAGE), ids=lambda p: p.name)
def test_no_print_in_src(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    prints = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "print"
    ]
    assert not prints, (
        f"{path.relative_to(PACKAGE)} calls print() on line(s) {prints}. Over "
        "stdio, stdout is the protocol channel; use logging (stderr) instead."
    )
