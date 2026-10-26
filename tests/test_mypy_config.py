"""Configuration mypy (issue #173) : la liste des modules typés ne recule pas."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parent.parent
CONFIG = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["mypy"]

# Modules vérifiés à 0 erreur au moment de l'issue #173. On peut en ajouter
# dans pyproject.toml ; en retirer un fait échouer ce test.
MODULES_TYPES_MINIMUM = {
    "gcm4_core.py",
    "logging_config.py",
    "models.py",
    "master_password_core.py",
    "folder_context_menu_core.py",
    "gesture_trigger_core.py",
    "inline_editor_core.py",
    "netmiko_bulk_core.py",
    "popup_menu_core.py",
    "putty_import_core.py",
    "snmp_bulk_core.py",
    "tab_context_menu_core.py",
    "plugins/plugin_base.py",
    "plugins/ssh/core.py",
}


class TestConfigMypy(unittest.TestCase):
    """Réglages et périmètre de [tool.mypy]."""

    def test_reglages(self):
        """Options demandées par l'issue."""
        self.assertTrue(CONFIG["python_version"])
        for option in ("ignore_missing_imports", "warn_unused_ignores", "warn_redundant_casts"):
            self.assertIs(CONFIG.get(option), True, option)

    def test_liste_des_modules_ne_recule_pas(self):
        """Tous les modules de référence restent vérifiés."""
        self.assertLessEqual(MODULES_TYPES_MINIMUM, set(CONFIG["files"]))

    def test_modules_existent(self):
        """Chaque entrée de `files` existe."""
        for path in CONFIG["files"]:
            self.assertTrue((ROOT / path).is_file(), path)

    def test_tout_src_gcm4_hors_ui_est_type(self):
        """Chaque module de src/gcm4 hors ui/ est vérifié par mypy (oubli du lot 1b)."""
        attendus = {
            p.relative_to(ROOT).as_posix()
            for p in (ROOT / "src" / "gcm4").rglob("*.py")
            if "ui" not in p.relative_to(ROOT / "src" / "gcm4").parts
        }
        self.assertEqual(sorted(attendus - set(CONFIG["files"])), [])

    def test_modules_types_sans_gtk(self):
        """Les modules typés aujourd'hui n'importent pas GTK (PyGObject n'a pas de types)."""
        for path in CONFIG["files"]:
            source = (ROOT / path).read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"^\\s*(from gi|import gi)\\b", source, re.M), path)

    def test_job_ci(self):
        """Job « Types (mypy) » qui lance `uv run mypy`."""
        ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("name: Types (mypy)", ci)
        self.assertIn("run: uv run mypy", ci)


if __name__ == "__main__":
    unittest.main()
