# SPDX-License-Identifier: GPL-3.0-or-later
"""Dialogues de confirmation et de saisie, compatibles libadwaita 1.2 à 1.9.

``Adw.AlertDialog`` (libadwaita ≥ 1.5) quand il existe, sinon
``Adw.MessageDialog`` (1.2–1.4, déprécié en 1.6). Les deux ont la même API
de réponses et émettent ``response`` avec l'identifiant choisi ; seul
l'affichage diffère. Toujours asynchrones : aucune boucle imbriquée.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Optional

from gi.repository import Adw, GLib, Gtk
from loguru import logger

#: (identifiant, libellé traduit, apparence ou None). ``Optional`` et non
#: ``X | None`` : un alias est évalué à l'exécution, et Python 3.9 (Rocky 9,
#: #223) ne connaît pas l'opérateur ``|`` entre types.
Reponse = tuple[str, str, Optional[Adw.ResponseAppearance]]  # noqa: UP045 (Python 3.9)


def creer(
    parent: Gtk.Widget,
    titre: str,
    corps: str,
    reponses: Sequence[Reponse],
    defaut: str,
    fermeture: str,
    rappel: Callable[[str], None],
    extra: Gtk.Widget | None = None,
) -> Adw.Dialog | Adw.MessageDialog:
    """Construit et affiche un dialogue ; ``rappel(identifiant)`` à la réponse.

    Renvoie le dialogue (les tests émettent ``response`` eux-mêmes).
    """
    logger.debug("creer: {!r}", titre)
    if hasattr(Adw, "AlertDialog"):
        dialogue = Adw.AlertDialog(heading=titre, body=corps)
    else:  # libadwaita < 1.5
        racine = parent.get_root()
        dialogue = Adw.MessageDialog(heading=titre, body=corps, modal=True)
        if isinstance(racine, Gtk.Window):
            dialogue.set_transient_for(racine)
    for identifiant, libelle, apparence in reponses:
        dialogue.add_response(identifiant, libelle)
        if apparence is not None:
            dialogue.set_response_appearance(identifiant, apparence)
    dialogue.set_default_response(defaut)
    dialogue.set_close_response(fermeture)
    if extra is not None:
        dialogue.set_extra_child(extra)
    dialogue.connect("response", lambda _d, reponse: rappel(reponse))
    if (
        isinstance(dialogue, Adw.MessageDialog)
        and extra is not None
        and isinstance(extra, (Gtk.Entry, Gtk.PasswordEntry))
    ):
        # Vieux libadwaita (< 1.5) : dans le repli ``AdwMessageDialog``
        # (fenêtre GtkWindow), le Return de l'entrée peut n'activer ni le
        # bouton par défaut ni le dialogue — constaté #72 sous GTK 4.8 /
        # Adw 1.2 comme sous GTK 4.12 / Adw 1.4 : signal « activate » de
        # l'entrée émis, aucune réponse, dialogue laissé ouvert. On
        # laisse d'abord le chemin normal se jouer, puis un idle répond
        # directement si aucune réponse n'est partie — le rappel reste
        # unique dans tous les cas. Sans objet pour ``Adw.AlertDialog``
        # (libadwaita ≥ 1.5), qui gère le Return lui-même.
        repondu = {"deja": False}
        dialogue.connect("response", lambda *_a: repondu.__setitem__("deja", True))

        def _valider_par_return(*_args: object) -> None:
            """Return dans l'entrée : réponse en idle si le dialogue reste muet."""
            logger.debug("_valider_par_return: Return dans l'entrée du dialogue")

            def _repondre_si_muet() -> bool:
                """Idle après le Return : répond seulement si rien n'est parti."""
                logger.debug("_repondre_si_muet: contrôle du dialogue")
                if not repondu["deja"] and dialogue.get_response_enabled(defaut):
                    logger.debug("_repondre_si_muet: réponse « {} » émise", defaut)
                    dialogue.response(defaut)
                    dialogue.set_visible(False)
                logger.debug("_repondre_si_muet: fait")
                return GLib.SOURCE_REMOVE

            GLib.idle_add(_repondre_si_muet)
            logger.debug("_valider_par_return: idle planifié")

        extra.connect("activate", _valider_par_return)
    if isinstance(dialogue, Adw.MessageDialog):
        dialogue.present()
    else:
        dialogue.present(parent)
    logger.debug("creer: affiché ({})", type(dialogue).__name__)
    return dialogue
