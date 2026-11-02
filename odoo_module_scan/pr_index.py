#!/usr/bin/env python3
"""Build an index of OCA modules from pull request head commits.

Module content is read with ``git show`` against shallow-fetched PR head and
base refs, so no worktree checkout is needed. The set of changed files is
supplied by the caller (from the GitHub API), keeping this module free of
network access.
"""

import ast
import re
from datetime import datetime, timezone
from pathlib import Path

from . import git_fetcher
from ._util import JsonDict, make_doc_id
from .git import git_output
from .modules import addon_roots, compute_hash


def select_prs_to_scan(
    branch_index: JsonDict,
    repo_name: str,
    repo_prs: list[JsonDict],
    force: bool,
) -> list[JsonDict]:
    """Flag which PRs need a fresh scan and return them.

    Sets ``head_sha_changed`` on every PR so the build step and the caller
    agree on the work to do without recomputing it. A PR is rescanned when it
    has no previous record, when any previous record has a different or missing
    head SHA, or when ``force`` is set.
    """
    previous = repo_modules(branch_index, repo_name)
    to_scan = []

    for pr in repo_prs:
        records = _records_for_pr(previous, pr["number"]).values()
        # A record with no stored SHA compares unequal, so records written
        # before head SHA tracking are rebuilt rather than trusted.
        pr["head_sha_changed"] = (
            force or not records or any(r.get("pr_head_sha") != pr["head_sha"] for r in records)
        )
        if pr["head_sha_changed"] and pr["pr_state"] != "merged":
            to_scan.append(pr)

    return to_scan


def build_pr_index_for_repo_branch(
    repos_dir: Path,
    repo_name: str,
    repo_prs: list[JsonDict],
    branch_index: JsonDict,
    verbose: bool,
) -> tuple[list[str], list[str], list[str], list[str], set[int], dict[str, JsonDict]]:
    """Build the PR module records for a single repo/branch.

    Expects ``select_prs_to_scan`` to have run and the caller to have attached
    ``changed_files`` to every PR that must be rescanned. Returns the change
    lists, the PR numbers that were handled, and the new module set. The caller
    is responsible for merging the new set into the branch index only after the
    D1 flush succeeds, so a failed upload does not advance the stored state.
    Previous records for unhandled, unmerged PRs are carried forward so a
    transient failure never silently archives modules.
    """
    repo_path = repos_dir / repo_name
    previous = repo_modules(branch_index, repo_name)
    new_modules: dict[str, JsonDict] = {}
    handled: set[int] = set()

    for pr in repo_prs:
        records, was_handled = _records_for_current_run(repo_path, repo_name, previous, pr, verbose)
        new_modules.update(records)
        if was_handled:
            handled.add(pr["number"])

    # Keep records for PRs outside this run's window, but let merged PRs fall
    # out of the index so they can be archived.
    for doc_id, record in previous.items():
        if (
            doc_id not in new_modules
            and record.get("pr_number") not in handled
            and record.get("pr_state") != "merged"
        ):
            new_modules[doc_id] = record

    created, updated, unchanged = _classify_records(previous, new_modules)
    deleted = sorted(set(previous) - set(new_modules))

    return created, updated, unchanged, deleted, handled, new_modules


def repo_modules(branch_index: JsonDict, repo_name: str) -> dict[str, JsonDict]:
    """Return the branch index records belonging to one repo."""
    return {
        doc_id: mod
        for doc_id, mod in branch_index.get("modules", {}).items()
        if mod.get("repo") == repo_name
    }


def _records_for_current_run(
    repo_path: Path,
    repo_name: str,
    previous: dict[str, JsonDict],
    pr: JsonDict,
    verbose: bool,
) -> tuple[dict[str, JsonDict], bool]:
    """Return this run's records for one PR and whether it was handled.

    An unhandled PR keeps its previous records untouched; a handled PR replaces
    them, so dropping to unhandled is the safe outcome for anything we could
    not read.
    """
    if pr["pr_state"] == "merged":
        # Merged code is indexed from the branch, so drop the PR records.
        return {}, True

    if not pr["head_sha_changed"]:
        return _carry_forward(previous, pr, "head SHA unchanged", verbose), True

    if "changed_files" not in pr:
        return _carry_forward(previous, pr, "changed files unavailable", verbose), False

    if not git_fetcher.ref_exists(repo_path, _pr_ref(pr)):
        return _carry_forward(previous, pr, "head ref missing", verbose), False

    return _scan_pr_modules(repo_path, repo_name, pr, verbose), True


def _carry_forward(
    previous: dict[str, JsonDict],
    pr: JsonDict,
    reason: str,
    verbose: bool,
) -> dict[str, JsonDict]:
    """Reuse a PR's previous records, refreshed with current PR metadata."""
    records = _records_for_pr(previous, pr["number"])
    if verbose:
        detail = f"carrying forward {len(records)} records" if records else "no previous record"
        print(f"    → PR #{pr['number']}: {reason}, {detail}")
    return {doc_id: _copy_with_pr_state(record, pr) for doc_id, record in records.items()}


def _classify_records(
    previous: dict[str, JsonDict],
    new_modules: dict[str, JsonDict],
) -> tuple[list[str], list[str], list[str]]:
    """Split the new records into created, updated, and unchanged doc ids."""
    created: list[str] = []
    updated: list[str] = []
    unchanged: list[str] = []

    for doc_id in sorted(new_modules):
        new_mod = new_modules[doc_id]
        old_mod = previous.get(doc_id)
        if old_mod is None:
            created.append(doc_id)
        elif any(new_mod.get(k) != old_mod.get(k) for k in new_mod if k != "updated_at"):
            updated.append(doc_id)
        else:
            unchanged.append(doc_id)

    return created, updated, unchanged


def replace_repo_modules(
    branch_index: JsonDict,
    previous: dict[str, JsonDict],
    new_modules: dict[str, JsonDict],
) -> None:
    """Swap one repo's records in the branch index and refresh its metadata."""
    modules = branch_index["modules"]
    for doc_id in previous:
        del modules[doc_id]
    modules.update(new_modules)
    branch_index["metadata"]["total_modules"] = len(modules)
    branch_index["metadata"]["generated_at"] = datetime.now(timezone.utc).isoformat()


def _pr_ref(pr: JsonDict) -> str:
    return f"origin/pr/{pr['number']}"


def _records_for_pr(modules: dict[str, JsonDict], pr_number: int) -> dict[str, JsonDict]:
    """Return the module records belonging to a specific PR."""
    return {doc_id: mod for doc_id, mod in modules.items() if mod.get("pr_number") == pr_number}


def _copy_with_pr_state(record: JsonDict, pr: JsonDict) -> JsonDict:
    """Update a carried-forward record with current PR metadata."""
    new_record = dict(record)
    new_record.update(
        {
            "pr_state": pr["pr_state"],
            "pr_title": pr["title"],
            "pr_author": pr["author"],
            "pr_head_sha": pr["head_sha"],
            "pr_updated_at": pr["updated_at"],
            "pr_url": pr["url"],
        }
    )
    return new_record


def _scan_pr_modules(
    repo_path: Path,
    repo_name: str,
    pr: JsonDict,
    verbose: bool,
) -> dict[str, JsonDict]:
    """Scan all changed modules for a single PR without a worktree checkout."""
    ref = _pr_ref(pr)
    base_ref = pr["base_ref"]
    changed_files = pr.get("changed_files", [])
    modules = _changed_modules(repo_path, ref, changed_files, repo_name)

    records: dict[str, JsonDict] = {}
    for module_path in modules:
        technical_name = Path(module_path).name
        record = _build_module_record(
            repo_name,
            technical_name,
            module_path,
            base_ref,
            pr,
            repo_path,
            ref,
            changed_files,
        )
        if record:
            records[record["doc_id"]] = record
    return records


def _empty_metrics() -> JsonDict:
    """Return zeroed code metrics for PR records."""
    return {
        "lines_py": 0,
        "lines_xml": 0,
        "lines_js": 0,
        "py_functions": 0,
        "py_complexity": 0,
        "py_mi": None,
        "py_mi_rank": None,
        "py_mi_explanation": None,
    }


def _build_module_record(
    repo_name: str,
    module: str,
    module_path: str,
    base_ref: str,
    pr: JsonDict,
    repo_path: Path,
    ref: str,
    changed_files: list[str],
) -> JsonDict | None:
    """Build a module record using git show (no worktree checkout)."""
    manifest = _manifest_from_ref(repo_path, ref, module_path)
    if not manifest or not manifest.get("installable", True):
        return None

    base_manifest = _manifest_from_ref(repo_path, f"origin/{base_ref}", module_path)
    readme = _readme_from_ref(repo_path, ref, module_path)
    content_hash = compute_hash(manifest, readme)

    return {
        "technical_name": module,
        "repo": repo_name,
        "version": base_ref,
        "manifest": manifest,
        "readme": readme,
        "hash": content_hash,
        **_empty_metrics(),
        "is_pr": True,
        "pr_number": pr["number"],
        "pr_title": pr["title"],
        "pr_author": pr["author"],
        "pr_state": pr["pr_state"],
        "pr_branch": base_ref,
        "pr_head_sha": pr["head_sha"],
        "pr_updated_at": pr["updated_at"],
        "pr_url": pr["url"],
        "pr_change_kind": _classify_change_kind(
            module_path, changed_files, base_manifest, manifest, pr["title"]
        ),
        "doc_id": make_doc_id(module, base_ref, pr["number"]),
    }


def _changed_modules(
    repo_path: Path, ref: str, changed_files: list[str], repo_name: str
) -> list[str]:
    """Return the list of module paths changed by a PR without a worktree."""
    roots = addon_roots(repo_name)
    # Specific roots first; the "" catch-all (repo root) must come last —
    # it matches every path and would shadow the real addon roots.
    prefixes = [f"{root}/" for root in roots if root != "."]
    if "." in roots:
        prefixes.append("")

    candidates: set[str] = set()
    for path in changed_files:
        for prefix in prefixes:
            if not path.startswith(prefix):
                continue
            rest = path[len(prefix) :]
            parts = rest.split("/")
            if not parts or not parts[0] or parts[0].startswith("."):
                continue
            module_path = (prefix + parts[0]).rstrip("/")
            candidates.add(module_path)
            break

    modules: list[str] = []
    for module_path in candidates:
        if _manifest_from_ref(repo_path, ref, module_path):
            modules.append(module_path)
    return sorted(modules)


def _manifest_from_ref(repo_path: Path, ref: str, module: str) -> dict | None:
    """Read a module manifest from an arbitrary ref (PR head or base)."""
    for manifest_name in ("__manifest__.py", "__openerp__.py"):
        text = _file_from_ref(repo_path, ref, module, manifest_name)
        if text:
            try:
                data = ast.literal_eval(text)
                return data if isinstance(data, dict) else None
            except (ValueError, SyntaxError):
                return None
    return None


def _readme_from_ref(repo_path: Path, ref: str, module: str) -> str:
    """Read the best available README from an arbitrary ref."""
    for readme_name in ("README.rst", "README.md", "README.txt"):
        text = _file_from_ref(repo_path, ref, module, readme_name)
        if text:
            return text
    return ""


def _file_from_ref(repo_path: Path, ref: str, module: str, filename: str) -> str:
    """Read one file of a module from a ref, empty when absent."""
    return git_output("show", f"{ref}:{module}/{filename}", cwd=repo_path)


def _classify_change_kind(
    module_path: str,
    changed_files: list[str],
    base_manifest: dict | None,
    head_manifest: dict | None,
    pr_title: str,
) -> str:
    """Classify the kind of change a PR introduces for a module."""
    title_lower = pr_title.lower()

    # No manifest on the base branch means the PR introduces the module.
    if base_manifest is None:
        return "new"

    # Version string in the manifest changed => migration
    if (
        head_manifest
        and base_manifest
        and head_manifest.get("version") != base_manifest.get("version")
    ):
        return "migration"

    if re.search(r"\[mig\b", title_lower):
        return "migration"
    if re.search(r"\badd\b|\[add\]", title_lower):
        return "new"
    if re.search(r"\[doc\]", title_lower):
        return "docs"

    # Determine from changed files inside the module
    # Files belonging to this addon live under its full module path
    # (``name/`` at repo root, ``addons/name/`` or ``odoo/addons/name/``
    # under nested roots) — matching only the first path component
    # misses nested addons and mislabels code changes as docs.
    module_files = [p for p in changed_files if p == module_path or p.startswith(module_path + "/")]
    docs_only = all(
        p.endswith((".md", ".rst")) or "/doc" in p or "/readme" in p for p in module_files
    )
    if docs_only:
        return "docs"

    if re.search(r"\[imp\]", title_lower):
        return "improvement"
    if re.search(r"\[fix\]", title_lower):
        return "fix"

    # Default: treat manifest/readme-only changes as docs, everything else as fix
    non_manifest = [
        p for p in module_files if not p.endswith(("__manifest__.py", "__openerp__.py"))
    ]
    if non_manifest and all(
        p.endswith((".md", ".rst", ".png", ".jpg", ".svg"))
        or "/readme" in p
        or "/doc" in p
        or "/static/description" in p
        for p in non_manifest
    ):
        return "docs"
    return "fix"
