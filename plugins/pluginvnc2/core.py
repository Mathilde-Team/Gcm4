# SPDX-License-Identifier: GPL-3.0-or-later
"""Options de l'adaptateur VNC v1, sans GTK ni client réseau (#179, #82).

Les clés historiques sont conservées. Le numéro d'affichage, s'il est
renseigné, prend priorité sur le port, comme l'ancien plugin GTK3.
``vnc_viewer`` reste conservé par le magasin mais ne choisit plus un
programme externe : la décision de #179 est la fusion avec pluginvnc2.
"""

from __future__ import annotations

import configparser
from collections.abc import Mapping
from dataclasses import dataclass

from loguru import logger

from gcm4.core.i18n import _
from gcm4.core.model import Host
from gcm4.core.validation import FieldError, erreur_de_port

PORT_DEFAUT = 5900
OPT_AFFICHAGE = "vnc_display"
OPT_LECTURE_SEULE = "vnc_view_only"
OPT_PLEIN_ECRAN = "vnc_fullscreen"
OPT_PRESSE_PAPIER = "vnc_share_clipboard"
OPT_PARTAGE = "vnc_shared"
AFFICHAGE_MAX = 65535 - PORT_DEFAUT


@dataclass(frozen=True)
class Reglages:
    """Réglages validés, sans identifiant ni secret à journaliser."""

    port: int
    lecture_seule: bool
    plein_ecran: bool
    presse_papier: bool
    partage: bool


def options_defaut() -> dict[str, str]:
    """Options d'un nouvel hôte : partagé et presse-papier, comme le widget existant.

    Returns:
        Une nouvelle copie des options INI, sans état partagé.
    """
    logger.debug("options_defaut: entrée")
    resultat = {
        OPT_AFFICHAGE: "",
        OPT_LECTURE_SEULE: "False",
        OPT_PLEIN_ECRAN: "False",
        OPT_PRESSE_PAPIER: "True",
        OPT_PARTAGE: "True",
    }
    logger.debug("options_defaut: sortie")
    return resultat


def booleen(options: Mapping[str, str], cle: str, defaut: bool) -> bool:
    """Relit les booléens INI ; une valeur absente ou inconnue garde le défaut.

    Args:
        options: Options INI du protocole.
        cle: Clé du réglage, jamais une donnée d'identification.
        defaut: Valeur utilisée en l'absence d'une forme booléenne connue.

    Returns:
        La valeur booléenne décodée.
    """
    logger.debug("booleen: entrée, clé={}", cle)
    brut = options.get(cle, "").strip().lower()
    resultat = configparser.RawConfigParser.BOOLEAN_STATES.get(brut, defaut)
    logger.debug("booleen: sortie {}", resultat)
    return resultat


def valider(options: Mapping[str, str]) -> list[FieldError]:
    """Refuse un affichage non ASCII ou dont le port calculé sortirait de 1–65535.

    Args:
        options: Options de l'hôte, inchangées par la validation.

    Returns:
        Les erreurs de formulaire ; liste vide si l'affichage est valide ou absent.
    """
    logger.debug("valider: entrée")
    affichage = options.get(OPT_AFFICHAGE, "").strip()
    chiffres = affichage.lstrip("0") or "0"
    erreurs = []
    if affichage and (
        not affichage.isascii() or not affichage.isdecimal() or len(chiffres) > 5 or int(chiffres) > AFFICHAGE_MAX
    ):
        erreurs.append(FieldError(OPT_AFFICHAGE, _("VNC display number must be between 0 and 59635.")))
    logger.debug("valider: sortie, {} erreur(s)", len(erreurs))
    return erreurs


def reglages(host: Host) -> Reglages:
    """Valide les options puis calcule le port et les modes de la connexion.

    Args:
        host: Hôte du magasin ; les secrets ne sont ni copiés ni journalisés.

    Raises:
        ValueError: Affichage ou port hors bornes.

    Returns:
        Les options utilisables par l'adaptateur GTK4.
    """
    logger.debug("reglages: entrée")
    options = host.protocol_options.get("vnc", {})
    erreurs = valider(options)
    affichage = options.get(OPT_AFFICHAGE, "").strip()
    port = PORT_DEFAUT + int(affichage.lstrip("0") or "0") if affichage and not erreurs else host.port
    if port is None:
        port = PORT_DEFAUT
    erreur_port = erreur_de_port(str(port))
    if erreur_port is not None:
        erreurs.append(erreur_port)
    if erreurs:
        logger.debug("reglages: sortie, options refusées")
        raise ValueError(erreurs[0].message)
    lecture_seule = booleen(options, OPT_LECTURE_SEULE, False)
    resultat = Reglages(
        port=port,
        lecture_seule=lecture_seule,
        plein_ecran=booleen(options, OPT_PLEIN_ECRAN, False),
        presse_papier=booleen(options, OPT_PRESSE_PAPIER, True) and not lecture_seule,
        partage=booleen(options, OPT_PARTAGE, True),
    )
    logger.debug("reglages: sortie, port={} lecture_seule={}", port, lecture_seule)
    return resultat
