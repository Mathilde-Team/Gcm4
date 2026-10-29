# SPDX-License-Identifier: GPL-3.0-or-later
"""Licence de gcm4 (issue #339) : LICENSE, NOTICE, en-têtes SPDX et paquets.

Trois régimes (NOTICE) : le code repris de l'amont gnome-connection-manager
reste en GPL-3.0 seule, pyAES.py en MIT, le code de l'équipe est en
GPL-3.0-or-later. Ces tests, sans réseau, verrouillent :

- ``LICENSE`` = texte GPLv3 seul, pour que GitHub reconnaisse la licence
  (un avis placé avant le texte la fait afficher « NOASSERTION ») ;
- ``NOTICE`` = avis amont conservé et liste des fichiers GPL-3.0 seule ;
- un en-tête ``SPDX-License-Identifier`` dans chaque fichier de code, avec
  le bon régime ;
- les mêmes listes dans ``debian/copyright`` et le champ License du .rpm.
"""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Fichiers repris ou dérivés de l'amont : GPL-3.0 seule (avis amont, NOTICE).
AMONT = {
    "gnome_connection_manager.py",
    "widgets.py",
    "gcm4_core.py",
    "models.py",
    "utils.py",
    "urlregex.py",
    "ssh.expect",
    "style.css",
    "postinst",
}
# Code tiers repris avec sa propre licence.
TIERS = {"pyAES.py": "MIT"}
# Sous-projets vendorisés : licence propre, documentée dans NOTICE.
VENDORISES = ("SSH-Studio/", "gtk-frdp/", "lang/")
SPDX = re.compile(r"SPDX-License-Identifier: (\S+)")


def _lire(chemin: str) -> str:
    return (ROOT / chemin).read_text(encoding="utf-8")


def _fichiers_de_code() -> list[str]:
    """Fichiers suivis par git qui doivent porter un en-tête SPDX."""
    sortie = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    return [
        f
        for f in sortie
        if not f.startswith(VENDORISES)
        and (
            re.search(r"\.(py|sh|bash|expect)$", f) or f in AMONT or f == "build-deb/wrappers/gcm4"
        )
    ]


class TestLicenseEtNotice(unittest.TestCase):
    """LICENSE contient la GPLv3 seule, NOTICE l'avis amont et les régimes."""

    def test_license_est_le_texte_gplv3_seul(self):
        """Texte GPLv3 de bout en bout, sans avis devant (sinon NOASSERTION)."""
        texte = _lire("LICENSE")
        self.assertTrue(texte.lstrip().startswith("GNU GENERAL PUBLIC LICENSE"))
        self.assertIn("Version 3, 29 June 2007", texte.splitlines()[1])
        self.assertTrue(
            texte.rstrip().endswith("<https://www.gnu.org/licenses/why-not-lgpl.html>.")
        )
        self.assertNotIn("Bertuzzi", texte)

    def test_notice_conserve_l_avis_amont(self):
        """L'avis de Renzo Bertuzzi est conservé mot pour mot."""
        notice = _lire("NOTICE")
        self.assertIn("Renzo Bertuzzi L. <kuthulu@gmail.com>", notice)
        self.assertIn(
            "it under the terms of the GNU General Public License version 3, as\npublished by the Free Software Foundation.",
            notice,
        )

    def test_notice_liste_les_fichiers_amont(self):
        """La liste GPL-3.0 seule de NOTICE est celle de AMONT."""
        notice = _lire("NOTICE")
        bloc = notice.split("- GPL-3.0-only :", 1)[1].split("- GPL-3.0-or-later :", 1)[0]
        listes = set(re.findall(r"[\w.-]+\.(?:py|expect|css)|postinst", bloc))
        self.assertEqual(listes, AMONT)

    def test_notice_documente_le_code_vendorise(self):
        """Code vendorisé et dépendances optionnelles documentés."""
        notice = _lire("NOTICE")
        for depot in ("gtk-frdp/", "SSH-Studio/", "pyAES.py"):
            self.assertIn(depot, notice)
        for extra in ("netmiko", "paramiko", "libvirt-python", "pycryptodomex", "loguru"):
            self.assertIn(extra, notice)


class TestEntetesSpdx(unittest.TestCase):
    """Chaque fichier de code annonce son régime de licence."""

    def test_chaque_fichier_de_code_a_le_bon_regime(self):
        """En-tête SPDX dans les 3 premières lignes, avec le régime attendu."""
        erreurs = []
        for f in _fichiers_de_code():
            tete = "\n".join(_lire(f).splitlines()[:3])
            trouve = SPDX.search(tete)
            attendu = TIERS.get(f) or ("GPL-3.0-only" if f in AMONT else "GPL-3.0-or-later")
            if not trouve:
                erreurs.append(f"{f} : pas d'en-tête SPDX dans les 3 premières lignes")
            elif trouve.group(1).rstrip("*/") != attendu:
                erreurs.append(f"{f} : {trouve.group(1)} au lieu de {attendu}")
        self.assertEqual(erreurs, [])

    def test_la_selection_couvre_les_fichiers_amont(self):
        """Les fichiers amont et tiers sont contrôlés, les vendorisés exclus."""
        fichiers = set(_fichiers_de_code())
        self.assertLessEqual(AMONT | set(TIERS), fichiers)
        self.assertFalse(any(f.startswith(VENDORISES) for f in fichiers))


class TestPaquets(unittest.TestCase):
    """Les paquets .deb et .rpm reprennent les mêmes régimes que NOTICE."""

    def test_debian_copyright_reprend_les_trois_regimes(self):
        """DEP-5 : fichiers GPL-3 (amont), Expat (pyAES) et GPL-3+."""
        copyright_ = _lire("build-deb/debian/copyright")
        bloc = copyright_.split("License: GPL-3+", 1)[1].split("License: GPL-3\n", 1)[0]
        fichiers = set(bloc.split("Files:", 1)[1].split("Copyright:", 1)[0].split())
        self.assertEqual(fichiers, AMONT)
        self.assertIn("Files: pyAES.py", copyright_)
        for licence in ("License: GPL-3+\n", "License: GPL-3\n", "License: Expat\n"):
            self.assertIn(licence, copyright_)

    def test_rpm_declare_l_expression_complete(self):
        """Champ License SPDX complet et NOTICE installé avec LICENSE."""
        spec = _lire("build-rpm/gcm4.spec")
        self.assertRegex(spec, r"(?m)^License:\s+GPL-3\.0-only AND GPL-3\.0-or-later AND MIT$")
        self.assertRegex(spec, r"(?m)^%license LICENSE NOTICE$")

    def test_notice_embarque_dans_les_paquets(self):
        """NOTICE est copié par les scripts de construction."""
        self.assertIn("LICENSE NOTICE", _lire("build-rpm/build.sh"))
        self.assertIn("LICENSE NOTICE", _lire("build-deb/build.sh"))
        self.assertIn("cat NOTICE LICENSE", _lire("Makefile"))


if __name__ == "__main__":
    unittest.main()
