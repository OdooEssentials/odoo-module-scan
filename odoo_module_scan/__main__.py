"""Allow ``python -m odoo_module_scan``."""

from .cli import main

raise SystemExit(main())
