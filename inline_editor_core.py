#!/usr/bin/python3
# -*- coding: UTF-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Logique métier pure de l'éditeur inline `CellTextView` (`widgets.py`, zéro import Gtk).

Premier des quatre signaux GTK3-only inventoriés par l'audit session-42
(`docs/gtk4-migration.md` §3.6-undecies) à être effectivement porté cette
session : `key-press-event` → `Gtk.EventControllerKey` (signal
`key-pressed`).

Contrairement à `Gtk.GestureMultiPress`/`Gtk.GestureClick`
(`gesture_trigger_core.py`), qui sont deux classes distinctes ne partageant
qu'un signal, `Gtk.EventControllerKey` est ici la **même classe** des deux
côtés (ajoutée en GTK3 3.24, inchangée en GTK4, vérifié sur
`docs.gtk.org`/gtk-rs) avec le même signal `key-pressed(keyval, keycode,
state)` renvoyant un booléen. Seule la construction diffère et devra être
ajustée lors du futur passage réel à GTK4 : `Gtk.EventControllerKey.new(widget)`
attache directement le contrôleur au widget passé en paramètre en GTK3,
alors qu'en GTK4 le constructeur ne prend aucun argument et l'attache se
fait séparément via `widget.add_controller(controller)`.

Proposition d'ordre pour les quatre signaux (à confirmer avec l'auteure,
comme pour les chantiers `plugins/ssh/gtk4.py`/RDP) : `key-press-event`
d'abord (ce module) — seul des quatre totalement indépendant des trois
autres (ne lit/n'écrit pas `_in_editor_menu`, contrairement au couple
`focus-out-event`/`populate-popup`), équivalence directe et documentée sans
aucun comportement d'exécution à vérifier empiriquement. `button-press-event`
ensuite — portage mécaniquement simple vers `Gtk.GestureClick`/
`Gtk.GestureMultiPress` (déjà utilisées ailleurs, voir
`gesture_trigger_core.py`), mais son utilité réelle (contournement d'un bug
GTK3 précis, voir la docstring de `MultilineCellRenderer._on_editor_pressed`
dans `widgets.py`) sur un GTK4 réel n'est pas vérifiable ici — nuance
distinguant ce signal de `key-press-event`, absente de l'audit initial de
la session-42, qui ne classait pas les quatre signaux par degré de risque.
Le couple `focus-out-event`/`populate-popup` reste en dernier, pour les
raisons déjà données en session-42 (question du focus du `Gtk.Popover`
interne du menu contextuel natif GTK4, invérifiable empiriquement dans cet
environnement de travail).

Deuxième des quatre signaux, `button-press-event` (session-44,
2026-09-18) : ``classify_editor_button_press()`` ci-dessous formalise, à
la différence de ``classify_editor_key_press()``, une classification qui
ne branche sur aucun de ses paramètres — l'ancien gestionnaire
(`MultilineCellRenderer._on_editor_pressed`) retournait inconditionnellement
`True` pour tout clic reçu, contournement d'un bug GTK3 précis
(`gtk_text_mark_get_buffer: assertion 'GTK_IS_TEXT_MARK (mark)' failed`,
voir son commentaire d'origine), sans jamais distinguer bouton ou nombre de
pressions — à la différence des classificateurs de
`gesture_trigger_core.py`, qui branchent tous sur au moins un de ces deux
paramètres. La fonction est néanmoins extraite ici, malgré l'absence de
branchement réel, pour rester cohérente avec la discipline de tests de
`CONSIGNES-AGENTS-IA.md` section 5 et documenter explicitement le
comportement inconditionnel plutôt que de le laisser implicite dans
`widgets.py`.
"""

from loguru import logger

# Convention bibliothèque (#130) : muet à l'import, réactivé par
# logging_config.configure_logging() (CORE_MODULES).
logger.disable(__name__)

#: Issues possibles de la classification d'un appui de touche dans
#: l'éditeur inline, voir :func:`classify_editor_key_press`.
EDITOR_KEY_PRESS_OUTCOMES = ("commit", "cancel", "ignore")


def classify_editor_key_press(
    *,
    keyval,
    state,
    shift_mask,
    control_mask,
    return_keyval,
    kp_enter_keyval,
    escape_keyval,
):
    """Classifie un appui de touche dans l'éditeur inline `CellTextView`.

    Reproduit à l'identique les branches de l'ancien gestionnaire
    `key-press-event` (`MultilineCellRenderer._on_editor_key_press_event`,
    avant son portage vers `Gtk.EventControllerKey`/`key-pressed` — voir
    `docs/gtk4-migration.md` §3.6-undecies) :

    - Si Shift ou Ctrl est enfoncé (``state & (shift_mask | control_mask)``),
      la touche est ignorée quelle qu'elle soit → ``"ignore"`` (l'ancien
      code retournait sans condition avant même de regarder ``keyval``).
    - Sinon, si ``keyval`` est Entrée ou Entrée du pavé numérique
      (``return_keyval``/``kp_enter_keyval``) → ``"commit"`` : valider
      l'édition en cours.
    - Sinon, si ``keyval`` est Échap (``escape_keyval``) → ``"cancel"`` :
      annuler l'édition en cours.
    - Tout le reste (texte normal à insérer) → ``"ignore"`` : ne rien faire
      de spécial, laisser l'éditeur traiter la touche normalement.

    Args:
        keyval (int): Code de la touche pressée (``Gdk.KEY_*``).
        state (int): Masque des modificateurs actifs à l'appui
            (``Gdk.ModifierType``, tel que transmis par le signal).
        shift_mask (int): Valeur de ``Gdk.ModifierType.SHIFT_MASK``.
        control_mask (int): Valeur de ``Gdk.ModifierType.CONTROL_MASK``.
        return_keyval (int): Valeur de ``Gdk.KEY_Return``.
        kp_enter_keyval (int): Valeur de ``Gdk.KEY_KP_Enter``.
        escape_keyval (int): Valeur de ``Gdk.KEY_Escape``.

    Returns:
        str: Une des valeurs de :data:`EDITOR_KEY_PRESS_OUTCOMES`.
    """
    logger.debug("classify_editor_key_press: entrée")
    if state & (shift_mask | control_mask):
        logger.debug("classify_editor_key_press: sortie")
        return "ignore"
    if keyval in (return_keyval, kp_enter_keyval):
        logger.debug("classify_editor_key_press: sortie")
        return "commit"
    if keyval == escape_keyval:
        logger.debug("classify_editor_key_press: sortie")
        return "cancel"
    logger.debug("classify_editor_key_press: sortie")
    return "ignore"


#: Issues possibles de la classification d'un clic dans l'éditeur inline,
#: voir :func:`classify_editor_button_press`.
EDITOR_BUTTON_PRESS_OUTCOMES = ("claim",)


def classify_editor_button_press(*, n_press):
    """Classifie un clic souris dans l'éditeur inline `CellTextView`.

    Reproduit à l'identique le comportement de l'ancien gestionnaire
    `button-press-event` (`MultilineCellRenderer._on_editor_pressed`, avant
    son portage vers `Gtk.GestureClick`/`Gtk.GestureMultiPress` — voir
    `docs/gtk4-migration.md` §3.6-terdecies) : celui-ci retournait
    inconditionnellement `True` quel que soit le clic reçu, contournement
    d'un bug GTK3 précis documenté dans son commentaire d'origine
    (``gtk_text_mark_get_buffer: assertion 'GTK_IS_TEXT_MARK (mark)'
    failed``), sans jamais distinguer bouton ou nombre de pressions — à la
    différence des classificateurs de `gesture_trigger_core.py`, qui
    branchent tous sur au moins un de ces deux paramètres.

    Contrairement à `key-press-event`, dont l'équivalence GTK3/GTK4 est
    documentée et vérifiée, l'utilité réelle de ce contournement sur un
    GTK4 réel n'est PAS vérifiable empiriquement dans cet environnement de
    travail (GTK4 absent) — à confirmer manuellement avant mise en
    production : le bug d'origine peut avoir disparu avec GTK4, auquel cas
    revendiquer systématiquement la séquence resterait sans effet néfaste
    mais deviendrait un contournement inutile plutôt qu'une régression.

    Args:
        n_press (int): Nombre de pressions consécutives. Non utilisé dans
            la classification (paramètre conservé pour uniformité avec les
            autres fonctions de classification de clic du dépôt, et pour
            une éventuelle distinction future si le bug d'origine s'avère
            spécifique à un seul type de clic).

    Returns:
        str: Toujours ``"claim"`` (seule valeur de
        :data:`EDITOR_BUTTON_PRESS_OUTCOMES`).
    """
    logger.debug("classify_editor_button_press: entrée")
    logger.debug("classify_editor_button_press: sortie")
    return "claim"
