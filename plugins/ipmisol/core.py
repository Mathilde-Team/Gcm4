"""Partie métier du plugin ipmisol (sans GTK) — portage #179.

Comportement repris de ``plugins/plugin_ipmisol.py`` (GTK3) : ``ipmitool
-I lanplus -H hôte -p port -U utilisateur -E sol activate``, mot de passe
dans la variable d'environnement ``IPMI_PASSWORD`` (option ``-E``), jamais
en argument de ligne de commande (visible par ``ps``), et ``sol deactivate``
explicite : une session SOL mal fermée reste verrouillée côté BMC (iLO,
iDRAC, IMM) jusqu'à expiration d'un timeout.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess

from loguru import logger

from gcm4.core.model import Host
from gcm4.core.terminal import Lancement

IPMITOOL = "ipmitool"
PORT_DEFAUT = 623
DELAI_COMMANDES_MS = 700.0  # comme local (#295) : saisie des commandes
DELAIS_DESACTIVATION_S = 10.0  # comme GTK3 : deactivate doit rendre la main


def chemin(environnement: dict[str, str]) -> str | None:
    """Chemin d'``ipmitool``, ou ``None`` s'il est absent du ``PATH``."""
    logger.debug("chemin: entrée")
    resultat = shutil.which(IPMITOOL, path=environnement.get("PATH"))
    logger.debug("chemin: {}", resultat)
    return resultat


def port(host: Host) -> int:
    """Port UDP de la carte de gestion (623 par défaut)."""
    logger.debug("port: entrée")
    resultat = host.port or PORT_DEFAUT
    logger.debug("port: {}", resultat)
    return resultat


def lancement(chemin: str, host: Host) -> Lancement:
    """``ipmitool sol activate`` ; le mot de passe va dans ``IPMI_PASSWORD``.

    Args:
        chemin: chemin d'``ipmitool`` (:func:`chemin`).
        host: hôte à ouvrir (``extra_params`` : options ipmitool
            additionnelles, découpées par ``shlex`` comme en GTK3).

    Returns:
        Le mot de passe n'est ni dans ``argv`` ni dans ``repr`` (#145) : il
        part dans la variable d'environnement ``IPMI_PASSWORD`` (option
        ``-E`` d'ipmitool).

    Raises:
        ValueError: ``host.extra_params`` mal formés (``shlex.split``).
    """
    logger.debug("lancement: entrée")
    argv = [
        chemin,
        "-I",
        "lanplus",
        "-H",
        host.host,
        "-p",
        str(port(host)),
        "-U",
        host.user,
        "-E",
        *shlex.split(host.extra_params),
        "sol",
        "activate",
    ]
    env: tuple[str, ...] = ()
    if host.password:
        env = (f"IPMI_PASSWORD={host.password}",)
    logger.debug(
        "lancement: {} argument(s), IPMI_PASSWORD {}", len(argv), "fourni" if env else "absent"
    )
    return Lancement(argv, env=env)


def desactivation(chemin: str, host: Host) -> bool:
    """``sol deactivate`` : déverrouille le BMC même après une fermeture brutale.

    Bloquant jusqu'à 10 s (comme le dialogue GTK3) ; le mot de passe reste
    dans l'environnement du processus, jamais en argument.

    Returns:
        ``True`` si ipmitool a rendu la main sans erreur.
    """
    logger.debug("desactivation: entrée")
    argv = [
        chemin,
        "-I",
        "lanplus",
        "-H",
        host.host,
        "-p",
        str(port(host)),
        "-U",
        host.user,
        "-E",
        *shlex.split(host.extra_params),
        "sol",
        "deactivate",
    ]
    env = dict(os.environ)
    if host.password:
        env["IPMI_PASSWORD"] = host.password
    try:
        processus = subprocess.run(
            argv, capture_output=True, timeout=DELAIS_DESACTIVATION_S, env=env, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        logger.debug("desactivation: échec")
        return False
    logger.debug("desactivation: code {}", processus.returncode)
    return processus.returncode == 0
