"""Contenu des paquets .deb/.rpm (issue #162).

``make install DESTDIR=...`` est la seule étape commune à ``make deb``,
``make rpm`` et ``make opensuse`` (fpm emballe ensuite ce répertoire tel
quel). Avant l'issue #162, seuls ``gnome_connection_manager.py``,
``pyAES.py`` et ``urlregex.py`` étaient copiés : l'application installée
échouait dès l'import de ``gcm4_core`` et n'avait aucun plugin.

Ce test lance la vraie cible (msgfmt requis : installé dans le job Qualité)
et vérifie que chaque module local importé par un fichier installé, y
compris par un import différé dans une fonction, est lui aussi installé.
"""

from __future__ import annotations

import ast
import re
import subprocess
import tempfile
import unittest
import warnings
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
APP = Path("usr/share/gnome-connection-manager")
LOCAL_ROOTS = {p.stem for p in ROOT.glob("*.py")} | {"plugins"}


def _imports(path: Path) -> set[str]:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
            found.update(f"{node.module}.{a.name}" for a in node.names)
    return {m for m in found if m.split(".")[0] in LOCAL_ROOTS}


def _resolu(app: Path, module: str) -> bool:
    """Le module (ou, pour ``from x import nom``, son paquet parent) est installé."""
    candidats = [module, module.rsplit(".", 1)[0]] if "." in module else [module]
    for nom in candidats:
        base = app / Path(*nom.split("."))
        if base.with_suffix(".py").is_file() or (base / "__init__.py").is_file():
            return True
        if base.is_dir() and nom == "plugins":
            return True  # plugins/ : répertoire chargé par plugin_base (pas un paquet)
    return False


@pytest.mark.requires_gettext
class TestContenuDuPaquet(unittest.TestCase):
    """Arborescence produite par ``make install``."""

    @classmethod
    def setUpClass(cls):
        """Installe une fois dans un répertoire temporaire."""
        cls._tmp = tempfile.TemporaryDirectory()
        dest = Path(cls._tmp.name)
        result = subprocess.run(
            ["make", "-s", "install", f"DESTDIR={dest}"], cwd=ROOT, capture_output=True, text=True
        )
        if result.returncode != 0:
            raise AssertionError(f"make install a échoué :\n{result.stdout}\n{result.stderr}")
        cls.app = dest / APP
        cls.dest = dest

    @classmethod
    def tearDownClass(cls):
        """Supprime l'installation temporaire."""
        cls._tmp.cleanup()

    def test_point_d_entree_executable(self):
        """Le .desktop lance gnome_connection_manager.py : présent et exécutable."""
        entree = self.app / "gnome_connection_manager.py"
        self.assertTrue(entree.is_file())
        self.assertTrue(entree.stat().st_mode & 0o111)
        desktop = (
            self.dest / "usr/share/applications/gnome-connection-manager.desktop"
        ).read_text(encoding="utf-8")
        self.assertIn(f"Exec=/{APP}/gnome_connection_manager.py", desktop)

    def test_tous_les_modules_de_la_racine(self):
        """Chaque .py de la racine du dépôt est installé."""
        manquants = sorted(p.name for p in ROOT.glob("*.py") if not (self.app / p.name).is_file())
        self.assertEqual(manquants, [])

    def test_plugins_installes_sans_tests(self):
        """Les plugins sont là, leurs tests non."""
        self.assertTrue((self.app / "plugins" / "plugin_base.py").is_file())
        self.assertTrue((self.app / "plugins" / "ssh" / "core.py").is_file())
        attendus = {p.name for p in (ROOT / "plugins").glob("plugin_*.py")}
        self.assertEqual({p.name for p in (self.app / "plugins").glob("plugin_*.py")}, attendus)
        self.assertEqual(
            [p for p in self.app.rglob("*") if "tests" in p.parts or p.name == "conftest.py"], []
        )

    def test_chaque_import_local_est_installe(self):
        """Aucun ImportError à prévoir : tout module local importé est embarqué."""
        manquants = {}
        for fichier in sorted(self.app.rglob("*.py")):
            absents = sorted(m for m in _imports(fichier) if not _resolu(self.app, m))
            if absents:
                manquants[str(fichier.relative_to(self.app))] = absents
        self.assertEqual(manquants, {})

    def test_traductions_compilees(self):
        """Les .mo des langues de lang/LINGUAS sont présents."""
        langues = [
            ligne.strip()
            for ligne in (ROOT / "lang" / "LINGUAS").read_text(encoding="utf-8").splitlines()
            if ligne.strip() and not ligne.startswith("#")
        ]
        self.assertTrue(langues)
        mo = list((self.app / "lang").rglob("*.mo"))
        self.assertGreaterEqual(len(mo), len(langues))


class TestDependancesDesPaquets(unittest.TestCase):
    """Dépendances déclarées à fpm (rpm) et dans debian/control (deb)."""

    def test_loguru_declaree_partout(self):
        """logging_config importe loguru au démarrage : dépendance de chaque paquet."""
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        for cible in ("rpm", "opensuse"):
            bloc = re.search(rf"^{cible}:\n(.*?)(?=^\S)", makefile, re.S | re.M)
            self.assertIsNotNone(bloc, cible)
            self.assertIn("-d python3-loguru", bloc.group(1), cible)
        # make deb construit le paquet gcm4 avec build-deb/build.sh (#156) :
        # la dépendance est déclarée dans debian/control, plus à fpm.
        control = (ROOT / "build-deb" / "debian" / "control").read_text(encoding="utf-8")
        depends = re.search(r"^Depends:(.*?)(?=^\S)", control, re.S | re.M)
        self.assertIsNotNone(depends, "Depends absent de debian/control")
        self.assertRegex(depends.group(1), r"\bpython3-loguru\b")


if __name__ == "__main__":
    unittest.main()
