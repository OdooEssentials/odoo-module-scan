"""Small shared helpers: JSON types, doc-id format, default dirs.

The doc-id helpers duplicate ``odooindex_common.util`` deliberately —
the ``technical_name/version[/pr-N]`` format is part of the scan
record contract, and this package must not depend on the private
odooindex packages.
"""

import json
import os
from pathlib import Path
from typing import Any

JsonDict = dict[str, Any]


def json_default(value: Any) -> Any:
    """JSON-serialise non-standard types encountered in Odoo manifests."""
    if isinstance(value, (set, frozenset)):
        try:
            return sorted(value)
        except TypeError:
            return list(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def make_doc_id(technical_name: str, version: str, pr_number: int = 0) -> str:
    """Return the canonical document ID used for indexing and display."""
    if pr_number:
        return f"{technical_name}/{version}/pr-{pr_number}"
    return f"{technical_name}/{version}"


def parse_doc_id(doc_id: str) -> tuple[str, str, int]:
    """Parse a document ID into (technical_name, version, pr_number).

    Mainline records use the form ``technical_name/version``; PR records use
    ``technical_name/version/pr-N``.
    """
    parts = doc_id.split("/")
    if len(parts) == 2:
        return parts[0], parts[1], 0
    if len(parts) == 3 and parts[2].startswith("pr-"):
        return parts[0], parts[1], int(parts[2][3:])
    raise ValueError(f"Invalid doc_id format: {doc_id}")


def load_json(path: Path, default: JsonDict | None = None) -> JsonDict:
    """Load a JSON file, returning ``default`` when it cannot be read."""
    try:
        data: JsonDict = json.loads(Path(path).read_text(encoding="utf-8"))
        return data
    except (OSError, ValueError):
        return {} if default is None else default


def write_json(path: Path, data: JsonDict, indent: int = 2) -> None:
    """Write a JSON file, creating parent directories."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=indent, default=json_default) + "\n",
        encoding="utf-8",
    )


def default_state_dir() -> Path:
    """Tool state dir: repo-list cache and other persistent state.

    ``ODOO_MODULE_SCAN_STATE_DIR`` env override, else
    ``$XDG_CACHE_HOME/odoo-module-scan`` (``~/.cache/odoo-module-scan``).
    """
    env = os.getenv("ODOO_MODULE_SCAN_STATE_DIR")
    if env:
        return Path(env)
    base = Path(os.getenv("XDG_CACHE_HOME") or Path.home() / ".cache")
    return base / "odoo-module-scan"


def default_repos_dir() -> Path:
    """Default repo checkout dir: ``<state-dir>/repos``."""
    return default_state_dir() / "repos"


def default_output_dir() -> Path:
    """Default artifact output dir: ``<state-dir>/output``."""
    return default_state_dir() / "output"
