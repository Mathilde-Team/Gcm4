# SPDX-License-Identifier: GPL-3.0-or-later
"""Logique de la session SSH GTK4, sans GTK (#396, partie 1).

Reprend ``SshTab.connect_ssh`` de ``plugins/plugin_ssh.py`` (GTK3) :
utilisateur, port, keep-alive, redirections de ports, X11, agent,
compression, clé privée, options ``-o`` avancées, paramètres
supplémentaires, puis l'adresse de l'hôte.

Les options de l'hôte sont celles de ``host.protocol_options["ssh"]``, avec
les **mêmes clés INI que GTK3** (``models.save_host_to_ini`` et
``SshPlugin.host_fields``) : un ``gcm.conf`` GTK3 s'ouvre sans conversion.

Écarts avec GTK3, voulus :

- sans utilisateur, ``-l`` n'est pas passé (GTK3 forçait l'utilisateur
  local) : ``ssh`` applique alors ``User`` de ``~/.ssh/config`` ;
- sans port, ``-p`` n'est pas passé : ``ssh`` applique ``Port`` de
  ``~/.ssh/config``, sinon 22 ;
- le mot de passe passe par un tube (descripteur 3 de ``ssh.expect``), et
  non plus par une frappe simulée 2 s après le lancement ;
- ``ssh.expect`` ne répond plus « yes » tout seul à la question de
  l'empreinte d'un hôte inconnu : la question reste à l'utilisateur.
"""

from __future__ import annotations

import os
import shlex
from collections.abc import Mapping
from pathlib import Path

from loguru import logger

from gcm4.core.i18n import N_
from gcm4.core.terminal import Lancement
from gcm4.core.terminal import etapes as _etapes

__all__ = [
    "CHAMPS_AVANCES",
    "DELAI_COMMANDES_MS",
    "OPT_AGENT",
    "OPT_CLE",
    "OPT_COMPRESSION",
    "OPT_KEEPALIVE",
    "OPT_NIVEAU_COMPRESSION",
    "OPT_TUNNELS",
    "OPT_X11",
    "PORT_DEFAUT",
    "SCRIPT_EXPECT",
    "arguments",
    "booleen",
    "cle_absente",
    "ecrire_tunnels",
    "etapes",
    "lancement",
    "lire_tunnels",
    "redirection",
]

#: Port SSH par défaut.
PORT_DEFAUT = 22

#: Délai avant le premier envoi de ``commands`` (ms), comme GTK3.
DELAI_COMMANDES_MS = 3000

#: Script expect livré avec le plugin (saisie du mot de passe).
SCRIPT_EXPECT = Path(__file__).with_name("ssh.expect")

#: Intervalle de keep-alive en secondes (``ServerAliveInterval``) ; vide ou 0 : aucun.
OPT_KEEPALIVE = "keepalive"
#: Redirections de ports, séparées par des virgules (format GTK3, :func:`redirection`).
OPT_TUNNELS = "tunnel"
#: Transfert X11 (``-X``), booléen INI.
OPT_X11 = "x11"
#: Transfert de l'agent (``-A``), booléen INI.
OPT_AGENT = "agent"
#: Compression (``-C``), booléen INI.
OPT_COMPRESSION = "compression"
#: Niveau de compression (``CompressionLevel``), seulement avec la compression.
OPT_NIVEAU_COMPRESSION = "compression-level"
#: Fichier de clé privée (``-i``).
OPT_CLE = "private_key"

#: Options ``-o`` avancées : (clé INI, option ssh, aide), dans l'ordre de GTK3
#: (``_ADVANCED_FIELDS`` de ``plugins/plugin_ssh.py``).
CHAMPS_AVANCES: tuple[tuple[str, str, str], ...] = (
    ("proxy_jump", "ProxyJump", N_("Bastion host(s) to hop through, e.g. user@bastion")),
    (
        "proxy_command",
        "ProxyCommand",
        N_("Arbitrary command to reach the target, e.g. via corkscrew or nc"),
    ),
    ("strict_host_key_checking", "StrictHostKeyChecking", N_("yes / no / accept-new")),
    ("user_known_hosts_file", "UserKnownHostsFile", ""),
    ("connect_timeout", "ConnectTimeout", N_("Seconds before giving up on the connection")),
    ("request_tty", "RequestTTY", N_("yes / no / force / auto")),
    ("remote_command", "RemoteCommand", N_("Command to run instead of a shell")),
    ("control_master", "ControlMaster", N_("yes / no / ask / auto")),
    ("control_persist", "ControlPersist", N_("e.g. 10m, or yes")),
    ("pubkey_authentication", "PubkeyAuthentication", N_("yes / no")),
    ("password_authentication", "PasswordAuthentication", N_("yes / no")),
    ("ssh_log_level", "LogLevel", N_("QUIET / ERROR / INFO / VERBOSE / DEBUG…")),
    ("add_keys_to_agent", "AddKeysToAgent", N_("yes / no / ask / confirm")),
    ("server_alive_count_max", "ServerAliveCountMax", ""),
    ("identities_only", "IdentitiesOnly", N_("yes / no")),
)

#: Suffixe GTK3 d'une redirection dynamique (``8080:*:*`` → ``-D 8080``).
_SUFFIXE_DYNAMIQUE = ":*:*"
#: Préfixe GTK3 d'une redirection distante (``R:8080:hôte:80`` → ``-R 8080:hôte:80``).
_PREFIXE_DISTANTE = "R:"


def booleen(texte: str) -> bool:
    """Booléen INI de GTK3 (``True``, ``1``, ``yes``…) ; toute autre valeur : faux."""
    logger.trace("booleen: {!r}", texte)
    resultat = texte.strip().lower() in ("true", "1", "yes", "on")
    logger.trace("booleen: -> {}", resultat)
    return resultat


def lire_tunnels(brut: str) -> list[str]:
    """Redirections de l'option :data:`OPT_TUNNELS`, sans les entrées vides."""
    logger.debug("lire_tunnels: {!r}", brut)
    resultat = [t.strip() for t in brut.split(",") if t.strip()]
    logger.debug("lire_tunnels: {} redirection(s)", len(resultat))
    return resultat


def ecrire_tunnels(tunnels: list[str]) -> str:
    """Forme INI de :data:`OPT_TUNNELS` (``,`` entre les redirections, comme GTK3)."""
    logger.debug("ecrire_tunnels: {} redirection(s)", len(tunnels))
    resultat = ",".join(t.strip() for t in tunnels if t.strip())
    logger.debug("ecrire_tunnels: {!r}", resultat)
    return resultat


def redirection(tunnel: str) -> list[str]:
    """Arguments ssh d'une redirection au format GTK3.

    - ``R:<spéc>`` : redirection distante, ``-R <spéc>`` ;
    - ``<port>:*:*`` : redirection dynamique (SOCKS), ``-D <port>`` ;
    - sinon : redirection locale, ``-L <spéc>``.
    """
    logger.debug("redirection: {!r}", tunnel)
    if tunnel.startswith(_PREFIXE_DISTANTE):
        resultat = ["-R", tunnel[len(_PREFIXE_DISTANTE) :]]
    elif tunnel.endswith(_SUFFIXE_DYNAMIQUE):
        resultat = ["-D", tunnel[: -len(_SUFFIXE_DYNAMIQUE)]]
    else:
        resultat = ["-L", tunnel]
    logger.debug("redirection: -> {!r}", resultat)
    return resultat


def arguments(options: Mapping[str, str]) -> list[str]:
    """Arguments ssh tirés des options de l'hôte, dans l'ordre de GTK3.

    Keep-alive, redirections, ``-X``, ``-A``, ``-C`` (et son niveau), ``-i``
    (``~`` développé), puis les options ``-o`` de :data:`CHAMPS_AVANCES`
    renseignées.
    """
    logger.debug("arguments: {} option(s)", len(options))
    args: list[str] = []
    keepalive = options.get(OPT_KEEPALIVE, "").strip()
    if keepalive not in ("", "0"):
        args += ["-o", f"ServerAliveInterval={keepalive}"]
    for tunnel in lire_tunnels(options.get(OPT_TUNNELS, "")):
        args += redirection(tunnel)
    if booleen(options.get(OPT_X11, "")):
        args.append("-X")
    if booleen(options.get(OPT_AGENT, "")):
        args.append("-A")
    if booleen(options.get(OPT_COMPRESSION, "")):
        args.append("-C")
        niveau = options.get(OPT_NIVEAU_COMPRESSION, "").strip()
        if niveau:
            args += ["-o", f"CompressionLevel={niveau}"]
    cle = options.get(OPT_CLE, "").strip()
    if cle:
        args += ["-i", os.path.expanduser(cle)]
    for cle_ini, option, _aide in CHAMPS_AVANCES:
        valeur = options.get(cle_ini, "").strip()
        if valeur:
            args += ["-o", f"{option}={valeur}"]
    logger.debug("arguments: {!r}", args)
    return args


def cle_absente(options: Mapping[str, str]) -> str | None:
    """Chemin développé de la clé privée si elle est renseignée mais introuvable, sinon ``None``.

    GTK3 prévenait l'utilisateur et lançait quand même ssh (qui ignore une
    clé absente) : l'appelant fait de même.
    """
    logger.debug("cle_absente: entrée")
    cle = options.get(OPT_CLE, "").strip()
    chemin = os.path.expanduser(cle) if cle else ""
    resultat = chemin if chemin and not os.path.exists(chemin) else None
    logger.debug("cle_absente: {!r}", resultat)
    return resultat


def lancement(
    adresse: str,
    port: int | None,
    utilisateur: str,
    mot_de_passe: str,
    parametres: str,
    options: Mapping[str, str],
    ssh: str,
    expect: str | None,
    script: Path = SCRIPT_EXPECT,
) -> Lancement:
    """Commande ssh de l'hôte, avec ``expect`` seulement s'il y a un mot de passe.

    Args:
        adresse: hôte distant (ou alias de ``~/.ssh/config``).
        port: port ; ``None`` : celui de ``~/.ssh/config``, sinon 22.
        utilisateur: utilisateur ; vide : celui de ``~/.ssh/config``.
        mot_de_passe: mot de passe, donné à ``ssh.expect`` par le descripteur 3.
        parametres: paramètres supplémentaires de ssh, découpés comme un shell.
        options: options de l'hôte (``host.protocol_options["ssh"]``).
        ssh: chemin du programme ``ssh``.
        expect: chemin du programme ``expect`` ; ``None`` s'il est absent.
        script: script expect.

    Raises:
        ValueError: ``parametres`` mal formé (guillemet non fermé).
        FileNotFoundError: mot de passe donné sans ``expect`` ; ``args[0]``
            est le nom du programme manquant.
    """
    logger.debug("lancement: {}:{} utilisateur={!r}", adresse, port, utilisateur)
    args: list[str] = []
    if utilisateur:
        args += ["-l", utilisateur]
    if port is not None:
        args += ["-p", str(port)]
    args += [*arguments(options), *shlex.split(parametres), adresse]
    if mot_de_passe:
        if expect is None:
            logger.debug("lancement: expect absent")
            raise FileNotFoundError("expect")
        resultat = Lancement([expect, "-f", str(script), ssh, *args], mot_de_passe)
    else:
        resultat = Lancement([ssh, *args])
    logger.debug("lancement: {!r}", resultat)
    return resultat


def etapes(commandes: str) -> list[tuple[int, str]]:
    """Envois de ``commands`` (:func:`gcm4.core.terminal.etapes`), le premier au bout de 3 s."""
    logger.debug("etapes: entrée")
    resultat = _etapes(commandes, DELAI_COMMANDES_MS)
    logger.debug("etapes: {} envoi(s)", len(resultat))
    return resultat
