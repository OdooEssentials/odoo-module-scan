"""Index building: scan local checkouts into index/changes/manifest dicts.

Pure scan functions over ``Path`` — no artifact filenames, no database.
Incremental scanning reuses records from a previous index when the
module's last-touching commit SHA is unchanged. ``record_hook`` lets a
consumer attach extra fields per record (ingest uses it for enrichment
joins) without the scanner knowing about them.
"""

from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from pathlib import Path

from ._util import JsonDict, default_repos_dir
from .branch import checkout_branch, get_current_branch, iter_repos
from .git import git_output
from .modules import (
    compute_code_metrics,
    compute_hash,
    get_manifest,
    iter_module_dir_candidates,
    read_readme,
)


def iter_modules(
    repos_dir: Path,
    version: str,
    specific_repos: list[str] | None = None,
    verbose: bool = False,
    previous_index: JsonDict | None = None,
    compute_metrics: bool = False,
) -> Iterator[dict]:
    """Yield module data with computed hash, skipping unchanged modules when possible."""
    prev_modules = (previous_index or {}).get("modules", {})
    for repo in iter_repos(repos_dir, specific_repos):
        if verbose:
            print(f"\nRepo: {repo.name} ({version})")
            print("-" * 30)

        # Ensure repo is on the expected branch
        current_branch = get_current_branch(repo)
        if current_branch and current_branch != version:
            checkout_branch(repo, version)

        module_count = 0
        seen_names: set[str] = set()
        for item in iter_module_dir_candidates(repo, verbose):
            if item.name in seen_names:
                if verbose:
                    print(f"    ⚠ duplicate {item.name}, skipping {item.relative_to(repo)}")
                continue

            # Best-effort per-module last-change SHA. On shallow clones this
            # may equal the branch HEAD, not the true last commit for the path.
            rel_path = item.relative_to(repo)
            last_commit_sha = git_output(
                "log", "-1", "--format=%H", "--", str(rel_path), cwd=repo, default=""
            ).strip()

            prev_module = prev_modules.get(item.name)
            if (
                prev_module
                and prev_module.get("repo") == repo.name
                and prev_module.get("version") == version
                and prev_module.get("last_commit_sha") == last_commit_sha
                # Metrics mode is part of reuse compatibility — a record
                # cached without metrics is reparsed when --metrics is
                # on, and stale metric fields drop when it is off.
                and ("lines_py" in prev_module) == compute_metrics
            ):
                # No new commit has touched this module; reuse the previous record
                # without re-parsing the manifest, README, or code metrics.
                yield {**prev_module, "last_commit_sha": last_commit_sha}
                seen_names.add(item.name)
                module_count += 1
                continue

            manifest = get_manifest(item, verbose)
            if not manifest or not manifest.get("installable", True):
                continue

            readme = read_readme(item)
            content_hash = compute_hash(manifest, readme)
            metrics = compute_code_metrics(item) if compute_metrics else {}

            # if verbose:
            #     print(f"    → {item.name}")

            yield {
                "technical_name": item.name,
                "repo": repo.name,
                "version": version,
                "manifest": manifest,
                "readme": readme,
                "hash": content_hash,
                "last_commit_sha": last_commit_sha,
                **metrics,
            }
            seen_names.add(item.name)
            module_count += 1

        print(f"    {repo.name} ({version}): {module_count} modules")


def enrich_modules(modules: JsonDict) -> None:
    """Post-process all modules in-place after the full index is built.

    Computes cross-module data that requires the complete set of modules:

    - depended_on_by (list[str]): names of modules in this index that declare
      this module as a dependency.

    Args:
        modules: The ``new_index["modules"]`` dict, keyed by technical_name.
                 Mutated in-place.
    """
    for module in modules.values():
        module["depended_on_by"] = []

    for name, module in modules.items():
        for dep in module.get("manifest", {}).get("depends", []):
            if dep in modules:
                modules[dep]["depended_on_by"].append(name)


def build_index(
    repos_dir: Path | None = None,
    *,
    version: str,
    specific_repos: list[str] | None = None,
    verbose: bool = False,
    previous_index: JsonDict | None = None,
    record_hook: Callable[[dict], None] | None = None,
    compute_metrics: bool = False,
) -> tuple[JsonDict, JsonDict]:
    """Build index from repos.

    ``record_hook`` is invoked on each module record (including records
    reused from the previous index) before change classification, so a
    consumer can attach fields that participate in update detection.

    Returns:
        Tuple of (full_index, changes) where full_index is the complete module
        index and changes describes what is new, updated, unchanged, or deleted
        relative to previous_index.
    """
    prev_modules = previous_index.get("modules", {}) if previous_index else {}

    new_index: JsonDict = {
        "metadata": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "version": version,
            "total_modules": 0,
        },
        "modules": {},
    }

    changes: JsonDict = {
        "metadata": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "version": version,
        },
        "new_modules": [],
        "updated_modules": [],
        "unchanged_modules": [],
        "deleted_modules": [],
    }

    current_names = set()

    repos_dir = Path(repos_dir) if repos_dir else default_repos_dir()
    for module in iter_modules(
        repos_dir, version, specific_repos, verbose, previous_index, compute_metrics
    ):
        name = module["technical_name"]
        current_names.add(name)

        if record_hook:
            record_hook(module)
        new_index["modules"][name] = module

        prev = prev_modules.get(name)
        if prev is None:
            changes["new_modules"].append(name)
            if verbose:
                print(f"    [NEW] {name}")
        elif (
            prev.get("hash") != module.get("hash")
            or prev.get("facts_hash") != module.get("facts_hash")
            or prev.get("enrichment_hash") != module.get("enrichment_hash")
        ):
            # Consumer-attached hashes (e.g. enrichment) mark a module
            # updated even when its source hash is unchanged.
            changes["updated_modules"].append(name)
            if verbose:
                print(f"    [UPDATED] {name}")
        else:
            changes["unchanged_modules"].append(name)

    # Detect deleted modules — only meaningful for a full scan; a partial scan
    # (specific_repos) cannot distinguish "deleted" from "not scanned".
    if not specific_repos:
        prev_names = set(prev_modules.keys())
        changes["deleted_modules"] = list(prev_names - current_names)

    new_index["metadata"]["total_modules"] = len(new_index["modules"])
    changes["metadata"]["new_count"] = len(changes["new_modules"])
    changes["metadata"]["updated_count"] = len(changes["updated_modules"])
    changes["metadata"]["unchanged_count"] = len(changes["unchanged_modules"])
    changes["metadata"]["deleted_count"] = len(changes["deleted_modules"])

    # Post-processing: enrich modules with derived and cross-module data
    enrich_modules(new_index["modules"])

    return new_index, changes
