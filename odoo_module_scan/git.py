"""Git command-line helpers."""

import os
import subprocess  # nosec B404
import sys
from pathlib import Path
from urllib.parse import urlparse


def _git_auth_env() -> dict[str, str] | None:
    """Return an env dict with a GitHub-only credential helper.

    The helper reads ``GITHUB_TOKEN``/``GH_TOKEN`` from the environment and
    only echoes credentials when the requested host is ``github.com`` over
    ``https``. The token value never appears on the git command line or in URL
    strings, and it is never sent to non-GitHub hosts.
    """
    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    if not token:
        return None
    env = os.environ.copy()
    # Normalise to GITHUB_TOKEN so the credential helper always sees the token
    # under the same variable name, whether the caller provided GITHUB_TOKEN or
    # the alternate GH_TOKEN.
    env["GITHUB_TOKEN"] = token
    env["GIT_CONFIG_COUNT"] = "1"
    env["GIT_CONFIG_KEY_0"] = "credential.helper"
    # Git invokes the helper with the credential context on stdin (e.g.
    # protocol=https, host=github.com). Only emit the token for github.com.
    env["GIT_CONFIG_VALUE_0"] = (
        "!f() { "
        '  protocol=""; host=""; '
        "  while read -r line; do "
        '    case "$line" in '
        '      protocol=*) protocol="${line#protocol=}" ;; '
        '      host=*) host="${line#host=}" ;; '
        "    esac; "
        "  done; "
        '  if [ "$protocol" = "https" ] && [ "$host" = "github.com" ]; then '
        '    echo "username=x-access-token"; '
        '    echo "password=$GITHUB_TOKEN"; '
        "  fi; "
        "}; f"
    )
    return env


def run_git(
    *args: str,
    cwd: str | Path | None = None,
    check: bool = False,
    env: dict[str, str] | None = None,
) -> int:
    """Run a git command and return its exit code.

    Args:
        *args: Git subcommand and arguments.
        cwd: Working directory for the command.
        check: If True, raise ``subprocess.CalledProcessError`` on non-zero exit.
        env: Optional environment; defaults to an auth-enabled env when
            ``GITHUB_TOKEN`` is present.

    Returns:
        The command's exit code (0 for success).
    """
    return run_git_capture(*args, cwd=cwd, check=check, env=env)[0]


def run_git_capture(
    *args: str,
    cwd: str | Path | None = None,
    check: bool = False,
    env: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    """Run a git command and return (returncode, stdout, stderr)."""
    cmd = ["git", *args]
    if env is None:
        env = _git_auth_env()
    try:
        result = subprocess.run(  # nosec B603 — cmd is always ["git", ...]
            cmd,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            check=check,
            env=env,
        )
        return result.returncode, result.stdout, result.stderr
    except FileNotFoundError:
        print("✗ git not found. Please install git.")
        sys.exit(1)


def git_output(
    *args: str,
    cwd: str | Path | None = None,
    default: str = "",
    env: dict[str, str] | None = None,
) -> str:
    """Run a git command and return its stdout, or ``default`` on failure."""
    cmd = ["git", *args]
    if env is None:
        env = _git_auth_env()
    try:
        result = subprocess.run(  # nosec B603
            cmd,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        return result.stdout.strip() if result.returncode == 0 else default
    except FileNotFoundError:
        return default


def git_url(spec: str) -> str:
    """Return a git clone URL for a repo spec or full URL."""
    if "://" in spec or spec.startswith("git@"):
        return spec
    return f"https://github.com/{spec}.git"


def _parse_repo_url(urlish: str) -> tuple[str, str] | None:
    """Extract (owner, repo) from a github.com path or full git URL.

    Supports ``https://...``, ``git@host:path/repo.git`` and bare
    ``github.com/owner/repo`` or ``owner/repo`` forms.
    Returns None if the value cannot be parsed.
    """
    value = urlish.strip()
    if not value or value.startswith("#"):
        return None
    if value.startswith("git@"):
        if ":" not in value:
            return None
        _, _, path = value.partition(":")
        parts = [p for p in path.strip("/").split("/") if p]
        if len(parts) < 2:
            return None
        owner = parts[-2]
        repo = parts[-1].removesuffix(".git")
        return owner, repo
    path = urlparse(value).path if "://" in value else value
    parts = [p for p in path.strip("/").split("/") if p]
    if len(parts) < 2:
        return None
    owner, repo = parts[-2], parts[-1]
    repo = repo.removesuffix(".git")
    return owner, repo
