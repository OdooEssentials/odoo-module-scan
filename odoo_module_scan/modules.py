"""OCA module/manifest inspection and metrics."""

import ast
import hashlib
import json
import math
import re
import warnings
from collections.abc import Iterator
from pathlib import Path

import lizard
import radon.metrics as radon_metrics
from docutils.utils import SystemMessage  # type: ignore[import-untyped]
from rst2gfm import convert_rst_to_md  # type: ignore[import-untyped]

from ._util import JsonDict, json_default

MANIFEST_NAMES = ("__manifest__.py", "__openerp__.py")


def get_manifest(module_dir: Path, verbose: bool = False) -> dict | None:
    """Parse the Odoo manifest file from a module directory.

    Tries __manifest__.py then __openerp__.py.

    Args:
        module_dir: Path to the module directory.
        verbose: Print a warning when a manifest exists but cannot be parsed.

    Returns:
        Parsed manifest as a dict, or None if not found or invalid.
    """
    for name in MANIFEST_NAMES:
        path = module_dir / name
        if path.is_file():
            data = _parse_manifest(path.read_text(encoding="utf-8"))
            if data is not None:
                return data
            if verbose:
                print(f"  ⚠ Failed to parse {path}")
    return None


def _parse_manifest(text: str) -> dict | None:
    """Try to parse a manifest string, fixing invalid escapes and recursing."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", SyntaxWarning)
            data = ast.literal_eval(text)
            return data if isinstance(data, dict) else None
    except (ValueError, SyntaxError, SyntaxWarning, OSError):
        fixed = re.sub(r"(?<!\\)\\.", lambda m: "\\" + m.group(), text)
        if fixed == text:
            return None
        return _parse_manifest(fixed)


def _rst_to_markdown(rst_text: str) -> str:
    """Convert reStructuredText README to Markdown for the web UI."""
    try:
        md = str(convert_rst_to_md(rst_text))
    except (SystemMessage, ValueError, TypeError, OSError, AttributeError):
        # If the converter fails, keep the raw RST text so indexing does not break.
        return rst_text

    # rst2gfm can glue a preceding link and the next heading on the same line,
    # e.g. "[Odoo Community Association](...)## Sale Advance Payment".
    # Ensure headings start on their own line preceded by a blank line.
    md = re.sub(r"(?<![\n\r])(#{1,6} )", r"\n\n\1", md)
    return md


def read_readme(module_dir: Path) -> str:
    """Read top-level README from a module directory (README.md or README.rst).

    README.rst files are converted to Markdown so the web UI can render them
    with its existing Markdown parser. Returns an empty string if no README
    exists or cannot be decoded.
    """
    for name in ("README.md", "README.rst"):
        path = module_dir / name
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if name.endswith(".rst"):
            return _rst_to_markdown(text)
        return text
    return ""


_CODE_SKIP_DIRS = {"tests", "test", "i18n", "static", "demo", "migrations", "__pycache__"}
_CODE_SKIP_FILES = {"__init__.py", "__manifest__.py", "__openerp__.py"}


def read_module_code(module_dir: Path) -> str:
    """Read source code files (.py, .js, .xml) from a module directory.

    Skips tests, i18n, static, demo, migrations, __init__ and manifest files.
    Each file is prefixed with its relative path as a header.
    """
    parts: list[str] = []
    for glob in ("**/*.py", "**/*.js", "**/*.xml"):
        for path in sorted(module_dir.glob(glob)):
            rel = path.relative_to(module_dir)
            if path.name in _CODE_SKIP_FILES:
                continue
            if any(p in _CODE_SKIP_DIRS for p in rel.parts[:-1]):
                continue
            try:
                content = path.read_text(encoding="utf-8").strip()
            except (OSError, UnicodeDecodeError):
                continue
            if content:
                parts.append(f"### {rel}\n\n{content}")
    return "\n\n".join(parts)


def _iter_code_files(module_dir: Path) -> Iterator[Path]:
    """Yield code files (.py/.js/.xml) applying the same skip rules as read_module_code."""
    for glob in ("**/*.py", "**/*.js", "**/*.xml"):
        for path in sorted(module_dir.glob(glob)):
            rel = path.relative_to(module_dir)
            if path.name in _CODE_SKIP_FILES:
                continue
            if any(p in _CODE_SKIP_DIRS for p in rel.parts[:-1]):
                continue
            yield path


def _build_mi_explanation(
    volume: float,
    complexity: float,
    sloc: float,
    comments_pct: float,
    py_mi: float,
    py_mi_rank: str,
) -> str:
    """Return a short explanation of the radon Maintainability Index components.

    The MI formula is:
        MI = (171 - 5.2*ln(volume) - 0.23*complexity - 16.2*ln(sloc)
              + 50*sin(sqrt(2.46*radians(comments_pct)))) * 100 / 171
    Higher penalty terms reduce the score; the comment term is a bonus.
    """
    if volume <= 0 or sloc <= 0:
        return f"MI {py_mi} ({py_mi_rank}): no measurable Python code to analyze."

    # Penalty magnitudes in raw MI units (before the 100/171 scaling)
    drivers = [
        ("code size", 16.2 * math.log(sloc), int(sloc)),
        ("Halstead volume", 5.2 * math.log(volume), int(volume)),
        ("cyclomatic complexity", 0.23 * complexity, int(complexity)),
    ]
    drivers.sort(key=lambda item: item[1], reverse=True)
    driver_text = ", ".join(f"{name} ({value})" for name, _, value in drivers)
    return (
        f"MI {py_mi} ({py_mi_rank}). The score is mainly driven by {driver_text}. "
        f"Comment ratio: {comments_pct:.0f}%."
    )


def compute_code_metrics(module_dir: Path) -> JsonDict:
    """Compute code size and complexity metrics for a module directory.

    Uses lizard for language-agnostic line counts and cyclomatic complexity
    across .py, .js, and .xml files, and radon for the Python Maintainability
    Index — the one metric radon offers that lizard does not.

    Applies the same file/directory skip rules as read_module_code.

    Returns a dict with:
        lines_py (int):       non-comment source lines across .py files
        lines_xml (int):      non-comment source lines across .xml files
        lines_js (int):       non-comment source lines across .js files
        py_functions (int):   number of function/method definitions in .py files
        py_complexity (int):  sum of cyclomatic complexity scores across .py functions
        py_mi (float|None):   radon Maintainability Index numeric score (0–100, higher is
                              better); useful for sorting and trending; None if no .py files
        py_mi_rank (str|None): letter grade derived from py_mi: A (>19, maintainable),
                              B (9–19, moderate), C (≤9, unmaintainable); None if no .py files
        py_mi_explanation (str|None): short sentence explaining what drives the MI score
    """
    lines: dict[str, int] = {"py": 0, "xml": 0, "js": 0}
    py_functions = 0
    py_complexity = 0
    py_source_parts: list[str] = []

    for path in _iter_code_files(module_dir):
        ext = path.suffix.lstrip(".")
        if ext not in lines:
            continue
        try:
            source = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError):
            continue

        file_result = lizard.analyze_file.analyze_source_code(str(path), source)
        lines[ext] += file_result.nloc

        if ext == "py":
            py_functions += len(file_result.function_list)
            py_complexity += sum(fn.cyclomatic_complexity for fn in file_result.function_list)
            py_source_parts.append(source)

    # Maintainability Index: computed over all .py source concatenated
    py_mi: float | None = None
    py_mi_rank: str | None = None
    py_mi_explanation: str | None = None
    if py_source_parts:
        combined = "\n".join(py_source_parts)
        try:
            volume, cc, sloc, comments_pct = radon_metrics.mi_parameters(combined, count_multi=True)
            py_mi = round(radon_metrics.mi_compute(volume, cc, sloc, comments_pct), 1)
            py_mi_rank = radon_metrics.mi_rank(py_mi)
            assert py_mi is not None and py_mi_rank is not None
            py_mi_explanation = _build_mi_explanation(
                volume, cc, sloc, comments_pct, py_mi, py_mi_rank
            )
        except (SyntaxError, ValueError, TypeError, OSError, AttributeError, ZeroDivisionError):
            # radon MI scoring is best-effort; failures leave the metric unset.
            # SyntaxError: module source may use grammar newer than the runner's
            # interpreter (e.g. PEP 701 nested-quote f-strings need Python 3.12+).
            py_mi = None
            py_mi_rank = None
            py_mi_explanation = None

    return {
        "lines_py": lines["py"],
        "lines_xml": lines["xml"],
        "lines_js": lines["js"],
        "py_functions": py_functions,
        "py_complexity": py_complexity,
        "py_mi": py_mi,
        "py_mi_rank": py_mi_rank,
        "py_mi_explanation": py_mi_explanation,
    }


def compute_hash(manifest: dict, readme: str) -> str:
    """Compute a short content hash for change detection.

    Args:
        manifest: Module manifest dictionary.
        readme: README content as a string.

    Returns:
        First 16 hex characters of the SHA-256 digest.
    """
    content = json.dumps(manifest, sort_keys=True, default=json_default) + readme
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def addon_roots(local_name: str) -> tuple[str, ...]:
    """Candidate addon root dirs for a repo, odoo-core layout aware.

    Generic repos may hold addons at the root and/or under ``addons`` /
    ``odoo/addons``. Odoo core repos (local name ``<owner>__odoo``) keep
    addons only under ``odoo/addons`` and ``addons`` — their root holds
    non-module content (``odoo-bin``, ``setup/``, ...).
    """
    if local_name.endswith("__odoo"):
        return ("odoo/addons", "addons")
    return (".", "addons", "odoo/addons")


def iter_module_dir_candidates(repo: Path, verbose: bool = False) -> Iterator[Path]:
    """Yield potential module directories from the repo root and addon roots."""
    seen: set[Path] = set()
    for rel_s in addon_roots(repo.name):
        rel = Path(rel_s)
        root = repo / rel
        if not root.is_dir():
            continue
        if verbose:
            print(f"  addon root: {rel}")
        for item in root.iterdir():
            if item.is_dir() and not item.name.startswith(".") and item not in seen:
                seen.add(item)
                yield item
