"""Alphabot — experimental PR-preview branches for OCA-style repos.

Per repo + base branch: fetch every open PR, merge them in PR-number
order onto a ``<branch>-preview`` ref rebuilt from the base tip, push
to the repo's fork remote, and maintain a draft PR describing the
composition. Unstable by design — see docs/ALPHABOT.md.
"""

import os

import requests

from . import git_fetcher
from ._util import JsonDict
from .git import git_url, run_git, run_git_capture
from .github import github_api_get, github_api_paginate

API_BASE = "https://api.github.com"

PREVIEW_REMOTE = "preview"


def list_open_prs(spec: str, base: str) -> list[dict]:
    """Open PRs on ``owner/repo`` targeting ``base``, PR-number order."""
    url = f"{API_BASE}/repos/{spec}/pulls?state=open&base={base}"
    return sorted(github_api_paginate(url), key=lambda p: p["number"])


def _api_write(method: str, url: str, data: JsonDict) -> JsonDict:
    """POST/PATCH to the GitHub API; returns the parsed body."""
    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    resp = requests.request(method, url, headers=headers, json=data, timeout=60)
    resp.raise_for_status()
    return resp.json()


def build_preview(repo_path, branch: str, pr_numbers: list[int], verbose: bool = False) -> dict:
    """Merge ``origin/pr/<n>`` heads onto base tip into ``<branch>-preview``.

    Returns ``{"merged": [...], "conflicts": [...], "skipped": [...]}``.
    """
    preview = f"{branch}-preview"
    to_fetch = [n for n in pr_numbers if not git_fetcher.ref_exists(repo_path, f"origin/pr/{n}")]
    if to_fetch and not git_fetcher.prepare_refs(repo_path, "", [branch], to_fetch, verbose):
        return {"merged": [], "conflicts": [], "skipped": pr_numbers}

    run_git("checkout", "-B", preview, f"origin/{branch}", cwd=repo_path)
    merged, conflicts = [], []
    for n in pr_numbers:
        code, _, err = run_git_capture(
            "merge", "--no-ff", "-m", f"alphabot: merge PR #{n}", f"origin/pr/{n}", cwd=repo_path
        )
        if code == 0:
            merged.append(n)
        else:
            run_git("merge", "--abort", cwd=repo_path)
            conflicts.append(
                {
                    "number": n,
                    "error": err.strip().splitlines()[-1] if err.strip() else "merge failed",
                }
            )
    return {"merged": merged, "conflicts": conflicts, "skipped": []}


def push_preview(repo_path, branch: str, fork_spec: str, verbose: bool = False) -> bool:
    """Force-push ``<branch>-preview`` to the ``preview`` remote (the fork)."""
    url = git_url(fork_spec)
    if run_git("remote", "get-url", PREVIEW_REMOTE, cwd=repo_path) != 0:
        run_git("remote", "add", PREVIEW_REMOTE, url, cwd=repo_path)
    else:
        run_git("remote", "set-url", PREVIEW_REMOTE, url, cwd=repo_path)
    return (
        run_git("push", "-f", PREVIEW_REMOTE, f"{branch}-preview:{branch}-preview", cwd=repo_path)
        == 0
    )


def upsert_draft_pr(fork_spec: str, branch: str, merged: list, conflicts: list) -> dict:
    """Create or update the draft PR describing the preview composition."""
    preview = f"{branch}-preview"
    title = f"[DRAFT] preview: {branch} + {len(merged)} open PRs"
    body = (
        f"Preview branch `{preview}` — `{branch}` tip plus all open PRs that merge cleanly.\n\n"
        f"**Merged:** {', '.join(f'#{n}' for n in merged) or '—'}\n\n"
        f"**Conflicts (skipped):** "
        + (", ".join(f"#{c['number']}" for c in conflicts) or "—")
        + "\n\n"
        "_Rebuilt by alphabot on every run; unstable by definition._"
    )
    owner = fork_spec.split("/", 1)[0]
    resp = github_api_get(f"{API_BASE}/repos/{fork_spec}/pulls?head={owner}:{preview}&state=open")
    existing = resp.json() if resp.status_code == 200 else []
    if existing:
        return _api_write(
            "PATCH",
            f"{API_BASE}/repos/{fork_spec}/pulls/{existing[0]['number']}",
            {"title": title, "body": body},
        )
    return _api_write(
        "POST",
        f"{API_BASE}/repos/{fork_spec}/pulls",
        {"title": title, "head": preview, "base": branch, "body": body, "draft": True},
    )


def alphabot_repo(
    repo_path, spec: str, branch: str, fork_owner: str, dry_run: bool = False, verbose: bool = False
) -> dict:
    """Run the full preview cycle for one repo; returns the outcome dict."""
    local_name = repo_path.name
    fork_spec = f"{fork_owner}/{spec.split('/', 1)[1]}"
    open_prs = list_open_prs(spec, branch)
    numbers = [p["number"] for p in open_prs]
    if verbose:
        print(f"  {local_name}: {len(numbers)} open PRs on {branch}")

    result = {
        "repo": local_name,
        "spec": spec,
        "branch": branch,
        "open_prs": numbers,
        "merged": [],
        "conflicts": [],
        "preview_ref": f"{branch}-preview",
    }
    if not numbers:
        result["status"] = "no open PRs"
        return result
    result.update(build_preview(repo_path, branch, numbers, verbose))
    if dry_run:
        result["status"] = "dry-run"
        return result
    result["pushed"] = push_preview(repo_path, branch, fork_spec, verbose=verbose)
    result["draft_pr"] = upsert_draft_pr(fork_spec, branch, result["merged"], result["conflicts"])
    result["status"] = "ok" if result["pushed"] else "push failed"
    return result
