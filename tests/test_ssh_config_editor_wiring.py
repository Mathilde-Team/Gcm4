# SPDX-License-Identifier: GPL-3.0-or-later
"""Verrou textuel : `ssh_config_editor.py` est bien câblé dans l'UI (audit session-34).

Contexte : `docs/features-backlog.md` (« Pas encore fait », urgence haute) et
`docs/architecture.md` §2.5 affirmaient depuis plusieurs sessions qu'aucun
appel à `ssh_config_editor.py` n'avait été retrouvé dans le dépôt et que son
point d'intégration menu restait à localiser — au même titre que
`snmp_push_core.py` (§2.2-bis), un cas où ne pas trancher seul(e) sans
l'auteure. La session-34 (2026-09-14) a repris cette recherche textuelle et
trouvé le câblage réel, déjà en place avant cette session mais jamais
documenté (même profil que la découverte de `_build_primary_menu()` déjà
câblé en menu hamburger à la session-28, ou du plugin SSH déjà intégralement
extrait à la session-23) :

- `SshPlugin.menu_actions()` (`plugins/plugin_ssh.py`) déclare l'action
  `"edit-ssh-config"` vers `SshPlugin.edit_ssh_config()`.
- `SshPlugin.edit_ssh_config()` importe `SshConfigEditorDialog` depuis
  `ssh_config_editor` et l'ouvre en onglet épinglé via
  `Wmain.open_management_tab()`.
- `Wmain._build_primary_menu()` (`gnome_connection_manager.py`) itère
  `plugin.menu_actions()` pour chaque plugin enregistré et ajoute chaque
  entrée à `edit_section3`, elle-même intégrée au menu « Edit » du menu
  hamburger (`edit_menu.append_section(None, edit_section3)`).

Résultat : l'entrée « Edit ~/.ssh/config… » est bien accessible depuis le
menu hamburger → Edit, contrairement à ce que documentait
`architecture.md` §2.5 (corrigé cette session). Ce n'était pas un choix
produit ouvert à trancher avec l'auteure, seulement une documentation restée
en retard sur le code — voir `docs/sessions/session-34.md`.

Ce fichier ne teste PAS un comportement runtime (même contrainte que
`test_gtk4_core_rupture_points.py`/`test_gtk4_context_menus_baseline.py` :
`plugins/plugin_ssh.py` et `gnome_connection_manager.py` importent
`gi`/`Gtk`/`Vte`, indisponibles dans cet environnement de développement, cf.
CLAUDE.md). Il verrouille l'état du *texte source* des trois méthodes
concernées, sur le même principe : si l'une d'elles perd ce câblage par
erreur dans un futur commit (ex. lors du découpage `plugins/ssh/gtk4.py`,
`docs/gtk4-migration.md` §3.6), le test correspondant échoue immédiatement,
sans avoir besoin de PyGObject/GTK3.
"""

import os
import re
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN_SSH_FILE = os.path.join(REPO_ROOT, "plugins", "plugin_ssh.py")
CORE_FILE = os.path.join(REPO_ROOT, "gnome_connection_manager.py")


def _read(path: str) -> str:
    """Lit un fichier texte du dépôt en UTF-8.

    Args:
        path (str): Chemin absolu du fichier à lire.

    Returns:
        str: Contenu intégral du fichier.
    """
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _extract_method_body(source: str, method_name: str) -> str:
    """Isole le corps texte d'une méthode ``def method_name(self...):``.

    Recherche par indentation (pas d'AST) : va de la ligne ``def
    method_name`` jusqu'à la prochaine ``def `` de même indentation
    (exclue), ou jusqu'à la fin du fichier si aucune ne suit — même
    principe que les scans de `test_gtk4_core_rupture_points.py`, en plus
    ciblé (une seule méthode plutôt que tout le fichier).

    Args:
        source (str): Code source complet du fichier contenant la méthode.
        method_name (str): Nom de la méthode à isoler.

    Returns:
        str: Texte du corps de la méthode, docstring comprise.

    Raises:
        AssertionError: si aucune méthode de ce nom n'est trouvée.
    """
    pattern = re.compile(
        rf"^(?P<indent>[ \t]*)def {re.escape(method_name)}\(self.*?\):.*?(?=\n(?P=indent)def |\Z)",
        re.DOTALL | re.MULTILINE,
    )
    match = pattern.search(source)
    assert match is not None, f"méthode {method_name}() introuvable dans le fichier fourni"
    return match.group(0)


class TestSshConfigEditorMenuWiring(unittest.TestCase):
    """Verrouille le câblage `ssh_config_editor.py` → menu hamburger « Edit »."""

    def test_menu_actions_exposes_edit_ssh_config_action(self):
        """`SshPlugin.menu_actions()` déclare bien l'action `edit-ssh-config`."""
        body = _extract_method_body(_read(PLUGIN_SSH_FILE), "menu_actions")
        self.assertIn('"edit-ssh-config"', body)
        self.assertIn("self.edit_ssh_config", body)

    def test_edit_ssh_config_imports_and_opens_dialog(self):
        """`SshPlugin.edit_ssh_config()` importe et ouvre `SshConfigEditorDialog`."""
        body = _extract_method_body(_read(PLUGIN_SSH_FILE), "edit_ssh_config")
        self.assertIn("from ssh_config_editor import SshConfigEditorDialog", body)
        self.assertIn("wMain.open_management_tab(", body)
        self.assertIn('"ssh-config"', body)

    def test_build_primary_menu_consumes_plugin_menu_actions(self):
        """`Wmain._build_primary_menu()` consomme `plugin.menu_actions()` par plugin."""
        body = _extract_method_body(_read(CORE_FILE), "_build_primary_menu")
        self.assertIn("plugin.menu_actions()", body)
        self.assertIn("edit_section3", body)
        self.assertIn("edit_menu.append_section(None, edit_section3)", body)


if __name__ == "__main__":
    unittest.main()
