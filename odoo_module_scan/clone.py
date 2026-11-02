"""Clone/fetch/checkout machinery for Odoo addon repos.

Generic git operations over a flat ``repos_dir`` of checkouts. Repo
list resolution (OCA org discovery + extra-repos files) lives in
``repos.py`` / ``github.py``; callers pass the resolved spec list or
an extra-repos file.
"""

import shutil
from pathlib import Path

from .constants import NOT_ADDONS
from .git import _parse_repo_url, git_output, git_url, run_git, run_git_capture
from .repos import load_repo_specs, repo_local_name


def _normalize_repo_spec(spec: str) -> str:
    """Convert a user-supplied repo identifier into an owner/repo string."""
    if "://" in spec or spec.startswith("git@"):
        return spec
    if "/" in spec:
        return spec
    return f"OCA/{spec}"


def _repo_url(spec: str) -> tuple[str, str]:
    """Parse repo spec into (local_name, clone_url).

    Accepts:
      - owner/repo          →  https://github.com/owner/repo.git
      - https://...         →  used as-is
      - git@github.com:...  →  used as-is
    """
    parsed = _parse_repo_url(spec)
    if not parsed:
        raise ValueError(f"Invalid repo spec: {spec}")
    owner, repo = parsed
    return repo_local_name(owner, repo), git_url(spec)


def sync_repo(repos_dir: Path, spec: str, branch: str | None, verbose: bool) -> tuple[bool, bool]:
    """Clone or update a single repo. Returns (success, changed)."""
    name, url = _repo_url(spec)
    repo_path = repos_dir / name
    changed = False

    if repo_path.is_dir() and (repo_path / ".git").is_dir():
        # Update existing — capture HEAD before and after
        before = git_output("rev-parse", "HEAD", cwd=repo_path)
        if (
            not before
            or len(before) != 40
            or not all(c in "0123456789abcdef" for c in before.lower())
        ):
            print("    ⚠ cannot determine current HEAD, skipping change detection")
            return True, False
        if verbose:
            print(f"  → {name}: updating...")
        if run_git("fetch", "--all", cwd=repo_path) != 0:
            print(f"    ⚠ fetch failed for {name}")
        pull_failed = False
        pull_rc = run_git("pull", "--ff-only", cwd=repo_path)
        if pull_rc != 0:
            print(f"    ⚠ pull failed for {name} (non-fast-forward?)")
            pull_failed = True
        if branch:
            run_git("checkout", branch, cwd=repo_path)
            pull_rc = run_git("pull", "--ff-only", "origin", branch, cwd=repo_path)
            if pull_rc != 0:
                print(f"    ⚠ pull failed for {name} branch {branch}")
                pull_failed = True
        after = git_output("rev-parse", "HEAD", cwd=repo_path)
        if not after or len(after) != 40 or not all(c in "0123456789abcdef" for c in after.lower()):
            print("    ⚠ cannot determine new HEAD, skipping change detection")
            return True, False
        changed = before != after
        # If pull failed but remote is ahead, mark as changed anyway
        if not changed and pull_failed:
            # Get the appropriate remote reference
            if branch:
                remote_ref = f"origin/{branch}"
            else:
                # Use current branch's remote tracking branch
                current_branch = git_output("rev-parse", "--abbrev-ref", "HEAD", cwd=repo_path)
                if current_branch == "HEAD" or not current_branch:
                    print(
                        "    ⚠ repo in detached HEAD state or no branch,"
                        " cannot determine remote reference"
                    )
                    return True, False
                remote_ref = f"origin/{current_branch}"
            remote_head = git_output("rev-parse", remote_ref, cwd=repo_path)
            if (
                remote_head
                and len(remote_head) == 40
                and all(c in "0123456789abcdef" for c in remote_head.lower())
            ):
                changed = before != remote_head
            else:
                print(
                    f"    ⚠ remote ref {remote_ref} not found or invalid,"
                    " cannot determine if changed"
                )
        if verbose:
            status = "✓ updated" + (" (new commits)" if changed else " (no changes)")
            print(f"    {status}")
        return True, changed
    else:
        # Clone new — always counts as changed
        if verbose:
            print(f"  → {name}: cloning...")
        cmd = ["clone", "--quiet", url, str(repo_path)]
        if branch:
            cmd += ["-b", branch, "--single-branch"]
        else:
            cmd += ["--depth", "1", "--single-branch"]
        if run_git(*cmd) != 0:
            print(f"    ✗ clone failed for {name}")
            return False, False
        if verbose:
            print("    ✓ cloned")
        return True, True


def clone_or_update_repo(repos_dir: Path, name: str, url: str, verbose: bool) -> tuple[bool, bool]:
    """Clone repo (shallow) if missing, or fetch/update if exists. Returns (success, changed)."""
    repo_path = repos_dir / name

    if repo_path.is_dir() and (repo_path / ".git").is_dir():
        # Repo exists - fetch all branches
        if run_git("fetch", "--all", cwd=repo_path) != 0 and verbose:
            print(f"    ⚠ fetch failed for {name}")
        return True, False
    else:
        # Clone new - shallow clone with all branch tips for disk savings + branch switching
        cmd = [
            "clone",
            "--quiet",
            "--depth",
            "1",
            "--no-single-branch",
            url,
            str(repo_path),
        ]
        if run_git(*cmd) != 0:
            # Error will be reported by caller
            return False, False
        return True, True


def _checkout_branch(repo_path: Path, branch: str) -> int:
    """Checkout branch, cleaning and retrying on a dirty working tree."""
    rc = run_git("checkout", branch, cwd=repo_path)
    if rc == 0:
        return 0
    run_git("reset", "--hard", cwd=repo_path)
    run_git("clean", "-fd", cwd=repo_path)
    rc = run_git("checkout", branch, cwd=repo_path)
    if rc == 0:
        return 0
    # Create or reset the local branch to track origin/{branch}.
    return run_git("checkout", "-B", branch, f"origin/{branch}", cwd=repo_path)


def checkout_and_pull_branch(repo_path: Path, branch: str, verbose: bool) -> tuple[bool, bool, str]:
    """Checkout branch and update. Returns (success, changed, status_message).

    Handles normal updates, amended commits, and force-pushes by comparing
    origin/{branch} with current HEAD and resetting if needed.
    """
    changed = False

    # Get current HEAD before any operations
    before = git_output("rev-parse", "HEAD", cwd=repo_path)
    if not before or len(before) != 40:
        return True, False, "no HEAD"

    # Fetch latest from origin (to ensure we have the latest refs)
    fetch_rc, _, fetch_err = run_git_capture("fetch", "origin", branch, cwd=repo_path)
    if fetch_rc != 0:
        if "couldn't find remote ref" in (fetch_err or ""):
            return True, False, f"branch {branch} not found"
        return True, False, "fetch failed"

    # Check if branch exists on origin
    remote_hash = git_output("rev-parse", f"origin/{branch}", cwd=repo_path)
    if not remote_hash or len(remote_hash) != 40:
        return True, False, f"branch {branch} not found"

    # Checkout the branch (create or reset local tracking branch if needed)
    if _checkout_branch(repo_path, branch) != 0:
        return False, False, "checkout failed"

    # Get current HEAD after checkout
    current = git_output("rev-parse", "HEAD", cwd=repo_path)

    # Compare with origin/{branch} to detect changes (including amended commits)
    if current != remote_hash:
        # Reset to origin/{branch} (handles normal updates and force-pushes)
        if run_git("reset", "--hard", f"origin/{branch}", cwd=repo_path) != 0:
            return True, False, "reset failed"
        changed = True

    # Get final HEAD
    after = git_output("rev-parse", "HEAD", cwd=repo_path)
    if after and len(after) == 40:
        changed = changed or (before != after)

    return True, changed, "changed" if changed else "ok"


def sync_for_branches(
    repos_dir: Path,
    branches: list[str | None],
    specific_repos: list[str] | None,
    remove_stale: bool,
    verbose: bool,
    extra_repos_file: Path | None = None,
    state_dir: Path | None = None,
) -> tuple[int, int, int, dict[str, list[str]]]:
    """Sync repos and checkout multiple branches efficiently.

    All repos are cloned once, then each branch is checked out sequentially.
    The OCA repo list is fetched from the GitHub API and merged with the
    optional extra-repos file.
    Returns: (cloned_count, updated_count, failed_count, changed_by_branch)
    where changed_by_branch maps branch name -> list of repos with changes.
    """
    # Determine repo list
    if specific_repos:
        all_specs = [_repo_url(_normalize_repo_spec(s)) for s in specific_repos]
        print(f"Repos: {len(all_specs)} specified")
    else:
        extra_file = extra_repos_file if extra_repos_file and extra_repos_file.is_file() else None
        all_specs = load_repo_specs(
            extra_file=extra_file,
            not_addons=NOT_ADDONS,
            state_dir=state_dir,
        )
        print(f"Repos: {len(all_specs)} loaded")

    # Step 1: Clone or update all repos (flat structure)
    repos_dir.mkdir(parents=True, exist_ok=True)

    cloned = updated = failed = 0
    for name, url in all_specs:
        repo_path = repos_dir / name
        is_new = not (repo_path.is_dir() and (repo_path / ".git").is_dir())

        # Show repo name inline
        action = "clone" if is_new else "fetch"
        print(f"  → {name}: {action}...", end="", flush=True)

        success, _ = clone_or_update_repo(repos_dir, name, url, verbose)
        if success:
            if is_new:
                cloned += 1
                print(" ✓ cloned")
            else:
                updated += 1
                print(" ✓ fetched")
        else:
            failed += 1
            print(" ✗ failed")

    print(f"  Summary: +{cloned} cloned, ~{updated} fetched, {failed} failed")

    # Remove stale repos if requested (only for single-branch or default mode)
    if remove_stale and len(branches) <= 1:
        expected = {name for name, _ in all_specs}
        for entry in repos_dir.iterdir():
            if entry.is_dir() and (entry / ".git").is_dir() and entry.name not in expected:
                print(f"  Removing stale: {entry.name}")
                shutil.rmtree(entry)

    # Step 2: Checkout each requested branch, tracking changes per branch
    changed_by_branch: dict[str, list[str]] = {}

    for branch in branches:
        if branch is None:
            continue  # Default branch, nothing to do

        print(f"\n  {branch}:")
        branch_changed_repos: list[str] = []
        missing_branch_repos: list[tuple[str, str]] = []

        for name, _ in all_specs:
            repo_path = repos_dir / name

            if not (repo_path.is_dir() and (repo_path / ".git").is_dir()):
                continue  # Skip if clone failed

            success, changed, status = checkout_and_pull_branch(repo_path, branch, verbose)

            if "not found" in status:
                missing_branch_repos.append((name, status))
            elif success and changed:
                branch_changed_repos.append(name)
                if verbose:
                    print(f"    → {name}: changed")
            elif verbose:
                print(f"    → {name}: {status}")

        # Report results
        if missing_branch_repos:
            for name, status in missing_branch_repos:
                print(f"    ⚠ {name}: {status}")

        if branch_changed_repos:
            changed_by_branch[branch] = branch_changed_repos
            print(f"  ✓ {len(branch_changed_repos)} repos changed")
        elif not missing_branch_repos:
            print("  ✓ no changes")

    return cloned, updated, failed, changed_by_branch
