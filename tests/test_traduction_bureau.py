# SPDX-License-Identifier: GPL-3.0-or-later
"""Lanceur et métadonnées AppStream traduits (issue #274).

``scripts/i18n-update.sh`` extrait les textes de ``data/`` dans
``lang/messages.pot`` ; ``--desktop DIR`` fusionne les traductions
(``msgfmt --desktop`` / ``--xml``) et ``build-deb/build.sh`` /
``build-rpm/build.sh`` installent les fichiers fusionnés. Ces tests lancent
la vraie fusion : gettext est requis (installé dans le job Qualité).
"""

from __future__ import annotations

import configparser
import os
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_ID = "io.github.MathildeDec.gcm4"
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
CLES_DESKTOP = ("Name", "GenericName", "Comment", "Keywords")


def linguas() -> list[str]:
    """Langues de lang/LINGUAS, commentaires retirés."""
    lignes = (ROOT / "lang" / "LINGUAS").read_text(encoding="utf-8").splitlines()
    return [ligne.split("#")[0].strip() for ligne in lignes if ligne.split("#")[0].strip()]


def code_bureau(langue: str) -> str:
    """Code attendu dans le lanceur : langue seule, sauf pt_BR et zh_CN."""
    return langue if langue in ("pt_BR", "zh_CN") else langue.split("_")[0]


def code_appstream(code: str) -> str:
    """Code xml:lang ramené au code du lanceur.

    msgfmt --xml écrit des étiquettes BCP 47 (``pt-BR``, ``zh-Hans-CN``
    avec gettext 0.23) ; une version plus ancienne peut garder ``pt_BR``.
    """
    return {"zh-Hans-CN": "zh_CN", "zh-CN": "zh_CN", "pt-BR": "pt_BR"}.get(code, code)


def _lire_desktop(chemin: Path) -> configparser.RawConfigParser:
    """Fichier .desktop, clés sensibles à la casse."""
    parseur = configparser.RawConfigParser(interpolation=None, strict=True)
    parseur.optionxform = str  # type: ignore[assignment,method-assign]
    parseur.read(chemin, encoding="utf-8")
    return parseur


class TestFusion(unittest.TestCase):
    """``scripts/i18n-update.sh --desktop`` sur les catalogues du dépôt."""

    @classmethod
    def setUpClass(cls):
        """Fusionne une fois dans un dossier temporaire."""
        cls._tmp = tempfile.TemporaryDirectory()
        cls.dest = Path(cls._tmp.name)
        resultat = subprocess.run(
            [str(ROOT / "scripts" / "i18n-update.sh"), "--desktop", str(cls.dest)],
            capture_output=True,
            text=True,
            check=False,
        )
        if resultat.returncode != 0:
            raise AssertionError(
                f"i18n-update.sh --desktop :\n{resultat.stdout}\n{resultat.stderr}"
            )
        cls.desktop = _lire_desktop(cls.dest / f"{APP_ID}.desktop")
        cls.metainfo = ET.parse(cls.dest / f"{APP_ID}.metainfo.xml").getroot()
        cls.source = ET.parse(ROOT / "data" / f"{APP_ID}.metainfo.xml").getroot()

    @classmethod
    def tearDownClass(cls):
        """Supprime le dossier temporaire."""
        cls._tmp.cleanup()

    def test_les_29_langues(self):
        """Garde-fou : LINGUAS compte bien les 29 langues du projet."""
        self.assertEqual(len(linguas()), 29)

    def test_lanceur_traduit_dans_chaque_langue(self):
        """Name, GenericName, Comment, Keywords et l'action debug dans chaque langue."""
        entree = self.desktop["Desktop Entry"]
        action = self.desktop["Desktop Action debug"]
        for langue in linguas():
            code = code_bureau(langue)
            with self.subTest(langue=langue):
                for cle in CLES_DESKTOP:
                    self.assertTrue(entree.get(f"{cle}[{code}]"), f"{cle}[{code}]")
                self.assertTrue(action.get(f"Name[{code}]"), f"action Name[{code}]")

    def test_mots_cles_bien_formes(self):
        """Chaque liste de mots-clés finit par « ; » et garde « gcm4 »."""
        for langue in linguas():
            mots = self.desktop["Desktop Entry"][f"Keywords[{code_bureau(langue)}]"]
            with self.subTest(langue=langue):
                self.assertTrue(mots.endswith(";"), mots)
                self.assertIn("gcm4", mots.split(";"))
                self.assertNotIn("", mots.split(";")[:-1])

    def test_traductions_effectives(self):
        """Hors anglais, le commentaire et la description courte sont traduits."""
        anglais = self.desktop["Desktop Entry"]["Comment"]
        for langue in linguas():
            if langue.startswith("en"):
                continue
            with self.subTest(langue=langue):
                self.assertNotEqual(
                    self.desktop["Desktop Entry"][f"Comment[{code_bureau(langue)}]"], anglais
                )

    def test_codes_de_langue(self):
        """fr_FR devient fr ; pt_BR et zh_CN gardent leur variante ; aucun ar_AR."""
        entree = self.desktop["Desktop Entry"]
        self.assertIn("Comment[fr]", entree)
        self.assertIn("Comment[pt_BR]", entree)
        self.assertIn("Comment[zh_CN]", entree)
        self.assertNotIn("Comment[fr_FR]", entree)
        self.assertNotIn("Comment[ar_AR]", entree)

    def test_appstream_traduit_dans_chaque_langue(self):
        """Résumé, paragraphes et puces (description et note de version), légendes."""
        langues = {code_bureau(langue) for langue in linguas()}
        for balise, attendus in (("summary", 1), ("p", 3), ("li", 4), ("caption", 3)):
            originaux = [e for e in self.source.iter(balise) if XML_LANG not in e.attrib]
            traduits = [e for e in self.metainfo.iter(balise) if e.get(XML_LANG)]
            with self.subTest(balise=balise):
                # Garde-fou : un texte ajouté à data/ doit l'être ici aussi.
                self.assertEqual(len(originaux), attendus)
                self.assertEqual(len(traduits), attendus * len(langues))
                self.assertEqual({code_appstream(e.get(XML_LANG)) for e in traduits}, langues)
                for element in traduits:
                    self.assertTrue(
                        (element.text or "").strip(), ET.tostring(element, encoding="unicode")
                    )

    def test_developpeur_non_traduit(self):
        """Le nom du développeur est marqué translate="no" : jamais traduit."""
        noms = self.metainfo.find("developer").findall("name")
        self.assertEqual(len(noms), 1)
        self.assertEqual(noms[0].text, "MathildeDec")

    def test_description_fidele_a_la_base(self):
        """Pas d'import/export annoncé : il n'est pas dans la base GTK4 (#152, #153)."""
        texte = ET.tostring(self.source.find("description"), encoding="unicode")
        self.assertNotIn("Import", texte)
        self.assertIn("Host passwords stored encrypted", texte)


class TestExtraction(unittest.TestCase):
    """Les textes de data/ sont dans le modèle, la fusion est utilisée par les paquets."""

    def test_textes_de_data_dans_le_modele(self):
        """Lanceur et AppStream référencés dans lang/messages.pot."""
        pot = (ROOT / "lang" / "messages.pot").read_text(encoding="utf-8")
        self.assertIn(f"data/{APP_ID}.desktop", pot)
        self.assertIn(f"data/{APP_ID}.metainfo.xml", pot)
        self.assertIn('msgid "gcm4 (debug mode)"', pot)
        self.assertIn('msgid "Groups and hosts in the main window"', pot)
        self.assertNotIn('msgid "MathildeDec"', pot)

    def test_paquets_installent_la_fusion(self):
        """build-deb/build.sh et build-rpm/build.sh remplacent data/ par la fusion."""
        for script, dossier in (
            ("build-deb/build.sh", "$BUILD_DIR"),
            ("build-rpm/build.sh", "$STAGE_DIR"),
        ):
            with self.subTest(script=script):
                contenu = (ROOT / script).read_text(encoding="utf-8")
                self.assertIn(f'i18n-update.sh" --desktop "{dossier}/data"', contenu)

    def test_test_de_fumee_verifie_les_traductions(self):
        """Le paquet installé contient le lanceur et AppStream traduits."""
        smoke = (ROOT / "scripts" / "smoke-test-packages.sh").read_text(encoding="utf-8")
        self.assertIn("Comment\\[$code\\]=", smoke)
        self.assertIn("for code in fr zh_CN", smoke)


class TestReglesIts(unittest.TestCase):
    """Règles ITS AppStream fournies par le dépôt (lang/its).

    Sans elles, le modèle dépendait de la machine : la règle intégrée à
    gettext 0.23 n'extrait pas ``releases/release/description`` et un runner
    sans règle *.metainfo.xml échouait (« cannot locate ITS rules »).
    """

    def test_regles_bien_formees_et_liees(self):
        """metainfo.loc associe *.metainfo.xml à metainfo.its, présent à côté."""
        dossier = ROOT / "lang" / "its"
        regles = ET.parse(dossier / "metainfo.loc").getroot()
        cibles = {
            regle.get("pattern"): [doc.get("target") for doc in regle]
            for regle in regles.iter("locatingRule")
        }
        self.assertEqual(cibles["*.metainfo.xml"], ["metainfo.its"])
        its = ET.parse(dossier / "metainfo.its").getroot()
        self.assertEqual(its.tag, "{http://www.w3.org/2005/11/its}rules")

    def test_modele_identique_sans_regles_du_systeme(self):
        """``--check`` passe quand les règles ITS du système sont masquées.

        ``GETTEXTDATADIR`` pointe sur un dossier vide : la règle installée par
        le paquet appstream (``/usr/share/gettext/its``) disparaît, seules
        restent la règle intégrée à gettext et celles du dépôt, que le script
        ajoute par ``GETTEXTDATADIRS``. Avec gettext 0.23, la règle intégrée
        seule perd les notes de version et ``--check`` échoue.
        """
        with tempfile.TemporaryDirectory() as vide:
            env = {
                cle: valeur
                for cle, valeur in os.environ.items()
                if cle not in ("GETTEXTDATADIR", "GETTEXTDATADIRS")
            }
            env["GETTEXTDATADIR"] = vide
            resultat = subprocess.run(
                [str(ROOT / "scripts" / "i18n-update.sh"), "--check"],
                capture_output=True,
                text=True,
                check=False,
                env=env,
            )
        self.assertEqual(resultat.returncode, 0, resultat.stdout + resultat.stderr)
        self.assertIn("catalogues à jour", resultat.stdout)
