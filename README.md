# odoo-module-scan

Scan Odoo addon repositories into JSON artifacts.

A Unix-style pipeline stage: network only for git/GitHub
(clone/fetch/discovery), then pure local-file scanning. It emits
index and change JSON to files or stdout, shaped for a downstream
step — e.g. uploading the records into a database. No database, no
Cloudflare, no orchestration policy: local files only.

## Install

```bash
pip install odoo-module-scan          # once published
pip install -e ./odoo-module-scan     # editable install of a checkout
```

Runtime deps: `git`, `requests`, `lizard`, `radon`, `rst2gfm`,
`docutils`. `GITHUB_TOKEN` (or `GH_TOKEN`) is honored for GitHub API
auth and HTTPS git credentials.

## CLI

```
odoo-module-scan sync   --repos-dir DIR [--branch 18.0 ...] [--repos ...]
                        [--extra-repos FILE] [--remove-stale]
                        [--output-dir DIR] [--state-dir DIR] [--print-repos] [-v]

odoo-module-scan scan   --repos-dir DIR --branch 18.0 [...]
                        [--output-dir DIR | --stdout] [--repos ...]
                        [--only-changed] [--metrics] [--state-dir DIR] [-v]

odoo-module-scan facts  REPO [--output DIR] [--sections ...] [--yaml]

odoo-module-scan pr-scan --repos-dir DIR --branch 18.0 --prs prs.json
                        [--output-dir DIR | --stdout] [--force]
                        [--state-dir DIR] [-v]
```

| command   | what it does |
|-----------|--------------|
| `sync`    | Resolve the repo list (OCA org via GitHub API + an extra-repos file), clone shallow, fetch, and checkout the requested branches into a flat `repos_dir`. `--print-repos` emits the resolved `[{name, url}]` list as JSON and exits. Writes `changed-<branch>.txt` lists of repos whose HEAD moved. |
| `scan`    | Walk checkouts, parse manifests and READMEs (RST→Markdown), compute code metrics and content hashes, and diff against the previous index. Emits `index-<branch>.json` and `changes-<branch>.json`. Incremental: modules whose last-touching commit is unchanged are reused without re-parsing. `--only-changed` exits 0/1 for CI gating. |
|  Deep deterministic static analysis of one repo checkout — see [facts output](#facts-output) below. Per-addon JSON (`<addon>.json` + `_index.json`) with models, inheritances, XML records by type, security declarations, i18n/tests, feature flags. `--yaml` also emits a YAML doc set under `<output>/yaml/`.| Deep deterministic static analysis of one repo checkout — see [facts output](#facts-output) below. Per-addon JSON (`<addon>.json` + `_index.json`) with models, inheritances, XML records by type, security declarations, i18n/tests, feature flags. |
| `pr-scan` | Scan PR head modules via `git show` (no worktree checkout) for a caller-prepared `prs.json`: `{"<local_name>": [{number, title, author, pr_state, head_sha, updated_at, url, base_ref, changed_files}, ...]}`. Shallow-fetches only the refs needed. Emits `pr-changes-<branch>.json`; `pr-index-<branch>.json` is the incremental state. |

### Output contract

- **Records** carry `repo`, `technical_name`, `version`, `manifest`,
  `readme`, `hash`, `last_commit_sha`, and code metrics
  (`lines_py/xml/js`, `py_functions`, `py_complexity`, `py_mi*`).
- **changes-<branch>.json** — `{metadata, new_modules[],
  updated_modules[], unchanged_modules[], deleted_modules[]}`.
- **pr-changes-<branch>.json** — `{branch, created[], updated[],
  unchanged[], deleted[], records{doc_id: record}}`.
- **`--stdout`** writes the changes document to stdout; all progress
  and diagnostics go to stderr, so the stream pipes cleanly:
  `odoo-module-scan scan --stdout | next-step`. Index files are
  incremental state and are always written.
- **State** — `--state-dir` (default `~/.cache/odoo-module-scan`)
  holds the repo-list cache; the previous `index-<branch>.json` /
  `pr-index-<branch>.json` in the output dir are the incremental
  state. `--repos-dir` and `--output-dir` default under the state dir.
- **Identity is not computed.** Records carry `repo` +
  `technical_name`; namespace/rename lineage is a downstream
  projection, not a scan concern.
- **`record_hook`** — `build_index(..., record_hook=fn)` invokes
  `fn(module_dict)` per record before change classification, so a
  consumer can attach fields (e.g. enrichment hashes) that
  participate in `updated_modules` detection.

### facts output

`facts` writes one `<addon>.json` per addon plus an `_index.json`
digest of the whole repo. Each addon document is a static inventory.
The example below shows `--sections all`; keys marked
`// --sections` are experimental detail and only emitted when
selected (`fields`, `methods`, `access`, `tests`):

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
new_models, inherits, features, counts}` — plus repo path and git
provenance (`commit`, `describe`) and a `failures` list for addons
that failed to extract.

The feature flags are derived from declared XML records
(`ir.cron`→`cron`, `res.groups`→`groups`, `ir.ui.view`→`views`,
`mail.template`→`mail_templates`, …) — "what does this addon wire
into the system", computed without an LLM.

## Python API

```python
from pathlib import Path
from odoo_module_scan import index, sync, pr_index, git_fetcher, module_facts

# clone/fetch/checkout a resolved repo list
sync.sync_for_branches(repos_dir, branches=["18.0"], specific_repos=None,
                       remove_stale=False, verbose=True,
                       extra_repos_file=Path("extra_repos.txt"))

# build index + changes for one branch (incremental via previous_index)
full_index, changes = index.build_index(
    repos_dir, "18.0", previous_index=prev_index,
    record_hook=attach_something_or_None,
)

# per-module primitives
from odoo_module_scan.modules import (
    get_manifest, read_readme, compute_code_metrics, compute_hash,
    iter_module_dir_candidates,
)

# deterministic per-addon facts
facts = module_facts.extract_addon(repo_path, "sale_order")
index_ = module_facts.extract_repo(repo_path, out_dir)

# PR record building (dicts over fetched refs; caller supplies PR lists)
to_scan = pr_index.select_prs_to_scan(branch_index, repo_name, prs, force=False)
created, updated, unchanged, deleted, handled, new_modules = (
    pr_index.build_pr_index_for_repo_branch(
        repos_dir, repo_name, prs, branch_index, verbose=True)
)

# repo-list resolution and git helpers
from odoo_module_scan.repos import load_repo_list, load_repo_specs, repo_local_name
from odoo_module_scan.github import fetch_oca_repos
from odoo_module_scan.git import run_git, git_output, git_url
from odoo_module_scan.branch import (
    checkout_branch, get_remote_branch_heads, iter_repos,
)
```

## Example pipeline

```bash
# nightly: refresh checkouts, rescan changed modules, stream changes
odoo-module-scan sync --repos-dir ~/repos --branch 18.0 19.0 \
    --extra-repos extra_repos.txt
odoo-module-scan scan --repos-dir ~/repos --branch 18.0 19.0 \
    --output-dir /var/lib/scan-out --stdout \
  | ./upload-index          # consumes changes JSON, upserts the store
```

Or in-process, when the consumer is Python (e.g. an upload script
running inside an Odoo instance — `pip install -e` in the same env):

```python
from pathlib import Path
from odoo_module_scan.index import build_index
from odoo_module_scan._util import load_json, write_json

state_file = Path("/var/lib/module-index/index-18.0.json")
prev = load_json(state_file) if state_file.is_file() else None

full_index, changes = build_index(
    Path("/opt/addons-src"), "18.0", previous_index=prev
)
write_json(state_file, full_index)  # incremental state is caller-owned

modules = full_index["modules"]
for name in changes["new_modules"] + changes["updated_modules"]:
    upsert_module(env, name, modules[name])   # dicts straight into ORM calls
for name in changes["deleted_modules"]:
    deactivate_module(env, name)
```

Tests: `pytest odoo_module_scan/tests`.
