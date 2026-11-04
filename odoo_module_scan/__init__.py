"""odoo-module-scan — scan Odoo addon repos into JSON artifacts.

Clones/fetches git repos, scans local checkouts (manifests, READMEs,
code metrics, deterministic addon facts, PR-head records), and emits
index/changes/manifest JSON for a downstream pipeline step. No
database, no Cloudflare — local files only.
"""

from .api import alphabot, facts, pr_scan, scan, sync  # noqa: F401

__version__ = "0.1.0"
