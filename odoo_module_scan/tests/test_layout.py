"""Tests for the shared repo-layout convention (addon_roots)."""

from odoo_module_scan.modules import addon_roots


class TestAddonRoots:
    def test_generic_repo_scans_root_and_addon_dirs(self):
        assert addon_roots("sale-workflow") == (".", "addons", "odoo/addons")

    def test_odoo_core_repo_skips_root(self):
        assert addon_roots("odoo__odoo") == ("odoo/addons", "addons")

    def test_non_odoo_owner_uses_generic_layout(self):
        assert addon_roots("dreispt__enterprise") == (".", "addons", "odoo/addons")
