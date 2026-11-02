#!/usr/bin/env python3
"""Lazy, shallow git fetching for PR ingestion.

Maintains a bare repo per upstream project and fetches only the base branch
tips and PR head refs a build step actually needs, instead of cloning history.
"""

from pathlib import Path

from .git import git_output, git_url, run_git

BASE_REFSPEC = "+refs/heads/{ref}:refs/remotes/origin/{ref}"
PR_REFSPEC = "+refs/pull/{number}/head:refs/remotes/origin/pr/{number}"

# Fetching every refspec in one call is much faster, but git aborts the whole
# call when a single ref is gone, so oversized batches make retries expensive.
FETCH_BATCH_SIZE = 50


def prepare_refs(
    repo_path: Path,
    spec: str,
    branches: list[str],
    pr_numbers: list[int],
    verbose: bool,
) -> bool:
    """Ensure a bare repo exists and shallow-fetch the refs needed to read it.

    Fetching is best effort: refs that no longer exist upstream (deleted PR
    heads, renamed branches) are reported and skipped rather than failing the
    whole repo. Returns False only when the repo itself is unusable.
    """
    if not ensure_bare_repo(repo_path, spec, verbose):
        return False

    refspecs = [BASE_REFSPEC.format(ref=b) for b in sorted(set(branches))]
    refspecs += [PR_REFSPEC.format(number=n) for n in sorted(set(pr_numbers))]
    if not refspecs:
        return True

    if verbose:
        print(f"    fetching {len(refspecs)} ref(s) for {repo_path.name}")

    for batch in _batches(refspecs, FETCH_BATCH_SIZE):
        if _fetch(repo_path, batch) == 0:
            continue
        # One bad ref fails the batch, so retry singly to keep the good ones.
        for refspec in batch:
            if _fetch(repo_path, [refspec]) != 0 and verbose:
                print(f"    ⚠ could not fetch {refspec} in {repo_path.name}")

    return True


def ensure_bare_repo(repo_path: Path, spec: str, verbose: bool) -> bool:
    """Create or repair a bare repo for ``spec`` at ``repo_path``."""
    url = git_url(spec)

    if _is_bare_repo(repo_path):
        return _ensure_origin(repo_path, url)

    if repo_path.exists():
        # A non-bare or half-written directory cannot be shallow-fetched into
        # reliably, and silently reusing it produces confusing ref errors.
        print(f"    ✗ not a bare repo, refusing to reuse: {repo_path}")
        return False

    repo_path.parent.mkdir(parents=True, exist_ok=True)
    if run_git("init", "--bare", str(repo_path)) != 0:
        print(f"    ✗ failed to init bare repo: {repo_path}")
        return False
    if verbose:
        print(f"    initialized bare repo: {repo_path.name}")
    return _ensure_origin(repo_path, url)


def ref_exists(repo_path: Path, ref: str) -> bool:
    """Return True if ``ref`` resolves in the local repo."""
    return run_git("rev-parse", "--verify", "--quiet", ref, cwd=repo_path) == 0


def _is_bare_repo(repo_path: Path) -> bool:
    if not repo_path.is_dir():
        return False
    return git_output("rev-parse", "--is-bare-repository", cwd=repo_path) == "true"


def _ensure_origin(repo_path: Path, url: str) -> bool:
    current = git_output("remote", "get-url", "origin", cwd=repo_path)
    if current == url:
        return True
    action = "set-url" if current else "add"
    if run_git("remote", action, "origin", url, cwd=repo_path) != 0:
        print(f"    ✗ failed to configure origin remote: {repo_path}")
        return False
    return True


def _fetch(repo_path: Path, refspecs: list[str]) -> int:
    return run_git("fetch", "--depth", "1", "origin", *refspecs, cwd=repo_path)


def _batches(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]
