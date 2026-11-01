# SPDX-License-Identifier: GPL-3.0-or-later
"""Partie métier du plugin rdp (sans GTK) — portage #105.

Reprend les options de ``plugins/plugin_rdp.py`` (GTK3, gtk-frdp), sous les
mêmes clés INI (``host.protocol_options["rdp"]``), pour que les
configurations existantes s'ouvrent sans conversion :

- ``rdp_domain`` : domaine Windows ;
- ``rdp_geometry`` : ``fullscreen`` (taille de l'onglet), ``1920x1080``,
  ``1280x1024``, ``1024x768``, ``800x600`` ou ``custom`` ;
- ``rdp_width`` / ``rdp_height`` : taille quand ``rdp_geometry=custom`` ;
- ``rdp_cert_ignore`` : accepter un certificat non vérifié ;
- ``rdp_dyn_res`` : résolution qui suit la taille de l'onglet ;
- ``rdp_remote_app`` : programme RemoteApp (``||notepad``) ;
- ``rdp_share_clipboard`` : presse-papier partagé (vrai par défaut) ;
- ``rdp_share_folder`` / ``rdp_shared_folder_path`` : dossier local
  redirigé (canal RDPDR « drive »).

Les booléens s'écrivent ``True``/``False`` comme GTK3 ; à la lecture,
``true``, ``1`` et ``yes`` (sans casse) valent vrai (``_bool_from_str``
GTK3). Ce module n'importe ni GTK ni asyncrdp : :func:`reglages` rend un
dictionnaire de champs de ``asyncrdp.RdpOptions``, que :mod:`gtk4`
convertit.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from gcm4.core.i18n import _
from gcm4.core.model import Host
from gcm4.core.validation import FieldError

PORT_DEFAUT = 3389

OPT_DOMAINE = "rdp_domain"
OPT_GEOMETRIE = "rdp_geometry"
OPT_LARGEUR = "rdp_width"
OPT_HAUTEUR = "rdp_height"
OPT_CERTIFICAT = "rdp_cert_ignore"
OPT_RESOLUTION_DYNAMIQUE = "rdp_dyn_res"
OPT_REMOTE_APP = "rdp_remote_app"
OPT_PRESSE_PAPIER = "rdp_share_clipboard"
OPT_PARTAGE_DOSSIER = "rdp_share_folder"
OPT_CHEMIN_DOSSIER = "rdp_shared_folder_path"

#: Géométries du dialogue GTK3 (``_GEOMETRY_ITEMS``), dans le même ordre.
GEOMETRIE_PLEIN_ECRAN = "fullscreen"
GEOMETRIE_PERSONNALISEE = "custom"
GEOMETRIES = (
    GEOMETRIE_PLEIN_ECRAN,
    "1920x1080",
    "1280x1024",
    "1024x768",
    "800x600",
    GEOMETRIE_PERSONNALISEE,
)

#: Taille demandée à la connexion quand elle suit l'onglet (``fullscreen``) :
#: la vue RDP la corrige dès la première allocation (canal Display Control).
TAILLE_PLEIN_ECRAN = (1920, 1080)

#: Bornes FreeRDP d'un bureau (``DesktopWidth``/``DesktopHeight``).
TAILLE_MIN, TAILLE_MAX = 200, 8192

#: Nom du dossier redirigé vu côté serveur (``\\tsclient\gcm4``).
NOM_DOSSIER_PARTAGE = "gcm4"

VRAI = ("true", "1", "yes")


def booleen(valeur: object) -> bool:
    """Valeur INI → booléen, comme ``_bool_from_str`` GTK3."""
    logger.debug("booleen: entrée")
    resultat = str(valeur).strip().lower() in VRAI
    logger.debug("booleen: {}", resultat)
    return resultat


def texte_booleen(valeur: bool) -> str:
    """Booléen → valeur INI (``True``/``False``, écrite ainsi par GTK3)."""
    logger.debug("texte_booleen: entrée")
    resultat = "True" if valeur else "False"
    logger.debug("texte_booleen: {}", resultat)
    return resultat


def options_defaut() -> dict[str, str]:
    """Options d'un nouvel hôte RDP (valeurs par défaut de ``host_fields`` GTK3)."""
    logger.debug("options_defaut: entrée")
    resultat = {
        OPT_DOMAINE: "",
        OPT_GEOMETRIE: GEOMETRIE_PLEIN_ECRAN,
        OPT_LARGEUR: "",
        OPT_HAUTEUR: "",
        OPT_CERTIFICAT: "False",
        OPT_RESOLUTION_DYNAMIQUE: "False",
        OPT_REMOTE_APP: "",
        OPT_PRESSE_PAPIER: "True",
        OPT_PARTAGE_DOSSIER: "False",
        OPT_CHEMIN_DOSSIER: "",
    }
    logger.debug("options_defaut: {} clé(s)", len(resultat))
    return resultat


def port(host: Host) -> int:
    """Port TCP du serveur (3389 par défaut)."""
    logger.debug("port: entrée")
    resultat = host.port or PORT_DEFAUT
    logger.debug("port: {}", resultat)
    return resultat


def _entier(texte: str) -> int | None:
    """Texte → entier, ``None`` si ce n'en est pas un."""
    logger.debug("_entier: entrée")
    try:
        resultat: int | None = int(texte.strip())
    except ValueError:
        logger.debug("_entier: {!r} n'est pas un entier", texte)
        resultat = None
    logger.debug("_entier: {}", resultat)
    return resultat


def _taille_valide(texte: str) -> bool:
    """Largeur ou hauteur acceptée par FreeRDP."""
    logger.debug("_taille_valide: entrée")
    valeur = _entier(texte)
    resultat = valeur is not None and TAILLE_MIN <= valeur <= TAILLE_MAX
    logger.debug("_taille_valide: {}", resultat)
    return resultat


def valider(options: Mapping[str, str]) -> list[FieldError]:
    """Erreurs des options (``FieldError.champ`` = clé INI), vide si tout va bien.

    - géométrie inconnue ;
    - ``custom`` sans largeur ou hauteur entière entre 200 et 8192 ;
    - dossier partagé coché sans chemin, chemin inexistant, ou image ISO
      (montée par ``udisksctl`` en GTK3, pas encore reprise).
    """
    logger.debug("valider: entrée")
    erreurs: list[FieldError] = []
    geometrie = options.get(OPT_GEOMETRIE, GEOMETRIE_PLEIN_ECRAN) or GEOMETRIE_PLEIN_ECRAN
    if geometrie not in GEOMETRIES:
        erreurs.append(FieldError(OPT_GEOMETRIE, _("Unknown geometry")))
    elif geometrie == GEOMETRIE_PERSONNALISEE:
        message = _("Enter a size between {min} and {max} pixels").format(
            min=TAILLE_MIN, max=TAILLE_MAX
        )
        for cle in (OPT_LARGEUR, OPT_HAUTEUR):
            if not _taille_valide(options.get(cle, "")):
                erreurs.append(FieldError(cle, message))
    if booleen(options.get(OPT_PARTAGE_DOSSIER, "False")):
        chemin = options.get(OPT_CHEMIN_DOSSIER, "").strip()
        if not chemin:
            erreurs.append(FieldError(OPT_CHEMIN_DOSSIER, _("Choose the folder to share")))
        elif chemin.lower().endswith(".iso"):
            erreurs.append(
                FieldError(OPT_CHEMIN_DOSSIER, _("Sharing an ISO image is not supported yet"))
            )
        elif not Path(chemin).expanduser().is_dir():
            erreurs.append(FieldError(OPT_CHEMIN_DOSSIER, _("This folder does not exist")))
    logger.debug("valider: {} erreur(s)", len(erreurs))
    return erreurs


def taille(options: Mapping[str, str]) -> tuple[int, int]:
    """Taille du bureau demandée à la connexion, selon ``rdp_geometry``.

    Raises:
        ValueError: options invalides (:func:`valider`).
    """
    logger.debug("taille: entrée")
    geometrie = options.get(OPT_GEOMETRIE, GEOMETRIE_PLEIN_ECRAN) or GEOMETRIE_PLEIN_ECRAN
    if geometrie == GEOMETRIE_PLEIN_ECRAN:
        resultat = TAILLE_PLEIN_ECRAN
    elif geometrie == GEOMETRIE_PERSONNALISEE:
        largeur = _entier(options.get(OPT_LARGEUR, ""))
        hauteur = _entier(options.get(OPT_HAUTEUR, ""))
        if largeur is None or hauteur is None:
            logger.debug("taille: taille personnalisée invalide")
            raise ValueError(_("Invalid custom size"))
        resultat = (largeur, hauteur)
    elif geometrie in GEOMETRIES:
        largeur_texte, hauteur_texte = geometrie.split("x")
        resultat = (int(largeur_texte), int(hauteur_texte))
    else:
        logger.debug("taille: géométrie inconnue")
        raise ValueError(_("Unknown geometry"))
    logger.debug("taille: {}x{}", resultat[0], resultat[1])
    return resultat


def suit_onglet(options: Mapping[str, str]) -> bool:
    """La résolution suit-elle la taille de l'onglet ?

    Oui en ``fullscreen`` (la session occupe l'onglet) ou si
    ``rdp_dyn_res`` est coché ; sinon la taille choisie est gardée et
    l'image est mise à l'échelle dans l'onglet.
    """
    logger.debug("suit_onglet: entrée")
    geometrie = options.get(OPT_GEOMETRIE, GEOMETRIE_PLEIN_ECRAN) or GEOMETRIE_PLEIN_ECRAN
    resultat = geometrie == GEOMETRIE_PLEIN_ECRAN or booleen(
        options.get(OPT_RESOLUTION_DYNAMIQUE, "False")
    )
    logger.debug("suit_onglet: {}", resultat)
    return resultat


@dataclass(frozen=True)
class Reglages:
    """Paramètres de connexion, sans asyncrdp ; le mot de passe n'est pas dans ``repr`` (#145)."""

    hote: str
    port: int
    utilisateur: str
    domaine: str | None
    options: dict[str, object]
    dossier: tuple[str, str] | None
    remote_app: str | None
    suit_onglet: bool


def reglages(host: Host) -> Reglages:
    """Réglages de connexion de ``host`` (options ``rdp`` de l'hôte).

    ``options`` contient les champs de ``asyncrdp.RdpOptions`` à fixer
    (``width``, ``height``, ``ignore_certificate``, ``redirect_clipboard``,
    ``redirect_drives``) ; ``dossier`` est le couple (nom côté serveur,
    chemin local) du lecteur redirigé.

    Raises:
        ValueError: options invalides (:func:`valider`), message traduit.
    """
    logger.debug("reglages: {!r}", host.path)
    options = {**options_defaut(), **host.protocol_options.get("rdp", {})}
    erreurs = valider(options)
    if erreurs:
        logger.debug("reglages: {} erreur(s)", len(erreurs))
        raise ValueError(erreurs[0].message)
    largeur, hauteur = taille(options)
    dossier = None
    if booleen(options[OPT_PARTAGE_DOSSIER]):
        chemin = str(Path(options[OPT_CHEMIN_DOSSIER].strip()).expanduser())
        dossier = (NOM_DOSSIER_PARTAGE, chemin)
    champs: dict[str, object] = {
        "width": largeur,
        "height": hauteur,
        "ignore_certificate": booleen(options[OPT_CERTIFICAT]),
        "redirect_clipboard": booleen(options[OPT_PRESSE_PAPIER]),
        "redirect_drives": False,
    }
    resultat = Reglages(
        hote=host.host,
        port=port(host),
        utilisateur=host.user,
        domaine=options[OPT_DOMAINE].strip() or None,
        options=champs,
        dossier=dossier,
        remote_app=options[OPT_REMOTE_APP].strip() or None,
        suit_onglet=suit_onglet(options),
    )
    logger.debug(
        "reglages: {}:{} {}x{}, dossier {}, RemoteApp {}",
        resultat.hote,
        resultat.port,
        largeur,
        hauteur,
        "oui" if dossier else "non",
        "oui" if resultat.remote_app else "non",
    )
    return resultat
