# SPDX-License-Identifier: GPL-3.0-or-later
"""Verrou textuel sur les ruptures GTK3 des fichiers de widgets SSH (audit session-45).

Contexte : dernier des trois chantiers GTK4 dont l'ordre reste à confirmer
avec l'auteure (`CLAUDE.md`, `docs/gtk4-migration.md` §3.6-terdecies) —
`plugins/ssh/gtk4.py`, le couple `focus-out-event`/`populate-popup`, et RDP.
Avant de choisir lequel traiter, inventaire du périmètre réel de
`plugins/ssh/gtk4.py` : les trois fichiers GTK3 qui devront être fusionnés
dedans (`docs/gtk4-migration.md` §3.0-bis) ne sont pour l'instant NI
audités NI touchés — `ssh_config_editor.py`, `ssh_key_manager_dialog.py`,
`key_picker_dialog.py`, à la racine du dépôt (`plugins/ssh/core.py`, lui,
a déjà été extrait en session-26 et ne contient aucun import GTK).

Contrairement aux audits précédents (menus contextuels, points de rupture
du cœur, éditeur inline), ce périmètre est nettement plus large et plus
répétitif : les widgets `Gtk.Box.pack_start()`/`pack_end()`/`.add()`
apparaissent des dizaines de fois par fichier (boilerplate de construction
d'interface), ce que la Stratégie B actée (réécriture complète, pas de
portage incrémental — `docs/gtk4-migration.md` §3.0/§3.3) traitera de toute
façon en bloc lors du portage réel. Verrouiller chacune de ces occurrences
une par une n'aurait donc aucune valeur de check-list (contrairement aux
quatre signaux de l'éditeur inline, session-42, chacun remplaçable
indépendamment). Ce fichier se limite donc aux marqueurs qui représentent
une **rupture d'API distincte** nécessitant une décision de remplacement
consciente (méthode supprimée, signature changée, classe dépréciée) :

- ``show_all()`` — supprimé en GTK4 (confirmé sur le guide de migration
  officiel GNOME, section « gtk_widget_show_all » : la fonction, la
  propriété ``no-show-all`` et leurs accesseurs ont disparu). Les widgets
  sont visibles par défaut en GTK4 ; la ligne équivalente peut simplement
  être retirée dans la majorité des cas.
- ``set_border_width()`` — ``Gtk.Container`` (et sa propriété
  ``border-width``) a été supprimé en GTK4 ; remplacement par des marges
  (``set_margin_*()``) sur chaque enfant plutôt que sur le conteneur.
- ``Gtk.Clipboard.get(...)`` — classe entièrement retirée en GTK4, section
  dédiée du guide de migration officiel (« Replace GtkClipboard with
  GdkClipboard ») ; remplacement par ``widget.get_clipboard()``
  (``Gdk.Clipboard``) et son API asynchrone.
- ``dlg.get_filename()`` — n'existe plus sur l'interface ``Gtk.FileChooser``
  en GTK4 (confirmée absente de la liste des méthodes de l'interface,
  ``api.pygobject.gnome.org/Gtk-4.0/interface-FileChooser.html`` ;
  remplacée par ``get_file()`` qui retourne un ``Gio.File``, dont
  ``.get_path()`` donne l'équivalent).
- ``set_current_folder(str(...))`` — la signature change de type en GTK4 :
  ``gtk_file_chooser_set_current_folder(chooser, file: GFile, error)``,
  plus une chaîne (confirmé sur ``docs.rs``/``valadoc.org``, bindings
  générés depuis les headers GTK4 officiels).
- ``Gtk.FileChooserDialog(...)`` — la classe existe toujours en GTK4 (donc
  pas une rupture immédiate au sens strict), mais chacune de ses méthodes
  listées ci-dessus est dépréciée depuis GTK 4.10 au profit de
  ``Gtk.FileDialog`` (API asynchrone, callback-based) — un choix de cible
  de portage (adapter l'existant a minima vs. réécrire vers l'API
  recommandée) qui n'est pas tranché ici, volontairement : décision de
  produit/architecture, pas un simple renommage d'API.

Ce fichier ne teste PAS un comportement runtime (ces trois fichiers
importent `gi`/`Gtk` en verrouillant GTK3, indisponible dans cet
environnement de travail — voir le verrou `gi.require_version` ci-dessous,
qui documente justement que le portage n'a pas commencé). Il verrouille,
sur le même principe que `test_gtk4_context_menus_baseline.py`/
`test_gtk4_inline_editor_baseline.py`, le *nombre exact* d'occurrences de
chaque marqueur — pas pour les compter en soi, mais pour détecter toute
dérive silencieuse de ce périmètre (site déplacé, renommé, ou déjà porté
sans mise à jour de cette baseline) et servir de check-list vivante pour
une future session de portage.

Si un test échoue ici après un changement délibéré (portage effectif d'un
de ces marqueurs, ou fusion des trois fichiers dans `plugins/ssh/gtk4.py`
conformément à `docs/gtk4-migration.md` §3.0-bis), c'est le signe attendu
qu'il faut ajuster la baseline et `docs/gtk4-migration.md` §3.7 — pas une
régression à l'aveugle.
"""

import os
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SSH_CONFIG_EDITOR = os.path.join(REPO_ROOT, "ssh_config_editor.py")
SSH_KEY_MANAGER_DIALOG = os.path.join(REPO_ROOT, "ssh_key_manager_dialog.py")
KEY_PICKER_DIALOG = os.path.join(REPO_ROOT, "key_picker_dialog.py")


def _read(path: str) -> str:
    """Lit un fichier texte du dépôt en UTF-8.

    Args:
        path (str): Chemin absolu du fichier à lire.

    Returns:
        str: Contenu intégral du fichier.
    """
    with open(path, encoding="utf-8") as fh:
        return fh.read()


# Baseline établie à l'audit session-45 (2026-09-18), avant tout portage
# réel de plugins/ssh/gtk4.py. Chaque clé est un marqueur textuel exact,
# chaque valeur le nombre d'occurrences attendu dans le fichier concerné.
EXPECTED_RUPTURE_MARKERS = {
    SSH_CONFIG_EDITOR: {
        "show_all()": 6,
        'gi.require_version("Gtk", "3.0")': 1,
        "Gtk.Clipboard.get(": 1,
    },
    SSH_KEY_MANAGER_DIALOG: {
        "show_all()": 8,
        "set_border_width(": 4,
        'gi.require_version("Gtk", "3.0")': 1,
        "Gtk.Clipboard.get(": 1,
        ".get_filename()": 3,
        "set_current_folder(str(": 2,
        "Gtk.FileChooserDialog(": 3,
    },
    KEY_PICKER_DIALOG: {
        "show_all()": 1,
        'gi.require_version("Gtk", "3.0")': 1,
    },
}


class TestSshGtk4RuptureBaseline(unittest.TestCase):
    """Nombre d'occurrences des marqueurs de rupture GTK4 dans les 3 fichiers SSH GTK3."""

    def test_rupture_marker_counts_match_baseline(self):
        """Chaque marqueur doit apparaître exactement le nombre de fois attendu.

        Toute variation doit être accompagnée d'une mise à jour consciente
        de `EXPECTED_RUPTURE_MARKERS` et de `docs/gtk4-migration.md` §3.7 —
        jamais d'un ajustement muet.
        """
        for path, markers in EXPECTED_RUPTURE_MARKERS.items():
            source = _read(path)
            relpath = os.path.relpath(path, REPO_ROOT)
            for needle, expected in markers.items():
                with self.subTest(file=relpath, needle=needle):
                    actual = source.count(needle)
                    self.assertEqual(
                        actual,
                        expected,
                        f"{relpath} : {actual} occurrence(s) de {needle!r} "
                        f"trouvée(s), {expected} attendue(s) (baseline "
                        "session-45). Voir docs/gtk4-migration.md §3.7 "
                        "avant d'ajuster ce test.",
                    )

    def test_gtk3_lock_still_present_on_all_three_files(self):
        """Les trois fichiers doivent encore verrouiller GTK3 (portage non commencé).

        Contrairement à `plugins/plugin_base.py` (verrou levé en
        session-25, `tests/test_plugin_base.py`), ces trois fichiers n'ont
        pas encore été touchés par la migration GTK4 : ce test documente
        cet état de fait plutôt qu'une régression. Il doit être retiré ou
        inversé le jour où `plugins/ssh/gtk4.py` est effectivement créé
        (`docs/gtk4-migration.md` §3.0-bis).
        """
        for path in (SSH_CONFIG_EDITOR, SSH_KEY_MANAGER_DIALOG, KEY_PICKER_DIALOG):
            source = _read(path)
            relpath = os.path.relpath(path, REPO_ROOT)
            with self.subTest(file=relpath):
                self.assertIn(
                    'gi.require_version("Gtk", "3.0")',
                    source,
                    f"{relpath} : verrou GTK3 attendu tant que "
                    "plugins/ssh/gtk4.py n'a pas été créé (portage non "
                    "commencé, audit session-45).",
                )

    def test_dialog_run_rupture_already_resolved_here_too(self):
        """Aucun de ces trois fichiers ne doit appeler `.run()` directement sur un Gtk.Dialog.

        Vérifie, pour ce périmètre précis, la même chose que
        `tests/test_gtk4_core_rupture_points.py::TestDialogRunRuptureResolved`
        pour `gnome_connection_manager.py` : `Gtk.Dialog.run()` est supprimé
        en GTK4, et tous les appelants ici passent déjà par
        `utils.run_dialog_sync()` (constaté dans les trois fichiers lors de
        cet audit) — un point de rupture de moins à traiter le jour du
        portage réel.
        """
        for path in (SSH_CONFIG_EDITOR, SSH_KEY_MANAGER_DIALOG, KEY_PICKER_DIALOG):
            source = _read(path)
            relpath = os.path.relpath(path, REPO_ROOT)
            with self.subTest(file=relpath):
                self.assertNotIn(
                    "dlg.run()",
                    source,
                    f"{relpath} : un appel direct à .run() sur un Gtk.Dialog "
                    "a été introduit — supprimé en GTK4, utiliser "
                    "utils.run_dialog_sync() à la place.",
                )
