# SPDX-License-Identifier: GPL-3.0-or-later
"""Logique sans GTK du plugin local (``plugins/local/core.py``, #295)."""

from __future__ import annotations

import pytest

import plugins.local.core as core
from gcm4.core import terminal


@pytest.mark.parametrize(
    ("environ", "attendu"),
    [
        ({"SHELL": "/bin/zsh"}, "/bin/zsh"),
        ({"SHELL": " /usr/bin/fish "}, "/usr/bin/fish"),
        ({"SHELL": ""}, "/bin/sh"),
        ({"SHELL": "  "}, "/bin/sh"),
        ({}, "/bin/sh"),
    ],
)
def test_shell(environ, attendu):
    """``SHELL`` de l'environnement ; absent ou vide (cron, systemd, CI) : ``/bin/sh`` (GTK3)."""
    assert core.shell(environ) == attendu


def test_lancement_shell_seul():
    """Le shell est lancé seul : aucun argument, aucun mot de passe, aucun descripteur 3."""
    resultat = core.lancement("/bin/bash")
    assert resultat == terminal.Lancement(["/bin/bash"])
    assert resultat.mot_de_passe == ""


def test_etapes_700_ms():
    """Premier envoi à 700 ms (GTK3 : ``basetime = 700``), puis délais cumulés."""
    assert core.DELAI_COMMANDES_MS == 700
    assert core.etapes("cd /srv\n##D=300\nls") == [(700, "cd /srv\r"), (1000, "ls\r")]
    assert core.etapes("") == []
