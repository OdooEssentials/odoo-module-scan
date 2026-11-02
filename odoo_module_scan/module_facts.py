"""Deterministic technical fact extraction for Odoo addons."""

import ast
import csv
import io
import json
import subprocess  # nosec B404
import xml.etree.ElementTree as ET  # nosec B405
from pathlib import Path
from typing import Any

MANIFEST_KEYS = {
    "name",
    "version",
    "summary",
    "author",
    "website",
    "license",
    "category",
    "depends",
    "data",
    "demo",
    "external_dependencies",
    "installable",
    "auto_install",
    "application",
    "post_init_hook",
    "pre_init_hook",
    "uninstall_hook",
    "post_load",
    "assets",
}
FEATURE_MODELS = {
    "ir.cron": "cron",
    "ir.sequence": "sequences",
    "ir.config_parameter": "config_parameters",
    "res.groups": "groups",
    "ir.rule": "record_rules",
    "ir.actions.server": "server_actions",
    "ir.actions.report": "reports",
    "ir.ui.view": "views",
    "mail.template": "mail_templates",
    "mail.activity.type": "activity_types",
    "product.category": "product_categories",
    "sale.order.type": "order_types",
    "decimal.precision": "decimal_precision",
}


def extract_repo(repo_path: str | Path, out_dir: str | Path, sections=None) -> dict[str, Any]:
    """Extract every addon in a repository and write per-addon facts plus an index."""
    repo = Path(repo_path)
    output = Path(out_dir)
    output.mkdir(parents=True, exist_ok=True)
    addons = sorted(p.name for p in repo.iterdir() if (p / "__manifest__.py").is_file())
    index: dict[str, Any] = {
        "repo": str(repo),
        "git": git_info(repo),
        "addons": {},
        "failures": [],
    }
    for addon in addons:
        try:
            facts = extract_addon(repo, addon, sections)
        except Exception as exc:  # noqa: BLE001
            index["failures"].append({"addon": addon, "error": f"{type(exc).__name__}: {exc}"})
            continue
        _write_json(output / f"{addon}.json", facts)
        index["addons"][addon] = index_entry(facts)
    _write_json(output / "_index.json", index)
    return index


DETAIL_SECTIONS = ("fields", "methods", "access", "tests")


def extract_addon(repo_path: str | Path, addon: str, sections=None) -> dict[str, Any]:
    """Build the deterministic fact record for one addon.

    ``sections`` opts into detail blocks that are experimental contract:
    ``fields``/``methods`` (per-class inventories), ``access`` (parsed
    ir.model.access.csv rows) and ``tests`` (per-file test detail).
    ``"all"`` enables every detail section.
    """
    detail = set() if sections is None else set(DETAIL_SECTIONS if sections == "all" else sections)
    repo = Path(repo_path)
    root = (repo / addon).resolve()
    if root.parent != repo.resolve() or not (root / "__manifest__.py").is_file():
        raise ValueError(f"Invalid addon path: {addon}")
    manifest = parse_manifest(root / "__manifest__.py")
    derived: dict[str, set[str]] = {
        "new_models": set(),
        "inherits": set(),
        "transient_models": set(),
    }
    python_files: list[dict[str, Any]] = []
    xml_files: list[dict[str, Any]] = []
    xml_records: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if path.suffix == ".py":
            entry = scan_python(path, rel, derived)
            if entry:
                python_files.append(entry)
        elif path.suffix == ".xml":
            xml_files.append(scan_xml(path, rel, xml_records))
    security = scan_security(root)
    tests = scan_tests(root)
    features = derive_features(manifest, python_files, xml_records, security, derived)
    counts = {
        "python_files": len(python_files),
        "xml_files": len(xml_files),
        "xml_records": len(xml_records),
        "fields_defined": sum(
            len(cls["fields"]) for item in python_files for cls in item.get("classes", [])
        ),
        "methods_defined": sum(
            len(cls["methods"]) for item in python_files for cls in item.get("classes", [])
        ),
        "test_files": len(tests["files"]),
        "test_methods": tests["test_methods"],
    }
    # Detail sections are gated — the emitted dict keeps the stable
    # inventory while counts are computed before stripping.
    py_out = [
        {
            **item,
            "classes": [
                {k: v for k, v in cls.items() if k in detail or k not in ("fields", "methods")}
                for cls in item.get("classes", [])
            ],
        }
        for item in python_files
    ]
    sec_out = {
        **security,
        "access_rules": security["access_rules"] if "access" in detail else [],
    }
    tests_out = {
        "files": tests["files"] if "tests" in detail else [],
        "test_methods": tests["test_methods"],
    }
    return {
        "format": "addon-facts/v1",
        "module": addon,
        "repo": repo.name,
        "manifest": manifest,
        "readme": scan_readme(root),
        "python": py_out,
        "xml": xml_files,
        "security": sec_out,
        "i18n": scan_i18n(root),
        "tests": tests_out,
        "derived": {
            "new_models": sorted(derived["new_models"]),
            "transient_models": sorted(derived["transient_models"]),
            "inherits": sorted(derived["inherits"]),
            "features": sorted(features),
            "record_models": sorted({record["model"] for record in xml_records}),
        },
        "counts": counts,
    }


def git_info(repo_path: str | Path) -> dict[str, str]:
    """Return best-effort Git provenance for a repository."""
    repo = str(repo_path)
    result = {"path": repo}
    try:
        result["commit"] = subprocess.check_output(  # nosec B603 B607
            ["git", "-C", repo, "rev-parse", "HEAD"], text=True
        ).strip()
        result["describe"] = subprocess.check_output(  # nosec B603 B607
            ["git", "-C", repo, "describe", "--always", "--dirty"], text=True
        ).strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return result


def index_entry(facts: dict[str, Any]) -> dict[str, Any]:
    """Return the repository-index digest for one addon."""
    return {
        "installable": facts["manifest"].get("installable", True),
        "depends": facts["manifest"].get("depends", []),
        "new_models": facts["derived"]["new_models"],
        "inherits": facts["derived"]["inherits"],
        "features": facts["derived"]["features"],
        "counts": facts["counts"],
    }


def parse_manifest(path: Path) -> dict[str, Any]:
    """Parse allow-listed literal manifest values."""
    raw = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(raw)
        # manifest must be a single dict literal — a leading import or
        # statement node makes body[0] non-Expr, which literal_eval
        # can't reach anyway
        first = tree.body[0] if tree.body else None
        value = ast.literal_eval(first.value) if isinstance(first, ast.Expr) else None
    except (SyntaxError, ValueError, TypeError) as exc:
        return {"_parse_error": f"{type(exc).__name__}: {exc}", "_raw": raw}
    if not isinstance(value, dict):
        return {"_parse_error": "manifest is not a dict literal", "_raw": raw}
    return {key: value for key, value in value.items() if key in MANIFEST_KEYS}


def scan_readme(root: Path) -> dict[str, Any]:
    """Inventory README fragments without copying their complete content."""
    result: dict[str, Any] = {
        "files": [],
        "readme_rst": (root / "README.rst").is_file(),
    }
    readme_dir = root / "readme"
    if not readme_dir.is_dir():
        return result
    for path in sorted(readme_dir.iterdir()):
        if not path.is_file():
            continue
        try:
            lead = next(
                (
                    line.strip().lstrip("#").strip()[:160]
                    for line in path.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ),
                None,
            )
        except (OSError, UnicodeDecodeError):
            lead = None
        result["files"].append({"file": path.relative_to(root).as_posix(), "lead": lead})
    return result


def scan_python(path: Path, rel: str, derived: dict[str, set[str]]) -> dict[str, Any] | None:
    """Inventory classes, Odoo fields, methods, decorators, and imports in a Python file."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return {"file": rel, "error": "syntax error, not scanned"}
    classes: list[dict[str, Any]] = []
    imports: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            bases = [_dotted_name(base) for base in node.bases]
            cls: dict[str, Any] = {
                "name": node.name,
                "bases": bases,
                "fields": [],
                "methods": [],
            }
            for statement in node.body:
                if isinstance(statement, (ast.Assign, ast.AnnAssign)):
                    targets = (
                        statement.targets
                        if isinstance(statement, ast.Assign)
                        else [statement.target]
                    )
                    for target in targets:
                        if isinstance(target, ast.Name):
                            _handle_class_attr(cls, statement, target.id)
                elif isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    decorators = [
                        name for item in statement.decorator_list if (name := _decorator_name(item))
                    ]
                    cls["methods"].append(
                        statement.name + (f" (@{','.join(decorators)})" if decorators else "")
                    )
            name, inherit = cls.get("_name"), cls.get("_inherit")
            if isinstance(name, str):
                bucket = (
                    "transient_models"
                    if any(base.endswith("TransientModel") for base in bases)
                    else "new_models"
                )
                derived[bucket].add(name)
            if isinstance(inherit, str):
                derived["inherits"].add(inherit)
            elif isinstance(inherit, list):
                derived["inherits"].update(item for item in inherit if isinstance(item, str))
            if name or inherit or cls["fields"] or cls["methods"]:
                classes.append(cls)
        elif isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
    if not classes and path.name == "__init__.py":
        return {"file": rel, "init_imports": sorted(imports)}
    if not classes and not imports:
        return None
    return {"file": rel, "classes": classes, "imports": sorted(set(imports))}


def scan_xml(path: Path, rel: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    """Inventory Odoo XML records, templates, menu items, and models."""
    entry: dict[str, Any] = {
        "file": rel,
        "records": 0,
        "templates": 0,
        "menuitems": 0,
        "models": set(),
        "noupdate": False,
    }
    try:
        root = ET.parse(path).getroot()  # nosec B314
    except ET.ParseError:
        return {"file": rel, "error": "XML parse error"}
    entry["noupdate"] = any(data.get("noupdate") in ("1", "true") for data in root.iter("data"))
    for record in root.iter("record"):
        model = record.get("model")
        if model:
            entry["models"].add(model)
            records.append({"file": rel, "id": record.get("id"), "model": model})
            entry["records"] += 1
    for template in root.iter("template"):
        entry["templates"] += 1
        if template.get("id"):
            records.append({"file": rel, "id": template.get("id"), "model": "ir.ui.view(template)"})
    entry["menuitems"] = sum(1 for _ in root.iter("menuitem"))
    entry["models"] = sorted(entry["models"])
    return entry


def scan_security(root: Path) -> dict[str, Any]:
    """Inventory security files and parse model access rows."""
    result: dict[str, Any] = {"files": [], "access_rules": []}
    security = root / "security"
    if not security.is_dir():
        return result
    for path in sorted(security.iterdir()):
        if not path.is_file():
            continue
        result["files"].append(path.relative_to(root).as_posix())
        if path.name == "ir.model.access.csv":
            try:
                for row in csv.DictReader(io.StringIO(path.read_text(encoding="utf-8"))):
                    if row.get("model_id:id"):
                        result["access_rules"].append(
                            {
                                "name": row.get("name"),
                                "model": row["model_id:id"],
                                "group": row.get("group_id:id"),
                            }
                        )
            except (OSError, UnicodeDecodeError, csv.Error):
                pass
    return result


def scan_i18n(root: Path) -> list[str]:
    """Return available translation language names."""
    directory = root / "i18n"
    return sorted(path.stem for path in directory.glob("*.po")) if directory.is_dir() else []


def scan_tests(root: Path) -> dict[str, Any]:
    """Inventory Python test files, classes, and method counts."""
    result: dict[str, Any] = {"files": [], "test_methods": 0}
    directory = root / "tests"
    if not directory.is_dir():
        return result
    for path in sorted(directory.glob("*.py")):
        if path.name == "__init__.py":
            continue
        entry: dict[str, Any] = {
            "file": path.relative_to(root).as_posix(),
            "classes": [],
            "test_methods": 0,
        }
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    methods = [
                        item.name
                        for item in node.body
                        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and item.name.startswith("test")
                    ]
                    if methods:
                        entry["classes"].append(node.name)
                        entry["test_methods"] += len(methods)
        except (SyntaxError, UnicodeDecodeError):
            entry["error"] = "syntax error"
        result["files"].append(entry)
        result["test_methods"] += entry["test_methods"]
    return result


def derive_features(
    manifest: dict[str, Any],
    python_files: list[dict[str, Any]],
    xml_records: list[dict[str, Any]],
    security: dict[str, Any],
    derived: dict[str, set[str]],
) -> set[str]:
    """Derive cross-file technical feature flags."""
    features = {
        FEATURE_MODELS[item["model"]] for item in xml_records if item["model"] in FEATURE_MODELS
    }
    if security["access_rules"]:
        features.add("access_rules")
    if security["files"]:
        features.add("security_files")
    if derived["transient_models"]:
        features.add("wizards")
    for item in python_files:
        if item.get("file", "").startswith("controllers/"):
            features.add("controllers")
        if any("portal" in imported for imported in item.get("imports", [])):
            features.add("portal")
    if any(
        manifest.get(name)
        for name in ("post_init_hook", "pre_init_hook", "uninstall_hook", "post_load")
    ):
        features.add("hooks")
    if manifest.get("assets"):
        features.add("web_assets")
    return features


def _handle_class_attr(
    cls: dict[str, Any], statement: ast.Assign | ast.AnnAssign, attr: str
) -> None:
    if attr in ("_name", "_inherit"):
        value = statement.value
        if value is None:  # bare AnnAssign, e.g. `_inherit: str`
            cls[attr] = None
            return
        try:
            cls[attr] = ast.literal_eval(value)
        except (ValueError, TypeError):
            cls[attr] = None
        return
    call = statement.value
    if isinstance(call, ast.Call) and _dotted_name(call.func).startswith("fields."):
        cls["fields"].append(f"{attr}: {_dotted_name(call.func)}")


def _decorator_name(node: ast.expr) -> str | None:
    return _decorator_name(node.func) if isinstance(node, ast.Call) else _dotted_name(node) or None


def _dotted_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return ""


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
