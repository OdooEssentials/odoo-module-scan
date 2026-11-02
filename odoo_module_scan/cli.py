"""``odoo-module-scan`` command line interface.

Unix-style stage: network only for git/GitHub, then local-file
scanning, emitting JSON artifacts to an output dir or stdout. All
progress and diagnostics go to stderr in ``--stdout`` mode so the
JSON stream pipes cleanly into the next step. The work itself lives
in :mod:`odoo_module_scan.api` — one function per subcommand.
"""

import argparse
import contextlib
import json
import sys
from pathlib import Path

from ._util import default_state_dir
from . import api
from .module_facts import DETAIL_SECTIONS


def _emit_stdout(data) -> None:
    json.dump(data, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")


def _redirect_to_stderr(stdout_mode: bool):
    """In --stdout mode every diagnostic goes to stderr."""
    return (
        contextlib.redirect_stdout(sys.stderr)
        if stdout_mode
        else contextlib.nullcontext()
    )


def _cmd_sync(args: argparse.Namespace) -> int:
    with _redirect_to_stderr(args.print_repos):
        result = api.sync(
            repos_dir=args.repos_dir,
            branches=args.branch,
            repos=args.repos,
            extra_repos=args.extra_repos,
            remove_stale=args.remove_stale,
            output_dir=args.output_dir,
            state_dir=args.state_dir,
            print_repos=args.print_repos,
            verbose=args.verbose,
        )
    if args.print_repos:
        _emit_stdout(result["specs"])
        return 0
    return 1 if result["failed"] else 0


def _cmd_scan(args: argparse.Namespace) -> int:
    try:
        with _redirect_to_stderr(args.stdout):
            result = api.scan(
                repos_dir=args.repos_dir,
                branches=args.branch,
                repos=args.repos,
                output_dir=args.output_dir,
                metrics=args.metrics,
                state_dir=args.state_dir,
                verbose=args.verbose,
            )
    except FileNotFoundError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1

    changes = result["changes"]
    for branch, doc in changes.items():
        meta = doc["metadata"]
        print(
            f"  {branch}: +{meta['new_count']} ~{meta['updated_count']}"
            f" ={meta['unchanged_count']} -{meta['deleted_count']}"
        )
    if args.stdout:
        _emit_stdout(
            next(iter(changes.values())) if len(changes) == 1 else {"changes": changes}
        )

    if args.only_changed:
        return (
            0
            if any(
                d["metadata"]["new_count"]
                + d["metadata"]["updated_count"]
                + d["metadata"]["deleted_count"]
                for d in changes.values()
            )
            else 1
        )
    return 0


def _cmd_pr_scan(args: argparse.Namespace) -> int:
    with _redirect_to_stderr(args.stdout):
        result = api.pr_scan(
            repos_dir=args.repos_dir,
            branch=args.branch,
            prs=args.prs,
            output_dir=args.output_dir,
            force=args.force,
            state_dir=args.state_dir,
            verbose=args.verbose,
        )
    if args.stdout:
        _emit_stdout(result)
    return 0


def _cmd_facts(args: argparse.Namespace) -> int:
    try:
        index = api.facts(
            args.repo, output=args.output, sections=args.sections, yaml_out=args.yaml
        )
    except FileNotFoundError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1

    print(f"extracted {len(index['addons'])} addons")
    if index["failures"]:
        print("failures:")
        for failure in index["failures"]:
            print(f"  {failure['addon']}: {failure['error']}")
    return 1 if index["failures"] else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="odoo-module-scan",
        description=(
            "Scan Odoo addon repos into JSON artifacts: clone/fetch checkouts,"
            " build incremental module indexes and change reports, extract"
            " deterministic addon facts. Emits files or JSON to stdout for a"
            " downstream pipeline step."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # sync ------------------------------------------------------------------
    p = sub.add_parser("sync", help="clone/fetch/checkout the repo list")
    p.add_argument(
        "--repos-dir",
        type=Path,
        default=None,
        help="repo checkouts (default: <state-dir>/repos)",
    )
    p.add_argument(
        "--branch",
        nargs="*",
        type=str,
        help="branch(es) to checkout; 'all' for every supported version",
    )
    p.add_argument("--extra-repos", type=Path, help="extra non-OCA repo list file")
    p.add_argument("--repos", nargs="+", help="specific repos (name or owner/repo)")
    p.add_argument(
        "--remove-stale",
        action="store_true",
        help="remove local repos no longer in the list (DANGEROUS)",
    )
    p.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="artifact output (default: <state-dir>/output)",
    )
    p.add_argument("--state-dir", type=Path, default=default_state_dir())
    p.add_argument(
        "--print-repos",
        action="store_true",
        help="emit the resolved repo list as JSON and exit",
    )
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=_cmd_sync)

    # scan ------------------------------------------------------------------
    p = sub.add_parser("scan", help="build module index + changes JSON from checkouts")
    p.add_argument(
        "--repos-dir",
        type=Path,
        default=None,
        help="repo checkouts (default: <state-dir>/repos)",
    )
    p.add_argument(
        "--branch",
        nargs="+",
        type=str,
        required=True,
        help="branch(es) to scan; 'all' for every supported version",
    )
    p.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="artifact output (default: <state-dir>/output)",
    )
    p.add_argument("--state-dir", type=Path, default=default_state_dir())
    p.add_argument("--repos", nargs="+", help="specific repos to scan (default: all)")
    p.add_argument(
        "--stdout",
        action="store_true",
        help="emit the changes JSON to stdout instead of writing files",
    )
    p.add_argument(
        "--only-changed",
        action="store_true",
        help="exit 0 = changes detected, exit 1 = none (CI gate)",
    )
    p.add_argument(
        "--metrics",
        action="store_true",
        help="emit radon/lizard code metrics (experimental)",
    )
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=_cmd_scan)

    # pr-scan ---------------------------------------------------------------
    p = sub.add_parser("pr-scan", help="scan PR head modules for a prepared PR list")
    p.add_argument(
        "--repos-dir",
        type=Path,
        default=None,
        help="repo checkouts (default: <state-dir>/repos)",
    )
    p.add_argument("--branch", required=True, help="base branch of the PRs")
    p.add_argument(
        "--prs",
        type=Path,
        required=True,
        help="JSON file: {local_name: [pr dicts with changed_files]}",
    )
    p.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="artifact output (default: <state-dir>/output)",
    )
    p.add_argument("--state-dir", type=Path, default=default_state_dir())
    p.add_argument(
        "--stdout",
        action="store_true",
        help="emit the changes JSON to stdout instead of writing files",
    )
    p.add_argument("--force", action="store_true", help="rescan every listed PR")
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=_cmd_pr_scan)

    # facts -----------------------------------------------------------------
    p = sub.add_parser("facts", help="deterministic per-addon fact extraction")
    p.add_argument("repo", type=Path, help="addon repo checkout to scan")
    p.add_argument(
        "--output", type=Path, help="output dir (default: <output>/facts/<repo>)"
    )
    p.add_argument(
        "--sections",
        nargs="+",
        choices=(*DETAIL_SECTIONS, "all"),
        default=None,
        help="opt-in detail sections: fields methods access tests (or 'all')",
    )
    p.add_argument(
        "--yaml",
        action="store_true",
        help="also emit a YAML doc set under <output>/yaml",
    )
    p.set_defaults(func=_cmd_facts)

    args = parser.parse_args(argv)
    result: int = args.func(args)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
