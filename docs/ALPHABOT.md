# Alphabot — design

**Status:** accepted
**Date:** 2026-11-04
**Scope:** `odoo-module-scan` (new experimental subcommand)
**Related:** `docs/SCANNER_EXTRACTION.md` (package boundary — this stays pure git/GitHub)

## Context

OCA repos accumulate open PRs that sit unreviewed for months. Users who
want "the repo plus everything proposed" must merge PRs locally by hand.
An automated **preview branch** answers the question "what would 18.0
look like with all open PRs merged?" — unstable by definition, but
invaluable for feature discoverability, early conflict detection, and
evaluation.

## Decision

Add an experimental `alphabot` subcommand that, per repo + base branch:

1. Fetches all open PRs targeting the base branch (GitHub API,
   paginated — reuses `github.py` primitives).
2. Builds a `<branch>-preview` ref by merging each PR head onto the
   base tip, in PR-number order, with `git merge --no-ff -m
   "alphabot: merge PR #<n>"`.
3. **Never silently drops a conflict:** a PR that fails to merge is
   skipped, recorded in a `conflicts` list, and the preview continues
   with the remaining PRs.
4. Pushes the preview branch to a writable remote (see open question)
   and maintains a draft PR `<branch>-preview → <branch>` listing every
   merged/skipped/conflicting PR as the body — the human-readable
   "what's in this preview" document.
5. Idempotent: the preview ref is rebuilt from base tip each run
   (deterministic, no accumulated merge history); the draft PR is
   updated, not duplicated.

Everything else stays put: this is pure git + GitHub work, no DB, no
scanner-output coupling. It reads PR metadata but does not consume
`scan`/`facts` artifacts.

## CLI shape

```
odoo-module-scan alphabot --repos-dir DIR --branch 18.0 [--repos ...]
                          --remote <push-target>
                          [--dry-run] [--state-dir DIR] [-v]
```

- `--remote` — the writable push target (required; see open question).
- `--dry-run` — resolve PR lists and merge locally without pushing;
  emits the would-be preview summary as JSON on stdout.
- Experimental: marked "may change without notice" in README, same as
  `--metrics`/`--sections`.

## Output / artifacts

- `<repo>-<branch>-preview` branch on the push remote, rebuilt per run.
- Draft PR `preview: <branch> + N open PRs` with a body table:
  merged PRs, skipped (already merged/closed), conflicts (with the
  conflicting paths when available).
- Local state: `alphabot-<branch>.json` in the state dir — last built
  base SHA + per-PR merge outcome, so `--dry-run` and logs stay
  reproducible.

## Open questions — resolved 2026-11-04

- **Push target:** per-repo **forks under `github.com/dreispt`**
  (`dreispt/<repo>`). The fork must exist; alphabot adds/updates a
  `preview` remote pointing at it — no upstream writes, ever.
- **Merge location:** the `--repos-dir` checkouts maintained by
  `sync` — works both locally and inside a GitHub Action runner (the
  runner provides the same local worktree + `GITHUB_TOKEN`).
- Merge order: PR-number order (deterministic, explainable).

## Alternatives considered

- **Rebase instead of merge** — cleaner history, but conflict handling
  gets messy per-PR and the preview is ephemeral anyway; merges are
  honest about what conflicts with what.
- **Reuse `pr-scan`** — it scans *individual* PR heads into records;
  alphabot composes *all* heads into one tree. Different output
  (records vs a branch), different consumer.
- **Server-side GitHub merge refs** (`refs/pull/N/merge`) — only
  exists per-PR, not a composed branch; can't produce the preview.

## Tasks

- [ ] Decide the push-target contract (`--remote` semantics)
- [ ] `alphabot.py`: PR listing per repo/branch (`github.py`)
- [ ] Preview ref build: base tip + sequential `git merge` per PR head
      (reuse `git_fetcher`/`branch` primitives)
- [ ] Conflict capture: failed merges skipped + recorded with paths
- [ ] Push `<branch>-preview` + create/update draft PR (`github.py`)
- [ ] `alphabot` CLI subcommand (experimental-marked), `--dry-run`
- [ ] `alphabot-<branch>.json` state file
- [ ] Tests: preview merge composition, conflict skip, draft-PR body
- [ ] README: experimental section
