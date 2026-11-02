"""Git branch and repository iteration helpers."""

import subprocess  # nosec B404
from collections.abc import Iterator
from pathlib import Path

from .constants import SKIP_DIRS
from .git import _git_auth_env, git_output, git_url, run_git


def get_remote_branch_heads(
    repo_spec: str, branches: list[str]
) -> dict[str, str] | None:
    """Return {branch: head_sha} for a remote repo without cloning.

    Uses ``git ls-remote`` against the GitHub HTTPS URL; works for public
    repositories without API authentication and for private repos when
    GITHUB_TOKEN is set (the token is supplied via a credential helper, never
    as part of the URL). Returns ``None`` when git is not available or the
    remote cannot be read, and an empty dict when none of the requested
    branches exist on the remote.
    """
    url = git_url(repo_spec)
    refs = [f"refs/heads/{b}" for b in branches]
    cmd = ["git", "ls-remote", url, *refs]
    try:
        result = subprocess.run(  # nosec B603
            cmd,
            capture_output=True,
            text=True,
            check=False,
            env=_git_auth_env(),
        )
    except FileNotFoundError:
        return None
    if result.returncode != 0:
        return None
    heads: dict[str, str] = {}
    for line in result.stdout.splitlines():
        parts = line.strip().split("\t")
        if len(parts) != 2:
            continue
        sha, ref = parts
        branch = ref.removeprefix("refs/heads/")
        if branch in branches:
            heads[branch] = sha
    return heads


def get_current_branch(repo_path: Path) -> str | None:
    """Return the current git branch name, or None if detached/not a git repo."""
    try:
        head_path = repo_path / ".git" / "HEAD"
        if not head_path.is_file():
            return None
        content = head_path.read_text().strip()
        if content.startswith("ref: refs/heads/"):
            return content[len("ref: refs/heads/") :]
        return content[:7] if len(content) >= 40 else content  # detached HEAD
    except OSError:
        return None


def checkout_branch(repo_path: Path, branch: str, verbose: bool = False) -> bool:
    """Checkout a branch in a local git repo. Returns True on success.

    Returns False (without an error message) when the branch simply does not
    exist in this repo — that is a normal condition when not all repos carry
    every OCA version.  Only prints a warning for unexpected git failures.
    """
    if (
        run_git(
            "show-ref", "--verify", "--quiet", f"refs/heads/{branch}", cwd=repo_path
        )
        != 0
    ):
        if verbose:
            print(f"  ○ {repo_path.name}: no {branch} branch, skipping")
        return False

    try:
        run_git("checkout", branch, cwd=repo_path, check=True)
        return True
    except subprocess.CalledProcessError as e:
        print(f"  ⚠ Failed to checkout {branch} in {repo_path.name}: {e}")
        return False


def checkout_ref(repo_path: Path, ref: str, verbose: bool = False) -> bool:
    """Checkout a ref (branch or remote ref) in detached HEAD. Returns True on success.

    Works for branches like ``18.0`` and PR refs like ``origin/pr/123``.
    """
    target_sha = git_output("rev-parse", ref, cwd=repo_path)
    if not target_sha:
        if verbose:
            print(f"  ○ {repo_path.name}: ref {ref} not found, skipping")
        return False

    current = git_output("rev-parse", "HEAD", cwd=repo_path)
    if current == target_sha:
        return True

    try:
        run_git("checkout", "--detach", ref, cwd=repo_path, check=True)
        return True
    except subprocess.CalledProcessError as e:
        if verbose:
            print(f"  ⚠ Failed to checkout {ref} in {repo_path.name}: {e}")
        return False


def iter_repos(
    repos_dir: Path, specific_repos: list[str] | None = None
) -> Iterator[Path]:
    """Yield valid repository directories from repos_dir.

    Args:
        repos_dir: Root directory containing all repositories.
        specific_repos: If given, restrict to these repo names (must exist as
                        subdirectories of repos_dir).

    Yields:
        Paths to repository directories, sorted alphabetically when scanning
        the full directory.
    """
    if specific_repos:
        for name in specific_repos:
            repo_path = repos_dir / name
            if (
                repo_path.is_dir()
                and name not in SKIP_DIRS
                and not name.startswith(".")
            ):
                yield repo_path
    else:
        for d in sorted(repos_dir.iterdir()):
            if d.is_dir() and d.name not in SKIP_DIRS and not d.name.startswith("."):
                yield d
