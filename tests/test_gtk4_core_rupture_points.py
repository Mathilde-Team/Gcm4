# SPDX-License-Identifier: GPL-3.0-or-later
"""Verrous textuels sur l'état des points de rupture GTK4 (audit session-28).

Contexte : `docs/gtk4-migration.md` §3.2 inventorie les points de rupture
GTK3 → GTK4 connus du cœur de l'application. La session-28 (2026-09-10) a
constaté que trois de ces points, présentés par la documentation comme
encore ouverts, étaient en réalité déjà résolus ou n'étaient jamais restés
des points de rupture *vivants* :

- ``Gtk.Dialog.run()`` — tous les appelants passent déjà par
  ``utils.run_dialog_sync()`` (palliatif ``GLib.MainLoop`` documenté comme
  fonctionnant aussi bien en GTK3 qu'en GTK4).
- ``GtkMenuBar``/``GtkMenu`` (menu principal) — déjà remplacé par un menu
  hamburger (``Gtk.MenuButton`` + ``Gio.Menu``/``Gio.SimpleAction``, voir
  ``Wmain._build_primary_menu()``), sans qu'aucune trace de ce travail
  n'apparaisse dans la documentation de suivi avant cette session.
- ``Gtk.Widget.reparent()`` — la seule occurrence du fichier vivait dans
  ``_inject_spice_frame_LEGACY``, une méthode jamais appelée (aucune
  référence ailleurs dans le dépôt) ; retirée cette session (voir
  ``docs/sessions/session-28.md``).

Un point supplémentaire, non répertorié comme résolu par la documentation
existante, a aussi été corrigé cette session : des constantes ``Gtk.STOCK_*``
(supprimées en GTK4) subsistaient dans ``plugins/plugin_spice.py``
(``_ask_password_dialog``), alors que le nettoyage équivalent était déjà
fait ailleurs dans le projet (ex. ``plugins/plugin_ssh.py``).

Ce fichier ne teste PAS un comportement runtime (aucun import réel de
``gnome_connection_manager.py``/``plugins/plugin_spice.py`` : ces modules
importent ``gi``/``Gtk``, indisponible dans cet environnement de
développement — voir CLAUDE.md). Il verrouille l'état du *texte source*,
sur le même principe que ``tests/test_plugin_base.py``
(``test_no_require_version_call_in_source``) : si l'un de ces points de
rupture est réintroduit par erreur (regression d'un futur commit qui
réutiliserait `.reparent()`, `Gtk.STOCK_*`, ou un ancien `GtkMenuBar`), le
test correspondant échoue immédiatement, sans avoir besoin de PyGObject.
"""

import os
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORE_FILE = os.path.join(REPO_ROOT, "gnome_connection_manager.py")
PLUGINS_DIR = os.path.join(REPO_ROOT, "plugins")


def _read(path: str) -> str:
    """Lit un fichier texte du dépôt en UTF-8.

    Args:
        path (str): Chemin absolu du fichier à lire.

    Returns:
        str: Contenu intégral du fichier.
    """
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _iter_plugin_files():
    """Itère les fichiers `.py` de premier niveau de `plugins/`.

    Returns:
        list[str]: Chemins absolus des fichiers `plugins/*.py` (hors
        sous-dossiers comme `plugins/ssh/`, non concernés par cet audit).
    """
    return sorted(
        os.path.join(PLUGINS_DIR, name)
        for name in os.listdir(PLUGINS_DIR)
        if name.endswith(".py") and os.path.isfile(os.path.join(PLUGINS_DIR, name))
    )


class TestReparentRuptureResolved(unittest.TestCase):
    """`Gtk.Widget.reparent()` : plus aucune occurrence vivante attendue."""

    def test_no_reparent_call_in_core_source(self):
        """Aucun appel `.reparent(` ne doit subsister dans le cœur.

        L'unique occurrence (`_inject_spice_frame_LEGACY`, jamais appelée)
        a été retirée en session-28 : elle rendait ce point de rupture
        faussement "ouvert" alors qu'il était déjà mort code.
        """
        source = _read(CORE_FILE)
        self.assertNotIn(
            ".reparent(",
            source,
            "Un appel .reparent() est réapparu dans gnome_connection_manager.py "
            "— supprimé en GTK4, sans remplacement direct (voir "
            "docs/gtk4-migration.md §3.2). Restructurer via remove()/insert "
            "plutôt que réintroduire reparent().",
        )

    def test_legacy_dead_method_removed(self):
        """`_inject_spice_frame_LEGACY` ne doit plus être défini comme méthode.

        Le nom peut légitimement subsister dans un commentaire historique
        (cf. le bloc « Méthodes supprimées » juste au-dessus dans le
        fichier) — seule sa *définition* (`def ...`) doit avoir disparu.
        """
        source = _read(CORE_FILE)
        self.assertNotIn("def _inject_spice_frame_LEGACY", source)


class TestMenuBarRuptureResolved(unittest.TestCase):
    """`GtkMenuBar`/`GtkMenu` (menu principal) : déjà remplacé par Gio.Menu."""

    def test_no_menubar_instantiation_in_core_source(self):
        """Aucune instanciation `Gtk.MenuBar(` ne doit exister dans le cœur."""
        source = _read(CORE_FILE)
        self.assertNotIn(
            "Gtk.MenuBar(",
            source,
            "Un Gtk.MenuBar() est apparu dans gnome_connection_manager.py — "
            "supprimé en GTK4 ; le menu principal doit passer par le menu "
            "hamburger (_build_primary_menu / Gio.Menu), pas par un menu bar "
            "classique.",
        )

    def test_primary_menu_builder_present_and_wired(self):
        """Le menu hamburger (`_build_primary_menu`) doit exister et être câblé."""
        source = _read(CORE_FILE)
        self.assertIn("def _build_primary_menu(self):", source)
        self.assertIn("self._build_primary_menu()", source)
        self.assertIn("btnPrimaryMenu.set_menu_model(menu)", source)


class TestDialogRunRuptureResolved(unittest.TestCase):
    """`Gtk.Dialog.run()` : tous les appelants passent par run_dialog_sync()."""

    # Préfixes/suffixes de ligne connus et acceptés pour `.run(` dans le
    # cœur — tout `.run(` qui ne matche aucun de ces motifs est considéré
    # comme une régression potentielle (un dialogue appelé en direct plutôt
    # que via run_dialog_sync).
    _ALLOWED_SNIPPETS = (
        "subprocess.run(",
        "_sp.run(",  # `import subprocess as _sp` (détection thème desktop)
        "_sp2.run(",  # idem, second point d'appel (détection dark mode)
        "w_main.run()",
        "run_dialog_sync",  # définition/appels/mentions en commentaire ou docstring
        ".run()`.",  # fragment de docstring citant Gtk.Dialog.run()
        "Gtk.Dialog.run()",  # mentions documentaires du nom de méthode supprimée
        "``.run()``",
        "`.run()`",
    )

    def test_no_unmanaged_run_call_in_core_source(self):
        """Chaque occurrence de `.run(` doit correspondre à un usage connu et sûr."""
        source = _read(CORE_FILE)
        offending_lines = []
        for lineno, line in enumerate(source.splitlines(), start=1):
            if ".run(" not in line:
                continue
            if any(snippet in line for snippet in self._ALLOWED_SNIPPETS):
                continue
            offending_lines.append((lineno, line.strip()))
        self.assertEqual(
            offending_lines,
            [],
            "Appel(s) `.run(` non reconnu(s) dans gnome_connection_manager.py "
            "— si un dialogue GTK est concerné, passer par "
            f"utils.run_dialog_sync() : {offending_lines}",
        )


class TestNoStockConstantsRemain(unittest.TestCase):
    """`Gtk.STOCK_*` : nettoyage étendu au plugin SPICE cette session."""

    def test_no_stock_constant_in_core_source(self):
        """Le cœur ne doit référencer aucune constante `Gtk.STOCK_*`."""
        source = _read(CORE_FILE)
        self.assertNotIn("Gtk.STOCK_", source)

    def test_no_stock_constant_in_plugin_sources(self):
        """Aucun plugin de premier niveau ne doit référencer `Gtk.STOCK_*`.

        `plugins/plugin_spice.py` utilisait encore `Gtk.STOCK_CANCEL`/
        `Gtk.STOCK_OK` avant cette session (`_ask_password_dialog`) —
        corrigé en libellés traduits littéraux, comme le reste du projet.
        """
        offenders = []
        for path in _iter_plugin_files():
            if "Gtk.STOCK_" in _read(path):
                offenders.append(os.path.relpath(path, REPO_ROOT))
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
