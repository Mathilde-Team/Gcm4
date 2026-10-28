# SPDX-License-Identifier: GPL-3.0-or-later
"""Intégration au bureau de gcm4 : lanceur, AppStream, icônes (issue #155).

Ces tests n'ont besoin ni de GTK ni des validateurs : ils vérifient la
cohérence entre les fichiers de ``data/`` et le code (identifiant, nom
affiché, commande, option ``--debug``, version, icônes). La conformité aux
spécifications est vérifiée par ``scripts/check-desktop.sh`` (job CI
« Intégration au bureau »).
"""

from __future__ import annotations

import configparser
import re
import struct
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parent.parent
APPLICATION = (ROOT / "src" / "gcm4" / "ui" / "application.py").read_text(encoding="utf-8")
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
TAILLES = (16, 22, 24, 32, 48, 64, 128)


def constante(nom: str) -> str:
    """Valeur d'une constante chaîne de ``gcm4/ui/application.py`` (sans importer gi)."""
    trouve = re.search(rf'^{nom} = "([^"]+)"$', APPLICATION, re.M)
    assert trouve, f"{nom} absent de application.py"
    return trouve.group(1)


APP_ID = constante("APPLICATION_ID")
NOM_AFFICHE = constante("NOM_AFFICHE")
DESKTOP = ROOT / "data" / f"{APP_ID}.desktop"
METAINFO = ROOT / "data" / f"{APP_ID}.metainfo.xml"


def lanceur() -> configparser.RawConfigParser:
    """Le fichier .desktop, clés sensibles à la casse."""
    cp = configparser.RawConfigParser(delimiters=("=",), comment_prefixes=("#",))
    cp.optionxform = str  # type: ignore[assignment,method-assign]
    cp.read(DESKTOP, encoding="utf-8")
    return cp


def dimensions_png(chemin: Path) -> tuple[int, int]:
    """Largeur et hauteur lues dans l'en-tête IHDR d'un PNG."""
    entete = chemin.read_bytes()[:24]
    assert entete[:8] == b"\x89PNG\r\n\x1a\n", f"{chemin} n'est pas un PNG"
    assert entete[12:16] == b"IHDR"
    largeur, hauteur = struct.unpack(">II", entete[16:24])
    return largeur, hauteur


class TestLanceur(unittest.TestCase):
    """data/io.github.MathildeDec.gcm4.desktop."""

    def setUp(self):
        """Section principale du lanceur."""
        self.entree = lanceur()["Desktop Entry"]

    def test_nomme_d_apres_l_identifiant(self):
        """Le nom du fichier est l'identifiant de l'application, rattaché par la classe."""
        self.assertTrue(DESKTOP.is_file())
        self.assertEqual(self.entree["Icon"], APP_ID)
        self.assertEqual(self.entree["StartupWMClass"], APP_ID)
        self.assertEqual(self.entree["Name"], NOM_AFFICHE)

    def test_commande_installee(self):
        """Exec lance le script déclaré dans pyproject.toml."""
        self.assertIn(self.entree["Exec"], PYPROJECT["project"]["scripts"])
        self.assertEqual(self.entree["Type"], "Application")
        self.assertEqual(self.entree["Terminal"], "false")

    def test_action_mode_debug(self):
        """Action « gcm4 (debug mode) » : même commande, option --debug de la CLI."""
        self.assertEqual(self.entree["Actions"], "debug;")
        action = lanceur()["Desktop Action debug"]
        self.assertEqual(action["Name"], "gcm4 (debug mode)")
        self.assertEqual(action["Exec"], f"{self.entree['Exec']} --debug")
        from gcm4.core.cli import build_parser

        options = action["Exec"].split()[1:]
        self.assertTrue(build_parser().parse_args(options).debug)

    def test_aucun_protocole_en_dur(self):
        """La base ne fournit aucun protocole : le lanceur n'en nomme pas."""
        texte = DESKTOP.read_text(encoding="utf-8").lower()
        for protocole in ("ssh", "telnet", "vnc", "rdp", "sftp"):
            self.assertNotIn(protocole, texte)


class TestMetainfo(unittest.TestCase):
    """data/io.github.MathildeDec.gcm4.metainfo.xml."""

    def setUp(self):
        """Racine du document AppStream."""
        self.racine = ET.parse(METAINFO).getroot()

    def test_identite(self):
        """Identifiant, nom et lanceur cohérents avec l'application."""
        self.assertEqual(self.racine.get("type"), "desktop-application")
        self.assertEqual(self.racine.findtext("id"), APP_ID)
        self.assertEqual(self.racine.findtext("name"), NOM_AFFICHE)
        self.assertEqual(self.racine.findtext("launchable"), DESKTOP.name)
        self.assertEqual(
            self.racine.findtext("provides/binary"), lanceur()["Desktop Entry"]["Exec"]
        )

    def test_licences(self):
        """Licence du projet = celle de pyproject.toml ; métadonnées en CC0."""
        self.assertEqual(self.racine.findtext("metadata_license"), "CC0-1.0")
        licence = PYPROJECT["project"]["license"]
        licence = licence if isinstance(licence, str) else licence.get("text", "")
        self.assertEqual(self.racine.findtext("project_license"), licence)

    def test_version_courante(self):
        """La dernière version annoncée est celle de pyproject.toml."""
        versions = [r.get("version") for r in self.racine.iter("release")]
        self.assertEqual(versions[0], PYPROJECT["project"]["version"])
        self.assertRegex(
            self.racine.find("releases/release").get("date", ""), r"^\d{4}-\d{2}-\d{2}$"
        )

    def test_captures_presentes_dans_le_depot(self):
        """Chaque capture citée existe dans docs/captures (branche dev)."""
        prefixe = "https://raw.githubusercontent.com/Mathilde-Team/Gcm4/dev/"
        images = [i.text or "" for i in self.racine.iter("image")]
        self.assertGreaterEqual(len(images), 1)
        for url in images:
            self.assertTrue(url.startswith(prefixe), url)
            self.assertTrue((ROOT / url.removeprefix(prefixe)).is_file(), url)

    def test_adresses_du_depot(self):
        """Les adresses visent l'organisation actuelle (Mathilde-Team), pas l'ancien nom."""
        site = constante("SITE")
        urls = {u.get("type"): u.text for u in self.racine.iter("url")}
        self.assertEqual(urls["homepage"], site)
        self.assertEqual(urls["bugtracker"], f"{site}/issues")
        self.assertNotIn("Mathide" + "-Team", METAINFO.read_text(encoding="utf-8"))


class TestIcones(unittest.TestCase):
    """data/icons/hicolor/<taille>/apps/io.github.MathildeDec.gcm4.png."""

    def test_tailles_standard(self):
        """Une icône carrée par taille, aux dimensions annoncées par son dossier."""
        for taille in TAILLES:
            icone = ROOT / "data" / "icons" / "hicolor" / f"{taille}x{taille}" / "apps"
            self.assertEqual(dimensions_png(icone / f"{APP_ID}.png"), (taille, taille))

    def test_jamais_agrandie(self):
        """Aucune taille ne dépasse la source icon.png (pas d'agrandissement)."""
        largeur, hauteur = dimensions_png(ROOT / "icon.png")
        self.assertLessEqual(max(TAILLES), min(largeur, hauteur))

    def test_script_et_tailles_alignes(self):
        """scripts/generer-icones.sh produit exactement ces tailles."""
        script = (ROOT / "scripts" / "generer-icones.sh").read_text(encoding="utf-8")
        trouve = re.search(r"^TAILLES=\(([\d ]+)\)$", script, re.M)
        assert trouve
        self.assertEqual(tuple(int(t) for t in trouve.group(1).split()), TAILLES)


class TestCi(unittest.TestCase):
    """Les validations sont exécutées en CI (critère d'acceptation de #155)."""

    def test_job_integration_au_bureau(self):
        """ci.yml installe les validateurs et lance scripts/check-desktop.sh."""
        ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("name: Intégration au bureau", ci)
        self.assertIn("desktop-file-utils appstream gettext", ci)
        self.assertIn("run: scripts/check-desktop.sh", ci)

    def test_check_desktop_valide_les_trois(self):
        """Le script appelle les deux validateurs et le contrôle des icônes."""
        script = (ROOT / "scripts" / "check-desktop.sh").read_text(encoding="utf-8")
        self.assertIn('desktop-file-validate "$1"', script)
        self.assertIn('appstreamcli validate --no-net --explain "$2"', script)
        self.assertIn('valider "$DESKTOP" "$METAINFO"', script)
        # Les fichiers traduits installés par les paquets (#274).
        self.assertIn('i18n-update.sh" --desktop "$FUSION"', script)
        self.assertIn('valider "$FUSION/$APP_ID.desktop" "$FUSION/$APP_ID.metainfo.xml"', script)
        self.assertIn('generer-icones.sh" --check', script)
