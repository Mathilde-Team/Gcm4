# SPDX-License-Identifier: GPL-3.0-or-later
"""Verrou : gestion des dépendances migrée de Poetry vers uv (session-36).

Contexte : `pyproject.toml` utilisait `[build-system]`/`[tool.poetry]` avec
`package-mode = false` (Poetry) — remplacé par `[tool.uv] package = false`
(uv), les dépendances de développement passant de
`[tool.poetry.group.dev.dependencies]` à `[dependency-groups] dev` (PEP
735). `poetry.lock`/`poetry.toml` supprimés, `uv.lock` généré. Voir
`docs/sessions/session-36.md`.

Comme `tests/test_ruff_config.py`, ces tests lisent uniquement la
*configuration* (fichiers du dépôt), sans invoquer `uv`/`flake8` en
sous-processus — aucun test de ce dépôt ne dépend d'un outil externe
installé.
"""

import os
import unittest

import tomllib

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYPROJECT_FILE = os.path.join(REPO_ROOT, "pyproject.toml")
FLAKE8_FILE = os.path.join(REPO_ROOT, ".flake8")


def _load_pyproject() -> dict:
    """Charge `pyproject.toml` entier.

    Returns:
        dict: Contenu TOML parsé.
    """
    with open(PYPROJECT_FILE, "rb") as fh:
        return tomllib.load(fh)


class TestUvMigration(unittest.TestCase):
    """Verrouille la migration Poetry → uv de `pyproject.toml`."""

    def test_projet_empaquete_par_hatchling(self):
        """Depuis #133, le projet est empaqueté (hatchling) : plus de ``package = false``."""
        data = _load_pyproject()
        self.assertIsNot(data.get("tool", {}).get("uv", {}).get("package"), False)
        self.assertEqual(data["build-system"]["build-backend"], "hatchling.build")

    def test_no_leftover_poetry_section(self):
        """`[tool.poetry]` n'existe plus dans pyproject.toml (migration complète)."""
        data = _load_pyproject()
        self.assertNotIn("poetry", data.get("tool", {}))

    def test_dev_dependency_group_declared(self):
        """`[dependency-groups] dev` reprend ruff/black/pytest (ex `[tool.poetry.group.dev]`)."""
        data = _load_pyproject()
        dev_group = data.get("dependency-groups", {}).get("dev")
        self.assertIsNotNone(dev_group, "[dependency-groups] dev absent de pyproject.toml")
        joined = " ".join(dev_group).lower()
        for tool in ("ruff", "black", "pytest"):
            self.assertIn(tool, joined)

    def test_poetry_lock_files_removed(self):
        """`poetry.lock`/`poetry.toml` n'existent plus, remplacés par `uv.lock`."""
        self.assertFalse(os.path.exists(os.path.join(REPO_ROOT, "poetry.lock")))
        self.assertFalse(os.path.exists(os.path.join(REPO_ROOT, "poetry.toml")))

    def test_uv_lock_present(self):
        """`uv.lock` a bien été généré à la racine du dépôt."""
        self.assertTrue(os.path.exists(os.path.join(REPO_ROOT, "uv.lock")))


class TestFlake8Config(unittest.TestCase):
    """Verrouille la config flake8 ajoutée en session-36 (absente jusque-là)."""

    def test_flake8_file_present(self):
        """`.flake8` existe (absent avant session-36 : flake8 tournait avec ses défauts)."""
        self.assertTrue(os.path.exists(FLAKE8_FILE))

    def test_flake8_line_length_matches_ruff_black(self):
        """`max-line-length` de flake8 est aligné sur `line-length` de ruff/black (99)."""
        with open(FLAKE8_FILE, encoding="utf-8") as fh:
            content = fh.read()
        self.assertIn("max-line-length = 99", content)

        data = _load_pyproject()
        ruff_len = data.get("tool", {}).get("ruff", {}).get("line-length")
        black_len = data.get("tool", {}).get("black", {}).get("line-length")
        self.assertEqual(ruff_len, 99)
        self.assertEqual(black_len, 99)


if __name__ == "__main__":
    unittest.main()
