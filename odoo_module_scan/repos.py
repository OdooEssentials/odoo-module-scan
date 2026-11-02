"""Repository list loading and parsing."""

from pathlib import Path

from .git import _parse_repo_url, git_url
from .github import fetch_oca_repos


def repo_local_name(owner: str, repo: str) -> str:
    """Return the local directory name used for a repository."""
    return f"{owner}__{repo}" if owner != "OCA" else repo


def parse_repo_list(text: str) -> list[tuple[str, str, str]]:
    """Parse a simple repo list.

    Each non-comment line is a full git URL or a short ``owner/repo``
    spec. Returns a list of (owner, repo, spec) tuples where ``spec``
    is the original line for full URLs and the short form otherwise.
    """
    repos: list[tuple[str, str, str]] = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parsed = _parse_repo_url(line)
        if parsed:
            owner, repo = parsed
            spec = line if "://" in line or line.startswith("git@") else f"{owner}/{repo}"
            repos.append((owner, repo, spec))
    return repos


def load_repo_list(
    extra_file: Path | None = None,
    not_addons: set[str] | None = None,
    state_dir: Path | None = None,
) -> list[tuple[str, str, str]]:
    """Load a combined, deduplicated repository list.

    Args:
        extra_file: Path to an extra repo list (owner/repo or URL per line).
        not_addons: Set of repo names to drop from the OCA list only.

    Returns:
        List of (owner, repo, spec) tuples in file order, deduplicated.
        ``spec`` is the short ``owner/repo`` form for OCA entries and the
        original full URL for extra entries.
    """
    specs: list[tuple[str, str, str]] = []
    seen: set[str] = set()

    for owner, repo, spec in fetch_oca_repos(not_addons, state_dir):
        key = f"{owner}/{repo}"
        if key not in seen:
            seen.add(key)
            specs.append((owner, repo, spec))

    if extra_file and extra_file.is_file():
        for owner, repo, spec in parse_repo_list(extra_file.read_text(encoding="utf-8")):
            key = f"{owner}/{repo}"
            if key not in seen:
                seen.add(key)
                specs.append((owner, repo, spec))

    return specs


def load_repo_specs(
    extra_file: Path | None = None,
    not_addons: set[str] | None = None,
    state_dir: Path | None = None,
) -> list[tuple[str, str]]:
    """Load a combined, deduplicated repo list for syncing.

    Returns a list of (local_name, clone_url) tuples ready to pass to
    ``git clone`` / ``git fetch`` without further parsing.
    """
    return [
        (repo_local_name(owner, repo), git_url(spec))
        for owner, repo, spec in load_repo_list(extra_file, not_addons, state_dir)
    ]
