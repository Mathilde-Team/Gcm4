# SPDX-License-Identifier: GPL-3.0-or-later
"""Dialogues de confirmation (``src/gcm4/ui/dialogues.py``) : repli Debian 12.

Libadwaita ≥ 1.5 fournit ``Adw.AlertDialog`` ; une vue d'``Adw`` sans cet
attribut force le repli ``Adw.MessageDialog`` (1.2–1.4), transitoire à la
racine de son parent et affiché par ``present()`` sans parent. Les autres
chemins (AlertDialog, apparence des réponses, rappel) sont couverts par
``test_echange.py``.
"""

from __future__ import annotations

from gi.repository import Adw, Gtk

import gcm4.ui  # noqa: F401  (require_version avant gi.repository)
from gcm4.ui import dialogues


class _VueAdwMessageDialog:
    """Vue d'``Adw`` sans ``AlertDialog`` : force ``Adw.MessageDialog``."""

    MessageDialog = Adw.MessageDialog


def test_repli_message_dialog(monkeypatch, pomper):
    """Vue sans AlertDialog : MessageDialog transitoire, réponse au rappel."""
    fenetre = Gtk.Window()
    parent = Gtk.Box()
    fenetre.set_child(parent)
    monkeypatch.setattr(dialogues, "Adw", _VueAdwMessageDialog)
    recus: list[str] = []
    dialogue = dialogues.creer(
        parent,
        "Titre",
        "Corps",
        [("oui", "Oui", Adw.ResponseAppearance.SUGGESTED), ("non", "Non", None)],
        "oui",
        "non",
        recus.append,
    )
    assert isinstance(dialogue, Adw.MessageDialog)
    dialogue.emit("response", "oui")
    assert recus == ["oui"]
    fenetre.destroy()
    pomper()


def test_repli_message_dialog_sans_racine(monkeypatch, pomper):
    """Parent sans fenêtre racine : pas de transitoire, le dialogue s'affiche quand même."""
    monkeypatch.setattr(dialogues, "Adw", _VueAdwMessageDialog())
    recus: list[str] = []
    dialogue = dialogues.creer(
        Gtk.Box(),
        "Titre",
        "Corps",
        [("ok", "OK", None)],
        "ok",
        "ok",
        recus.append,
    )
    assert isinstance(dialogue, Adw.MessageDialog)
    dialogue.emit("response", "ok")
    assert recus == ["ok"]
    pomper()
