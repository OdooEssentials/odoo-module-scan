<h1 align="center">odoo-module-scan</h1>

<p align="center"><i>Scan and index Odoo community addon modules</i></p>

<p align="center">
  <a href="https://github.com/OdooEssentials/odoo-module-scan/releases/latest"><img src="https://img.shields.io/github/v/release/OdooEssentials/odoo-module-scan" alt="Latest release"></a>
  <a href="https://github.com/OdooEssentials/odoo-module-scan/actions/workflows/test.yml"><img src="https://github.com/OdooEssentials/odoo-module-scan/actions/workflows/test.yml/badge.svg" alt="Tests"></a>
  <img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License: MIT">
  <img src="https://img.shields.io/badge/python-%E2%89%A53.10-blue.svg" alt="Python ≥3.10">
  <a href="https://pre-commit.com"><img src="https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit&logoColor=white" alt="pre-commit"></a>
</p>

**Build and maintain an index of Odoo community addons — from the
OCA or any other source.** `odoo-module-scan` clones the repos,
scans every addon into structured JSON, and tells you exactly what
is new or changed on each re-run, so a scheduled incremental scan
costs seconds instead of a full re-crawl.

Use it to:

- **Feed a module catalog** — the diff lands as `changes-*.json`,
  ready for a database or search index.
- **Audit addons without running Odoo** — `facts` statically reports
  models, views, security rules, and tests. No LLM needed.
- **Review PRs at scale** — `pr-scan` inspects PR head modules via
  `git show`, no worktree checkouts.
- **Build pipelines** — every command emits JSON to files or stdout.
  Progress goes to stderr. Pipe it into whatever comes next.

It is a Unix-style stage: network only for git/GitHub, then pure
local-file work. No database, no framework, no orchestration policy.

## Install

```bash
pip install git+https://github.com/OdooEssentials/odoo-module-scan.git
pip install -e ./odoo-module-scan     # or editable, from a checkout
```

Runtime deps: `git`, `requests`, `lizard`, `radon`, `rst2gfm`,
`docutils`, `pyyaml`. `GITHUB_TOKEN` (or `GH_TOKEN`) is used for
GitHub API auth and HTTPS git credentials.

## Quickstart

```bash
# 1. fetch a couple of OCA repos at the 18.0 branch
odoo-module-scan sync --branch 18.0 \
    --repos OCA/sale-workflow OCA/stock-logistics-workflow

# 2. scan them - checkouts and artifacts land under the cache dir
odoo-module-scan scan --branch 18.0

# 3. look at what you got (default output: ~/.cache/odoo-module-scan/output)
jq '.metadata' ~/.cache/odoo-module-scan/output/changes-18.0.json   # diff counts: {"new_count": 3, ...}
jq -r '.modules | keys[]' ~/.cache/odoo-module-scan/output/index-18.0.json | head   # module names found
```

Where things land:

| directory | contents |
|-----------|----------|
| `--repos-dir` | the checkouts — one subdirectory per repo (e.g. `~/repos/sale-workflow`), populated by `sync`. Default `~/.cache/odoo-module-scan/repos` |
| `--output-dir` | the artifacts — `index-<branch>.json`, `changes-<branch>.json`. Default `~/.cache/odoo-module-scan/output` |
| `--state-dir` | the root both of the above derive from, plus the repo-list cache. Default `~/.cache/odoo-module-scan` (honors `$XDG_CACHE_HOME`) |

And the two key artifacts:

- **`index-18.0.json`** — the full record set. It doubles as last known
  state to be used by incremental scans:
  `scan` reuses unchanged records from it on the next run.
- **`changes-18.0.json`** — the diff (`new`, `updated`, `unchanged`,
  `deleted`) — the piece your pipeline consumes:

  ```jsonc
  {
    "metadata": {"new_count": 3, "updated_count": 2,
                 "unchanged_count": 41, "deleted_count": 0},
    "new_modules": ["sale_loyalty"],
    "updated_modules": ["sale_stock", "sale_order"],
    "unchanged_modules": ["sale", "sale_mrp", ...],
    "deleted_modules": []
  }
  ```

## Commands

```
odoo-module-scan sync   --repos-dir DIR [--branch 18.0 ...] [--repos ...]
                        [--extra-repos FILE] [--remove-stale]
                        [--output-dir DIR] [--state-dir DIR] [--print-repos] [-v]

odoo-module-scan scan   --repos-dir DIR --branch 18.0 [...]
                        [--output-dir DIR | --stdout] [--repos ...]
                        [--only-changed] [--metrics] [--state-dir DIR] [-v]

odoo-module-scan pr-scan --repos-dir DIR --branch 18.0 --prs prs.json
                        [--output-dir DIR | --stdout] [--force]
                        [--state-dir DIR] [-v]

odoo-module-scan facts  REPO [--output DIR] [--sections ...] [--yaml]
```

| command   | what it does |
|-----------|--------------|
| `sync`    | Clone, fetch, and checkout the repo list into a flat `repos_dir`. |
| `scan`    | Scan checkouts → `index-<branch>.json` + `changes-<branch>.json`. |
| `pr-scan` | Scan PR head modules → `pr-changes-<branch>.json`. |
| `facts`   | Static analysis of one repo → per-addon JSON + `_index.json`. |

### sync

Build the corpus. It resolves the repo list — the OCA org via the
GitHub API, plus an `--extra-repos` file for anything else — then
clones shallow and checks out each branch. Pass `--print-repos` to
see the resolved `[{name, url}]` JSON without cloning.

_Experimental — may change without notice:_ `changed-<branch>.txt`
(diff lists of repos whose HEAD moved) and `--remove-stale`.

### scan

The main event. `scan` walks every checkout, parses manifests and
READMEs (RST→Markdown), hashes the code, and diffs against the
previous index. Unchanged modules are reused without re-parsing —
only new work costs time. Output: `index-<branch>.json` (the full
record set) and `changes-<branch>.json` (the diff for your next
step). `--only-changed` exits 0 or 1 so CI can gate on it.

_Experimental — may change without notice:_ `--metrics` (radon/lizard
code metrics) — the `py_*`/`lines_*` fields it emits are not part of
the stable record contract.

### pr-scan

Feed it a JSON list of PRs and it scans their head modules straight
from git refs — no checkout needed. It shallow-fetches only what the
listed PRs touch, then emits `pr-changes-<branch>.json` with the
records a reviewer or indexer needs. `pr-index-<branch>.json` is the
incremental state; `--force` rescans everything.

### facts

A structural X-ray of one repo. For each addon you get a JSON
inventory: the models it defines and inherits, its views and data
files, security rules, translations, and tests — plus feature flags
that say what it wires into Odoo (`cron`, `views`, `record_rules`…).
See [facts output](#facts-output).

_Experimental — may change without notice:_ `--sections` detail
blocks (`fields`, `methods`, `access`, `tests`, `all`) — marked
`// --sections` in the schema — and the `--yaml` output format.

### Output contract

- **Records** carry `repo`, `technical_name`, `version`, `manifest`,
  `readme`, `hash`, `last_commit_sha`, and — with `--metrics` — code
  metrics (`lines_py/xml/js`, `py_functions`, `py_complexity`,
  `py_mi*`).
- **changes-<branch>.json** — `{metadata, new_modules[],
  updated_modules[], unchanged_modules[], deleted_modules[]}`.
- **pr-changes-<branch>.json** — `{branch, created[], updated[],
  unchanged[], deleted[], records{doc_id: record}}`.
- **`--stdout`** writes JSON to stdout; all progress goes to stderr,
  so `odoo-module-scan scan --stdout | next-step` pipes cleanly.
  Index files are always written — they are the incremental state.
- **State** lives under `--state-dir` (default
  `~/.cache/odoo-module-scan`): repo-list cache, checkouts, output.
- **No identity projection.** Records carry `repo` +
  `technical_name`; namespace and rename lineage are downstream
  concerns.
- **`record_hook`** — `build_index(..., record_hook=fn)` calls
  `fn(module_dict)` per record before change classification. Attach
  your own fields and they take part in `updated_modules` detection.

### facts output

`facts` writes one `<addon>.json` per addon plus `_index.json` for
the repo. The example below is `--sections all`; keys marked
`// --sections` appear only when selected:

```jsonc
{
  "format": "addon-facts/v1",
  "module": "sale_stock",          // addon technical name
  "repo": "stock-logistics-workflow",
  "manifest": { ... },             // allow-listed manifest keys only
  "readme": {                      // fragment inventory, not full text
    "readme_rst": true,
    "files": [{"file": "readme/USAGE.md", "lead": "first line"}]
  },
  "python": [{                     // one entry per .py file
    "file": "models/sale_order.py",
    "classes": [{
      "name": "SaleOrder",
      "bases": ["models.Model"],
      "fields": ["warehouse_id: fields.Many2one", ...],   // --sections fields
      "methods": ["action_confirm", "_prepare_line (@api.depends)"]  // --sections methods
    }],
    "imports": ["odoo.addons.stock"]
  }],
  "xml": [{                        // one entry per .xml file
    "file": "views/sale_views.xml",
    "records": 3, "templates": 0, "menuitems": 1,
    "models": ["ir.ui.view", "ir.ui.menu"], "noupdate": false
  }],
  "security": {
    "files": ["security/ir.model.access.csv"],
    "access_rules": [{"name", "model", "group"}]       // --sections access
  },
  "i18n": ["fr", "pt_BR"],         // .po stems
  "tests": {"files": [...],        // --sections tests
            "test_methods": 12},
  "derived": {
    "new_models": ["sale.stock.mixin"],      // _name on Model
    "transient_models": ["sale.stock.wizard"], // _name on TransientModel
    "inherits": ["sale.order"],              // _inherit targets
    "features": ["views", "record_rules", "cron"],  // declared Odoo features
    "record_models": ["ir.cron", "ir.ui.view"]     // models seen in XML
  },
  "counts": {"python_files": 4, "xml_files": 2, "xml_records": 3,
             "fields_defined": 27, "methods_defined": 19,
             "test_files": 1, "test_methods": 12}
}
```

`_index.json` keeps one digest per addon — `{installable, depends,
new_models, inherits, features, counts}` — plus git provenance and a
`failures` list for addons that failed to extract.

## Python API

Everything the CLI does is importable. The top-level contract is
one function per subcommand, with the same arguments:

| command | function | returns |
|---------|----------|---------|
| `sync` | `odoo_module_scan.sync(repos_dir, branches, repos, extra_repos, remove_stale, output_dir, state_dir, print_repos, verbose)` | `{"specs": [...]}` or `{"cloned", "updated", "failed", "changed_by_branch"}` |
| `scan` | `odoo_module_scan.scan(repos_dir, branches, repos, output_dir, metrics, state_dir, verbose)` | `{"indexes", "changes"}` (also writes `index-*`/`changes-*` files) |
| `facts` | `odoo_module_scan.facts(repo, output, sections, yaml_out, verbose)` | the `_index.json` digest dict |
| `pr-scan` | `odoo_module_scan.pr_scan(repos_dir, branch, prs, output_dir, force, state_dir, verbose)` | the `pr-changes` dict (also writes `pr-index-*`/`pr-changes-*` files) |

The Quickstart, in Python:

```python
from odoo_module_scan import scan, sync

# 1. same as `sync --branch 18.0 --repos ...` — default dirs
sync(branches=["18.0"], repos=["OCA/sale-workflow", "OCA/stock-logistics-workflow"])

# 2. same as `scan --branch 18.0` — writes the artifacts and
#    returns them: {"indexes": {branch: index}, "changes": {...}}
result = scan(branches=["18.0"])

# 3. consume the dicts directly — no file juggling needed
changes = result["changes"]["18.0"]
index_18 = result["indexes"]["18.0"]
```

The rest of the surface, by module:

| module | what it gives you |
|--------|-------------------|
| `odoo_module_scan.clone` | `sync_for_branches` — the `sync` command's engine |
| `odoo_module_scan.index` | `build_index`, `iter_modules`, `enrich_modules` — index + changes builders |
| `odoo_module_scan.modules` | `get_manifest`, `read_readme`, `compute_hash`, `compute_code_metrics`, `addon_roots` — per-module primitives |
| `odoo_module_scan.module_facts` | `extract_addon`, `extract_repo` — the `facts` engine |
| `odoo_module_scan.pr_index` | `select_prs_to_scan`, `build_pr_index_for_repo_branch` — PR record builders |
| `odoo_module_scan.git_fetcher` | `prepare_refs`, `ensure_bare_repo` — shallow ref fetching for PRs |
| `odoo_module_scan.repos` | `load_repo_list`, `load_repo_specs`, `repo_local_name` — repo-list resolution |
| `odoo_module_scan.github` | `fetch_oca_repos`, `github_api_*` — OCA repo discovery |
| `odoo_module_scan.git`, `.branch` | `run_git`, `git_output`, `checkout_branch`, `iter_repos` — git plumbing |
| `odoo_module_scan._util` | internal — `load_json`, `write_json`, `make_doc_id`, `default_*_dir`; not public contract |

## Example pipeline

```bash
# nightly: refresh checkouts, rescan changed modules, stream changes
odoo-module-scan sync --repos-dir ~/repos --branch 18.0 19.0 \
    --extra-repos extra_repos.txt
odoo-module-scan scan --repos-dir ~/repos --branch 18.0 19.0 \
    --output-dir /var/lib/scan-out --stdout \
  | ./upload-index          # consumes changes JSON, upserts the store
```

Or consume the dicts in-process — e.g. an upload script inside an
Odoo instance:

```python
from pathlib import Path
from odoo_module_scan.index import build_index
from odoo_module_scan._util import load_json, write_json

state_file = Path("/var/lib/module-index/index-18.0.json")
prev = load_json(state_file) if state_file.is_file() else None

full_index, changes = build_index(
    repos_dir=Path("/opt/addons-src"), version="18.0", previous_index=prev
)
write_json(state_file, full_index)  # incremental state is caller-owned

modules = full_index["modules"]
for name in changes["new_modules"] + changes["updated_modules"]:
    upsert_module(env, name, modules[name])   # dicts straight into ORM calls
for name in changes["deleted_modules"]:
    deactivate_module(env, name)
```

Tests: `pytest odoo_module_scan/tests`.
