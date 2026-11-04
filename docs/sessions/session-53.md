# Session 53 (2026-11-04) — Portage du menu contextuel RDP/VNC/SPICE vers Gio.Menu (#71, partie portable)

## Contexte

Issue #71 (« [GTK4] Porter le chantier RDP ») recouvre deux chantiers
distincts : le portage de la bibliothèque `gtk-frdp` vers `asyncrdp`
(suivi par #105/AsyncRdp4#36, question d'architecture non tranchée) et
la part purement applicative du menu contextuel bureau distant. Cette
session traite la seconde, dernier des quatre menus contextuels
applicatifs de l'audit session-29 resté en `Gtk.Menu` classique —
découvert par l'audit session-46 (§3.8.4), qui avait corrigé au passage
le résumé session-33 de `CLAUDE.md` (« les quatre menus contextuels
applicatifs... sont désormais tous portés » : inexact, trois sur
quatre).

## Ce qui a été fait

1. **`remote_desktop_menu_core.py`** (nouveau module, zéro import
   Gtk/Gio) : logique pure extraite sur le même principe que
   `tab_context_menu_core.py`/`folder_context_menu_core.py`/
   `popup_menu_core.py` — disposition
   (`build_remote_desktop_menu_layout`), sensibilité
   (`compute_remote_desktop_enabled_actions`) et combinaisons de
   touches spéciales historiques (`REMOTE_DESKTOP_SPECIAL_KEY_COMBOS`,
   déplacées inchangées depuis `widgets._SPECIAL_KEY_COMBOS` : cinq
   combinaisons, libellés français littéraux, noms de touches Gdk).
2. **`widgets.RemoteDesktopContextMenu`** (nouvelle classe) : trio
   `Gio.Menu`/`Gio.SimpleActionGroup`/`Gtk.Popover`, mêmes référence
   (`TabContextMenu` session-31), préfixe d'action `rdmenu.`,
   `popup_at(relative_to, x, y)` copié de `TabContextMenu`.
   `build_remote_desktop_context_menu()` garde sa signature et délègue :
   les trois plugins RDP/VNC/SPICE (un appel chacun via
   `build_context_menu()`) ne changent pas.
3. **Déclenchement** (`NotebookTabLabel.on_label_pressed`) : le dernier
   appel `Gtk.Menu.popup(None, None, None, None, button,
   Gtk.get_current_event_time())` du dépôt devient
   `remote_menu.popup_at(self.eb, x, y)`.
4. **Baselines inversées** (échec attendu après portage délibéré, pas
   une régression) : `tests/test_gtk4_rdp_baseline.py`
   (`TestRemoteDesktopContextMenuStillLegacy` →
   `TestRemoteDesktopContextMenuPorted`, compteur `Gtk.Menu()` de
   `widgets.py` 2 → 0, docstrings et note de mise à jour session-53) et
   `tests/test_gtk4_context_menus_baseline.py` (compteurs `WIDGETS_FILE`
   de `EXPECTED_GTK_MENU_INSTANTIATIONS` 2 → 0 et
   `EXPECTED_LEGACY_POPUP_CALLS` 1 → 0, note de mise à jour session-53).
5. **Tests** : `tests/test_remote_desktop_menu_core.py` (15 tests /
   36 sous-tests) — disposition avec/sans entrées supplémentaires,
   unicité des noms d'action, les 4 combinaisons de sensibilité,
   verrou des combinaisons historiques inchangées.
6. **Documentation** : `docs/gtk4-migration.md` §3.8.6 (nouveau),
   CHANGELOG (entrée « Non publié »).

## Décisions

- Portée volontairement identique aux sessions 31-33 : seul le
  *contenu* du menu change de mécanisme ; le *déclenchement* était déjà
  porté depuis la session-41. Deux différences assumées, documentées
  dans la docstring de la classe : sensibilité Connect/Disconnect
  reportée sur les `Gio.SimpleAction` (le menu est reconstruit à chaque
  clic droit, l'état lu sur les boutons est le même qu'avant) ;
  callbacks `extra_items` recevant l'action en premier argument au lieu
  du `Gtk.MenuItem` (les callbacks existants l'ignorent : `lambda _w:
  ...`).
- La question `gtk-frdp`/asyncrdp (#105) reste hors périmètre : ce
  portage ne touche qu'aux menus.

## Vérifications

`pytest tests/test_remote_desktop_menu_core.py
tests/test_gtk4_rdp_baseline.py
tests/test_gtk4_context_menus_baseline.py` verts (23 tests / 56
sous-tests) ; suite complète : mêmes résultats que la base (6 échecs +
13 erreurs d'environnement local préexistants — gettext/msgfmt et
diagramme de classes, vérifiés identiques sur `dev` sans ce portage,
non liés aux menus) ; `ruff check .` et `ruff format --check .` verts ;
`python scripts/audit_debug_sorties.py` à 0. Couverture de
`remote_desktop_menu_core.py` : 100 % (logique pure, testée sans stub
GTK).
