"""Garde-fous de l'internationalisation (issue #168).

1. Aucune liste de langues codée en dur hors de ``lang/LINGUAS``.
2. Chaque catalogue de ``lang/LINGUAS`` se compile, son ``.mo`` est présent,
   et une locale sans catalogue retombe sur les textes source.
3. Les textes littéraux passés aux widgets (``set_label``, ``set_title``,
   ``Gtk.Label(...)``, etc.) sont marqués ``_()``.
"""

from __future__ import annotations

import ast
import os
import re
import shutil
import subprocess
import sys
import unittest
import warnings
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
LANG_DIR = ROOT / "lang"
DOMAIN = "gcm-lang"
# Sous-projets qui ont leur propre outillage (et leurs propres langues).
SUBPROJECTS = ("SSH-Studio/", "gtk-frdp/", "plugins/pluginvnc2/")
LOCALE_RE = re.compile(r"\b[a-z]{2,3}_[A-Z]{2}\b")
# Au-delà, un fichier énumère des langues (les docs citent 2 ou 3 exemples).
MAX_LOCALES_PER_FILE = 4


def linguas() -> list[str]:
    """Locales de ``lang/LINGUAS`` (commentaires ignorés)."""
    out: list[str] = []
    for line in (LANG_DIR / "LINGUAS").read_text(encoding="utf-8").splitlines():
        out.extend(line.split("#", 1)[0].split())
    return out


def tracked(pattern: str = "") -> list[str]:
    """Fichiers suivis par git, hors lang/ et sous-projets."""
    args = ["git", "ls-files"] + ([pattern] if pattern else [])
    files = subprocess.run(
        args, cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    return [f for f in files if not f.startswith(("lang/", *SUBPROJECTS))]


def parse(source: str) -> ast.Module:
    """``ast.parse`` sans les SyntaxWarning des docstrings existantes."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        return ast.parse(source)


# -- 1. listes de langues ---------------------------------------------------


def locale_literals(source: str) -> list[int]:
    """Lignes des list/tuple/set/dict Python contenant au moins 3 codes de langue."""
    lines = []
    for node in ast.walk(parse(source)):
        items = node.keys if isinstance(node, ast.Dict) else getattr(node, "elts", None)
        if not isinstance(node, (ast.List, ast.Tuple, ast.Set, ast.Dict)) or items is None:
            continue
        codes = {
            item.value
            for item in items
            if isinstance(item, ast.Constant)
            and isinstance(item.value, str)
            and LOCALE_RE.fullmatch(item.value)
        }
        if len(codes) >= 3:
            lines.append(node.lineno)
    return lines


class TestPasDeListeDeLangues(unittest.TestCase):
    """lang/LINGUAS est la seule liste des langues."""

    def test_python(self):
        """Aucune collection Python de codes de langue."""
        fautes = {}
        for f in tracked("*.py"):
            lines = locale_literals((ROOT / f).read_text(encoding="utf-8"))
            if lines:
                fautes[f] = lines
        self.assertEqual(fautes, {}, "lire lang/LINGUAS au lieu d'une liste en dur")

    def test_autres_fichiers(self):
        """Scripts, Makefile, workflows, paquets : pas d'énumération de langues."""
        fautes = {}
        for f in tracked():
            if f.endswith((".py", ".md")):
                continue
            try:
                text = (ROOT / f).read_text(encoding="utf-8")
            except (UnicodeDecodeError, IsADirectoryError, FileNotFoundError):
                continue
            codes = set(LOCALE_RE.findall(text))
            if len(codes) > MAX_LOCALES_PER_FILE:
                fautes[f] = sorted(codes)
        self.assertEqual(fautes, {})

    def test_detecteur(self):
        """Le détecteur voit une liste de langues, pas un exemple isolé."""
        self.assertEqual(locale_literals('L = ["fr_FR", "de_DE", "it_IT"]\nx = "fr_FR"\n'), [1])
        self.assertEqual(locale_literals('d = {"fr_FR": 1, "de_DE": 2, "es_ES": 3}\n'), [1])


# -- 2. catalogues -----------------------------------------------------------


class TestCatalogues(unittest.TestCase):
    """Compilation, .mo présents, langue de repli."""

    def test_linguas_et_po_concordent(self):
        """Un .po par langue de LINGUAS, et aucun .po orphelin."""
        po = sorted(p.stem for p in LANG_DIR.glob("*.po"))
        self.assertEqual(po, sorted(linguas()))
        self.assertEqual(len(set(linguas())), len(linguas()), "doublon dans LINGUAS")

    @pytest.mark.requires_gettext
    @unittest.skipUnless(shutil.which("msgfmt"), "gettext non installé")
    def test_catalogues_compilables(self):
        """Msgfmt --check accepte chaque catalogue."""
        for locale in linguas():
            with self.subTest(locale=locale):
                proc = subprocess.run(
                    [
                        "msgfmt",
                        "--check",
                        "--output-file=/dev/null",
                        str(LANG_DIR / f"{locale}.po"),
                    ],
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_mo_present_pour_chaque_langue(self):
        """lang/<locale>/LC_MESSAGES/gcm-lang.mo existe pour chaque langue, et seulement elles."""
        mo = sorted(p.parent.parent.name for p in LANG_DIR.glob(f"*/LC_MESSAGES/{DOMAIN}.mo"))
        self.assertEqual(mo, sorted(linguas()))

    def _traduire(self, language: str, text: str) -> str:
        code = (
            "import gcm4_core, builtins, sys\n"
            f"gcm4_core.bindtextdomain({DOMAIN!r}, {str(LANG_DIR)!r})\n"
            "sys.stdout.write(builtins._(sys.argv[1]))\n"
        )
        env = {
            **os.environ,
            "LANGUAGE": language,
            "LANG": "C.UTF-8",
            "LC_ALL": "",
            "LC_MESSAGES": "",
        }
        proc = subprocess.run(
            [sys.executable, "-c", code, text],
            cwd=ROOT,
            capture_output=True,
            text=True,
            env=env,
            check=True,
        )
        return proc.stdout

    def test_repli_quand_la_locale_manque(self):
        """Une langue sans catalogue affiche le texte source (aucune exception)."""
        self.assertEqual(self._traduire("xx_XX", "Servers"), "Servers")

    def test_traduction_chargee(self):
        """Une langue de LINGUAS avec une traduction utilisable la charge."""
        # fr_FR est la langue de référence du projet (traduite à 100 %) :
        # son absence de LINGUAS est une régression, pas un motif de saut.
        self.assertIn("fr_FR", linguas())
        source = "Server list will be overwritten, continue?"
        traduit = self._traduire("fr_FR", source)
        self.assertNotEqual(traduit, source)
        self.assertTrue(traduit)


# -- 3. textes d'interface marqués ------------------------------------------

UI_METHODS = {
    "set_label",
    "set_title",
    "set_subtitle",
    "set_tooltip_text",
    "set_placeholder_text",
    "set_heading",
    "set_body",
}
UI_WIDGETS = {
    "Label",
    "Button",
    "CheckButton",
    "ToggleButton",
    "RadioButton",
    "LinkButton",
    "MenuButton",
    "MenuItem",
    "CheckMenuItem",
    "Frame",
    "Expander",
}
UI_KEYWORDS = {"label", "title", "tooltip_text", "placeholder_text", "heading", "body", "subtitle"}
# Identifiants techniques (noms d'icône, d'action, chemins) : pas des phrases.
_TECHNICAL = re.compile(r"[a-z0-9][a-z0-9_.:/\-]*")


def _is_ui_text(node: ast.AST) -> bool:
    if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
        return False
    text = node.value.strip()
    return bool(re.search(r"[^\W\d_]{2}", text)) and not _TECHNICAL.fullmatch(text)


def unmarked_ui_texts(source: str) -> list[int]:
    """Lignes où un littéral de texte arrive à un widget sans passer par ``_()``."""
    lines = []
    for node in ast.walk(parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in UI_METHODS:
            if node.args and _is_ui_text(node.args[0]):
                lines.append(node.lineno)
        elif (
            isinstance(func, ast.Attribute)
            and func.attr in UI_WIDGETS
            and isinstance(func.value, ast.Name)
            and func.value.id in {"Gtk", "Adw"}
        ):
            if node.args and _is_ui_text(node.args[0]):
                lines.append(node.lineno)
            lines += [
                node.lineno
                for kw in node.keywords
                if kw.arg in UI_KEYWORDS and _is_ui_text(kw.value)
            ]
    return sorted(set(lines))


class TestTextesInterfaceMarques(unittest.TestCase):
    """Fichiers scannés par xgettext : tout texte de widget passe par _()."""

    def test_sources_de_l_application(self):
        """Aucun littéral non marqué (cœur, plugins, future base src/gcm4/ui/)."""
        fautes = {}
        for f in tracked("*.py"):
            if f.startswith(("tests/", "tools/", "scripts/")) or "/tests/" in f:
                continue
            lines = unmarked_ui_texts((ROOT / f).read_text(encoding="utf-8"))
            if lines:
                fautes[f] = lines
        self.assertEqual(fautes, {}, 'utiliser _("...") (voir docs/i18n.md)')

    def test_detecteur(self):
        """Littéraux repérés ; _(), variables et identifiants ignorés."""
        source = (
            'w.set_title("Servers")\n'
            'w.set_title(_("Servers"))\n'
            "w.set_label(name)\n"
            'Gtk.Button(label="Fermer")\n'
            'Gtk.Image(icon_name="go-home")\n'
            'Gtk.Label("dialog-warning")\n'
        )
        self.assertEqual(unmarked_ui_texts(source), [1, 4])


if __name__ == "__main__":
    unittest.main()
