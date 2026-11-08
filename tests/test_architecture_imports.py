# SPDX-License-Identifier: GPL-3.0-or-later
"""Contrats d'architecture des imports (issue #174), par analyse AST.

Le cœur GTK3 ayant été supprimé (#182), ces règles s'appliquent au code
vivant : ``src/gcm4`` et les plugins GTK4 :

- les modules cœur (sans GTK) n'importent pas ``gi``, ni au niveau module
  ni dans une fonction ;
- aucun plugin n'importe un autre plugin ;
- ``plugins/*/core.py`` n'importe pas ``gi`` ;
- le cœur n'importe aucun plugin nommé ;
- dans ``src/gcm4/core/`` et ``src/gcm4/ui/``, aucun nom de protocole en
  littéral, hors exceptions justifiées : le cœur passe par le registre de
  plugins.
"""

from __future__ import annotations

import ast
import tempfile
import unittest
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGINS = ROOT / "plugins"

# Modules sans GTK : logique pure, testable sans affichage.
MODULES_COEUR = sorted(
    p.relative_to(ROOT).as_posix() for p in (ROOT / "src" / "gcm4" / "core").glob("*.py")
)
NOMS_PLUGINS = sorted(p.parent.name for p in PLUGINS.glob("*/core.py"))
CORES_DE_PLUGINS = sorted(p.relative_to(ROOT).as_posix() for p in PLUGINS.glob("*/core.py"))
PROTOCOLES = {"ssh", "telnet", "rdp", "vnc", "spice", "serial", "local", "web", "ipmisol", "ipmi"}

# Littéraux de protocole tolérés dans src/gcm4 : (fichier relatif, littéral) -> justification.
EXCEPTIONS_PROTOCOLES: dict[tuple[str, str], str] = {}


def _parse(path: Path) -> ast.Module:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        return ast.parse(path.read_text(encoding="utf-8"))


def modules_importes(path: Path) -> list[tuple[int, str]]:
    """(ligne, module) de chaque import, y compris ``from x import y`` -> ``x.y``.

    Args:
        path: Fichier Python.

    Returns:
        list[tuple[int, str]]: Imports absolus du fichier (les relatifs sont
        préfixés par des points).
    """
    found = []
    for node in ast.walk(_parse(path)):
        if isinstance(node, ast.Import):
            found += [(node.lineno, a.name) for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = "." * node.level + (node.module or "")
            found.append((node.lineno, base))
            found += [
                (node.lineno, f"{base}.{a.name}".lstrip(".") if base else a.name)
                for a in node.names
            ]
    return found


def plugin_vise(module: str) -> str | None:
    """Nom du plugin visé par un import (``ssh``, ``plugins.ssh.core``...)."""
    for part in module.lstrip(".").split("."):
        if part in NOMS_PLUGINS:
            return part
    return None


def _sous_type_checking(path: Path, lineno: int) -> bool:
    """Vrai si la ligne ``lineno`` est dans un bloc ``if TYPE_CHECKING:``."""
    for node in ast.walk(_parse(path)):
        if not isinstance(node, ast.If):
            continue
        if (
            isinstance(node.test, ast.Name)
            and node.test.id == "TYPE_CHECKING"
            and node.lineno
            <= lineno
            <= max(getattr(s, "end_lineno", node.lineno) for s in ast.walk(node))
        ):
            return True
    return False


class TestContratsImports(unittest.TestCase):
    """Règles de couches sur le code vivant (src/gcm4 + plugins GTK4)."""

    def test_perimetre_non_vide(self):
        """Garde-fou : les globs trouvent bien des modules."""
        self.assertGreaterEqual(len(MODULES_COEUR), 9)
        # Les plugins GTK4 sont des dossiers (telnet, local, ipmisol,
        # serial, web, rdp, ssh...) ; les plugin_*.py GTK3 ont été
        # supprimés (#179, #182).
        self.assertGreaterEqual(len(NOMS_PLUGINS), 3)
        self.assertIn("plugins/ssh/core.py", CORES_DE_PLUGINS)
        self.assertIn("plugins/spice/core.py", CORES_DE_PLUGINS)  # portage #179
        # Cœurs des outils rangés dans leur plugin (#427, lot 3).
        for coeur in (
            "plugins/import_hyperviseurs/core.py",
            "plugins/import_putty/core.py",
            "plugins/netmiko_push/core.py",
            "plugins/snmp_push/core.py",
        ):
            self.assertIn(coeur, CORES_DE_PLUGINS)

    def test_coeur_sans_gtk(self):
        """Une PR qui fait importer GTK au cœur échoue (typage excepté)."""
        fautes = [
            f"{nom}:{ligne} {mod}"
            for nom in MODULES_COEUR + CORES_DE_PLUGINS
            for ligne, mod in modules_importes(ROOT / nom)
            if mod.split(".")[0] == "gi" and not _sous_type_checking(ROOT / nom, ligne)
        ]
        self.assertEqual(
            fautes, [], "le cœur ne dépend pas de GTK : passer par l'interface ou un rappel"
        )

    def test_coeur_sans_plugin_nomme(self):
        """Le cœur découvre les plugins par le registre, jamais par leur nom."""
        fautes = [
            f"{nom}:{ligne} {mod}"
            for nom in MODULES_COEUR
            for ligne, mod in modules_importes(ROOT / nom)
            if plugin_vise(mod)
        ]
        self.assertEqual(fautes, [])

    def test_aucun_plugin_n_importe_un_autre_plugin(self):
        """Un plugin n'importe que lui-même (dossier du plugin ou cœur partagé)."""
        fautes = []
        for nom in NOMS_PLUGINS:
            for fichier in sorted((PLUGINS / nom).glob("*.py")):
                for ligne, mod in modules_importes(fichier):
                    vise = plugin_vise(mod)
                    if vise and vise != nom:
                        fautes.append(f"plugins/{nom}/{fichier.name}:{ligne} {mod}")
        self.assertEqual(fautes, [], "partager le code via le cœur gcm4, pas entre plugins")

    def test_aucun_protocole_en_litteral_dans_src_gcm4(self):
        """Cœur et interface du futur paquet : pas de "ssh"/"rdp"... en dur."""
        racines = [ROOT / "src" / "gcm4" / "core", ROOT / "src" / "gcm4" / "ui"]
        # src/gcm4 existe depuis #133 : un dossier manquant (renommage,
        # déplacement) ferait passer le test à vide, il doit échouer.
        for racine in racines:
            self.assertTrue(racine.is_dir(), f"{racine} absent")
        fautes = []
        for racine in racines:
            for path in sorted(racine.rglob("*.py")):
                rel = path.relative_to(ROOT).as_posix()
                tree = _parse(path)
                docstrings = {
                    id(n.body[0].value)
                    for n in ast.walk(tree)
                    if isinstance(
                        n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
                    )
                    and n.body
                    and isinstance(n.body[0], ast.Expr)
                    and isinstance(n.body[0].value, ast.Constant)
                }
                for node in ast.walk(tree):
                    if (
                        isinstance(node, ast.Constant)
                        and isinstance(node.value, str)
                        and node.value.lower() in PROTOCOLES
                        and id(node) not in docstrings
                        and (rel, node.value.lower()) not in EXCEPTIONS_PROTOCOLES
                    ):
                        fautes.append(f"{rel}:{node.lineno} {node.value!r}")
        self.assertEqual(
            fautes, [], "passer par le registre de plugins ou justifier dans EXCEPTIONS_PROTOCOLES"
        )


class TestDetecteurs(unittest.TestCase):
    """Les détecteurs eux-mêmes."""

    def test_plugin_vise(self):
        """Formes d'import reconnues."""
        self.assertEqual(plugin_vise("ssh"), "ssh")
        self.assertEqual(plugin_vise("plugins.ssh.core"), "ssh")
        self.assertEqual(plugin_vise("plugins.telnet.gtk4.TelnetPlugin"), "telnet")
        self.assertIsNone(plugin_vise("gcm4.core.plugins"))

    def test_modules_importes(self):
        """Import différé dans une fonction compris."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "m.py"
            path.write_text("def f():\n    from gi.repository import Gtk\n", encoding="utf-8")
            self.assertIn((2, "gi.repository.Gtk"), modules_importes(path))


if __name__ == "__main__":
    unittest.main()
