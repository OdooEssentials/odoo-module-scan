"""Tests for build_index.py — focusing on the enrich_modules post-processing pass."""

from odoo_module_scan.index import enrich_modules


def _make_module(name: str, depends: list[str]) -> dict:
    """Helper: build a minimal module dict as produced by iter_modules."""
    return {
        "technical_name": name,
        "repo": "test-repo",
        "version": "18.0",
        "manifest": {"name": name, "depends": depends},
        "readme": "",
        "hash": "abc123",
    }


class TestEnrichModulesDependedOnBy:
    """depended_on_by is the only field computed by enrich_modules."""

    def test_no_deps_produces_empty_list(self):
        modules = {"mod_a": _make_module("mod_a", [])}
        enrich_modules(modules)
        assert modules["mod_a"]["depended_on_by"] == []

    def test_single_dependent(self):
        modules = {
            "mod_a": _make_module("mod_a", ["mod_b"]),
            "mod_b": _make_module("mod_b", []),
        }
        enrich_modules(modules)
        assert modules["mod_b"]["depended_on_by"] == ["mod_a"]
        assert modules["mod_a"]["depended_on_by"] == []

    def test_multiple_dependents(self):
        modules = {
            "mod_a": _make_module("mod_a", ["shared"]),
            "mod_c": _make_module("mod_c", ["shared"]),
            "shared": _make_module("shared", []),
        }
        enrich_modules(modules)
        assert sorted(modules["shared"]["depended_on_by"]) == ["mod_a", "mod_c"]

    def test_external_dep_ignored(self):
        """Dependencies not in the index (e.g. 'base') are simply skipped."""
        modules = {"mod_a": _make_module("mod_a", ["base", "mail"])}
        enrich_modules(modules)
        assert modules["mod_a"]["depended_on_by"] == []

    def test_empty_modules_dict(self):
        """enrich_modules on an empty dict should not raise."""
        enrich_modules({})

    def test_field_present_after_enrichment(self):
        modules = {"mod_a": _make_module("mod_a", [])}
        enrich_modules(modules)
        assert "depended_on_by" in modules["mod_a"]
