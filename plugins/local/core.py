"""Logique du plugin local, sans GTK : shell à lancer et délai des commandes.

Reprend ``plugins/plugin_local.py`` (GTK3) :

- lance ``$SHELL``, ou ``/bin/sh`` si la variable est absente ou vide
  (``SHELL`` de ``gnome_connection_manager.py``), sans argument ;
- ``commands`` : lignes envoyées après l'ouverture, la première fois au
  bout de 700 ms (et non 3 s comme telnet) ; une ligne ``##D=<ms>`` ajoute
  un délai avant les suivantes.

Options de l'hôte (``host.protocol_options["local"]``), environnement,
liaisons et forme INI de ``commands`` : communs aux plugins terminal, dans
:mod:`gcm4.core.terminal` (#295).
"""

from __future__ import annotations

from collections.abc import Mapping

from loguru import logger

from gcm4.core.terminal import Lancement
from gcm4.core.terminal import etapes as _etapes

#: Délai avant le premier envoi de ``commands`` (ms), comme GTK3.
DELAI_COMMANDES_MS = 700

#: Shell quand ``SHELL`` est absent ou vide (cron, systemd, CI).
SHELL_DEFAUT = "/bin/sh"


def shell(environ: Mapping[str, str]) -> str:
    """Shell de l'utilisateur : ``SHELL`` de ``environ``, sinon :data:`SHELL_DEFAUT`."""
    logger.debug("shell: entrée")
    resultat = environ.get("SHELL", "").strip() or SHELL_DEFAUT
    logger.debug("shell: {}", resultat)
    return resultat


def lancement(chemin: str) -> Lancement:
    """Commande du terminal local : le shell seul, sans argument ni mot de passe (GTK3)."""
    logger.debug("lancement: {}", chemin)
    resultat = Lancement([chemin])
    logger.debug("lancement: {!r}", resultat)
    return resultat


def etapes(commandes: str) -> list[tuple[int, str]]:
    """Envois de ``commands`` (:func:`gcm4.core.terminal.etapes`), le premier au bout de 700 ms."""
    logger.debug("etapes: entrée")
    resultat = _etapes(commandes, DELAI_COMMANDES_MS)
    logger.debug("etapes: {} envoi(s)", len(resultat))
    return resultat
