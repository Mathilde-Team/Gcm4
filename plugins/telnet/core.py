"""Logique du plugin telnet, sans GTK : commande et délai des commandes après connexion.

Reprend ``plugins/plugin_telnet.py`` (GTK3) :

- sans utilisateur **ou** sans mot de passe : ``telnet [paramètres] hôte port``
  (pas de saisie automatique, l'utilisateur est ignoré comme en GTK3) ;
- avec les deux : ``expect -f telnet.expect telnet -l utilisateur
  [paramètres] hôte port`` ; le mot de passe est donné au script par un
  tube (descripteur 3), jamais par la ligne de commande ;
- ``commands`` : lignes envoyées après la connexion, la première fois au
  bout de 3 s ; une ligne ``##D=<ms>`` ajoute un délai avant les suivantes.

Options de l'hôte (``host.protocol_options["telnet"]``), environnement,
liaisons et forme INI de ``commands`` : communs aux plugins terminal,
dans :mod:`gcm4.core.terminal` (#295) et réexportés ici.
"""

from __future__ import annotations

import shlex
from pathlib import Path

from loguru import logger

from gcm4.core.terminal import (
    NB_LIAISONS,
    OPT_COMMANDES,
    OPT_RETOUR_ARRIERE,
    OPT_SUPPRESSION,
    OPT_TERM,
    PREFIXE_DELAI,
    TERM_DEFAUT,
    Lancement,
    ecrire_commandes,
    environnement,
    est_delai,
    liaison,
    lire_commandes,
)
from gcm4.core.terminal import etapes as _etapes

__all__ = [
    "DELAI_COMMANDES_MS",
    "NB_LIAISONS",
    "OPT_COMMANDES",
    "OPT_RETOUR_ARRIERE",
    "OPT_SUPPRESSION",
    "OPT_TERM",
    "PORT_DEFAUT",
    "PREFIXE_DELAI",
    "SCRIPT_EXPECT",
    "TERM_DEFAUT",
    "Lancement",
    "ecrire_commandes",
    "environnement",
    "est_delai",
    "etapes",
    "lancement",
    "liaison",
    "lire_commandes",
]

#: Port telnet par défaut.
PORT_DEFAUT = 23

#: Délai avant le premier envoi de ``commands`` (ms), comme GTK3.
DELAI_COMMANDES_MS = 3000

#: Script expect livré avec le plugin.
SCRIPT_EXPECT = Path(__file__).with_name("telnet.expect")


def lancement(
    adresse: str,
    port: int | None,
    utilisateur: str,
    mot_de_passe: str,
    parametres: str,
    telnet: str,
    expect: str | None,
    script: Path = SCRIPT_EXPECT,
) -> Lancement:
    """Commande telnet de l'hôte, avec ``expect`` seulement si utilisateur et mot de passe sont donnés.

    Args:
        adresse: hôte distant.
        port: port ; ``None`` : :data:`PORT_DEFAUT`.
        utilisateur: utilisateur (ignoré sans mot de passe, comme GTK3).
        mot_de_passe: mot de passe (ignoré sans utilisateur).
        parametres: paramètres supplémentaires de telnet, découpés comme un shell.
        telnet: chemin du programme ``telnet``.
        expect: chemin du programme ``expect`` ; ``None`` s'il est absent.
        script: script expect.

    Raises:
        ValueError: ``parametres`` mal formé (guillemet non fermé).
        FileNotFoundError: saisie automatique demandée sans ``expect`` ;
            ``args[0]`` est le nom du programme manquant.
    """
    logger.debug("lancement: {}:{} utilisateur={!r}", adresse, port, utilisateur)
    fin = [*shlex.split(parametres), adresse, str(PORT_DEFAUT if port is None else port)]
    if utilisateur and mot_de_passe:
        if expect is None:
            logger.debug("lancement: expect absent")
            raise FileNotFoundError("expect")
        resultat = Lancement(
            [expect, "-f", str(script), telnet, "-l", utilisateur, *fin], mot_de_passe
        )
    else:
        resultat = Lancement([telnet, *fin])
    logger.debug("lancement: {!r}", resultat)
    return resultat


def etapes(commandes: str) -> list[tuple[int, str]]:
    """Envois de ``commands`` (:func:`gcm4.core.terminal.etapes`), le premier au bout de 3 s."""
    logger.debug("etapes: entrée")
    resultat = _etapes(commandes, DELAI_COMMANDES_MS)
    logger.debug("etapes: {} envoi(s)", len(resultat))
    return resultat
