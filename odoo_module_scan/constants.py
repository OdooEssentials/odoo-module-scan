"""Corpus defaults — overridable via function parameters."""

SUPPORTED_OCA_VERSIONS = ["20.0", "19.0", "18.0", "17.0", "16.0", "15.0", "14.0"]

SKIP_DIRS = {
    "env",
    "oca-port",
    "data",
    "scripts",
    "odoo-community.org",
    ".git",
    "oca-search-api",
    "node_modules",
}
NOT_ADDONS = {
    ".github",
    "ansible-odoo",
    "connector-magento-php-extension",
    "contribute-md-template",
    "maintainer-quality-tools",
    "maintainer-tools",
    "mirrors-flake8",
    "oca.recipe.odoo",
    "oca-addons-repo-template",
    "oca-ci",
    "oca-custom",
    "oca-decorators",
    "oca-github-bot",
    "oca-port",
    "oca-weblate-deployment",
    "OCB",
    "odoo-community.org",
    "odoo-module-migrator",
    "odoo-pre-commit-hooks",
    "odoo-sentinel",
    "odoo-sphinx-autodoc",
    "odoorpc",
    "OpenUpgrade",
    "openupgradelib",
    "pylint-odoo",
    "repo-maintainer",
    "repo-maintainer-conf",
}
