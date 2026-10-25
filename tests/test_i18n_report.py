"""scripts/i18n_report.py et job CI i18n (issue #166)."""

from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("i18n_report", ROOT / "scripts" / "i18n_report.py")
report = importlib.util.module_from_spec(_spec)
sys.modules["i18n_report"] = report  # requis par @dataclass
_spec.loader.exec_module(report)

SAMPLE = r"""# en-tête
msgid ""
msgstr ""
"Content-Type: text/plain; charset=UTF-8\n"
"Plural-Forms: nplurals=2; plural=(n > 1);\n"

#: a.py
msgid "Open"
msgstr "Ouvrir"

#: a.py
#, fuzzy, python-brace-format
msgid "Host {name}"
msgstr "Hôte {nom}"

#: a.py
msgid ""
"Long "
"text"
msgstr ""

#: a.py
msgctxt "menu"
msgid "File"
msgstr "Fichier"

#: a.py
msgid "{n} host"
msgid_plural "{n} hosts"
msgstr[0] "{n} hôte"
msgstr[1] ""

#: a.py
msgid "{n} tab"
msgid_plural "{n} tabs"
msgstr[0] "{n} onglet"
msgstr[1] "{n} onglets"

#~ msgid "Old"
#~ msgstr "Ancien"
"""


class TestParse(unittest.TestCase):
    """Lecture des catalogues."""

    def test_entrees(self):
        """Contexte, pluriels, suites de lignes et fuzzy ; obsolètes ignorées."""
        entries = report.parse_po(SAMPLE)
        self.assertEqual(
            [e[0] for e in entries],
            ["", "Open", "Host {name}", "Long text", "File", "{n} host", "{n} tab"],
        )
        self.assertEqual(entries[2][2], True)
        self.assertEqual(entries[5][1], ["{n} hôte", ""])

    def test_statistiques(self):
        """Pluriel incomplet = vide, fuzzy à part, en-tête exclu."""
        stats = report.catalog_stats("fr_FR", SAMPLE)
        self.assertEqual((stats.translated, stats.fuzzy, stats.untranslated), (3, 1, 2))
        self.assertEqual(stats.total, 6)
        self.assertAlmostEqual(stats.percent, 50.0)

    @pytest.mark.requires_gettext
    @unittest.skipUnless(shutil.which("msgfmt"), "gettext non installé")
    def test_memes_chiffres_que_msgfmt(self):
        """Sur un vrai catalogue, les compteurs sont ceux de msgfmt --statistics."""
        locale = report.read_linguas()[0]
        path = ROOT / "lang" / f"{locale}.po"
        out = subprocess.run(
            ["msgfmt", "--statistics", "-o", "/dev/null", str(path)],
            capture_output=True,
            text=True,
            check=True,
            env={"LC_ALL": "C", "PATH": "/usr/bin:/bin"},
        ).stderr
        stats = report.catalog_stats(locale, path.read_text(encoding="utf-8"))
        self.assertIn(f"{stats.translated} translated", out)
        if stats.fuzzy:
            self.assertIn(f"{stats.fuzzy} fuzzy", out)
        if stats.untranslated:
            self.assertIn(f"{stats.untranslated} untranslated", out)


class TestRapport(unittest.TestCase):
    """Rendu et point d'entrée."""

    def test_toutes_les_langues_de_linguas(self):
        """Une ligne par langue de lang/LINGUAS, sans liste en dur."""
        stats, errors = report.collect()
        self.assertEqual(errors, [])
        self.assertEqual(sorted(s.locale for s in stats), sorted(report.read_linguas()))
        md = report.render(stats, markdown=True)
        for locale in report.read_linguas():
            self.assertIn(f"| `{locale}` |", md)

    def test_resume_github_et_catalogue_manquant(self):
        """Écrit dans $GITHUB_STEP_SUMMARY ; code 2 si un catalogue listé manque."""
        with tempfile.TemporaryDirectory() as tmp:
            lang = Path(tmp) / "lang"
            lang.mkdir()
            (lang / "LINGUAS").write_text("# langues\nfr_FR de_DE\n", encoding="utf-8")
            (lang / "fr_FR.po").write_text(SAMPLE, encoding="utf-8")
            summary = Path(tmp) / "summary.md"
            proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "i18n_report.py"),
                    "--lang-dir",
                    str(lang),
                ],
                capture_output=True,
                text=True,
                env={"GITHUB_STEP_SUMMARY": str(summary), "PATH": "/usr/bin:/bin"},
                cwd=ROOT,
            )
            self.assertEqual(proc.returncode, 2, proc.stderr)
            self.assertIn("fr_FR", proc.stdout)
            self.assertIn("lang/de_DE.po", proc.stderr)
            self.assertIn("| `fr_FR` | 3 | 1 | 2 | 50.0 % |", summary.read_text(encoding="utf-8"))


class TestJobCI(unittest.TestCase):
    """Le job i18n bloque les catalogues désynchronisés."""

    def test_job_i18n(self):
        """--check, puis --compile de toutes les locales, puis rapport."""
        ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        match = re.search(r"^  i18n:\n(.*?)(?=^  \S|\Z)", ci, re.M | re.S)
        self.assertIsNotNone(match, "job i18n absent de ci.yml")
        job = match.group(1)
        positions = [
            job.find(cmd)
            for cmd in (
                "scripts/i18n-update.sh --check",
                "scripts/i18n-update.sh --compile",
                "scripts/i18n_report.py --markdown",
            )
        ]
        self.assertNotIn(-1, positions, job)
        self.assertEqual(positions, sorted(positions))


if __name__ == "__main__":
    unittest.main()
