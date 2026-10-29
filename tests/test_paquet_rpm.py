# SPDX-License-Identifier: GPL-3.0-or-later
"""Paquet .rpm de la base GTK4 (issue #157) : cohérence de build-rpm/.

Le paquet est construit sur Fedora puis vérifié et installé par les jobs CI
« Paquet .rpm (construction) » et « Paquets (.rpm, …) »
(``scripts/smoke-test-packages.sh rpm-dnf``). Ces tests, sans rpm ni GTK,
vérifient l'accord entre ``build-rpm/``, le .deb (#156), ``pyproject.toml``,
``data/`` et la CI.
"""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parent.parent
SPEC = (ROOT / "build-rpm" / "gcm4.spec").read_text(encoding="utf-8")
BUILD_SH = (ROOT / "build-rpm" / "build.sh").read_text(encoding="utf-8")
DEB_RULES = (ROOT / "build-deb" / "debian" / "rules").read_text(encoding="utf-8")
SMOKE = (ROOT / "scripts" / "smoke-test-packages.sh").read_text(encoding="utf-8")
CI = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
APP_ID = "io.github.MathildeDec.gcm4"
DEPENDANCES_DE_PLUGIN = re.compile(r"vte|gtk-?vnc|spice|freerdp|expect|paramiko", re.I)


def requires() -> dict[str, str]:
    """``Requires:`` du spec : nom -> contrainte de version (vide si aucune)."""
    resultat = {}
    for ligne in re.findall(r"^Requires:\s*(.+)$", SPEC, re.M):
        nom, _, contrainte = ligne.strip().partition(" ")
        resultat[nom] = contrainte.strip()
    return resultat


def section(nom: str) -> str:
    """Corps d'une section du spec (``%install``, ``%files``...)."""
    sections = "description|prep|build|install|check|files|changelog|pre|post|preun|postun"
    trouve = re.search(rf"^%{nom}\b.*?\n(.*?)(?=^%(?:{sections})\b|\Z)", SPEC, re.M | re.S)
    assert trouve, f"section %{nom} absente"
    return trouve.group(1)


class TestSpec(unittest.TestCase):
    """build-rpm/gcm4.spec : dépendances D1 de #118, aucune de plugin."""

    def test_dependances_de_la_base(self):
        """PyGObject, GTK 4 et libadwaita (conseil Fedora de gcm4.app), loguru, Python."""
        app = (ROOT / "src" / "gcm4" / "app.py").read_text(encoding="utf-8")
        paquets = re.search(r'"fedora": "([^"]+)"', app)
        assert paquets
        attendues = {*paquets.group(1).split(), "python3-loguru", "python3"}
        self.assertEqual(set(requires()), attendues)

    def test_aucune_dependance_de_plugin(self):
        """Ni Requires, ni Recommends, ni Suggests vers une bibliothèque de plugin."""
        self.assertEqual([d for d in requires() if DEPENDANCES_DE_PLUGIN.search(d)], [])
        self.assertNotRegex(SPEC, r"(?m)^(Recommends|Suggests):")

    def test_version_de_python_celle_de_pyproject(self):
        """``python3 >= 3.12`` suit ``requires-python`` (D4 de #118)."""
        minimum = re.match(r">=\s*([\d.]+)", PYPROJECT["project"]["requires-python"])
        assert minimum
        self.assertEqual(requires()["python3"], f">= {minimum.group(1)}")

    def test_version_fournie_par_build_sh(self):
        """Une seule version : celle de pyproject.toml, passée au spec."""
        self.assertIn("Version:        %{gcm4_version}", SPEC)
        self.assertIn('--define "gcm4_version $VERSION"', BUILD_SH)
        self.assertIn("pyproject.toml", BUILD_SH)

    def test_paquet_noarch(self):
        """Python pur, un seul paquet pour Fedora et EL."""
        self.assertRegex(SPEC, r"(?m)^BuildArch:\s+noarch$")
        self.assertRegex(SPEC, r"(?m)^Name:\s+gcm4$")


class TestContenu(unittest.TestCase):
    """Même contenu que le .deb (#156)."""

    def test_meme_dossier_que_le_deb_et_le_lanceur(self):
        """/usr/share/gcm4, lu par le lanceur commun build-deb/wrappers/gcm4."""
        self.assertIn("%{buildroot}%{_datadir}/gcm4", section("install"))
        self.assertIn("$(PKGDIR)/usr/share/gcm4", DEB_RULES)
        self.assertIn('cp "$REPO_ROOT/build-deb/wrappers/gcm4" "$STAGE_DIR/gcm4"', BUILD_SH)
        self.assertIn("install -D -m 755 gcm4 %{buildroot}%{_bindir}/gcm4", section("install"))

    def test_wheel_construit_avec_tout_son_contenu(self):
        """Les fichiers du wheel (only-include) sont copiés avant la construction."""
        copie = re.search(r"git ls-files -z -- (src/gcm4.+?)\| xargs", BUILD_SH, re.S)
        assert copie
        copies = copie.group(1).replace("\\\n", " ").split()
        inclus = PYPROJECT["tool"]["hatch"]["build"]["targets"]["wheel"]["only-include"]
        self.assertEqual(sorted(set(inclus) - set(copies)), [])
        self.assertIn("-py3-none-any.whl", section("install"))

    def test_fichiers_du_bureau_et_icones(self):
        """Lanceur .desktop, metainfo et icônes de data/, déclarés dans %files."""
        fichiers = section("files")
        for chemin in (
            f"%{{_datadir}}/applications/{APP_ID}.desktop",
            f"%{{_datadir}}/metainfo/{APP_ID}.metainfo.xml",
            f"%{{_datadir}}/icons/hicolor/*/apps/{APP_ID}.png",
        ):
            with self.subTest(chemin=chemin):
                self.assertIn(chemin, fichiers)
        self.assertIn(f"-name '{APP_ID}.png'", section("install"))

    def test_catalogues_du_domaine_de_gcm4(self):
        """%find_lang porte sur le domaine que gcm4.core.i18n charge."""
        i18n = (ROOT / "src" / "gcm4" / "core" / "i18n.py").read_text(encoding="utf-8")
        domaine = re.search(r'^DOMAIN = "([^"]+)"$', i18n, re.M)
        assert domaine
        self.assertIn(f"%find_lang {domaine.group(1)}", section("install"))
        self.assertIn(f"%files -f {domaine.group(1)}.lang", SPEC)
        self.assertIn('scripts/i18n-update.sh" --compile "$STAGE_DIR/locale"', BUILD_SH)

    def test_modules_prives_sans_provides_python(self):
        """Les modules de /usr/share/gcm4 ne sont pas proposés aux autres paquets."""
        self.assertIn("%global __provides_exclude_from ^%{_datadir}/gcm4/.*$", SPEC)

    def test_build_sh_syntaxe_et_executable(self):
        """``bash -n`` et bit exécutable (lancé tel quel par la CI)."""
        script = ROOT / "build-rpm" / "build.sh"
        self.assertTrue(script.stat().st_mode & 0o111)
        subprocess.run(["bash", "-n", str(script)], check=True)


class TestFumee(unittest.TestCase):
    """Le test de fumée vérifie le .rpm comme le .deb."""

    def test_memes_dependances_attendues_que_le_spec(self):
        """Le mode rpm exige exactement les Requires du spec."""
        attendues = re.search(r'check_depends "\$\(rpm -qpR.*?\n\s+(.+)$', SMOKE, re.M)
        assert attendues
        self.assertEqual(set(attendues.group(1).split()), set(requires()))

    def test_rpm_dnf_reprend_les_verifications_du_deb(self):
        """Installation avec dnf puis mêmes vérifications 2 à 7 que le .deb."""
        corps = re.search(r"^check_rpm_dnf\(\) \{\n(.*?)^\}", SMOKE, re.M | re.S)
        assert corps
        self.assertIn("check_rpm_content", corps.group(1))
        self.assertIn("dnf install", corps.group(1))
        self.assertIn("check_installed", corps.group(1))

    def test_jobs_ci(self):
        """Construction sur Fedora, test de fumée avec installation dnf."""
        self.assertIn("build-rpm/build.sh", CI)
        self.assertIn("needs: packages-rpm-build", CI)
        self.assertIn("run: scripts/smoke-test-packages.sh rpm-dnf dist/gcm4-*.noarch.rpm", CI)
        self.assertIn("rm -f /etc/rpm/macros.image-language-conf", CI)
