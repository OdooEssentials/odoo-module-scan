"""GitHub API and OCA repository list helpers."""

import json
import os
import time
from collections.abc import Iterator
from pathlib import Path

import requests

from ._util import JsonDict, default_state_dir
from .constants import NOT_ADDONS


def github_api_get(url: str, timeout: int = 60) -> requests.Response:
    """Make an authenticated GitHub API GET request."""
    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return requests.get(url, headers=headers, timeout=timeout)


def github_api_paginate(url: str, per_page: int = 100, timeout: int = 60) -> Iterator[JsonDict]:
    """Yield items from a paginated GitHub API GET endpoint."""
    page = 1
    while True:
        page_url = f"{url}?per_page={per_page}&page={page}"
        resp = github_api_get(page_url, timeout)
        if resp.status_code == 403:
            print("✗ GitHub API rate limit hit. Set GITHUB_TOKEN to increase limits.")
            break
        resp.raise_for_status()
        data = resp.json()
        if not data:
            break
        yield from data
        if len(data) < per_page:
            break
        page += 1


def _repo_list_cache_file(state_dir: Path | None = None) -> Path:
    """Path for the cached OCA repo list from the GitHub API."""
    return Path(state_dir or default_state_dir()) / "oca_repos_cache.json"


def _load_repo_cache(cache_file: Path, ttl: int) -> list[str] | None:
    """Return cached repo names if the cache exists and is fresh."""
    if not cache_file.is_file():
        return None
    try:
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        fetched_at = data.get("fetched_at")
        names = data.get("names")
        if not isinstance(fetched_at, (int, float)) or not isinstance(names, list):
            return None
        if time.time() - fetched_at > ttl:
            return None
        return [str(n) for n in names]
    except (OSError, ValueError, TypeError):
        return None


def _save_repo_cache(cache_file: Path, names: list[str]) -> None:
    """Write repo names and a timestamp to the cache file."""
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    data = {"fetched_at": time.time(), "names": names}
    cache_file.write_text(json.dumps(data), encoding="utf-8")


def fetch_oca_repos(
    not_addons: set[str] | None = None,
    state_dir: Path | None = None,
) -> list[tuple[str, str, str]]:
    """List current OCA repositories from the GitHub API (cached).

    Excludes archived repos and repos in ``NOT_ADDONS`` (or the provided
    ``not_addons`` set). ``state_dir`` overrides the cache directory
    (default: the tool state dir). Returns (owner, repo, spec) tuples
    with the short ``OCA/repo`` spec form.
    """
    if not (os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")):
        print("⚠ No GITHUB_TOKEN set — GitHub API rate limits will be very low")

    cache_file = _repo_list_cache_file(state_dir)
    ttl = int(os.getenv("OCA_REPO_CACHE_TTL", "86400"))
    names = _load_repo_cache(cache_file, ttl)

    if names is None:
        names = [
            repo["name"]
            for repo in github_api_paginate("https://api.github.com/orgs/OCA/repos")
            if repo.get("name") and not repo.get("archived")
        ]
        if names:
            _save_repo_cache(cache_file, names)
        else:
            print("⚠ No OCA repos returned from GitHub API")

    exclude = not_addons if not_addons is not None else NOT_ADDONS
    return [("OCA", name, f"OCA/{name}") for name in names if name not in exclude]
