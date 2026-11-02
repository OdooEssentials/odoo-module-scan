"""``odoo-module-scan`` command line interface.

Unix-style stage: network only for git/GitHub, then local-file
scanning, emitting JSON artifacts to an output dir or stdout. All
progress and diagnostics go to stderr in ``--stdout`` mode so the
JSON stream pipes cleanly into the next step.
"""

import argparse
import contextlib
import json
import sys
from pathlib import Path

from . import git_fetcher, pr_index
from ._util import (
    JsonDict,
    default_output_dir,
    default_state_dir,
    load_json,
    write_json,
)
from .constants import SUPPORTED_OCA_VERSIONS
from .index import build_index
from .module_facts import DETAIL_SECTIONS, extract_repo
from .repos import load_repo_specs
from .sync import sync_for_branches


def _emit_stdout(data: JsonDict | list) -> None:
    json.dump(data, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")


def _resolve_branches(branches_arg: list[str] | None, ascending: bool) -> list[str | None]:
    if branches_arg is None:
        return [None]
    resolved: list[str | None] = []
    source = SUPPORTED_OCA_VERSIONS if "all" in branches_arg else branches_arg
    resolved.extend(sorted(source, reverse=not ascending))
    return resolved


def _cmd_sync(args: argparse.Namespace) -> int:
    repos_dir = (args.repos_dir or args.state_dir / "repos").expanduser().resolve()

    if args.print_repos:
        # Resolve only: emit the spec list as JSON, no cloning.
        with contextlib.redirect_stdout(sys.stderr):
            if args.repos:
                from .sync import _normalize_repo_spec, _repo_url

                specs = [_repo_url(_normalize_repo_spec(s)) for s in args.repos]
            else:
                extra = (
                    args.extra_repos if args.extra_repos and args.extra_repos.is_file() else None
                )
                specs = load_repo_specs(extra_file=extra, state_dir=args.state_dir)
        _emit_stdout([{"name": name, "url": url} for name, url in specs])
        return 0

    branches_to_process = _resolve_branches(args.branch, ascending=True)

    repos_dir.mkdir(parents=True, exist_ok=True)
    output_dir = (args.output_dir or args.state_dir / "output").expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"📁 repos:  {repos_dir}")
    print(f"📁 output: {output_dir}")
    print()

    _cloned, _updated, failed, changed_by_branch = sync_for_branches(
        repos_dir=repos_dir,
        branches=branches_to_process,
        specific_repos=args.repos,
        remove_stale=args.remove_stale,
        verbose=args.verbose,
        extra_repos_file=args.extra_repos,
        state_dir=args.state_dir,
    )

    for branch in branches_to_process:
        branch_name = branch if branch else "default"
        changed_file = output_dir / f"changed-{branch_name}.txt"
        changed_repos = changed_by_branch.get(branch, []) if branch else []
        changed_file.write_text(" ".join(changed_repos), encoding="utf-8")

    output_files = [f"changed-{b if b else 'default'}.txt" for b in branches_to_process]
    print(f"\n✓ Wrote: {', '.join(output_files)}")

    return 1 if failed else 0


def _cmd_scan(args: argparse.Namespace) -> int:
    repos_dir = (args.repos_dir or args.state_dir / "repos").expanduser().resolve()
    if not repos_dir.is_dir():
        print(f"✗ Repos directory not found: {repos_dir}", file=sys.stderr)
        return 1

    branches = [b for b in _resolve_branches(args.branch, ascending=False) if b]
    if not branches:
        print("✗ 'scan' requires explicit branches", file=sys.stderr)
        return 1

    output_dir = (args.output_dir or args.state_dir / "output").expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    any_changes = False
    all_changes: dict[str, JsonDict] = {}

    # In --stdout mode every diagnostic — ours and the package's —
    # goes to stderr so stdout carries only the JSON document.
    with contextlib.redirect_stdout(sys.stderr) if args.stdout else contextlib.nullcontext():
        print(f"📁 repos:  {repos_dir}")
        print(f"📁 output: {output_dir}")
        print()
        if len(branches) > 1:
            print(f"Building index for branches: {', '.join(branches)}")

        for branch in branches:
            index_file = output_dir / f"index-{branch}.json"
            changes_file = output_dir / f"changes-{branch}.json"

            previous_index = (
                load_json(index_file, default={"metadata": {}, "modules": {}})
                if index_file.is_file()
                else None
            )

            print(f"  {branch}:")
            full_index, changes = build_index(
                repos_dir=repos_dir,
                version=branch,
                specific_repos=args.repos,
                verbose=args.verbose,
                previous_index=previous_index,
                compute_metrics=args.metrics,
            )

            # The index is incremental state — always write it. The
            # changes doc is the step output: file, or stdout in
            # --stdout mode.
            write_json(index_file, full_index)
            if not args.stdout:
                write_json(changes_file, changes)

            meta = changes["metadata"]
            total = full_index["metadata"]["total_modules"]
            print(
                f"    → {total} modules (+{meta['new_count']} ~{meta['updated_count']}"
                f" ={meta['unchanged_count']} -{meta['deleted_count']})"
            )

            if meta["new_count"] + meta["updated_count"] + meta["deleted_count"]:
                any_changes = True

            all_changes[branch] = changes

    if args.stdout:
        if len(all_changes) == 1:
            _emit_stdout(next(iter(all_changes.values())))
        else:
            _emit_stdout({"changes": all_changes})

    if args.only_changed:
        return 0 if any_changes else 1
    return 0


def _repo_spec(local_name: str) -> str:
    """Derive the ``owner/repo`` spec from a repo local dir name."""
    owner, sep, name = local_name.partition("__")
    return f"{owner}/{name}" if sep else f"OCA/{local_name}"


def _cmd_pr_scan(args: argparse.Namespace) -> int:
    """Scan PR head modules for caller-prepared PR lists.

    ``--prs`` is a JSON object ``{"<local_name>": [pr, ...]}``; each PR
    dict carries ``number``, ``title``, ``author``, ``pr_state``,
    ``head_sha``, ``updated_at``, ``url``, ``base_ref`` and, for PRs that
    must be rescanned, ``changed_files``. Incremental state is the
    previous ``pr-index-<branch>.json`` in the output dir; the emitted
    document lists the doc-ids created/updated/deleted plus the records
    a downstream step should store.
    """
    repos_dir = (args.repos_dir or args.state_dir / "repos").expanduser().resolve()
    output_dir = (args.output_dir or args.state_dir / "output").expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    prs_by_repo: dict[str, list[JsonDict]] = json.loads(args.prs.read_text(encoding="utf-8"))
    index_file = output_dir / f"pr-index-{args.branch}.json"
    branch_index = load_json(index_file, default={"metadata": {}, "modules": {}})

    result: JsonDict = {
        "branch": args.branch,
        "created": [],
        "updated": [],
        "unchanged": [],
        "deleted": [],
        "records": {},
    }

    with contextlib.redirect_stdout(sys.stderr) if args.stdout else contextlib.nullcontext():
        for local_name, prs in prs_by_repo.items():
            branch_prs = [pr for pr in prs if pr["base_ref"] == args.branch]
            if not branch_prs:
                continue
            to_scan = pr_index.select_prs_to_scan(branch_index, local_name, branch_prs, args.force)
            if to_scan:
                ok = git_fetcher.prepare_refs(
                    repos_dir / local_name,
                    _repo_spec(local_name),
                    [pr["base_ref"] for pr in to_scan],
                    [pr["number"] for pr in to_scan],
                    args.verbose,
                )
                if not ok:
                    print(f"    ✗ {local_name}: bare repo unusable, skipping")
                    continue

            created, updated, unchanged, deleted, _, new_modules = (
                pr_index.build_pr_index_for_repo_branch(
                    repos_dir, local_name, branch_prs, branch_index, args.verbose
                )
            )
            previous = pr_index.repo_modules(branch_index, local_name)
            pr_index.replace_repo_modules(branch_index, previous, new_modules)

            result["created"] += created
            result["updated"] += updated
            result["unchanged"] += unchanged
            result["deleted"] += deleted
            for doc_id in created + updated + (unchanged if args.force else []):
                result["records"][doc_id] = new_modules[doc_id]
            print(
                f"    {local_name}: +{len(created)} ~{len(updated)}"
                f" ={len(unchanged)} -{len(deleted)}"
            )

    # The PR index is incremental state — always write it.
    write_json(index_file, branch_index)
    if args.stdout:
        _emit_stdout(result)
    else:
        write_json(output_dir / f"pr-changes-{args.branch}.json", result)
    return 0


def _cmd_facts(args: argparse.Namespace) -> int:
    if not args.repo.is_dir():
        print(f"✗ repo checkout not found: {args.repo}", file=sys.stderr)
        return 1

    out_dir = args.output or default_output_dir() / "facts" / args.repo.resolve().name
    sections = "all" if "all" in (args.sections or []) else args.sections
    index = extract_repo(args.repo, out_dir, sections=sections)
    print(f"extracted {len(index['addons'])} addons -> {out_dir}/")

    if args.yaml:
        import yaml

        docs = Path(out_dir) / "yaml"
        docs.mkdir(parents=True, exist_ok=True)
        (docs / "index.yaml").write_text(
            yaml.safe_dump(index, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )
        for name in index["addons"]:
            (docs / f"{name}.yaml").write_text(
                yaml.safe_dump(
                    load_json(Path(out_dir) / f"{name}.json"),
                    sort_keys=False,
                    allow_unicode=True,
                ),
                encoding="utf-8",
            )
        print(f"yaml -> {docs}/")
    if index["failures"]:
        print("failures:")
        for failure in index["failures"]:
            print(f"  {failure['addon']}: {failure['error']}")
    print("\nper-addon summary:")
    for name, entry in index["addons"].items():
        counts = entry["counts"]
        flags = ",".join(entry["features"]) or "-"
        print(
            f"  {name}: {len(entry['inherits'])} inherits, "
            f"{len(entry['new_models'])} new models, "
            f"{counts['xml_records']} records, [{flags}]"
        )
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
        "--repos-dir", type=Path, default=None, help="repo checkouts (default: <state-dir>/repos)"
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
        "--print-repos", action="store_true", help="emit the resolved repo list as JSON and exit"
    )
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=_cmd_sync)

    # scan ------------------------------------------------------------------
    p = sub.add_parser("scan", help="build module index + changes JSON from checkouts")
    p.add_argument(
        "--repos-dir", type=Path, default=None, help="repo checkouts (default: <state-dir>/repos)"
    )
    p.add_argument(
        "--branch",
        nargs="*",
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
        "--metrics", action="store_true", help="emit radon/lizard code metrics (experimental)"
    )
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=_cmd_scan)

    # pr-scan ---------------------------------------------------------------
    p = sub.add_parser("pr-scan", help="scan PR head modules for a prepared PR list")
    p.add_argument(
        "--repos-dir", type=Path, default=None, help="repo checkouts (default: <state-dir>/repos)"
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
    p.add_argument("--output", type=Path, help="output dir (default: <output>/facts/<repo>)")
    p.add_argument(
        "--sections",
        nargs="+",
        choices=(*DETAIL_SECTIONS, "all"),
        default=None,
        help="opt-in detail sections: fields methods access tests (or 'all')",
    )
    p.add_argument(
        "--yaml", action="store_true", help="also emit a YAML doc set under <output>/yaml"
    )
    p.set_defaults(func=_cmd_facts)

    args = parser.parse_args(argv)
    result: int = args.func(args)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
