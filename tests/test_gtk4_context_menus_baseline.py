# SPDX-License-Identifier: GPL-3.0-or-later
"""Verrou textuel sur l'état des menus contextuels `Gtk.Menu` (audit session-29).

Contexte : la session-28 (`docs/sessions/session-28.md`,
`docs/gtk4-migration.md` §3.6-bis) a isolé les menus contextuels
(clic droit sur `treeServers`/les onglets/la console, et le sous-menu
« Custom commands ») comme le dernier point de rupture GTK3→GTK4 du cœur
restant après la résolution de `Gtk.Dialog.run()`, du menu principal
(`GtkMenuBar`/`GtkMenu`) et de `Gtk.Widget.reparent()`. La session-29
(2026-09-10) a fait l'audit exhaustif de ce point (inventaire complet,
voir `docs/gtk4-migration.md` §3.6-ter) sans encore le porter — le chantier
réel (`Gio.Menu`/`Gio.SimpleActionGroup`/`Gtk.PopoverMenu`, remplacement de
la signature `.popup(None, None, None, None, ...)` par un déclencheur
`Gtk.GestureClick`, conversion des `Gtk.CheckMenuItem` en actions à état)
est trop large pour une session, et CONSIGNES-AGENTS-IA.md interdit de
trancher seul un gros chantier sans plan validé au préalable.

Ce fichier ne teste PAS un comportement runtime (même contrainte que
`test_gtk4_core_rupture_points.py` : `gnome_connection_manager.py` et
`widgets.py` importent `gi`/`Gtk`, indisponible dans cet environnement de
développement). Il verrouille le *nombre exact* d'occurrences connues à la
fin de la session-29, sur le même principe que
`tests/test_gtk4_core_rupture_points.py` mais dans l'autre sens : au lieu
de verrouiller l'*absence* d'un point de rupture résolu, il verrouille
l'état *actuel, non résolu et documenté* de celui-ci, pour deux raisons :

1. Détecter toute nouvelle occurrence introduite ailleurs par erreur
   pendant que ce chantier n'est pas encore traité (le compteur ne doit
   pas augmenter sans qu'on s'en aperçoive) ;
2. Servir de check-list vivante pour une future session de portage : à
   mesure qu'un menu est migré vers `Gio.Menu`, le compteur correspondant
   doit être mis à jour ici en même temps, pas oublié.

Si un test échoue ici après un changement délibéré (portage effectif d'un
menu), c'est le signe attendu qu'il faut ajuster le compteur — pas une
régression à l'aveugle.

Mise à jour session-31 (2026-09-11) : ``popupMenuTab`` (menu contextuel des
onglets), retenu par l'audit session-29 comme le plus simple des quatre à
porter, est le premier effectivement migré vers ``Gio.Menu``/
``Gio.SimpleActionGroup``/``Gtk.Popover`` (voir ``widgets.TabContextMenu``
et ``docs/gtk4-migration.md`` §3.6-ter pour le détail). Les compteurs
ci-dessous ont été ajustés en conséquence ; ``popupMenu``,
``popupMenu.mnuCommands`` et ``popupMenuFolder`` restent en ``Gtk.Menu``
classique et inchangés.

Mise à jour session-32 (2026-09-11) : ``popupMenuFolder`` (menu contextuel
du panneau de serveurs), deuxième des quatre menus contextuels à être
porté, migré à son tour vers ``Gio.Menu``/``Gio.SimpleActionGroup``/
``Gtk.Popover`` (voir ``widgets.FolderContextMenu`` et
``docs/gtk4-migration.md`` §3.6-quinquies). Compteurs ajustés en
conséquence ; seul ``popupMenu`` (et son sous-menu dynamique
``mnuCommands``) reste en ``Gtk.Menu`` classique.

Mise à jour session-33 (2026-09-11) : ``popupMenu`` (menu contextuel du
terminal, sous-menu dynamique « Custom commands » compris), troisième et
dernier des quatre menus contextuels applicatifs identifiés par l'audit
session-29, migré à son tour vers ``Gio.Menu``/``Gio.SimpleActionGroup``/
``Gtk.Popover`` (voir ``widgets.PopupMenu`` et ``docs/gtk4-migration.md``
§3.6-sexies). Les trois compteurs de ``CORE_FILE`` tombent à zéro : plus
aucune des trois familles historiques (``Gtk.Menu()``, signature
``.popup()`` à six arguments, pont « Custom commands » via
``get_menu().popup(...)``) ne subsiste dans
``gnome_connection_manager.py``. Restent hors périmètre de ce fichier : le
constructeur générique RDP/VNC/SPICE de ``widgets.py``
(``build_remote_desktop_context_menu()``, ``WIDGETS_FILE`` inchangé), le
déclenchement ``Gtk.GestureClick`` (commun aux quatre menus contextuels) et
le cas ``Gtk.Entry``/``populate-popup``.

Mise à jour session-38 (2026-09-16) : audit du déclenchement
``Gtk.GestureClick`` (point 3 ci-dessus, voir ``docs/gtk4-migration.md``
§3.6-septies) — inventaire exhaustif des 3 familles de sites
``button-press-event``/``button_press_event`` réellement liées au
déclenchement d'un menu contextuel applicatif (``on_terminal_click`` × 2
sites, ``on_tvServers_button_press_event``,
``NotebookTabLabel.popupmenu``), distinguées des sites textuellement
similaires mais sans rapport avec un menu
(``on_hpMain_button_press_event``, ``on_double_click`` × 2 sites,
``_on_editor_pressed``). Audit et plan seulement — aucun portage de code
cette session, voir ``docs/sessions/session-38.md``. Nouvelle classe
``TestGestureClickTriggerBaseline`` ci-dessous, verrouillant ces
comptages sur le même principe que les classes existantes.

Mise à jour session-39 (2026-09-17) : premier portage effectif du
déclenchement, pour ``treeServers``/``popupMenuFolder``
(``Wmain.on_tvServers_pressed``, anciennement
``on_tvServers_button_press_event``) — voir ``docs/gtk4-migration.md``
§3.6-octies. Site choisi en premier conformément à la proposition d'ordre
de la session-38 (seul des trois sans branche dépendant d'un modificateur
clavier). Compteur ``on_tvServers_button_press_event`` de
``EXPECTED_GESTURECLICK_TRIGGER_SITES`` tombé à zéro ; ``on_terminal_click``
× 2 sites et ``NotebookTabLabel.popupmenu`` restent en
``button-press-event``/``button_press_event`` classique, compteurs
inchangés. Nouveau test ``test_tree_servers_trigger_uses_gesture``
ci-dessous, verrouillant la présence du nouveau code
(``Gtk.GestureMultiPress``) sur le même principe que
``test_custom_commands_bridge_now_uses_popup_commands_at``.

Mise à jour session-40 (2026-09-17) : deuxième portage effectif du
déclenchement, pour ``on_terminal_click`` (2 sites identiques) —
``Wmain.on_terminal_pressed``, voir ``docs/gtk4-migration.md``
§3.6-nonies. Site choisi en deuxième conformément à la proposition d'ordre
de la session-38 (après ``treeServers``, seul sans branche modificateur ;
avant l'étiquette d'onglet, seule à alimenter aussi le menu RDP/VNC/SPICE
encore non porté). Compteur ``on_terminal_click`` de
``EXPECTED_GESTURECLICK_TRIGGER_SITES`` tombé à zéro ; seul
``NotebookTabLabel.popupmenu`` reste en ``button-press-event`` classique.
Nouveau test ``test_terminal_click_trigger_uses_gesture`` ci-dessous, même
principe que ``test_tree_servers_trigger_uses_gesture``.

Mise à jour session-41 (2026-09-17) : troisième et dernier portage
effectif du déclenchement, pour l'étiquette d'onglet
(``NotebookTabLabel.popupmenu`` → ``NotebookTabLabel.on_label_pressed``,
voir ``docs/gtk4-migration.md`` §3.6-decies). Les trois familles de sites
identifiées par l'audit session-38 (point 3) sont désormais toutes
portées. Compteur ``(WIDGETS_FILE, '"button-press-event", self.popupmenu,
label)')`` de ``EXPECTED_GESTURECLICK_TRIGGER_SITES`` tombé à zéro.
Nouveau test ``test_tab_label_trigger_uses_gesture`` ci-dessous, même
principe que les deux tests positifs précédents.

Mise à jour session-42 (2026-09-18) : audit du dernier point restant,
jusqu'ici désigné partout comme « le cas ``Gtk.Entry``/``populate-popup`` »
(item 5 du tableau session-29 ci-dessus). Terminologie corrigée : le widget
réel est ``CellTextView`` (sous-classe de ``Gtk.TextView``), pas
``Gtk.Entry`` ; le périmètre réel couvre 4 signaux GTK3-only
(``focus-out-event``, ``key-press-event``, ``populate-popup``,
``button-press-event``, dont le site ``_on_editor_pressed`` déjà compté
ci-dessus dans ``EXPECTED_GESTURECLICK_DECOY_SITES``), pas 1 seul. Détail
complet et raison de ne pas porter le code cette session (comportement
GTK4 du menu natif non vérifiable empiriquement ici) :
``docs/gtk4-migration.md`` §3.6-undecies, ``docs/sessions/session-42.md``.
Nouveau fichier dédié ``tests/test_gtk4_inline_editor_baseline.py``
(2 tests) plutôt qu'une extension de celui-ci : ce fichier-ci verrouille
spécifiquement les menus contextuels *applicatifs* (``popupMenu`` et
consorts) et leur déclenchement, un périmètre différent de l'éditeur
inline d'une cellule.

Mise à jour session-44 (2026-09-18) : le décoy ``_on_editor_pressed``
ci-dessus (compté ici depuis la session-38 comme hors périmètre du point
3) est désormais porté vers ``Gtk.GestureMultiPress``/``pressed`` — voir
``docs/gtk4-migration.md`` §3.6-terdecies. Son compteur dans
``EXPECTED_GESTURECLICK_DECOY_SITES`` tombe à zéro ; le verrou positif du
nouveau câblage vit dans ``tests/test_gtk4_inline_editor_baseline.py``
(``test_button_gesture_is_wired``), pas ici — ce fichier ne suivait ce
site que comme décoy textuel, hors de son périmètre applicatif.
"""

import os
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORE_FILE = os.path.join(REPO_ROOT, "gnome_connection_manager.py")
WIDGETS_FILE = os.path.join(REPO_ROOT, "widgets.py")

# Baseline mise à jour à la session-33 (2026-09-11) — popupMenu (et son
# sous-menu dynamique "Custom commands") porté vers
# Gio.Menu/Gio.SimpleActionGroup/Gtk.Popover (voir widgets.PopupMenu et
# docs/gtk4-migration.md §3.6-sexies) : les trois compteurs de CORE_FILE
# tombent à zéro, les quatre menus contextuels applicatifs de l'audit
# session-29 sont désormais tous portés. Baseline précédente (session-32,
# 2026-09-11) : CORE_FILE=2 / 1 / 1 (respectivement), WIDGETS_FILE inchangé.
EXPECTED_GTK_MENU_INSTANTIATIONS = {
    CORE_FILE: 0,  # popupMenu et popupMenu.mnuCommands portés (popupMenuTab/popupMenuFolder déjà portés)
    WIDGETS_FILE: 2,  # build_remote_desktop_context_menu() : menu + keys_submenu
}
EXPECTED_LEGACY_POPUP_CALLS = {
    CORE_FILE: 0,  # popupMenu.popup(...) devenu popup_at() (popupMenuTab/popupMenuFolder déjà portés)
    WIDGETS_FILE: 1,  # remote_menu.popup(...) — self.popup.popup(...) devenu popup_at()
}
# get_menu().popup(...) (pont "Custom commands" depuis le bouton hamburger)
# n'existe plus sous cette forme : remplacé par PopupMenu.popup_commands_at()
# — voir test_custom_commands_bridge_now_uses_popup_commands_at ci-dessous.
# Compteur conservé (à zéro) par cohérence avec les baselines précédentes,
# plutôt que supprimé, pour que toute réapparition de l'ancien pont saute
# aux yeux au même endroit que son historique.
EXPECTED_CUSTOM_COMMANDS_BRIDGE_POPUP = 0


def _read(path: str) -> str:
    """Lit un fichier texte du dépôt en UTF-8.

    Args:
        path (str): Chemin absolu du fichier à lire.

    Returns:
        str: Contenu intégral du fichier.
    """
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class TestContextMenuInstantiationBaseline(unittest.TestCase):
    """Nombre d'instanciations `Gtk.Menu()` — inchangé tant que non porté."""

    def test_core_and_widgets_menu_counts_match_baseline(self):
        """Chaque fichier doit contenir exactement le nombre attendu de `Gtk.Menu()`.

        Toute variation (à la hausse comme à la baisse) doit être
        accompagnée d'une mise à jour consciente de
        `EXPECTED_GTK_MENU_INSTANTIATIONS` et de
        `docs/gtk4-migration.md` §3.6-ter — jamais d'un ajustement muet.
        """
        for path, expected in EXPECTED_GTK_MENU_INSTANTIATIONS.items():
            with self.subTest(path=os.path.relpath(path, REPO_ROOT)):
                actual = _read(path).count("Gtk.Menu()")
                self.assertEqual(
                    actual,
                    expected,
                    f"{os.path.relpath(path, REPO_ROOT)} : {actual} occurrence(s) de "
                    f"Gtk.Menu() trouvée(s), {expected} attendue(s) (baseline session-29). "
                    "Si ce menu vient d'être porté vers Gio.Menu, mettre à jour la baseline "
                    "et docs/gtk4-migration.md §3.6-ter ; sinon, ceci est une régression.",
                )


class TestLegacyPopupSignatureBaseline(unittest.TestCase):
    """Nombre d'appels `.popup(None, None, None, None, ...)` — signature GTK2/3 legacy."""

    def test_core_and_widgets_legacy_popup_counts_match_baseline(self):
        """Chaque fichier doit contenir exactement le nombre attendu d'appels.

        Cette signature à 6 arguments positionnels est déjà dépréciée en
        GTK3 (remplacée par `popup_at_pointer()`) et purement et simplement
        supprimée en GTK4 (plus de `Gtk.Menu` du tout). Un nouvel appel
        ailleurs dans le code serait de la dette supplémentaire pour le
        futur portage.
        """
        for path, expected in EXPECTED_LEGACY_POPUP_CALLS.items():
            with self.subTest(path=os.path.relpath(path, REPO_ROOT)):
                # Le pont "Custom commands" (get_menu().popup(...)) est une
                # troisième famille comptée séparément par le test suivant,
                # exclue ici pour ne pas la compter deux fois.
                actual = sum(
                    1
                    for line in _read(path).splitlines()
                    if ".popup(None, None, None, None," in line and "get_menu().popup(" not in line
                )
                self.assertEqual(
                    actual,
                    expected,
                    f"{os.path.relpath(path, REPO_ROOT)} : {actual} appel(s) trouvé(s), "
                    f"{expected} attendu(s) (baseline session-29). Voir "
                    "docs/gtk4-migration.md §3.6-ter avant d'ajuster ce test.",
                )

    def test_custom_commands_bridge_now_uses_popup_commands_at(self):
        """Le pont « Custom commands » du menu hamburger est passé à `popup_commands_at()`.

        Avant la session-33, `get_menu().popup(None, None, None, None, 0,
        Gtk.get_current_event_time())` (voir `_build_primary_menu`) ouvrait
        `self.popupMenu.mnuCommands` en pop-up depuis une action du menu
        hamburger — un site distinct des menus contextuels clic-droit,
        compté séparément. `mnuCommands` n'existant plus en tant que widget
        `Gtk.Menu` autonome depuis le portage de `popupMenu` vers `Gio.Menu`
        (voir `docs/gtk4-migration.md` §3.6-sexies), ce pont est remplacé
        par `PopupMenu.popup_commands_at()`, qui affiche le même modèle
        `Gio.Menu` partagé (`self.popupMenu.commands_model`) dans un second
        `Gtk.Popover` autonome.
        """
        source = _read(CORE_FILE)
        actual = source.count("get_menu().popup(None, None, None, None,")
        self.assertEqual(actual, EXPECTED_CUSTOM_COMMANDS_BRIDGE_POPUP)
        self.assertIn(
            "popupMenu.popup_commands_at(self.btnPrimaryMenu)",
            source,
            "Le pont « Custom commands » du menu hamburger doit passer par "
            "PopupMenu.popup_commands_at() — voir docs/gtk4-migration.md §3.6-sexies.",
        )


EXPECTED_GESTURECLICK_TRIGGER_SITES = {
    # on_terminal_click (2 sites) porté vers Gtk.GestureMultiPress en
    # session-40 (Wmain.on_terminal_pressed via
    # attach_terminal_click_gesture(), docs/gtk4-migration.md §3.6-nonies)
    # — compteur conservé à zéro plutôt que retiré, même logique que
    # EXPECTED_CUSTOM_COMMANDS_BRIDGE_POPUP : toute réapparition de ce
    # connect() doit sauter aux yeux au même endroit que son historique.
    (CORE_FILE, '"button_press_event", self.on_terminal_click)'): 0,
    # treeServers porté vers Gtk.GestureMultiPress en session-39
    # (Wmain.on_tvServers_pressed, docs/gtk4-migration.md §3.6-octies) —
    # compteur conservé à zéro plutôt que retiré, même logique que
    # EXPECTED_CUSTOM_COMMANDS_BRIDGE_POPUP : toute réapparition de ce
    # connect() doit sauter aux yeux au même endroit que son historique.
    (CORE_FILE, '"button-press-event", self.on_tvServers_button_press_event)'): 0,
    # Étiquette d'onglet (NotebookTabLabel.popupmenu) portée vers
    # Gtk.GestureMultiPress en session-41 (NotebookTabLabel.on_label_pressed,
    # docs/gtk4-migration.md §3.6-decies) — compteur conservé à zéro,
    # même logique que les deux précédents. Les trois sites de l'audit
    # session-38 (point 3) sont désormais tous à zéro.
    (WIDGETS_FILE, '"button-press-event", self.popupmenu, label)'): 0,
}
# Sites textuellement voisins (même famille de signal) mais sans rapport
# avec le déclenchement d'un menu contextuel — voir
# docs/gtk4-migration.md §3.6-septies « hors périmètre de ce point » pour
# le détail de chacun. Comptés séparément pour ne pas fausser silencieusement
# les compteurs ci-dessus si l'un d'eux venait à disparaître ou changer de
# forme (signal de la disparition d'un gestionnaire, pas de ce chantier).
EXPECTED_GESTURECLICK_DECOY_SITES = {
    (CORE_FILE, '"button-press-event", self.on_hpMain_button_press_event)'): 1,
    (CORE_FILE, '"button-press-event", self.on_double_click)'): 1,
    (CORE_FILE, '"button_press_event", self.on_double_click, None)'): 1,
    # `_on_editor_pressed` (widgets.py) : compteur tombé à zéro en
    # session-44, ce décoy est désormais porté vers
    # `Gtk.GestureMultiPress`/`pressed` (`_on_editor_button_pressed`) —
    # voir docs/gtk4-migration.md §3.6-terdecies et
    # tests/test_gtk4_inline_editor_baseline.py pour le verrou dédié de ce
    # portage (hors périmètre du point 3, ce fichier ne gardait la trace
    # de ce site que comme décoy textuel).
    (WIDGETS_FILE, '"button-press-event", self._on_editor_pressed)'): 0,
}


class TestGestureClickTriggerBaseline(unittest.TestCase):
    """Verrou textuel sur les sites de déclenchement à porter vers `Gtk.GestureClick`.

    Point 3 de l'audit session-29 (`docs/gtk4-migration.md` §3.6-ter),
    isolé et inventorié en détail par la session-38 (§3.6-septies) sans
    être porté cette session-là — voir `docs/sessions/session-38.md`.
    """

    def test_menu_trigger_sites_match_baseline(self):
        """Chaque site connectant un menu contextuel à son événement souris.

        Toute variation doit être accompagnée d'une mise à jour consciente
        de `EXPECTED_GESTURECLICK_TRIGGER_SITES` et de
        `docs/gtk4-migration.md` §3.6-septies — jamais d'un ajustement muet.
        Si ce test échoue après un portage effectif vers
        `Gtk.GestureClick`/`Gtk.GestureMultiPress`, c'est le signal attendu
        qu'il faut mettre à jour la baseline, pas une régression.
        """
        for (path, needle), expected in EXPECTED_GESTURECLICK_TRIGGER_SITES.items():
            with self.subTest(path=os.path.relpath(path, REPO_ROOT), needle=needle):
                actual = _read(path).count(needle)
                self.assertEqual(
                    actual,
                    expected,
                    f"{os.path.relpath(path, REPO_ROOT)} : {actual} occurrence(s) de "
                    f"{needle!r} trouvée(s), {expected} attendue(s) (baseline session-38). "
                    "Voir docs/gtk4-migration.md §3.6-septies avant d'ajuster ce test.",
                )

    def test_decoy_sites_match_baseline(self):
        """Sites voisins hors périmètre du point 3 — inchangés, comptés à part.

        Ces sites partagent le même nom de signal GTK3
        (`button-press-event`/`button_press_event`) mais ne déclenchent
        aucun menu contextuel (bascule de panneau, ajout d'onglet au
        double-clic, contournement d'un bug GTK) — voir
        `docs/gtk4-migration.md` §3.6-septies. Les compter ici évite de les
        confondre avec les sites réels lors d'un futur audit par simple
        grep du nom du signal.
        """
        for (path, needle), expected in EXPECTED_GESTURECLICK_DECOY_SITES.items():
            with self.subTest(path=os.path.relpath(path, REPO_ROOT), needle=needle):
                actual = _read(path).count(needle)
                self.assertEqual(
                    actual,
                    expected,
                    f"{os.path.relpath(path, REPO_ROOT)} : {actual} occurrence(s) de "
                    f"{needle!r} trouvée(s), {expected} attendue(s) (baseline session-38, "
                    "site hors périmètre du point 3).",
                )

    def test_tree_servers_trigger_uses_gesture(self):
        """Le widget treeServers utilise désormais Gtk.GestureMultiPress, plus button-press-event.

        Complément positif au compteur à zéro ci-dessus (qui verrouille
        seulement l'absence de l'ancien ``connect()``) : vérifie la
        présence effective du nouveau code, même principe que
        ``test_custom_commands_bridge_now_uses_popup_commands_at`` pour le
        pont « Custom commands » — voir ``docs/gtk4-migration.md``
        §3.6-octies.
        """
        source = _read(CORE_FILE)
        for needle in (
            "Gtk.GestureMultiPress.new(treeServers)",
            'connect("pressed", self.on_tvServers_pressed)',
        ):
            with self.subTest(needle=needle):
                self.assertIn(
                    needle,
                    source,
                    f"{needle!r} introuvable dans {os.path.relpath(CORE_FILE, REPO_ROOT)} — "
                    "voir docs/gtk4-migration.md §3.6-octies avant d'ajuster ce test.",
                )

    def test_terminal_click_trigger_uses_gesture(self):
        """Les terminaux VTE utilisent désormais Gtk.GestureMultiPress, plus button_press_event.

        Complément positif au compteur à zéro ci-dessus (qui verrouille
        seulement l'absence de l'ancien ``connect()``) : vérifie la
        présence effective du nouveau code, même principe que
        ``test_tree_servers_trigger_uses_gesture`` — voir
        ``docs/gtk4-migration.md`` §3.6-nonies. Contrairement à
        ``treeServers`` (widget unique et statique), les deux sites
        d'``on_terminal_click`` passent par une seule méthode d'attache
        partagée (``attach_terminal_click_gesture``, un terminal étant créé
        dynamiquement par onglet) : ce test vérifie donc la présence de
        cette méthode et de son appel aux deux sites, plutôt qu'une
        instanciation de geste répétée deux fois dans le texte.
        """
        source = _read(CORE_FILE)
        for needle in (
            "def attach_terminal_click_gesture(self, widget):",
            "Gtk.GestureMultiPress.new(widget)",
            'gesture.connect("pressed", self.on_terminal_pressed, widget)',
            "def on_terminal_pressed(self, gesture, n_press, x, y, widget):",
        ):
            with self.subTest(needle=needle):
                self.assertIn(
                    needle,
                    source,
                    f"{needle!r} introuvable dans {os.path.relpath(CORE_FILE, REPO_ROOT)} — "
                    "voir docs/gtk4-migration.md §3.6-nonies avant d'ajuster ce test.",
                )
        actual_call_sites = source.count("self.attach_terminal_click_gesture(v)")
        self.assertEqual(
            actual_call_sites,
            2,
            f"{actual_call_sites} appel(s) à attach_terminal_click_gesture(v) trouvé(s), "
            "2 attendus (les deux anciens sites de connexion à on_terminal_click) — voir "
            "docs/gtk4-migration.md §3.6-nonies avant d'ajuster ce test.",
        )

    def test_tab_label_trigger_uses_gesture(self):
        """L'étiquette d'onglet utilise désormais Gtk.GestureMultiPress, plus button-press-event.

        Complément positif au compteur à zéro ci-dessus (qui verrouille
        seulement l'absence de l'ancien ``connect()``) : vérifie la
        présence effective du nouveau code, même principe que
        ``test_tree_servers_trigger_uses_gesture`` et
        ``test_terminal_click_trigger_uses_gesture`` — voir
        ``docs/gtk4-migration.md`` §3.6-decies. Troisième et dernier des
        trois sites de l'audit session-38 (point 3) à être ainsi
        verrouillé.
        """
        source = _read(WIDGETS_FILE)
        for needle in (
            "self._label_click_gesture = Gtk.GestureMultiPress.new(self.eb)",
            'self._label_click_gesture.connect("pressed", self.on_label_pressed)',
            "def on_label_pressed(self, gesture, n_press, x, y):",
        ):
            with self.subTest(needle=needle):
                self.assertIn(
                    needle,
                    source,
                    f"{needle!r} introuvable dans {os.path.relpath(WIDGETS_FILE, REPO_ROOT)} — "
                    "voir docs/gtk4-migration.md §3.6-decies avant d'ajuster ce test.",
                )


if __name__ == "__main__":
    unittest.main()
