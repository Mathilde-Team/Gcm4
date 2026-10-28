# SPDX-License-Identifier: GPL-3.0-or-later
"""Paquet .deb de la base GTK4 (issue #156) : cohérence des fichiers de build-deb/.

Le paquet est construit puis installé et lancé par les jobs CI « Paquet .deb
(construction) » et « Paquets (.deb, …) » (``scripts/smoke-test-packages.sh``).
Ces tests, sans dpkg ni GTK, vérifient ce qui doit rester d'accord entre
``build-deb/``, ``pyproject.toml``, ``data/`` et la CI : dépendances (aucune
de plugin), dossier d'installation, lanceur, contenu du wheel, icônes.
"""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parent.parent
BUILD_DEB = ROOT / "build-deb"
CONTROL = (BUILD_DEB / "debian" / "control").read_text(encoding="utf-8")
RULES = (BUILD_DEB / "debian" / "rules").read_text(encoding="utf-8")
BUILD_SH = (BUILD_DEB / "build.sh").read_text(encoding="utf-8")
WRAPPER = BUILD_DEB / "wrappers" / "gcm4"
SMOKE = ROOT / "scripts" / "smoke-test-packages.sh"
CI = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
APP_ID = "io.github.MathildeDec.gcm4"
# Bibliothèques des plugins de connexion (VTE, gtk-vnc, spice, freerdp,
# expect, paramiko) : jamais dans la base (#126).
DEPENDANCES_DE_PLUGIN = re.compile(r"vte|gtk-?vnc|spice|freerdp|expect|paramiko", re.I)


def paragraphe_binaire() -> dict[str, str]:
    """Champs du paquet binaire de debian/control (lignes de suite jointes)."""
    bloc = CONTROL.split("\n\n")[1]
    champs: dict[str, str] = {}
    cle = ""
    for ligne in bloc.splitlines():
        if ligne.startswith("#"):
            continue
        if ligne[:1].isspace():
            champs[cle] += " " + ligne.strip()
        else:
            cle, _, valeur = ligne.partition(":")
            champs[cle] = valeur.strip()
    return champs


def dependances() -> list[str]:
    """Noms (sans version) des dépendances déclarées, hors substitutions."""
    brut = paragraphe_binaire()["Depends"]
    return [
        d.split("(")[0].strip()
        for d in brut.split(",")
        if d.strip() and not d.strip().startswith("${")
    ]


class TestControl(unittest.TestCase):
    """debian/control : paquet de la base, dépendances D1 de #118."""

    def test_paquet_gcm4_independant_de_l_architecture(self):
        """Paquet ``gcm4``, ``Architecture: all`` (Python pur)."""
        champs = paragraphe_binaire()
        self.assertEqual(champs["Package"], "gcm4")
        self.assertEqual(champs["Architecture"], "all")

    def test_dependances_de_la_base(self):
        """PyGObject, GTK 4, libadwaita et loguru, comme le conseille gcm4.app."""
        app = (ROOT / "src" / "gcm4" / "app.py").read_text(encoding="utf-8")
        paquets_gtk = re.search(r'"debian": "([^"]+)"', app)
        assert paquets_gtk
        attendues = {*paquets_gtk.group(1).split(), "python3-loguru", "python3"}
        self.assertEqual(set(dependances()), attendues)

    def test_aucune_dependance_de_plugin(self):
        """Ni Depends, ni Recommends, ni Suggests vers une bibliothèque de plugin."""
        self.assertEqual(
            [d for d in dependances() if DEPENDANCES_DE_PLUGIN.search(d)],
            [],
        )
        for champ in ("Recommends", "Suggests"):
            self.assertNotIn(champ, paragraphe_binaire())

    def test_version_de_python_celle_de_pyproject(self):
        """``python3 (>= 3.12)`` suit ``requires-python`` (D4 de #118)."""
        minimum = re.match(r">=\s*([\d.]+)", PYPROJECT["project"]["requires-python"])
        assert minimum
        self.assertIn(f"python3 (>= {minimum.group(1)})", paragraphe_binaire()["Depends"])

    def test_python3_depends_pour_la_compilation_a_l_installation(self):
        """Sans ``${python3:Depends}``, dh_python3 refuse d'agir (pas de py3compile)."""
        self.assertIn("${python3:Depends}", paragraphe_binaire()["Depends"])
        self.assertIn("dh_python3 /usr/share/gcm4", RULES)


class TestInstallation(unittest.TestCase):
    """debian/rules, build.sh et le lanceur s'accordent sur l'emplacement."""

    def test_le_lanceur_lit_le_dossier_installe_par_rules(self):
        """/usr/bin/gcm4 cherche les modules là où rules les installe."""
        texte = WRAPPER.read_text(encoding="utf-8")
        self.assertTrue(texte.startswith("#!/usr/bin/python3\n"))
        self.assertIn('sys.path.insert(0, "/usr/share/gcm4")', texte)
        self.assertIn("$(PKGDIR)/usr/share/gcm4", RULES)
        self.assertIn("install -D -m 755 wrappers/gcm4 $(PKGDIR)/usr/bin/gcm4", RULES)

    def test_le_lanceur_appelle_la_commande_de_pyproject(self):
        """Même point d'entrée que ``[project.scripts]``."""
        module, fonction = PYPROJECT["project"]["scripts"]["gcm4"].split(":")
        self.assertIn(f"from {module} import {fonction}", WRAPPER.read_text(encoding="utf-8"))

    def test_scripts_executables(self):
        """Scripts lancés directement par la CI et dpkg-buildpackage."""
        for script in (BUILD_DEB / "build.sh", BUILD_DEB / "debian" / "rules", WRAPPER, SMOKE):
            with self.subTest(script=script.name):
                self.assertTrue(script.stat().st_mode & 0o111, f"{script} non exécutable")

    def test_scripts_bash_syntaxe(self):
        """``bash -n`` : aucune erreur de syntaxe."""
        for script in (BUILD_DEB / "build.sh", SMOKE):
            with self.subTest(script=script.name):
                subprocess.run(["bash", "-n", str(script)], check=True)

    def test_build_copie_tout_le_contenu_du_wheel(self):
        """Ce que le wheel inclut (only-include) est copié dans l'arborescence de build."""
        copie = re.search(r"git ls-files -z -- (.+?)\| xargs", BUILD_SH, re.S)
        assert copie
        copies = copie.group(1).replace("\\\n", " ").split()
        inclus = PYPROJECT["tool"]["hatch"]["build"]["targets"]["wheel"]["only-include"]
        self.assertEqual(sorted(set(inclus) - set(copies)), [])
        for metadonnee in ("pyproject.toml", "README.md", "LICENSE", "data"):
            self.assertIn(metadonnee, copies)

    def test_version_lue_dans_pyproject(self):
        """Paquet et ``gcm4 --version`` (dist-info du wheel) ont la même version."""
        self.assertIn("pyproject.toml", BUILD_SH)
        self.assertIn("gcm4 ($VERSION) unstable", BUILD_SH)

    def test_catalogues_compiles_pour_usr_share_locale(self):
        """Les .mo vont où gcm4.core.i18n les cherche : <sys.prefix>/share/locale."""
        self.assertIn('scripts/i18n-update.sh" --compile "$BUILD_DIR/locale"', BUILD_SH)
        self.assertIn("$(PKGDIR)/usr/share/locale/", RULES)
        i18n = (ROOT / "src" / "gcm4" / "core" / "i18n.py").read_text(encoding="utf-8")
        self.assertIn('Path(sys.prefix) / "share" / "locale"', i18n)

    def test_fichiers_du_bureau_installes(self):
        """Lanceur, metainfo et icônes de data/ installés sous /usr/share."""
        self.assertIn("APP_ID := " + APP_ID, RULES)
        for fichier in (f"{APP_ID}.desktop", f"{APP_ID}.metainfo.xml"):
            with self.subTest(fichier=fichier):
                self.assertTrue((ROOT / "data" / fichier).is_file())
        self.assertIn("usr/share/applications/$(APP_ID).desktop", RULES)
        self.assertIn("usr/share/metainfo/$(APP_ID).metainfo.xml", RULES)
        self.assertIn("usr/share/icons/{}", RULES)


class TestFumee(unittest.TestCase):
    """scripts/smoke-test-packages.sh et les jobs CI qui l'utilisent."""

    def test_tailles_d_icones_verifiees_celles_de_data(self):
        """Le test de fumée vérifie chaque taille d'icône de data/."""
        texte = SMOKE.read_text(encoding="utf-8")
        tailles = re.search(r"ICON_SIZES=\(([^)]*)\)", texte)
        assert tailles
        presentes = sorted(
            p.parent.parent.name
            for p in (ROOT / "data" / "icons" / "hicolor").glob("*/apps/*.png")
        )
        self.assertEqual(sorted(tailles.group(1).split()), presentes)

    def test_meme_motif_de_dependances_de_plugin(self):
        """Le test de fumée refuse les mêmes dépendances que ce test."""
        texte = SMOKE.read_text(encoding="utf-8")
        self.assertIn(f"PLUGIN_DEPS_RE='{DEPENDANCES_DE_PLUGIN.pattern}'", texte)

    def test_jobs_ci(self):
        """Construction puis installation dans des conteneurs propres."""
        self.assertIn("run: |\n          git config --global --add safe.directory", CI)
        self.assertIn("build-deb/build.sh", CI)
        self.assertIn("needs: packages-deb-build", CI)
        self.assertIn("run: scripts/smoke-test-packages.sh deb dist/gcm4_*_all.deb", CI)
        matrice = re.search(r'image: \[("debian:13".*?)\]', CI)
        assert matrice
        self.assertEqual(
            [i.strip().strip('"') for i in matrice.group(1).split(",")],
            ["debian:13", "ubuntu:24.04", "ubuntu:26.04"],
        )

    def test_mode_inconnu_refuse(self):
        """Un mode inconnu n'est pas un succès vide."""
        resultat = subprocess.run(
            ["bash", str(SMOKE), "msi", "absent.msi"], capture_output=True, text=True
        )
        self.assertNotEqual(resultat.returncode, 0)
        self.assertIn("mode inconnu", resultat.stderr)


class TestCibleMakeDeb(unittest.TestCase):
    """``make deb`` construit le paquet gcm4 : plus de .deb GTK3 (#156)."""

    MAKEFILE = (ROOT / "Makefile").read_text(encoding="utf-8")
    RELEASE = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    def recette(self, cible: str) -> list[str]:
        """Lignes de la recette d'une cible du Makefile."""
        bloc = re.search(rf"^{cible}:.*\n((?:\t.*\n)*)", self.MAKEFILE, re.M)
        assert bloc, f"cible {cible} absente du Makefile"
        return [ligne.strip() for ligne in bloc.group(1).splitlines()]

    def test_make_deb_appelle_build_sh(self):
        """La recette de ``deb`` est build-deb/build.sh, sans fpm."""
        self.assertEqual(self.recette("deb"), ["build-deb/build.sh"])
        self.assertNotIn("fpm -t deb", self.MAKEFILE)

    def test_ancien_nom_deb_gcm4_conserve(self):
        """``make deb-gcm4`` reste un alias de ``make deb``."""
        self.assertRegex(self.MAKEFILE, r"(?m)^deb-gcm4: deb$")

    def test_release_publie_le_paquet_gcm4(self):
        """Le workflow de release construit et joint le .deb de gcm4."""
        self.assertIn("make deb\n", self.RELEASE)
        self.assertNotIn("make deb PKG_VERSION", self.RELEASE)
        self.assertIn("cp build-deb/dist/gcm4_*_all.deb .", self.RELEASE)
        for outil in ("debhelper", "dh-python", "dpkg-dev", "python3-build", "python3-hatchling"):
            self.assertIn(outil, self.RELEASE)

    def test_release_verifie_le_contenu_du_deb_gcm4(self):
        """Le .deb est vérifié sur le lanceur et le dossier /usr/share/gcm4."""
        self.assertIn("/usr/bin/gcm4 /usr/share/gcm4/gcm4/__init__.py", self.RELEASE)
        self.assertNotIn('"$RUNNER_TEMP"/deb.txt "$RUNNER_TEMP"/*.rpm.txt', self.RELEASE)
