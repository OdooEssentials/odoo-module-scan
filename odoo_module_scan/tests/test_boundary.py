"""Boundary gate: the package must not import ingest-private modules.

``odoo_module_scan`` is carved out to become a standalone public
package — it may only depend on itself, third-party libraries, and
the standard library. Any import of ``utils`` or ``odooindex_common``
breaks the extraction boundary.
"""

import ast
from pathlib import Path

import pytest

PACKAGE_DIR = Path(__file__).resolve().parent.parent
FORBIDDEN_PREFIXES = ("utils", "odooindex_common")


def _imported_modules(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


@pytest.mark.parametrize(
    "path",
    sorted(PACKAGE_DIR.rglob("*.py")),
    ids=lambda p: str(p.relative_to(PACKAGE_DIR)),
)
def test_no_ingest_imports(path: Path):
    """No package file imports ingest-private modules."""
    offenders = [
        name for name in _imported_modules(path) if name.split(".")[0] in FORBIDDEN_PREFIXES
    ]
    assert not offenders, f"{path} imports ingest modules: {offenders}"
