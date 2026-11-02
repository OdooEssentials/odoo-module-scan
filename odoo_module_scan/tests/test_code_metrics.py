"""Tests for compute_code_metrics."""

from odoo_module_scan.modules import compute_code_metrics

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_module(tmp_path, files: dict[str, str]):
    """Write a dict of {relative_path: content} into a fresh module dir."""
    module_dir = tmp_path / "test_module"
    module_dir.mkdir()
    for rel, content in files.items():
        dest = module_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
    return module_dir


# ---------------------------------------------------------------------------
# Empty / no code files
# ---------------------------------------------------------------------------


class TestEmpty:
    def test_no_code_files(self, tmp_path):
        module_dir = make_module(tmp_path, {})
        m = compute_code_metrics(module_dir)
        assert m == {
            "lines_py": 0,
            "lines_xml": 0,
            "lines_js": 0,
            "py_functions": 0,
            "py_complexity": 0,
            "py_mi": None,
            "py_mi_rank": None,
            "py_mi_explanation": None,
        }

    def test_only_manifest_skipped(self, tmp_path):
        """__manifest__.py is in _CODE_SKIP_FILES and must not be counted."""
        module_dir = make_module(
            tmp_path,
            {
                "__manifest__.py": "{'name': 'Test', 'version': '1.0', 'depends': ['base']}"
            },
        )
        m = compute_code_metrics(module_dir)
        assert m["lines_py"] == 0
        assert m["py_functions"] == 0


# ---------------------------------------------------------------------------
# Python metrics
# ---------------------------------------------------------------------------


SIMPLE_PY = """\
def greet(name):
    return f"Hello, {name}"


def add(a, b):
    return a + b
"""

BRANCHY_PY = """\
def classify(x):
    if x > 100:
        return "high"
    elif x > 50:
        return "medium"
    elif x > 0:
        return "low"
    else:
        return "zero"
"""


class TestPythonMetrics:
    def test_lines_py_counted(self, tmp_path):
        module_dir = make_module(tmp_path, {"models/foo.py": SIMPLE_PY})
        m = compute_code_metrics(module_dir)
        assert m["lines_py"] > 0
        assert m["lines_xml"] == 0
        assert m["lines_js"] == 0

    def test_py_functions_count(self, tmp_path):
        module_dir = make_module(tmp_path, {"models/foo.py": SIMPLE_PY})
        m = compute_code_metrics(module_dir)
        assert m["py_functions"] == 2

    def test_py_complexity_simple(self, tmp_path):
        """Two straight-line functions → complexity 1 each → sum 2."""
        module_dir = make_module(tmp_path, {"models/foo.py": SIMPLE_PY})
        m = compute_code_metrics(module_dir)
        assert m["py_complexity"] == 2

    def test_py_complexity_branches(self, tmp_path):
        """4-branch if/elif/else → complexity > 1."""
        module_dir = make_module(tmp_path, {"models/foo.py": BRANCHY_PY})
        m = compute_code_metrics(module_dir)
        assert m["py_complexity"] > 1

    def test_py_mi_present_when_py_exists(self, tmp_path):
        module_dir = make_module(tmp_path, {"models/foo.py": SIMPLE_PY})
        m = compute_code_metrics(module_dir)
        assert m["py_mi"] is not None
        assert 0.0 <= m["py_mi"] <= 100.0
        assert m["py_mi_rank"] in ("A", "B", "C")
        assert m["py_mi_explanation"] is not None
        assert "MI" in m["py_mi_explanation"]

    def test_py_mi_none_when_no_py(self, tmp_path):
        module_dir = make_module(tmp_path, {"views/foo.xml": "<odoo/>"})
        m = compute_code_metrics(module_dir)
        assert m["py_mi"] is None
        assert m["py_mi_rank"] is None

    def test_multiple_py_files_aggregated(self, tmp_path):
        module_dir = make_module(
            tmp_path,
            {
                "models/a.py": SIMPLE_PY,
                "models/b.py": BRANCHY_PY,
            },
        )
        m = compute_code_metrics(module_dir)
        assert m["py_functions"] == 3  # 2 + 1

    def test_unparseable_py_leaves_mi_unset(self, tmp_path):
        """Regression: a module whose Python source cannot be ast-parsed
        (e.g. grammar newer than the runner, such as PEP 701 nested-quote
        f-strings on Python <3.12) must not abort the index build — the
        module is still indexed, only the MI metric stays unset."""
        module_dir = make_module(
            tmp_path,
            {
                "models/foo.py": SIMPLE_PY,
                "models/broken.py": "x = (\n",
            },
        )
        m = compute_code_metrics(module_dir)
        assert m["lines_py"] > 0
        assert m["py_mi"] is None
        assert m["py_mi_rank"] is None
        assert m["py_mi_explanation"] is None


# ---------------------------------------------------------------------------
# XML metrics
# ---------------------------------------------------------------------------


SIMPLE_XML = """\
<?xml version="1.0"?>
<odoo>
    <record id="view_form" model="ir.ui.view">
        <field name="name">sale.order.form</field>
        <field name="model">sale.order</field>
    </record>
</odoo>
"""


class TestXmlMetrics:
    def test_lines_xml_counted(self, tmp_path):
        module_dir = make_module(tmp_path, {"views/views.xml": SIMPLE_XML})
        m = compute_code_metrics(module_dir)
        assert m["lines_xml"] > 0
        assert m["lines_py"] == 0
        assert m["lines_js"] == 0


# ---------------------------------------------------------------------------
# JS metrics
# ---------------------------------------------------------------------------


SIMPLE_JS = """\
/** @odoo-module **/
import { Component } from "@odoo/owl";

export class MyWidget extends Component {
    setup() {
        this.value = 42;
    }
}
"""


class TestJsMetrics:
    def test_lines_js_counted(self, tmp_path):
        module_dir = make_module(tmp_path, {"static/src/js/widget.js": SIMPLE_JS})
        m = compute_code_metrics(module_dir)
        # static/ is in _CODE_SKIP_DIRS → should be excluded
        assert m["lines_js"] == 0

    def test_lines_js_outside_static(self, tmp_path):
        module_dir = make_module(tmp_path, {"js/widget.js": SIMPLE_JS})
        m = compute_code_metrics(module_dir)
        assert m["lines_js"] > 0


# ---------------------------------------------------------------------------
# Skip rules
# ---------------------------------------------------------------------------


class TestSkipRules:
    def test_tests_dir_skipped(self, tmp_path):
        module_dir = make_module(tmp_path, {"tests/test_foo.py": SIMPLE_PY})
        m = compute_code_metrics(module_dir)
        assert m["lines_py"] == 0
        assert m["py_functions"] == 0

    def test_migrations_dir_skipped(self, tmp_path):
        module_dir = make_module(tmp_path, {"migrations/1.0.py": SIMPLE_PY})
        m = compute_code_metrics(module_dir)
        assert m["lines_py"] == 0

    def test_init_skipped(self, tmp_path):
        module_dir = make_module(tmp_path, {"__init__.py": "from . import models\n"})
        m = compute_code_metrics(module_dir)
        assert m["lines_py"] == 0
