"""Tests for manifest parsing and README reading."""

from odoo_module_scan.modules import get_manifest, read_readme


class TestGetManifest:
    """Test manifest parsing."""

    def test_get_manifest_not_found(self, tmp_path):
        """Test returns None when manifest doesn't exist."""
        result = get_manifest(tmp_path)
        assert result is None

    def test_get_manifest_openerp(self, tmp_path):
        """Test parsing __openerp__.py files."""
        manifest_file = tmp_path / "__openerp__.py"
        manifest_file.write_text('{"name": "test", "version": "1.0"}')

        result = get_manifest(tmp_path)
        assert result == {"name": "test", "version": "1.0"}

    def test_get_manifest_manifest(self, tmp_path):
        """Test parsing __manifest__.py files."""
        manifest_file = tmp_path / "__manifest__.py"
        manifest_file.write_text('{"name": "test", "installable": True}')

        result = get_manifest(tmp_path)
        assert result == {"name": "test", "installable": True}

    def test_get_manifest_invalid_syntax(self, tmp_path):
        """Test handling invalid Python syntax."""
        manifest_file = tmp_path / "__manifest__.py"
        manifest_file.write_text("not valid python {]")

        result = get_manifest(tmp_path)
        assert result is None


class TestReadReadme:
    """Test README reading."""

    def test_read_readme_not_found(self, tmp_path):
        """Test returns empty string when README doesn't exist."""
        result = read_readme(tmp_path)
        assert result == ""

    def test_read_readme_success(self, tmp_path):
        """Test reading README.md content."""
        readme_file = tmp_path / "README.md"
        readme_file.write_text("# Test Module\n\nDescription here.")

        result = read_readme(tmp_path)
        assert result == "# Test Module\n\nDescription here."

    def test_read_readme_unicode(self, tmp_path):
        """Test handling unicode content."""
        readme_file = tmp_path / "README.md"
        readme_file.write_text("# Tëst Módülé\n", encoding="utf-8")

        result = read_readme(tmp_path)
        assert "Tëst" in result
