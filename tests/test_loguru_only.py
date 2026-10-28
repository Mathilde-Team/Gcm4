# SPDX-License-Identifier: GPL-3.0-or-later
"""Un seul système de journalisation : loguru (issue #170).

Analyse ``ast`` de tous les fichiers Python du dépôt (hors sous-projets
SSH-Studio/ et gtk-frdp/, qui ont leur propre outillage) : aucun
``import logging`` ni ``from logging import ...``, et aucun appel à
``logger.add(...)`` en dehors de ``logging_config.py`` et du point d'entrée
des outils autonomes de ``tools/`` (``_configure_logging``, appelée par
``main()`` et jamais à l'import).
"""

from __future__ import annotations

import ast
import subprocess
import unittest
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXCLUDED_DIRS = ("SSH-Studio/", "gtk-frdp/")
# Exclusions documentées (docs/journalisation.md) : aucune à ce jour.
ALLOWED_STDLIB_LOGGING: set[str] = set()
# Points d'entrée autonomes (lancés hors de l'application) qui configurent
# leur propre sortie console au démarrage de main(), jamais à l'import.
ALLOWED_SINK_FILES = {
    "logging_config.py",
    "tools/libvirt_inventory.py",
    "tools/ssh_deploy.py",
    "plugins/ssh/core.py",  # CLI `python -m plugins.ssh.core`
    "scripts/pr_coverage_comment.py",  # script de CI
    "scripts/i18n_report.py",  # rapport de traduction (CI, make i18n-report)
    "scripts/audit_debug_sorties.py",  # audit debug entrée/sortie (issue #171)
    "scripts/check_branch_protection.py",  # protection de dev (issue #161)
    "scripts/generate_class_diagram.py",  # diagramme de classes (issue #175)
}


def _python_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "*.py"], cwd=ROOT, capture_output=True, text=True, check=True
    )
    return [f for f in out.stdout.split() if not f.startswith(EXCLUDED_DIRS)]


def _parse(source: str) -> ast.Module:
    # Certains fichiers existants contiennent des séquences d'échappement
    # invalides dans des docstrings : sans effet sur ce contrôle.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        return ast.parse(source)


def _stdlib_logging_imports(source: str) -> list[int]:
    lines = []
    for node in ast.walk(_parse(source)):
        if (
            isinstance(node, ast.Import)
            and any(a.name == "logging" or a.name.startswith("logging.") for a in node.names)
            or isinstance(node, ast.ImportFrom)
            and node.module
            and node.module.split(".")[0] == "logging"
            and node.level == 0
        ):
            lines.append(node.lineno)
    return lines


def _sink_additions(source: str) -> list[int]:
    return [
        node.lineno
        for node in ast.walk(_parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "add"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id in {"logger", "app_logger", "log", "_logger", "_loguru_logger"}
    ]


class TestLoguruOnly(unittest.TestCase):
    """Garde-fous de l'issue #170."""

    def test_aucun_import_du_module_logging(self):
        """Aucun module du dépôt n'importe le module logging standard."""
        fautes = {}
        for f in _python_files():
            if f in ALLOWED_STDLIB_LOGGING:
                continue
            lines = _stdlib_logging_imports((ROOT / f).read_text(encoding="utf-8"))
            if lines:
                fautes[f] = lines
        self.assertEqual(
            fautes, {}, "utiliser loguru (from loguru import logger), voir issue #170"
        )

    def test_puits_ajoutes_a_un_seul_endroit(self):
        """logger.add() seulement dans logging_config.py et les points d'entrée autonomes."""
        fautes = {}
        for f in _python_files():
            if f in ALLOWED_SINK_FILES or f.startswith("tests/"):
                continue
            lines = _sink_additions((ROOT / f).read_text(encoding="utf-8"))
            if lines:
                fautes[f] = lines
        self.assertEqual(fautes, {}, "les puits loguru se configurent dans logging_config.py")

    def test_outils_autonomes_configurent_a_l_execution_seulement(self):
        """Les outils de tools/ configurent leur sortie dans main(), pas à l'import."""
        for f in ("tools/libvirt_inventory.py", "tools/ssh_deploy.py"):
            tree = ast.parse((ROOT / f).read_text(encoding="utf-8"))
            module_level_calls = [
                n
                for n in tree.body
                if isinstance(n, ast.Expr)
                and isinstance(n.value, ast.Call)
                and _sink_additions(ast.unparse(n))
            ]
            self.assertEqual(module_level_calls, [], f)
            main = next(
                n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main"
            )
            self.assertIn("_configure_logging", ast.unparse(main), f)

    def test_le_garde_fou_detecte_les_deux_formes(self):
        """Le détecteur voit import et from-import, et ignore les homonymes."""
        self.assertEqual(
            _stdlib_logging_imports("import os\nimport logging\nfrom logging import handlers\n"),
            [2, 3],
        )
        self.assertEqual(
            _stdlib_logging_imports("import logging_config\nfrom . import logging\n"), []
        )
        self.assertEqual(_sink_additions("logger.add(x)\nitems.add(y)\n"), [1])


if __name__ == "__main__":
    unittest.main()
