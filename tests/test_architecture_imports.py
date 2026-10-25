"""Contrats d'architecture des imports (issue #174), par analyse AST.

L'ancien code n'est pas encore un paquet : import-linter (configuré dans
``pyproject.toml`` pour le futur ``src/gcm4``) ne peut pas l'analyser. Ce
test applique les mêmes règles aux modules actuels :

- les modules cœur (sans GTK) n'importent pas ``gi``, ni au niveau module
  ni dans une fonction ;
- aucun plugin n'importe un autre plugin (``plugins/plugin_base.py`` et le
  cœur du plugin lui-même, ex. ``plugins/ssh/core.py`` pour
  ``plugin_ssh.py``, sont autorisés) ;
- ``plugins/*/core.py`` n'importe pas ``gi`` ;
- le cœur n'importe aucun plugin nommé ;
- dans ``src/gcm4/core/`` et ``src/gcm4/ui/`` (quand ils existeront),
  aucun nom de protocole en littéral, hors exceptions justifiées : le cœur
  passe par le registre de plugins.
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
    {p.name for p in ROOT.glob("*_core.py")}
    | {"gcm4_core.py", "logging_config.py", "master_password_core.py"}
)
NOMS_PLUGINS = sorted(p.stem for p in PLUGINS.glob("plugin_*.py") if p.stem != "plugin_base")
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
    """Nom du plugin visé par un import (``plugin_vnc``, ``plugins.plugin_ssh.X``...)."""
    for part in module.lstrip(".").split("."):
        if part in NOMS_PLUGINS:
            return part
    return None


class TestContratsImports(unittest.TestCase):
    """Règles de couches sur l'ancien code."""

    def test_perimetre_non_vide(self):
        """Garde-fou : les globs trouvent bien des modules."""
        self.assertGreaterEqual(len(MODULES_COEUR), 10)
        self.assertGreaterEqual(len(NOMS_PLUGINS), 15)
        self.assertIn("plugins/ssh/core.py", CORES_DE_PLUGINS)

    def test_coeur_sans_gtk(self):
        """Une PR qui fait importer GTK au cœur échoue."""
        fautes = [
            f"{nom}:{ligne} {mod}"
            for nom in MODULES_COEUR + CORES_DE_PLUGINS
            for ligne, mod in modules_importes(ROOT / nom)
            if mod.split(".")[0] == "gi"
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
        """Seuls plugin_base et le cœur du plugin lui-même sont importables."""
        fautes = []
        for nom in NOMS_PLUGINS:
            for ligne, mod in modules_importes(PLUGINS / f"{nom}.py"):
                vise = plugin_vise(mod)
                if vise and vise != nom:
                    fautes.append(f"plugins/{nom}.py:{ligne} {mod}")
        for core in CORES_DE_PLUGINS:
            for ligne, mod in modules_importes(ROOT / core):
                if plugin_vise(mod):
                    fautes.append(f"{core}:{ligne} {mod}")
        self.assertEqual(fautes, [], "partager le code via un module *_core.py ou plugin_base")

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
        self.assertEqual(plugin_vise("plugin_vnc"), "plugin_vnc")
        self.assertEqual(plugin_vise("plugins.plugin_ssh.SshPlugin"), "plugin_ssh")
        self.assertIsNone(plugin_vise("plugins.plugin_base"))
        self.assertIsNone(plugin_vise("plugins.ssh.core"))

    def test_modules_importes(self):
        """Import différé dans une fonction compris."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "m.py"
            path.write_text("def f():\n    from gi.repository import Gtk\n", encoding="utf-8")
            self.assertIn((2, "gi.repository.Gtk"), modules_importes(path))


if __name__ == "__main__":
    unittest.main()
