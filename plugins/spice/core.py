# SPDX-License-Identifier: GPL-3.0-or-later
"""Partie métier du plugin spice (sans GTK) — portage #179.

Voie retenue par l'auteur (08/11/2026) : la session SPICE s'ouvre dans la
visionneuse externe ``remote-viewer`` (paquet ``virt-viewer``), pas dans un
affichage intégré : spice-gtk n'existe pas en GTK4.

Mêmes clés d'options que ``plugins/plugin_spice.py`` (GTK3), pour relire les
configurations existantes sans ressaisie :

- ``spice_mode`` : ``uri`` (défaut, adresse et port de l'hôte), ``libvirt``
  (``virt-viewer --connect <URI> <VM>``) ou ``proxmox`` (ticket SPICE
  demandé par ``pvesh`` sur le nœud, à travers ssh) ;
- ``spice_tls_port``, ``spice_ca_cert`` (mode ``uri``) ;
- ``spice_libvirt_uri``, ``spice_vm_name`` (mode ``libvirt``) ;
- ``spice_px_node``, ``spice_px_vmid`` (mode ``proxmox``) ;
- ``spice_share_usb``, ``spice_share_audio``, ``spice_share_folder``,
  ``spice_shared_folder_path`` : partages, traduits en options
  ``--spice-*`` de la visionneuse ; ``spice_share_clipboard`` est conservé
  tel quel (``remote-viewer`` partage toujours le presse-papier par l'agent
  SPICE, il n'a pas d'option pour le couper).

Écarts avec GTK3 :

- le mot de passe ne passe plus dans l'URI ``spice://…?password=`` (visible
  dans la liste des processus) mais dans un fichier ``.vv`` privé (droits
  ``0600``) que la visionneuse efface après lecture (``delete-this-file``) ;
- mode Proxmox : le ticket est demandé avec ``--proxy <adresse de l'hôte>``
  (paramètre de l'API ``spiceproxy``) ; GTK3 réécrivait ``/etc/hosts`` par
  ``sudo`` pour résoudre le nom du nœud. Le certificat du ticket (``ca``,
  sur plusieurs lignes) est écrit sur une ligne, comme le fait Proxmox ;
- seules les options de partage connues sont transmises ; les paramètres
  supplémentaires de l'hôte sont ajoutés tels quels (``shlex``), comme GTK3.
"""

from __future__ import annotations

import configparser
import json
import os
import re
import shlex
import shutil
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path

from loguru import logger

from gcm4.core.i18n import _
from gcm4.core.model import Host
from gcm4.core.validation import FieldError

#: Clés INI, identiques à GTK3 (``SpicePlugin.host_fields``).
OPT_MODE = "spice_mode"
OPT_PORT_TLS = "spice_tls_port"
OPT_CA = "spice_ca_cert"
OPT_URI_LIBVIRT = "spice_libvirt_uri"
OPT_VM = "spice_vm_name"
OPT_NOEUD = "spice_px_node"
OPT_VMID = "spice_px_vmid"
OPT_PRESSE_PAPIER = "spice_share_clipboard"
OPT_USB = "spice_share_usb"
OPT_AUDIO = "spice_share_audio"
OPT_DOSSIER = "spice_share_folder"
OPT_CHEMIN_DOSSIER = "spice_shared_folder_path"

MODE_URI = "uri"
MODE_LIBVIRT = "libvirt"
MODE_PROXMOX = "proxmox"
MODES = (MODE_URI, MODE_LIBVIRT, MODE_PROXMOX)

PORT_DEFAUT = 5930
#: Ports SPICE/VNC qui ne peuvent pas être celui de ssh (règle GTK3 du mode Proxmox).
PORTS_AFFICHAGE = (5900, 5930)
PORT_SSH = 22

#: Visionneuse de chaque mode : ``remote-viewer`` lit les URI et les ``.vv`` ;
#: ``virt-viewer`` (même paquet) seul sait se connecter à libvirt.
VISIONNEUSES = {
    MODE_URI: "remote-viewer",
    MODE_LIBVIRT: "virt-viewer",
    MODE_PROXMOX: "remote-viewer",
}

_NOEUD = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]*$")


class ErreurTicket(ValueError):
    """Réponse de ``pvesh`` illisible ou incomplète (message traduit)."""


def options_defaut() -> dict[str, str]:
    """Options d'un nouvel hôte SPICE : mode URI, presse-papier partagé, rien d'autre (GTK3)."""
    logger.debug("options_defaut: entrée")
    options = {
        OPT_MODE: MODE_URI,
        OPT_PORT_TLS: "",
        OPT_CA: "",
        OPT_URI_LIBVIRT: "",
        OPT_VM: "",
        OPT_NOEUD: "",
        OPT_VMID: "",
        OPT_PRESSE_PAPIER: ecrire_booleen(True),
        OPT_USB: ecrire_booleen(False),
        OPT_AUDIO: ecrire_booleen(False),
        OPT_DOSSIER: ecrire_booleen(False),
        OPT_CHEMIN_DOSSIER: "",
    }
    logger.debug("options_defaut: fait")
    return options


def ecrire_booleen(valeur: bool) -> str:
    """Booléen écrit comme GTK3 (``str(bool)``) : ``True`` ou ``False``."""
    logger.debug("ecrire_booleen: {}", valeur)
    texte = "True" if valeur else "False"
    logger.debug("ecrire_booleen: {!r}", texte)
    return texte


def lire_booleen(options: Mapping[str, str], cle: str, defaut: bool) -> bool:
    """Booléen relu comme ``configparser`` ; absent ou illisible : ``defaut``."""
    logger.debug("lire_booleen: {!r}", cle)
    brut = options.get(cle, "").strip().lower()
    resultat = configparser.RawConfigParser.BOOLEAN_STATES.get(brut, defaut)
    logger.debug("lire_booleen: {}", resultat)
    return resultat


def texte(options: Mapping[str, str], cle: str) -> str:
    """Option texte sans espaces autour ; chaîne vide si absente."""
    logger.trace("texte: {!r}", cle)
    valeur = options.get(cle, "").strip()
    logger.trace("texte: fait")
    return valeur


def mode(options: Mapping[str, str]) -> str:
    """Mode de connexion ; absent ou inconnu : ``uri``, comme GTK3."""
    logger.debug("mode: entrée")
    valeur = texte(options, OPT_MODE).lower()
    resultat = valeur if valeur in MODES else MODE_URI
    logger.debug("mode: {!r}", resultat)
    return resultat


def _port_valide(valeur: str) -> bool:
    """Vrai pour un entier de 1 à 65535."""
    logger.trace("_port_valide: {!r}", valeur)
    resultat = valeur.isdigit() and 0 < int(valeur) < 65536
    logger.trace("_port_valide: {}", resultat)
    return resultat


def valider(options: Mapping[str, str]) -> list[FieldError]:
    """Erreurs des options du mode choisi (champs obligatoires et formats)."""
    logger.debug("valider: entrée")
    erreurs: list[FieldError] = []
    choisi = mode(options)
    if choisi == MODE_URI:
        port_tls = texte(options, OPT_PORT_TLS)
        if port_tls and not _port_valide(port_tls):
            erreurs.append(
                FieldError(OPT_PORT_TLS, _("The TLS port must be a number from 1 to 65535."))
            )
    elif choisi == MODE_LIBVIRT:
        if not texte(options, OPT_URI_LIBVIRT):
            erreurs.append(FieldError(OPT_URI_LIBVIRT, _("Error: libvirt URI required")))
    else:
        if not _NOEUD.match(texte(options, OPT_NOEUD)):
            erreurs.append(FieldError(OPT_NOEUD, _("Error: Proxmox node and VMID required")))
        if not texte(options, OPT_VMID).isdigit():
            erreurs.append(FieldError(OPT_VMID, _("Error: Proxmox node and VMID required")))
    if lire_booleen(options, OPT_DOSSIER, False) and not texte(options, OPT_CHEMIN_DOSSIER):
        erreurs.append(FieldError(OPT_CHEMIN_DOSSIER, _("Choose the folder to share.")))
    logger.debug("valider: {} erreur(s)", len(erreurs))
    return erreurs


def trouver_visionneuse(
    choisi: str, which: Callable[[str], str | None] = shutil.which
) -> str | None:
    """Chemin de la visionneuse du mode (``remote-viewer`` ou ``virt-viewer``), ou ``None``."""
    logger.debug("trouver_visionneuse: {!r}", choisi)
    chemin = which(VISIONNEUSES[choisi])
    logger.debug("trouver_visionneuse: {!r}", chemin)
    return chemin


def options_partage(options: Mapping[str, str]) -> list[str]:
    """Options ``--spice-*`` des partages : USB et audio coupés sauf demande, dossier partagé."""
    logger.debug("options_partage: entrée")
    arguments: list[str] = []
    if not lire_booleen(options, OPT_USB, False):
        arguments.append("--spice-disable-usbredir")
    if not lire_booleen(options, OPT_AUDIO, False):
        arguments.append("--spice-disable-audio")
    dossier = texte(options, OPT_CHEMIN_DOSSIER)
    if lire_booleen(options, OPT_DOSSIER, False) and dossier:
        arguments.append(f"--spice-shared-dir={os.path.expanduser(dossier)}")
    logger.debug("options_partage: {} option(s)", len(arguments))
    return arguments


def parametres_supplementaires(host: Host) -> list[str]:
    """Paramètres supplémentaires de l'hôte (``extra_params``), découpés comme un shell."""
    logger.debug("parametres_supplementaires: entrée")
    try:
        arguments = shlex.split(host.extra_params or "")
    except ValueError as exc:
        logger.debug("parametres_supplementaires: illisibles ({})", exc)
        raise ValueError(_("Invalid extra parameters: {error}").format(error=exc)) from exc
    logger.debug("parametres_supplementaires: {} argument(s)", len(arguments))
    return arguments


def titre(host: Host) -> str:
    """Titre de la fenêtre de la visionneuse : nom de l'hôte."""
    logger.trace("titre: entrée")
    resultat = host.name or host.host
    logger.trace("titre: fait")
    return resultat


def champs_vv_direct(host: Host, options: Mapping[str, str]) -> dict[str, str]:
    """Section ``[virt-viewer]`` du mode URI : adresse, ports, mot de passe éventuel."""
    logger.debug("champs_vv_direct: {!r}", host.path)
    champs = {
        "type": "spice",
        "host": host.host,
        "port": str(host.port or PORT_DEFAUT),
        "title": titre(host),
        "delete-this-file": "1",
    }
    port_tls = texte(options, OPT_PORT_TLS)
    if port_tls:
        champs["tls-port"] = port_tls
    if host.password:
        champs["password"] = host.password
    logger.debug("champs_vv_direct: {} champ(s)", len(champs))
    return champs


def contenu_vv(champs: Mapping[str, str]) -> str:
    """Fichier ``.vv`` : section ``[virt-viewer]``, valeurs échappées comme le veut GKeyFile."""
    logger.debug("contenu_vv: {} champ(s)", len(champs))
    lignes = ["[virt-viewer]"]
    for cle, valeur in champs.items():
        # Échappements de GKeyFile, qui lit le .vv : « \\ », « \n », « \s » en tête.
        propre = str(valeur).replace("\\", "\\\\").replace("\r", "").replace("\n", "\\n")
        if propre.startswith(" "):
            propre = "\\s" + propre[1:]
        lignes.append(f"{cle}={propre}")
    resultat = "\n".join(lignes) + "\n"
    logger.debug("contenu_vv: fait")
    return resultat


def ecrire_vv(champs: Mapping[str, str], dossier: str | None = None) -> Path:
    """Écrit le ``.vv`` dans un fichier privé (``0600``) ; rend son chemin.

    Args:
        champs: section ``[virt-viewer]`` (mot de passe compris, jamais journalisé).
        dossier: dossier du fichier ; défaut ``$XDG_RUNTIME_DIR`` s'il existe,
            sinon le dossier temporaire du système.
    """
    logger.debug("ecrire_vv: entrée")
    if dossier is None:
        runtime = os.environ.get("XDG_RUNTIME_DIR", "")
        dossier = runtime if runtime and os.path.isdir(runtime) else None
    descripteur, chemin = tempfile.mkstemp(prefix="gcm4-spice-", suffix=".vv", dir=dossier)
    with os.fdopen(descripteur, "w", encoding="utf-8") as fichier:
        fichier.write(contenu_vv(champs))
    logger.debug("ecrire_vv: {} champ(s) écrits", len(champs))
    return Path(chemin)


def commande_vv(
    binaire: str, chemin_vv: Path, host: Host, options: Mapping[str, str], *, ca: bool = True
) -> list[str]:
    """``remote-viewer`` sur un ``.vv`` (modes URI et Proxmox).

    Args:
        binaire: chemin de ``remote-viewer``.
        chemin_vv: fichier ``.vv`` écrit par :func:`ecrire_vv`.
        host: hôte ouvert (paramètres supplémentaires).
        options: options ``spice`` (partages, certificat).
        ca: ajoute ``--spice-ca-file`` si ``spice_ca_cert`` est rempli (mode URI ;
            le ticket Proxmox porte son propre certificat).
    """
    logger.debug("commande_vv: {!r}", host.path)
    argv = [binaire, *options_partage(options)]
    fichier_ca = texte(options, OPT_CA)
    if ca and fichier_ca:
        argv.append(f"--spice-ca-file={os.path.expanduser(fichier_ca)}")
    argv += parametres_supplementaires(host)
    argv.append(str(chemin_vv))
    logger.debug("commande_vv: {} argument(s)", len(argv))
    return argv


def commande_libvirt(binaire: str, host: Host, options: Mapping[str, str]) -> list[str]:
    """``virt-viewer --connect <URI> [VM]`` (mode libvirt, comme GTK3)."""
    logger.debug("commande_libvirt: {!r}", host.path)
    argv = [
        binaire,
        "--connect",
        texte(options, OPT_URI_LIBVIRT),
        "--title",
        titre(host),
        *options_partage(options),
        *parametres_supplementaires(host),
    ]
    machine = texte(options, OPT_VM)
    if machine:
        argv.append(machine)
    logger.debug("commande_libvirt: {} argument(s)", len(argv))
    return argv


def port_ssh(host: Host) -> int:
    """Port ssh du nœud Proxmox : celui de l'hôte, sauf vide ou port d'affichage (GTK3)."""
    logger.debug("port_ssh: entrée")
    port = host.port if host.port and host.port not in PORTS_AFFICHAGE else PORT_SSH
    logger.debug("port_ssh: {}", port)
    return port


def commande_ticket(host: Host, options: Mapping[str, str]) -> list[str]:
    """``ssh`` qui demande un ticket SPICE à ``pvesh`` sur le nœud Proxmox.

    La commande distante est citée argument par argument (``shlex.quote``) ;
    ``--proxy`` désigne l'adresse de l'hôte, joignable par la visionneuse.
    ``BatchMode`` : clé ou agent ssh, jamais de question dans un terminal absent.
    """
    logger.debug("commande_ticket: {!r}", host.path)
    distante = [
        "pvesh",
        "create",
        f"/nodes/{texte(options, OPT_NOEUD)}/qemu/{texte(options, OPT_VMID)}/spiceproxy",
        "--proxy",
        host.host,
        "--output-format",
        "json",
    ]
    argv = [
        "ssh",
        "-p",
        str(port_ssh(host)),
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=10",
        "-l",
        host.user or "root",
        host.host,
        " ".join(shlex.quote(morceau) for morceau in distante),
    ]
    logger.debug("commande_ticket: fait")
    return argv


def lire_ticket(sortie: str) -> dict[str, str]:
    """Ticket ``spiceproxy`` (JSON) en champs ``.vv`` ; effacement après lecture forcé.

    Raises:
        ErreurTicket: sortie non JSON, ou sans ``host``/``password``.
    """
    logger.debug("lire_ticket: {} caractère(s)", len(sortie))
    try:
        ticket = json.loads(sortie)
    except json.JSONDecodeError as exc:
        logger.debug("lire_ticket: JSON illisible")
        raise ErreurTicket(_("SPICE ticket error: {out}").format(out=sortie[:80])) from exc
    if not isinstance(ticket, dict) or not ticket.get("host") or not ticket.get("password"):
        logger.debug("lire_ticket: ticket incomplet")
        raise ErreurTicket(_("SPICE ticket error: {out}").format(out=_("incomplete ticket")))
    champs = {str(cle): str(valeur) for cle, valeur in ticket.items() if valeur is not None}
    champs.setdefault("type", "spice")
    champs["delete-this-file"] = "1"
    logger.debug("lire_ticket: {} champ(s)", len(champs))
    return champs
