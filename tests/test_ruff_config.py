# SPDX-License-Identifier: GPL-3.0-or-later
"""Verrou : `pyproject.toml` exclut bien les dépôts vendorisés du lint ruff (session-35).

Contexte : `[tool.ruff] exclude` ne mentionnait ni `SSH-Studio/` (code source
amont vendoré comme base de `ssh_config_editor.py`/`ssh_key_manager_dialog.py`/
`key_picker_dialog.py`, cf. `docs/architecture.md` §1/§2.5) ni `gtk-frdp/`
(sous-module meson vendoré fournissant le typelib GtkFrdp pour RDP, cf.
`docs/architecture.md` §2.5/§8). `ruff check .` les scannait donc comme du
code GCM, gonflant le compte d'erreurs de 196 lignes qui ne concernent pas
ce dépôt (194 dans SSH-Studio, 2 dans gtk-frdp) — corrigé session-35, voir
`docs/sessions/session-35.md`.

Ce test verrouille uniquement la *configuration* (le fichier `pyproject.toml`
lui-même), sans invoquer `ruff` en sous-processus : aucun test de ce dépôt
ne dépend d'un outil externe installé (voir `tests/test_gtk4_core_rupture_points.py`
et consorts, qui scannent du texte source plutôt que d'exécuter des outils),
et `ruff` n'est pas une dépendance déclarée de `pyproject.toml` (outil de
développement, pas d'exécution).
"""

import os
import unittest
from pathlib import Path

import tomllib

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYPROJECT_FILE = os.path.join(REPO_ROOT, "pyproject.toml")


def _load_ruff_exclude() -> list[str]:
    """Charge la liste ``[tool.ruff] exclude`` depuis ``pyproject.toml``.

    Returns:
        list[str]: Motifs d'exclusion déclarés (chemins/globs), dans
        l'ordre du fichier.

    Raises:
        AssertionError: si la section ``[tool.ruff]`` ou sa clé ``exclude``
            est absente du fichier.
    """
    with open(PYPROJECT_FILE, "rb") as fh:
        data = tomllib.load(fh)
    ruff_cfg = data.get("tool", {}).get("ruff")
    assert ruff_cfg is not None, "section [tool.ruff] absente de pyproject.toml"
    exclude = ruff_cfg.get("exclude")
    assert exclude is not None, "[tool.ruff] n'a pas de clé exclude"
    return exclude


class TestRuffExcludesVendoredDirectories(unittest.TestCase):
    """Verrouille l'exclusion des répertoires vendorisés du lint ruff."""

    def test_ssh_studio_excluded(self):
        """`SSH-Studio/` (code amont vendoré) est exclu du lint ruff."""
        self.assertIn("SSH-Studio", _load_ruff_exclude())

    def test_gtk_frdp_retire(self):
        """`gtk-frdp/` est retiré (#71, remplacé par AsyncRdp) : plus rien à exclure."""
        self.assertFalse((Path(__file__).resolve().parents[1] / "gtk-frdp").exists())
        self.assertNotIn("gtk-frdp", _load_ruff_exclude())

    def test_own_source_not_excluded(self):
        """Le code GCM lui-même (plugins/, tests/) reste couvert, pas exclu par erreur."""
        exclude = _load_ruff_exclude()
        self.assertNotIn("plugins", exclude)
        self.assertNotIn("tests", exclude)

    def test_venv_excluded(self):
        """`.venv/` (environnement uv, session-36) est exclu du lint/format ruff.

        Un `[tool.ruff] exclude` personnalisé remplace entièrement la liste
        par défaut de ruff (qui exclut `.venv` nativement) au lieu de
        l'étendre. Sans cette entrée, `ruff check .`/`ruff format --check .`
        scannent aussi les paquets tiers installés dans `.venv/` une fois
        l'environnement créé (21419 fausses erreurs et 1122 fichiers de mise
        en forme en trop constatés en session-36, tous dans `.venv/`) — voir
        `docs/sessions/session-36.md`.
        """
        self.assertIn(".venv", _load_ruff_exclude())


if __name__ == "__main__":
    unittest.main()
