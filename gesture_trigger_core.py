#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""Logique métier pure du déclenchement des menus contextuels (zéro import Gtk/Gio).

Point 3 de l'audit session-29 (`docs/gtk4-migration.md` §3.6-ter), inventorié
en détail par la session-38 (§3.6-septies) : migration du déclenchement
`button-press-event`/`Gdk.EventButton` vers `Gtk.GestureMultiPress` (GTK3) /
`Gtk.GestureClick` (GTK4), commune aux trois familles de sites identifiées
(`on_tvServers_button_press_event`, `on_terminal_click` × 2, et
`NotebookTabLabel.popupmenu`). Contrairement aux menus eux-mêmes (un fichier
`_core.py` par menu : `tab_context_menu_core.py`, `folder_context_menu_core.py`,
`popup_menu_core.py`), ce chantier est transversal à plusieurs widgets — ce
module rassemble donc, site par site et session après session, la logique de
classification d'un clic (bouton + nombre de pressions) que chaque ancien
gestionnaire `button-press-event` encodait dans son `if`/`else`, pour rester
testable sans stub GTK conformément à `CONSIGNES-AGENTS-IA.md` section 5.

Portée de la session-39 : un seul site, `treeServers`/`popupMenuFolder`
(`Wmain.on_tvServers_pressed`, anciennement `on_tvServers_button_press_event`)
— retenu par la proposition d'ordre de la session-38 comme le plus simple des
trois (aucune branche dépendant d'un modificateur clavier). Les deux sites
restants (`on_terminal_click`, `NotebookTabLabel.popupmenu`) auront chacun
leur propre fonction de classification ajoutée ici lors de leur portage,
plutôt qu'un nouveau fichier par site — voir `docs/gtk4-migration.md`
§3.6-octies pour le détail du portage et une correction importante apportée
à l'audit session-38 (disponibilité réelle de `get_current_event()`/
`get_current_event_state()` en GTK3).

Portée de la session-40 : deuxième des trois sites, `on_terminal_click`
(2 points de connexion identiques, voir `docs/gtk4-migration.md`
§3.6-nonies) — retenu par la proposition d'ordre de la session-38 comme
deuxième (après `treeServers`, seule des trois sans branche dépendant d'un
modificateur clavier ; avant l'étiquette d'onglet, seule à alimenter aussi
le menu RDP/VNC/SPICE encore non porté). Contrairement à
`classify_tree_servers_press`, `classify_terminal_click` a besoin d'un
paramètre supplémentaire (`ctrl_pressed`) car l'ancien gestionnaire
distinguait un troisième cas (Ctrl+clic gauche, détection d'URL) non
présent sur `treeServers` — voir sa docstring pour le détail complet de la
correspondance avec l'ancien `if`/`elif`.

Portée de la session-41 : troisième et dernier des trois sites,
`NotebookTabLabel.popupmenu` (`widgets.py`) — voir `docs/gtk4-migration.md`
§3.6-decies. Comme `classify_tree_servers_press`, `classify_tab_label_click`
n'a besoin d'aucun paramètre de modificateur clavier (aucune branche Ctrl
sur ce site) ; comme `classify_terminal_click`, aucune issue `"swallow"`
(l'ancien code ne testait que `event.type == Gdk.EventType.BUTTON_PRESS`,
jamais `_2BUTTON_PRESS`/`_3BUTTON_PRESS`). Différence propre à ce site :
l'asymétrie de `Gtk.EventSequenceState.CLAIMED` entre les issues (l'ancien
code retournait `True` sur le clic droit et sur un clic milieu annulé par
confirmation, mais pas quand l'onglet était effectivement fermé) est une
décision de l'appelant, prise à l'exécution une fois `msgconfirm()` connu
— hors de portée d'une fonction pure, donc non représentée ici. Les trois
sites de l'audit session-38 (point 3) sont désormais tous portés.
"""

from loguru import logger

# Convention bibliothèque (#130) : muet à l'import, réactivé par
# logging_config.configure_logging() (CORE_MODULES).
logger.disable(__name__)

#: Issues possibles de la classification d'un clic sur treeServers, voir
#: :func:`classify_tree_servers_press`.
TREE_SERVERS_PRESS_OUTCOMES = ("open-menu", "swallow", "propagate")


def classify_tree_servers_press(*, button, n_press):
    """Classifie un clic sur treeServers selon le bouton et le nombre de pressions.

    Reproduit à l'identique les deux branches de l'ancien gestionnaire
    `button-press-event` (`Wmain.on_tvServers_button_press_event`, avant son
    portage session-39 vers `Gtk.GestureMultiPress` — voir
    `docs/gtk4-migration.md` §3.6-octies) :

    - ``event.type == Gdk.EventType.BUTTON_PRESS and event.button == 3``
      (clic droit simple) devient ``button == 3 and n_press == 1``
      → ``"open-menu"`` : ouvrir popupMenuFolder.
    - Sinon, ``event.type in (Gdk.EventType._2BUTTON_PRESS,
      Gdk.EventType._3BUTTON_PRESS)`` (double/triple clic, tous boutons
      confondus) devient ``n_press >= 2`` → ``"swallow"`` : avaler
      l'évènement sans action (l'ancien code retournait ``True`` dans les
      deux cas ci-dessus pour arrêter la propagation).
    - Tout le reste (clic simple, bouton différent de 3 — typiquement le
      clic gauche qui sélectionne une ligne) → ``"propagate"`` : ne rien
      faire et laisser le traitement par défaut du widget suivre son cours
      (l'ancien code retournait ``False``).

    Note de conception : `Gtk.GestureMultiPress`/`Gtk.GestureClick` livrent
    un signal `pressed` par pression physique (`n_press` incrémente à
    chaque pression), alors que GDK délivrait pour un double-clic à la fois
    un évènement `BUTTON_PRESS` et, juste après, un évènement de type
    `_2BUTTON_PRESS` distinct pour la même pression physique. Les deux
    mécanismes convergent malgré tout vers le même verdict pour chaque
    pression prise isolément — voir `docs/gtk4-migration.md` §3.6-octies
    pour la table de correspondance complète.

    Args:
        button (int): Numéro du bouton de la souris à l'origine du clic,
            tel que renvoyé par `Gtk.GestureSingle.get_current_button()`
            (1 = gauche, 2 = milieu, 3 = droit).
        n_press (int): Nombre de pressions consécutives, tel que transmis
            par le paramètre `n_press` du signal `pressed` de
            `Gtk.GestureMultiPress`/`Gtk.GestureClick` (1 = clic simple,
            2 = double clic, 3 = triple clic).

    Returns:
        str: Une valeur de :data:`TREE_SERVERS_PRESS_OUTCOMES` — voir
            ci-dessus pour la signification de chacune.
    """
    logger.debug("classify_tree_servers_press: entrée")
    if button == 3 and n_press == 1:
        logger.debug("classify_tree_servers_press: sortie")
        return "open-menu"
    if n_press >= 2:
        logger.debug("classify_tree_servers_press: sortie")
        return "swallow"
    logger.debug("classify_tree_servers_press: sortie")
    return "propagate"


def masque_actif_dans_etat(retour_get_state, masque):
    """Teste si un masque de modificateur est actif dans l'état renvoyé par ``Gdk.Event.get_state()``.

    Contexte (bug exposé par la vérification empirique de l'issue #72, voir
    `docs/gtk4-migration.md` §3.6-quaterdecies) : le type de retour de
    ``Gdk.EventButton.get_state()`` dépend de la version de PyGObject. Un
    appel direct peut renvoyer les flags ``Gdk.ModifierType`` seuls (le
    ``etat & Gdk.ModifierType.CONTROL_MASK`` usuel fonctionne alors), ou
    un tuple résultat ``(succès, état)`` (``_ResultTuple``, format observé
    empiriquement sous PyGObject 3.56 / Ubuntu 26.04) — le même ``&``
    lève alors ``TypeError: unsupported operand type(s) for &`` dans
    ``Wmain.on_terminal_pressed`` (plantage journalisé puis avalé du
    Ctrl+clic gauche « ouvrir le lien », jamais détecté en développement
    car GTK3 y est absent — motivation même de la vérification manuelle de
    l'issue #72). Cette fonction pure normalise les deux formats pour que
    l'appelant reste identique quel que soit le PyGObject : elle déplie le
    tuple (l'état est son dernier élément) avant d'appliquer le masque,
    et ne se fie pas à un état signalé non garanti (succès à ``False``).

    Args:
        retour_get_state: Valeur de retour brute de ``get_state()`` — flags
            ``Gdk.ModifierType`` ou équivalent entier, tuple
            ``(succès, état)`` tel que ``_ResultTuple``, ou ``None`` (aucun
            état disponible → masque réputé inactif).
        masque: Masque à tester (ex. ``Gdk.ModifierType.CONTROL_MASK``) ;
            flags ou entier, l'opérateur ``&`` s'appliquant aux deux.

    Returns:
        bool: `True` si le masque est actif dans l'état, `False` sinon
        (notamment si ``retour_get_state`` est ``None``, un tuple vide, ou
        un tuple résultat dont le succès est explicitement ``False`` —
        l'état n'y est pas garanti et n'est pas testé).
    """
    logger.debug("masque_actif_dans_etat: entrée")
    if retour_get_state is None:
        logger.debug("masque_actif_dans_etat: sortie (sans état)")
        return False
    if isinstance(retour_get_state, tuple):
        if not retour_get_state:
            # tuple vide : pas d'état exploitable
            logger.debug("masque_actif_dans_etat: sortie (tuple vide)")
            return False
        if isinstance(retour_get_state[0], bool) and not retour_get_state[0]:
            # tuple résultat (succès, état) : l'état n'est pas garanti,
            # on ne se fie pas à sa valeur
            logger.debug("masque_actif_dans_etat: sortie (état non garanti)")
            return False
        # l'état est le dernier élément ; un « & » direct sur le tuple
        # lèverait TypeError, on le déplie d'abord
        retour_get_state = retour_get_state[-1]
    logger.debug("masque_actif_dans_etat: sortie")
    return bool(retour_get_state & masque)


#: Issues possibles de la classification d'un clic sur un terminal VTE, voir
#: :func:`classify_terminal_click`.
TERMINAL_CLICK_OUTCOMES = ("paste", "open-menu", "check-url", "propagate")


def classify_terminal_click(*, button, n_press, ctrl_pressed, paste_on_right_click):
    """Classifie un clic sur un terminal VTE selon le bouton, les pressions et Ctrl.

    Reproduit à l'identique les deux branches `if`/`elif` de l'ancien
    gestionnaire `button-press-event` (`Wmain.on_terminal_click`, avant son
    portage session-40 vers `Gtk.GestureMultiPress` — voir
    `docs/gtk4-migration.md` §3.6-nonies) :

    - ``event.type == Gdk.EventType.BUTTON_PRESS and event.button == 3``
      (clic droit simple) devient ``button == 3 and n_press == 1`` →
      ``"paste"`` si `PASTE_ON_RIGHT_CLICK` est actif, sinon ``"open-menu"``
      (ouvrir popupMenu). L'ancien code retournait ``True`` dans les deux
      cas pour empêcher le code de mise au focus, en bas de la fonction, de
      s'exécuter.
    - Sinon, ``event.type == Gdk.EventType.BUTTON_PRESS and event.button
      == 1 and event.get_state() & Gdk.ModifierType.CONTROL_MASK`` (Ctrl+clic
      gauche simple) devient ``button == 1 and n_press == 1 and
      ctrl_pressed`` → ``"check-url"`` : détecter et ouvrir un lien sous le
      curseur. Contrairement au cas précédent, l'ancien code ne retournait
      rien ici (`None`, implicitement falsy) — le code de mise au focus
      s'exécutait donc quand même après.
    - Tout le reste (clic simple sans Ctrl, double/triple clic quel que
      soit le bouton — laissé à la sélection native de VTE, mot/ligne au
      double/triple-clic) → ``"propagate"`` : même traitement que
      ``"check-url"`` du point de vue de la mise au focus (elle s'exécute
      toujours), mais aucune action supplémentaire.

    Note de conception : à la différence de
    :func:`classify_tree_servers_press`, aucune issue n'« avale »
    (`swallow`) un double/triple-clic ici — l'ancien code ne testait que
    `event.type == Gdk.EventType.BUTTON_PRESS`, jamais `_2BUTTON_PRESS`/
    `_3BUTTON_PRESS`, laissant VTE traiter nativement ces pressions
    (sélection d'un mot puis d'une ligne). Un `"swallow"` sur ce site
    casserait donc cette sélection native — voir `docs/gtk4-migration.md`
    §3.6-nonies pour la mise en garde complète.

    Args:
        button (int): Numéro du bouton de la souris à l'origine du clic,
            tel que renvoyé par `Gtk.GestureSingle.get_current_button()`
            (1 = gauche, 2 = milieu, 3 = droit).
        n_press (int): Nombre de pressions consécutives, tel que transmis
            par le paramètre `n_press` du signal `pressed` de
            `Gtk.GestureMultiPress`/`Gtk.GestureClick` (1 = clic simple,
            2 = double clic, 3 = triple clic).
        ctrl_pressed (bool): `True` si le modificateur Ctrl était actif au
            moment du clic — anciennement lu directement sur
            `event.get_state()`, désormais sur l'évènement renvoyé par
            `Gtk.Gesture.get_last_event(None)` (voir la mise en garde
            §3.6-octies : ni `get_current_event()` ni
            `get_current_event_state()` n'existent en GTK3).
        paste_on_right_click (bool): Valeur de la préférence
            `conf.PASTE_ON_RIGHT_CLICK` au moment du clic, passée
            explicitement plutôt que lue sur un global
            (`CONSIGNES-AGENTS-IA.md` section 2).

    Returns:
        str: Une valeur de :data:`TERMINAL_CLICK_OUTCOMES` — voir ci-dessus
            pour la signification de chacune.
    """
    logger.debug("classify_terminal_click: entrée")
    if button == 3 and n_press == 1:
        logger.debug("classify_terminal_click: sortie")
        return "paste" if paste_on_right_click else "open-menu"
    if button == 1 and n_press == 1 and ctrl_pressed:
        logger.debug("classify_terminal_click: sortie")
        return "check-url"
    logger.debug("classify_terminal_click: sortie")
    return "propagate"


#: Issues possibles de la classification d'un clic sur l'étiquette d'un
#: onglet, voir :func:`classify_tab_label_click`.
TAB_LABEL_CLICK_OUTCOMES = ("open-menu", "close-tab", "propagate")


def classify_tab_label_click(*, button, n_press):
    """Classifie un clic sur l'étiquette d'un onglet selon le bouton et le nombre de pressions.

    Reproduit à l'identique les deux branches `if`/`elif` de l'ancien
    gestionnaire `button-press-event` (`widgets.NotebookTabLabel.popupmenu`,
    avant son portage session-41 vers `Gtk.GestureMultiPress` — voir
    `docs/gtk4-migration.md` §3.6-decies) :

    - `event.type == Gdk.EventType.BUTTON_PRESS and event.button == 3`
      (clic droit simple) devient `button == 3 and n_press == 1` →
      `"open-menu"` : ouvrir le menu contextuel de l'onglet — celui du
      bureau distant (`Gtk.Menu` legacy de
      `build_remote_desktop_context_menu()`) si l'onglet en est un,
      sinon `TabContextMenu`. Ce choix entre les deux menus reste une
      décision de l'appelant (`NotebookTabLabel._get_remote_desktop_widget()`
      lit un état d'instance hors de portée d'une fonction pure) —
      `classify_tab_label_click()` ne distingue que le déclenchement, pas
      la destination.
    - `event.type == Gdk.EventType.BUTTON_PRESS and event.button == 2`
      (clic milieu simple) devient `button == 2 and n_press == 1` →
      `"close-tab"` : fermer l'onglet, après confirmation éventuelle
      (`conf.CONFIRM_ON_CLOSE_TAB_MIDDLE`) — également une décision de
      l'appelant, hors de portée ici.
    - Tout le reste (clic gauche, bouton > 3, double/triple-clic quel que
      soit le bouton) → `"propagate"` : ne rien faire. Même mapping que
      `classify_terminal_click()` pour les pressions multiples : l'ancien
      code ne testait que `event.type == Gdk.EventType.BUTTON_PRESS`,
      jamais `_2BUTTON_PRESS`/`_3BUTTON_PRESS` — aucune branche dédiée au
      multi-clic à reproduire ici, contrairement à
      `classify_tree_servers_press()`.

    Note de conception — asymétrie de `Gtk.EventSequenceState.CLAIMED`
    (gérée côté appelant, pas ici) : l'ancien code retournait `True`
    (stop de propagation) sur le clic droit et sur un clic milieu annulé
    par confirmation, mais **pas** quand l'onglet était effectivement
    fermé (chute en fin de fonction, après `self.close_tab(...)`, sans
    `return`). Cette asymétrie ne peut pas être repliée dans cette
    fonction pure : elle dépend du résultat de `msgconfirm()`, connu
    seulement à l'exécution — voir `NotebookTabLabel.on_label_pressed`
    pour sa reproduction fidèle.

    Args:
        button (int): Numéro du bouton de la souris à l'origine du clic,
            tel que renvoyé par `Gtk.GestureSingle.get_current_button()`
            (1 = gauche, 2 = milieu, 3 = droit).
        n_press (int): Nombre de pressions consécutives, tel que transmis
            par le paramètre `n_press` du signal `pressed` de
            `Gtk.GestureMultiPress`/`Gtk.GestureClick` (1 = clic simple,
            2 = double clic, 3 = triple clic).

    Returns:
        str: Une valeur de :data:`TAB_LABEL_CLICK_OUTCOMES` — voir
            ci-dessus pour la signification de chacune.
    """
    logger.debug("classify_tab_label_click: entrée")
    if button == 3 and n_press == 1:
        logger.debug("classify_tab_label_click: sortie")
        return "open-menu"
    if button == 2 and n_press == 1:
        logger.debug("classify_tab_label_click: sortie")
        return "close-tab"
    logger.debug("classify_tab_label_click: sortie")
    return "propagate"
