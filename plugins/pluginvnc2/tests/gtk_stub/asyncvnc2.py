"""Stub minimal pour permettre l'import de vnc_tab.py sans le vrai
asyncvnc2 installe (utilise seulement au moment de la collecte des tests
de logique pure -- rien ici n'a besoin d'etre fonctionnellement correct,
juste present pour que `import asyncvnc2` reussisse)."""

import enum


class UpdateType(enum.Enum):
    VIDEO = "video"
    CLIPBOARD = "clipboard"
    BELL = "bell"
    SERVER_FENCE = "server_fence"
    END_OF_CONTINUOUS_UPDATES = "end_of_continuous_updates"


def connect(*args, **kwargs):
    raise NotImplementedError("stub asyncvnc2: connect() n'est pas implemente")


def listen(*args, **kwargs):
    raise NotImplementedError("stub asyncvnc2: listen() n'est pas implemente")
