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

from gi.repository import Adw, Gtk
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
    if isinstance(dialogue, Adw.MessageDialog):
        dialogue.present()
    else:
        dialogue.present(parent)
    logger.debug("creer: affiché ({})", type(dialogue).__name__)
    return dialogue
