# SPDX-License-Identifier: GPL-3.0-or-later
"""Pas de f-string autour ou dans `_()` / `N_()` (issue #165).

Une f-string est évaluée avant l'appel : `_()` reçoit le texte déjà
rempli, qui ne figure dans aucun catalogue, donc jamais traduit. De plus,
selon leur version, xgettext extrait ou ignore ces appels, si bien que
`scripts/i18n-update.sh --check` donnerait des résultats différents en
local et en CI. Forme attendue : `_("... {nom} ...").format(nom=valeur)`.
"""

from __future__ import annotations

import ast
import subprocess
import unittest
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXCLUDED = ("tests/", "tools/", "scripts/", "SSH-Studio/", "gtk-frdp/")


_GETTEXT = {"_", "N_", "ngettext", "pgettext"}


def _is_gettext(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _GETTEXT
    )


def _fstring_calls(source: str) -> list[int]:
    """Lignes des `_(f"...")` et des `_()` placés DANS une f-string.

    Les deux formes sont traitées différemment selon la version de
    xgettext (la seconde est ignorée par les versions antérieures à 0.23).
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        tree = ast.parse(source)
    lines = set()
    for node in ast.walk(tree):
        if _is_gettext(node) and any(isinstance(arg, ast.JoinedStr) for arg in node.args):
            lines.add(node.lineno)
        if isinstance(node, ast.JoinedStr):
            for value in node.values:
                if isinstance(value, ast.FormattedValue):
                    lines.update(c.lineno for c in ast.walk(value.value) if _is_gettext(c))
    return sorted(lines)


class TestPasDeFStringDansGettext(unittest.TestCase):
    """Garde-fou des chaînes traduisibles."""

    def test_sources_sans_fstring_traduite(self):
        """Ni `_(f"...")` ni `f"{_('...')}"` dans les sources scannées par xgettext."""
        files = subprocess.run(
            ["git", "ls-files", "*.py"], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout.split()
        fautes = {
            f: lines
            for f in files
            if not f.startswith(EXCLUDED)
            and (lines := _fstring_calls((ROOT / f).read_text(encoding="utf-8")))
        }
        self.assertEqual(fautes, {})

    def test_detecteur(self):
        """Le détecteur voit `_(f"...")` et ignore `_("...").format(...)`."""
        self.assertEqual(_fstring_calls('_(f"a {x}")\n_("a {x}").format(x=1)\nf"{x}"\n'), [1])
        self.assertEqual(_fstring_calls("x = 1\nf\"<b>{_('t')}</b>\"\n"), [2])


if __name__ == "__main__":
    unittest.main()
