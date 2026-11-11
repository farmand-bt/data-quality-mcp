"""Structural rules from CLAUDE.md, enforced mechanically.

1. The core (profiler_core/, cleaner_core/, config.py) never imports the MCP
   SDK or a web stack, so it stays transport-agnostic.
2. http_auth.py, the HTTP auth, uses Starlette and PyJWT but never the MCP SDK.
3. Nothing under src/ calls print(): over stdio, stdout is the JSON-RPC channel.
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
# The SDK's own packages, and the web stack it brings (planning/milestone-4.md)
SDK_PACKAGES = {"mcp", "mcp_types"}
WEB_PACKAGES = {"starlette", "uvicorn", "httpx2", "sse_starlette"}
# The JWT library behind OAuth mode (planning/milestone-4.5.md)
AUTH_PACKAGES = {"jwt"}


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


def _imports_from(path: Path, packages: set[str]) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [m for m in _imported_modules(tree) if m.split(".")[0] in packages]


@pytest.mark.parametrize("path", _core_files(), ids=lambda p: p.name)
def test_core_never_imports_mcp(path: Path):
    offending = _imports_from(path, SDK_PACKAGES | WEB_PACKAGES | AUTH_PACKAGES)
    assert not offending, (
        f"{path.relative_to(PACKAGE)} imports {offending}. The core must stay "
        "transport-agnostic: only tools/ and server.py may import mcp, and the "
        "web stack and the JWT library stay outside the core too."
    )


def test_http_auth_never_imports_mcp():
    offending = _imports_from(PACKAGE / "http_auth.py", SDK_PACKAGES)
    assert not offending, (
        f"http_auth.py imports {offending}. The HTTP auth uses Starlette and "
        "PyJWT only, so it can't depend on the SDK."
    )


def test_forbidden_imports_are_detected(tmp_path: Path):
    # Guard against the checks above passing because the matcher is broken
    sample = tmp_path / "sample.py"
    sample.write_text(
        "import mcp_types\nfrom starlette.requests import Request\n"
        "import mcpx\nfrom uvicorn import run\nimport jwt\nimport jwtx\n",
        encoding="utf-8",
    )
    assert _imports_from(sample, SDK_PACKAGES | WEB_PACKAGES | AUTH_PACKAGES) == [
        "mcp_types",
        "starlette.requests",
        "uvicorn",
        "jwt",
    ]


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
