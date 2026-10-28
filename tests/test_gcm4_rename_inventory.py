# SPDX-License-Identifier: GPL-3.0-or-later
"""Verrou d'inventaire pour le renommage GCM → gcm4 (audit session-30).

Contexte : la décision de renommer le projet (GCM → gcm4) a été actée le
2026-08-29 (`docs/gtk4-migration.md` §3.0) et appliquée aux livrables et à
la documentation, mais pas encore au code — chantier à part entière
(`CLAUDE.md`, « Prochaine étape » point 2), distinct de la migration GTK4
elle-même. La session-30 (2026-09-10) en a fait l'inventaire exhaustif
(voir `docs/gcm4-rename.md`) sans exécuter le renommage : six choix de noms
restent ouverts (module, dossier de config, domaine i18n, id `.desktop`,
`PKG_NAME`) et ne peuvent pas être tranchés seul
(`CONSIGNES-AGENTS-IA.md` §8).

Ce fichier ne teste pas de comportement runtime — il verrouille, par scan
textuel (même principe que `test_gtk4_context_menus_baseline.py` et
`test_gtk4_core_rupture_points.py`), les chiffres et occurrences connus à
la fin de la session-30 :

1. Détecter toute dérive (nouvel import du module, nouvelle occurrence du
   domaine i18n) introduite ailleurs par erreur pendant que le renommage
   n'est pas encore fait ;
2. Servir de check-list vivante pour la future session de renommage réel :
   quand un site est effectivement renommé, le test correspondant doit
   être mis à jour ici (voire supprimé une fois le renommage terminé), pas
   oublié ;
3. Verrouiller la régression corrigée cette session sur le `Makefile`
   (référence à un fichier `.glade` supprimé depuis la session-08, qui
   faisait échouer `make validate`/`make check` silencieusement).

Si un test échoue ici après un changement délibéré (renommage effectif),
c'est le signe attendu qu'il faut ajuster ou retirer le verrou concerné —
pas une régression à l'aveugle.
"""

import os
import re
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORE_FILE = os.path.join(REPO_ROOT, "gnome_connection_manager.py")
GCM4_CORE_FILE = os.path.join(REPO_ROOT, "gcm4_core.py")
MAKEFILE = os.path.join(REPO_ROOT, "Makefile")
DESKTOP_FILE = os.path.join(REPO_ROOT, "gnome-connection-manager.desktop")

VENDORED_DIR_NAMES = {"SSH-Studio", "gtk-frdp", "__pycache__", ".git"}

# Sites d'import réel du module principal, verrouillés à la fin de la
# session-30 (voir docs/gcm4-rename.md §1) — la ligne d'auto-alias
# `sys.modules["gnome_connection_manager"] = ...` dans le module lui-même
# n'est pas un import et n'est pas comptée ici, mais devra être adaptée en
# même temps que ces 12 sites lors du renommage réel.
EXPECTED_REAL_IMPORT_SITES = {
    os.path.join(REPO_ROOT, "widgets.py"),
    os.path.join(REPO_ROOT, "models.py"),
    os.path.join(REPO_ROOT, "ssh_config_editor.py"),
    os.path.join(REPO_ROOT, "ssh_key_manager_dialog.py"),
    os.path.join(REPO_ROOT, "key_picker_dialog.py"),
    os.path.join(REPO_ROOT, "utils.py"),
    os.path.join(REPO_ROOT, "plugins", "plugin_import_proxmox.py"),
    os.path.join(REPO_ROOT, "plugins", "plugin_telnet.py"),
    os.path.join(REPO_ROOT, "plugins", "plugin_local.py"),
    os.path.join(REPO_ROOT, "plugins", "plugin_ssh.py"),
    os.path.join(REPO_ROOT, "plugins", "plugin_import_libvirt.py"),
    os.path.join(REPO_ROOT, "tests", "test_gcm.py"),
}

REAL_IMPORT_RE = re.compile(
    r"^\s*(import gnome_connection_manager\b|from gnome_connection_manager import)"
)

EXPECTED_GCM_LANG_OCCURRENCES_IN_CORE = 1
# 0 depuis l'issue #165 : `make translate` délègue à scripts/i18n-update.sh,
# qui porte le domaine (variable DOMAIN, une seule occurrence).
EXPECTED_GCM_LANG_LINES_IN_MAKEFILE = 0

EXPECTED_DESKTOP_LINES = {
    "Name=Gnome Connection Manager",
    "Exec=/usr/share/gnome-connection-manager/gnome_connection_manager.py",
    "Icon=/usr/share/gnome-connection-manager/icon.png",
    "StartupWMClass=gnome_connection_manager.py",
    "Name[en]=Gnome Connection Manager",
}


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _iter_repo_python_files():
    """Itère les .py du dépôt, hors dossiers vendorisés/générés."""
    for dirpath, dirnames, filenames in os.walk(REPO_ROOT):
        dirnames[:] = [d for d in dirnames if d not in VENDORED_DIR_NAMES]
        for name in filenames:
            if name.endswith(".py"):
                yield os.path.join(dirpath, name)


class TestModuleImportSites(unittest.TestCase):
    """Sites d'import réel de `gnome_connection_manager` — 12 à la session-30."""

    def test_real_import_sites_match_baseline_exactly(self):
        """Le jeu exact des 12 fichiers ne doit ni grandir ni rétrécir sans le voir."""
        found = set()
        for path in _iter_repo_python_files():
            if path == CORE_FILE:
                continue  # le module ne s'importe pas lui-même
            for line in _read(path).splitlines():
                if REAL_IMPORT_RE.match(line):
                    found.add(path)
                    break
        self.assertEqual(
            found,
            EXPECTED_REAL_IMPORT_SITES,
            "La liste des fichiers important réellement gnome_connection_manager "
            "a changé depuis la session-30 — mettre à jour docs/gcm4-rename.md "
            "§1 et EXPECTED_REAL_IMPORT_SITES en même temps.",
        )

    def test_core_module_still_has_its_sys_modules_alias_trick(self):
        """L'astuce d'auto-alias doit exister — à adapter, pas supprimer, au renommage."""
        content = _read(CORE_FILE)
        self.assertIn('sys.modules["gnome_connection_manager"]', content)


class TestI18nDomainOccurrences(unittest.TestCase):
    """Occurrences du domaine i18n `gcm-lang` — 2 depuis l'issue #165."""

    def test_domain_name_declared_once_in_core(self):
        """Une seule déclaration `domain_name = "gcm-lang"` dans le module principal."""
        content = _read(CORE_FILE)
        self.assertEqual(content.count("gcm-lang"), EXPECTED_GCM_LANG_OCCURRENCES_IN_CORE)

    def test_makefile_gcm_lang_line_count(self):
        """Plus aucune ligne `msgfmt` codée en dur dans le Makefile (issue #165)."""
        content = _read(MAKEFILE)
        self.assertEqual(content.count("gcm-lang"), EXPECTED_GCM_LANG_LINES_IN_MAKEFILE)

    def test_i18n_script_declares_domain_once(self):
        """scripts/i18n-update.sh porte le domaine en un seul endroit."""
        content = _read(os.path.join(REPO_ROOT, "scripts", "i18n-update.sh"))
        self.assertEqual(content.count('"${GCM_I18N_DOMAIN:-gcm-lang}"'), 1)


class TestDesktopFileBaseline(unittest.TestCase):
    """Contenu du .desktop encore sous l'ancien nom — baseline à la session-30."""

    def test_desktop_file_still_uses_legacy_branding(self):
        """Les 5 lignes Name/Exec/Icon/StartupWMClass sous l'ancien nom sont encore là."""
        lines = {line.strip() for line in _read(DESKTOP_FILE).splitlines()}
        missing = EXPECTED_DESKTOP_LINES - lines
        self.assertFalse(
            missing,
            f"Lignes attendues absentes du .desktop (déjà renommé ?) : {missing}",
        )


class TestMakefileGladeRegressionFix(unittest.TestCase):
    """Non-régression sur le fix session-30 : plus de référence au .glade supprimé."""

    def test_makefile_no_longer_references_missing_glade_file(self):
        """Aucune ligne *exécutable* (hors commentaire) ne doit plus l'utiliser.

        Le nom du fichier supprimé reste légitimement cité dans le
        commentaire expliquant le fix (traçabilité) : on ne vérifie donc que
        les lignes de recette (hors `#`), pas le fichier dans son ensemble.
        """
        offending = [
            line
            for line in _read(MAKEFILE).splitlines()
            if "gnome-connection-manager.glade" in line and not line.strip().startswith("#")
        ]
        self.assertEqual(offending, [])


class TestConfigDirBaseline(unittest.TestCase):
    """Nom du dossier de config par défaut — baseline à la session-30."""

    def test_default_config_dir_name_is_still_dot_gcm(self):
        """`_resolve_config_dir()` résout encore `~/.gcm` par défaut."""
        content = _read(GCM4_CORE_FILE)
        self.assertIn('os.path.join(default_home, ".gcm")', content)


if __name__ == "__main__":
    unittest.main()
