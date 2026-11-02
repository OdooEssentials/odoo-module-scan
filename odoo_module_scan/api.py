"""Public top-level API — one function per ``odoo-module-scan`` subcommand.

Each function mirrors its CLI counterpart's arguments and side
effects, and returns the data the command would emit:

- :func:`sync`    — ``odoo-module-scan sync``
- :func:`scan`    — ``odoo-module-scan scan``
- :func:`facts`   — ``odoo-module-scan facts``
- :func:`pr_scan` — ``odoo-module-scan pr-scan``

Directory arguments accept ``str | Path`` and default to the
``--state-dir`` tree (``~/.cache/odoo-module-scan`` by default).
"""

import json
from collections.abc import Sequence
from pathlib import Path

from . import git_fetcher, pr_index
from ._util import (
    JsonDict,
    default_output_dir,
    default_state_dir,
    load_json,
    write_json,
)
from .clone import _normalize_repo_spec, _repo_url, sync_for_branches
from .constants import SUPPORTED_OCA_VERSIONS
from .index import build_index
from .module_facts import extract_repo
from .repos import load_repo_specs


def resolve_branches(branches: Sequence[str] | None, ascending: bool) -> list[str | None]:
    """Expand ``"all"`` in a branch list, or return ``[None]`` for none given."""
    if branches is None:
        return [None]
    source = SUPPORTED_OCA_VERSIONS if "all" in branches else branches
    return list[str | None](sorted(source, reverse=not ascending))


def sync(
    repos_dir: str | Path | None = None,
    branches: list[str] | None = None,
    repos: list[str] | None = None,
    extra_repos: str | Path | None = None,
    remove_stale: bool = False,
    output_dir: str | Path | None = None,
    state_dir: str | Path | None = None,
    print_repos: bool = False,
    verbose: bool = False,
) -> JsonDict:
    """Clone/fetch/checkout the repo list — the ``sync`` subcommand.

    With ``print_repos``, only resolves the repo list and returns
    ``{"specs": [{name, url}, ...]}`` without cloning. Otherwise returns
    ``{"cloned", "updated", "failed", "changed_by_branch"}`` and writes
    ``changed-<branch>.txt`` files under ``output_dir``.
    """
    state_dir = Path(state_dir) if state_dir else default_state_dir()
    if print_repos:
        extra = Path(extra_repos) if extra_repos and Path(extra_repos).is_file() else None
        if repos:
            specs = [_repo_url(_normalize_repo_spec(s)) for s in repos]
        else:
            specs = load_repo_specs(extra_file=extra, state_dir=state_dir)
        return {"specs": [{"name": name, "url": url} for name, url in specs]}

    repos_dir = Path(repos_dir) if repos_dir else state_dir / "repos"
    repos_dir.mkdir(parents=True, exist_ok=True)
    output_dir = Path(output_dir) if output_dir else state_dir / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    branches_to_process = resolve_branches(branches, ascending=True)
    cloned, updated, failed, changed_by_branch = sync_for_branches(
        repos_dir=repos_dir,
        branches=branches_to_process,
        specific_repos=repos,
        remove_stale=remove_stale,
        verbose=verbose,
        extra_repos_file=Path(extra_repos) if extra_repos else None,
        state_dir=state_dir,
    )

    for branch in branches_to_process:
        branch_name = branch if branch else "default"
        changed_file = output_dir / f"changed-{branch_name}.txt"
        changed_repos = changed_by_branch.get(branch, []) if branch else []
        changed_file.write_text(" ".join(changed_repos), encoding="utf-8")

    return {
        "cloned": cloned,
        "updated": updated,
        "failed": failed,
        "changed_by_branch": changed_by_branch,
    }


def scan(
    repos_dir: str | Path | None = None,
    branches: list[str] | None = None,
    repos: list[str] | None = None,
    output_dir: str | Path | None = None,
    metrics: bool = False,
    record_hook=None,
    state_dir: str | Path | None = None,
    verbose: bool = False,
) -> JsonDict:
    """Build incremental module index + changes — the ``scan`` subcommand.

    Writes ``index-<branch>.json`` (incremental state) and
    ``changes-<branch>.json`` under ``output_dir``; returns
    ``{"indexes": {branch: index}, "changes": {branch: changes}}``.
    ``record_hook`` is the extension seam — a callable applied to each
    module record before change classification.
    """
    state_dir = Path(state_dir) if state_dir else default_state_dir()
    repos_dir = Path(repos_dir) if repos_dir else state_dir / "repos"
    if not repos_dir.is_dir():
        raise FileNotFoundError(f"repos directory not found: {repos_dir}")
    output_dir = Path(output_dir) if output_dir else state_dir / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    wanted = [b for b in resolve_branches(branches, ascending=False) if b]
    result: JsonDict = {"indexes": {}, "changes": {}}
    for branch in wanted:
        index_file = output_dir / f"index-{branch}.json"
        previous_index = (
            load_json(index_file, default={"metadata": {}, "modules": {}})
            if index_file.is_file()
            else None
        )
        full_index, changes = build_index(
            repos_dir=repos_dir,
            version=branch,
            specific_repos=repos,
            verbose=verbose,
            previous_index=previous_index,
            compute_metrics=metrics,
            record_hook=record_hook,
        )
        write_json(index_file, full_index)
        write_json(output_dir / f"changes-{branch}.json", changes)
        result["indexes"][branch] = full_index
        result["changes"][branch] = changes
    return result


def pr_scan(
    repos_dir: str | Path | None = None,
    branch: str | None = None,
    prs: str | Path | dict | None = None,
    output_dir: str | Path | None = None,
    force: bool = False,
    state_dir: str | Path | None = None,
    verbose: bool = False,
) -> JsonDict:
    """Scan PR head modules for a prepared PR list — ``pr-scan``.

    ``prs`` is a ``{"<local_name>": [pr dicts with changed_files]}``
    mapping or a path to such a JSON file. Writes
    ``pr-index-<branch>.json`` (incremental state) and
    ``pr-changes-<branch>.json``; returns the changes document.
    """
    state_dir = Path(state_dir) if state_dir else default_state_dir()
    repos_dir = Path(repos_dir) if repos_dir else state_dir / "repos"
    output_dir = Path(output_dir) if output_dir else state_dir / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    if prs is None:
        raise ValueError("prs is required")
    prs_by_repo: dict[str, list[JsonDict]] = (
        prs if isinstance(prs, dict) else json.loads(Path(prs).read_text(encoding="utf-8"))
    )
    index_file = output_dir / f"pr-index-{branch}.json"
    branch_index = load_json(index_file, default={"metadata": {}, "modules": {}})

    result: JsonDict = {
        "branch": branch,
        "created": [],
        "updated": [],
        "unchanged": [],
        "deleted": [],
        "records": {},
    }
    for local_name, repo_prs in prs_by_repo.items():
        branch_prs = [pr for pr in repo_prs if pr["base_ref"] == branch]
        if not branch_prs:
            continue
        to_scan = pr_index.select_prs_to_scan(branch_index, local_name, branch_prs, force)
        if to_scan:
            ok = git_fetcher.prepare_refs(
                repos_dir / local_name,
                _repo_spec(local_name),
                [pr["base_ref"] for pr in to_scan],
                [pr["number"] for pr in to_scan],
                verbose,
            )
            if not ok:
                print(f"    ✗ {local_name}: bare repo unusable, skipping")
                continue

        created, updated, unchanged, deleted, _, new_modules = (
            pr_index.build_pr_index_for_repo_branch(
                repos_dir, local_name, branch_prs, branch_index, verbose
            )
        )
        previous = pr_index.repo_modules(branch_index, local_name)
        pr_index.replace_repo_modules(branch_index, previous, new_modules)

        result["created"] += created
        result["updated"] += updated
        result["unchanged"] += unchanged
        result["deleted"] += deleted
        for doc_id in created + updated + (unchanged if force else []):
            result["records"][doc_id] = new_modules[doc_id]

    write_json(index_file, branch_index)
    write_json(output_dir / f"pr-changes-{branch}.json", result)
    return result


def facts(
    repo: str | Path,
    output: str | Path | None = None,
    sections: list[str] | str | None = None,
    yaml_out: bool = False,
    verbose: bool = False,
) -> JsonDict:
    """Deterministic per-addon fact extraction — the ``facts`` subcommand.

    ``sections`` opts into experimental detail blocks
    (``fields``, ``methods``, ``access``, ``tests``, ``all``);
    ``yaml_out`` also emits a YAML doc set under ``<output>/yaml``.
    Returns the ``_index.json`` digest.
    """
    repo = Path(repo)
    if not repo.is_dir():
        raise FileNotFoundError(f"repo checkout not found: {repo}")
    out_dir = Path(output) if output else default_output_dir() / "facts" / repo.resolve().name
    if sections == "all" or (isinstance(sections, list) and "all" in sections):
        sections = "all"
    index = extract_repo(repo, out_dir, sections=sections)

    if yaml_out:
        import yaml

        docs = out_dir / "yaml"
        docs.mkdir(parents=True, exist_ok=True)
        (docs / "index.yaml").write_text(
            yaml.safe_dump(index, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )
        for name in index["addons"]:
            (docs / f"{name}.yaml").write_text(
                yaml.safe_dump(
                    load_json(out_dir / f"{name}.json"),
                    sort_keys=False,
                    allow_unicode=True,
                ),
                encoding="utf-8",
            )
    return index


def _repo_spec(local_name: str) -> str:
    """Derive the ``owner/repo`` spec from a repo local dir name."""
    owner, sep, name = local_name.partition("__")
    return f"{owner}/{name}" if sep else f"OCA/{local_name}"
