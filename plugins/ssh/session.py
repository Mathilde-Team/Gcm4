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

import getpass
import os
import re
import shlex
import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from gcm4.core.i18n import N_, _
from gcm4.core.terminal import Lancement
from gcm4.core.terminal import etapes as _etapes

if TYPE_CHECKING:
    from gcm4.core.model import Host

__all__ = [
    "CHAMPS_AVANCES",
    "DELAI_COMMANDES_MS",
    "OPT_AGENT",
    "OPT_CLE",
    "OPT_COMPRESSION",
    "OPT_KEEPALIVE",
    "OPT_NIVEAU_COMPRESSION",
    "OPT_SESSION_PARTAGEE",
    "OPT_NOM_SESSION",
    "OPT_TUNNELS",
    "OPT_X11",
    "OPT_SSHFS_MONTAGE",
    "OPT_SSHFS_DISTANT",
    "OPT_SSHFS_LOCAL",
    "arreter_montage",
    "demarrer_montage",
    "commande_auth",
    "commande_montage",
    "PORT_DEFAUT",
    "SCRIPT_EXPECT",
    "arguments",
    "booleen",
    "cle_absente",
    "ecrire_tunnels",
    "etapes",
    "lancement",
    "lire_tunnels",
    "nom_session",
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
#: Montage sshfs à la connexion (clé INI de GTK3, sans conversion).
OPT_SSHFS_MONTAGE = "ssh_sshfs_mount"
#: Chemin distant monté par sshfs (clé INI de GTK3).
OPT_SSHFS_DISTANT = "ssh_sshfs_remote_path"
#: Point de montage local de sshfs (clé INI de GTK3).
OPT_SSHFS_LOCAL = "ssh_sshfs_local_mount"
#: Session tmux distante, désactivée si la clé est absente.
OPT_SESSION_PARTAGEE = "shared_session"
#: Nom tmux, limité aux caractères ASCII sûrs ; vide : ``gcm4``.
OPT_NOM_SESSION = "shared_session_name"

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


def nom_session(options: Mapping[str, str]) -> str:
    """Nom tmux sûr ; une valeur vide sélectionne ``gcm4``.

    Args:
        options: options SSH du profil.

    Returns:
        Nom ASCII de 1 à 64 caractères, sans métacaractère de shell ou tmux.

    Raises:
        ValueError: nom non vide hors de l'alphabet autorisé.
    """
    logger.debug("nom_session: entrée")
    nom = options.get(OPT_NOM_SESSION, "").strip() or "gcm4"
    if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", nom) is None:
        logger.debug("nom_session: sortie | nom refusé")
        raise ValueError(
            _("Session name: use 1 to 64 ASCII letters, digits, underscores or hyphens.")
        )
    logger.debug("nom_session: sortie | nom valide")
    return nom


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
        ValueError: ``parametres`` mal formé ou nom de session tmux invalide.
        FileNotFoundError: mot de passe donné sans ``expect`` ; ``args[0]``
            est le nom du programme manquant.
    """
    logger.debug("lancement: {}:{} utilisateur={!r}", adresse, port, utilisateur)
    args: list[str] = []
    partage = booleen(options.get(OPT_SESSION_PARTAGEE, ""))
    # La première valeur RemoteCommand gagne dans OpenSSH : neutraliser
    # explicitement celle du profil et de ~/.ssh/config avant la commande tmux.
    if partage:
        args += ["-o", "RemoteCommand=none"]
    if utilisateur:
        args += ["-l", utilisateur]
    if port is not None:
        args += ["-p", str(port)]
    args += [*arguments(options), *shlex.split(parametres)]
    if partage:
        # -tt après les options du profil impose le pseudo-terminal nécessaire
        # à tmux. Le nom validé ne peut injecter ni commande ni cible tmux.
        args += ["-tt", "--", adresse, f"exec tmux new-session -A -s {nom_session(options)}"]
    else:
        args.append(adresse)
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


def commande_auth(
    cle: str, mot_de_passe: str, commande: list[str], sshpass: str | None = None
) -> list[str] | None:
    """Commande préfixée par ``sshpass`` si l'hôte n'a pas de clé, mais un mot de passe.

    Portage de ``_ssh_auth_wrap`` de GTK3. Le mot de passe n'est jamais
    journalisé.

    Returns:
        La commande (éventuellement préfixée), ou ``None`` quand aucune
        authentification non interactive n'est disponible (ni clé, ni
        ``sshpass``).
    """
    logger.debug("commande_auth: entrée | clé={} sshpass={}", bool(cle), sshpass is not None)
    if cle or not mot_de_passe:
        logger.debug("commande_auth: sortie | commande inchangée")
        return commande
    resultat = [sshpass, "-p", mot_de_passe, *commande] if sshpass else None
    logger.debug("commande_auth: {}", "préfixée" if resultat else "aucune auth non interactive")
    return resultat


def commande_montage(
    sshfs: str,
    port: int | None,
    utilisateur: str,
    adresse: str,
    distant: str,
    local: str,
    cle: str,
) -> list[str]:
    """Arguments du montage sshfs, comme ``_start_sshfs_mount`` de GTK3."""
    logger.debug(
        "commande_montage: {}@{}:{} -> {} | port={}",
        utilisateur,
        adresse,
        distant,
        local,
        port,
    )
    commande = [
        sshfs,
        "-p",
        str(port if port is not None else PORT_DEFAUT),
        "-o",
        "reconnect,ServerAliveInterval=15,ServerAliveCountMax=3",
    ]
    if cle:
        commande += ["-o", f"IdentityFile={os.path.expanduser(cle)}"]
    commande += [f"{utilisateur}@{adresse}:{distant}", local]
    logger.debug("commande_montage: sortie")
    return commande


def demarrer_montage(
    hote: Host,
    options: Mapping[str, str],
    log,
    *,
    sshfs: str | None = None,
    ssh: str | None = None,
    sshpass: str | None = None,
    utilisateur_courant: str | None = None,
) -> str | None:
    """Lance le montage sshfs de l'hôte si l'option est active.

    Reprend ``_start_sshfs_mount`` et ``_ensure_remote_sshfs_dir`` de GTK3 :
    création du point de montage local, ``mkdir -p`` distant par ssh, puis
    processus sshfs détaché. Aucune exception ne remonte : un échec est
    journalé (avertissement) et le montage est simplement ignoré.

    Args:
        hote: hôte ouvert (adresse, port, utilisateur, clé, mot de passe).
        options: options ssh de l'hôte (``OPT_SSHFS_*``).
        log: journal du plugin (``warning``/``debug`` comme loguru).
        sshfs: chemin de ``sshfs`` ; ``None`` : cherché dans ``PATH``.
        ssh: chemin de ``ssh`` pour le ``mkdir`` distant ; ``None`` : ``PATH``.
        sshpass: chemin de ``sshpass`` ; ``None`` : ``PATH``.
        utilisateur_courant: nom de l'utilisateur local (défauts des chemins) ;
            ``None`` : ``getpass.getuser()``.

    Returns:
        Le point de montage local, ou ``None`` si le montage n'a pas été lancé.
    """
    logger.debug("demarrer_montage: {!r}", hote.path)
    if not booleen(options.get(OPT_SSHFS_MONTAGE, "")):
        logger.debug("demarrer_montage: sortie | option inactive")
        return None
    if utilisateur_courant is None:
        utilisateur_courant = getpass.getuser()
    distant = options.get(OPT_SSHFS_DISTANT, "").strip() or f"/mnt/{utilisateur_courant}"
    nom_hote = hote.host or hote.name or "host"
    local = (
        options.get(OPT_SSHFS_LOCAL, "").strip() or f"/home/{utilisateur_courant}/mnt/{nom_hote}"
    )
    binaire = sshfs if sshfs is not None else shutil.which("sshfs")
    if not binaire:
        log.warning("sshfs introuvable, montage ignoré pour {}", hote.path)
        return None
    try:
        os.makedirs(local, exist_ok=True)
    except OSError as exc:
        log.warning("impossible de créer {} : {}", local, exc)
        return None
    _assurer_dossier_distant(
        hote,
        distant,
        log,
        ssh=ssh if ssh is not None else shutil.which("ssh"),
        sshpass=sshpass if sshpass is not None else shutil.which("sshpass"),
    )
    commande = commande_auth(
        options.get(OPT_CLE, "").strip(),
        hote.password,
        commande_montage(
            binaire,
            hote.port,
            hote.user or utilisateur_courant,
            hote.host or hote.name,
            distant,
            local,
            options.get(OPT_CLE, "").strip(),
        ),
        sshpass if sshpass is not None else shutil.which("sshpass"),
    )
    if commande is None:
        log.warning("pas de clé privée ni de sshpass, montage ignoré pour {}", hote.path)
        return None
    try:
        subprocess.Popen(commande, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        log.warning("échec du lancement du montage pour {} : {}", hote.path, exc)
        return None
    logger.debug("demarrer_montage: {} -> {}", distant, local)
    return local


def _assurer_dossier_distant(
    hote: Host, distant: str, log, *, ssh: str | None, sshpass: str | None
) -> None:
    """``mkdir -p`` du dossier distant par ssh, juste avant le montage (GTK3)."""
    logger.debug("_assurer_dossier_distant: {!r}", distant)
    if distant in ("", ".", "~") or not ssh:
        logger.debug("_assurer_dossier_distant: sortie | rien à faire")
        return
    commande = [ssh, "-p", str(hote.port or PORT_DEFAUT), "-o", "ConnectTimeout=8"]
    cle = hote.protocol_options.get("ssh", {}).get(OPT_CLE, "").strip()
    if cle:
        commande += ["-i", os.path.expanduser(cle), "-o", "BatchMode=yes"]
    commande += [f"{hote.user or getpass.getuser()}@{hote.host}", "mkdir", "-p", distant]
    final = commande_auth(cle, hote.password, commande, sshpass)
    if final is None:
        log.warning("mkdir distant ignoré pour {} : aucune auth non interactive", hote.path)
        logger.debug("_assurer_dossier_distant: sortie | sans auth")
        return
    try:
        subprocess.run(final, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("échec du mkdir distant {} pour {} : {}", distant, hote.path, exc)
    logger.debug("_assurer_dossier_distant: fin")


def arreter_montage(local: str | None, log, *, fusermount: str | None = None) -> None:
    """Démonte (``fusermount -u``) le montage sshfs, comme GTK3 à la fin de session.

    Args:
        local: point de montage local, ou ``None`` (rien à démonter).
        log: journal du plugin (``warning``/``debug``).
        fusermount: chemin de ``fusermount3``/``fusermount`` ; ``None`` : ``PATH``.
    """
    logger.debug("arreter_montage: {!r}", local)
    if not local:
        logger.debug("arreter_montage: sortie | rien à démonter")
        return
    binaire = fusermount
    if binaire is None:
        binaire = shutil.which("fusermount3") or shutil.which("fusermount")
    if not binaire:
        log.warning("fusermount introuvable, démontage manuel requis pour {}", local)
        logger.debug("arreter_montage: sortie | fusermount absent")
        return
    try:
        subprocess.run([binaire, "-u", local], capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("échec du démontage de {} : {}", local, exc)
    logger.debug("arreter_montage: fin")
