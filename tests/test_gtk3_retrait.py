# SPDX-License-Identifier: GPL-3.0-or-later
"""Garde-fou du retrait de GTK3 (issues #178/#177/#182) : zéro fichier GTK3.

L'inventaire versionné de la transition (``tests/gtk3_inventaire.txt``) a
été supprimé avec le dernier code GTK3 (#182) : le garde-fou ne peut plus
qu'échouer si du GTK3 revient.

Détection par AST, sans importer les modules :

- **explicite** : un ``require_version`` Gtk/Gdk en 3.0 ou Vte en 2.91
  (littéraux construits ci-dessous, pour que le critère d'acceptation de
  #182 — aucun require_version Gtk 3.0 dans le dépôt — passe aussi sur
  ce fichier) ;
- **implicite** : ``from gi.repository import Gtk|Gdk|Vte`` hors
  ``if TYPE_CHECKING:``, sans ``require_version("Gtk", "4.0")`` dans le
  fichier ni dans un fichier du même dossier ou d'un dossier parent (hors
  racine). Exemple : ``plugins/pluginvnc2/tests/*.py`` héritent du
  ``require_version("Gtk", "4.0")`` de leur ``conftest.py``.

Sous-projets tiers exclus : ``SSH-Studio/``.
"""

from __future__ import annotations

import ast
import subprocess
import warnings
from functools import cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCLUS = ("SSH-Studio/",)
VERSIONS_GTK3 = {("Gtk", "3.0"), ("Gdk", "3.0"), ("Vte", "2.91")}
MODULES_GTK = {"Gtk", "Gdk", "Vte"}


def _fichiers_python() -> list[str]:
    sortie = subprocess.run(
        ["git", "ls-files", "*.py"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    return sorted(f for f in sortie if not f.startswith(EXCLUS) and (ROOT / f).is_file())


def _sous_type_checking(parents: list[ast.AST]) -> bool:
    for p in parents:
        if isinstance(p, ast.If):
            t = p.test
            if (isinstance(t, ast.Name) and t.id == "TYPE_CHECKING") or (
                isinstance(t, ast.Attribute) and t.attr == "TYPE_CHECKING"
            ):
                return True
    return False


@cache
def _analyse(rel: str) -> tuple[frozenset[tuple[str, str]], bool]:
    """(versions demandées par require_version, import Gtk/Gdk/Vte effectif)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)  # échappements des anciens fichiers GTK3
        arbre = ast.parse((ROOT / rel).read_text(encoding="utf-8"), filename=rel)
    versions: set[tuple[str, str]] = set()
    importe = False

    def visiter(noeud: ast.AST, parents: list[ast.AST]) -> None:
        nonlocal importe
        if isinstance(noeud, ast.Call):
            f = noeud.func
            nom = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
            args = noeud.args[:2]
            if (
                nom == "require_version"
                and len(args) == 2
                and all(isinstance(a, ast.Constant) for a in args)
            ):
                versions.add((args[0].value, args[1].value))
        if (
            isinstance(noeud, ast.ImportFrom)
            and noeud.module == "gi.repository"
            and {a.name for a in noeud.names} & MODULES_GTK
            and not _sous_type_checking(parents)
        ):
            importe = True
        for enfant in ast.iter_child_nodes(noeud):
            visiter(enfant, [*parents, noeud])

    visiter(arbre, [])
    return frozenset(versions), importe


def _dossiers_gtk4(fichiers: list[str]) -> set[Path]:
    return {Path(f).parent for f in fichiers if ("Gtk", "4.0") in _analyse(f)[0]}


def est_gtk3(rel: str, dossiers_gtk4: set[Path]) -> bool:
    """Vrai si ``rel`` est un fichier GTK3 (règle du docstring du module)."""
    versions, importe = _analyse(rel)
    if versions & VERSIONS_GTK3:
        return True
    if not importe or ("Gtk", "4.0") in versions:
        return False
    dossier = Path(rel).parent
    while dossier != Path("."):
        if dossier in dossiers_gtk4:
            return False
        dossier = dossier.parent
    return True


def fichiers_gtk3() -> list[str]:
    """Fichiers Python suivis par git et encore GTK3, triés."""
    fichiers = _fichiers_python()
    gtk4 = _dossiers_gtk4(fichiers)
    return [f for f in fichiers if est_gtk3(f, gtk4)]


def test_aucun_fichier_gtk3():
    """GTK3 a disparu du dépôt (#182) : plus aucun fichier Python GTK3."""
    assert fichiers_gtk3() == [], (
        "du code GTK3 est réapparu : écrire en GTK4 "
        "(require_version('Gtk', '4.0')), jamais en GTK3 (#178)."
    )


# --- La détection elle-même --------------------------------------------------


def _classe(tmp_path, fichiers: dict[str, str]) -> list[str]:
    for rel, src in fichiers.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(src, encoding="utf-8")
    globals()["ROOT"] = tmp_path
    _analyse.cache_clear()
    try:
        noms = sorted(fichiers)
        gtk4 = _dossiers_gtk4(noms)
        return [f for f in noms if est_gtk3(f, gtk4)]
    finally:
        globals()["ROOT"] = Path(__file__).resolve().parents[1]
        _analyse.cache_clear()


def test_detection(tmp_path):
    """GTK3 explicite et implicite détectés ; GTK4, héritage de dossier et TYPE_CHECKING ignorés."""
    # Versions construites : aucun littéral require_version GTK3 dans le dépôt.
    gtk3 = ".".join(("3", "0"))
    vte291 = ".".join(("2", "91"))
    g3 = f'import gi\ngi.require_version("Gtk", "{gtk3}")\nfrom gi.repository import Gtk\n'
    g4 = 'import gi\ngi.require_version("Gtk", "4.0")\nfrom gi.repository import Gtk\n'
    implicite = "from gi.repository import Gtk\n"
    resultat = _classe(
        tmp_path,
        {
            "app3.py": g3,
            "vte.py": f'import gi\ngi.require_version("Vte", "{vte291}")\n',
            "app4.py": g4,
            "plugins/p.py": implicite,
            "plugins/x4/vue.py": g4,
            "plugins/x4/tests/t.py": "def f():\n    from gi.repository import Gdk\n",
            "typage.py": "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from gi.repository import Gtk\n",
            "glib.py": "from gi.repository import GLib\n",
        },
    )
    assert resultat == ["app3.py", "plugins/p.py", "vte.py"]
