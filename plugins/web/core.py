# SPDX-License-Identifier: GPL-3.0-or-later
"""Partie métier du plugin web (sans GTK) — portage #179, #397.

Mêmes clés d'options que ``plugins/plugin_web.py`` (GTK3), pour relire les
configurations existantes sans ressaisie : ``web_url`` (adresse de la
console) et ``web_ignore_cert`` (accepter un certificat TLS non vérifié,
vrai par défaut comme en GTK3 : les cartes de gestion ont presque toutes un
certificat auto-signé). Les booléens sont relus comme ``configparser``
(``True``, ``yes``, ``1``, ``on``…) et écrits ``True`` / ``False`` comme GTK3.

Écart avec GTK3, validé dans #397 : seule une adresse ``http`` ou ``https``
est acceptée (une adresse ``file:`` ou ``javascript:`` n'a rien à faire dans
une fiche d'hôte) ; une adresse sans schéma (« ilo.example.org ») est
complétée en ``https://``, comme le fait un navigateur.
"""

from __future__ import annotations

import configparser
import re
from collections.abc import Mapping
from urllib.parse import urlsplit

from loguru import logger

from gcm4.core.i18n import _
from gcm4.core.validation import FieldError

#: Clés INI, identiques à GTK3 (``WebPlugin.host_fields``).
OPT_URL = "web_url"
OPT_IGNORER_CERTIFICAT = "web_ignore_cert"
PORT_DEFAUT = 443
SCHEMAS = ("http", "https")
#: Schéma explicite sans « // » (``javascript:``, ``file:``…) ; « hôte:port » n'en est pas un.
_SCHEMA_SEUL = re.compile(r"^[a-z][a-z0-9+.-]*:(?!\d)", re.IGNORECASE)


def options_defaut() -> dict[str, str]:
    """Options d'un nouvel hôte web : URL vide, certificats non vérifiés acceptés (GTK3)."""
    logger.debug("options_defaut: entrée")
    options = {OPT_URL: "", OPT_IGNORER_CERTIFICAT: ecrire_booleen(True)}
    logger.debug("options_defaut: fait")
    return options


def ecrire_booleen(valeur: bool) -> str:
    """Booléen écrit comme GTK3 (``str(bool)``) : ``True`` ou ``False``."""
    logger.debug("ecrire_booleen: {}", valeur)
    texte = "True" if valeur else "False"
    logger.debug("ecrire_booleen: {!r}", texte)
    return texte


def ignorer_certificat(options: Mapping[str, str]) -> bool:
    """Option ``web_ignore_cert`` ; absente ou illisible : vrai, comme GTK3."""
    logger.debug("ignorer_certificat: entrée")
    brut = options.get(OPT_IGNORER_CERTIFICAT, "").strip().lower()
    resultat = configparser.RawConfigParser.BOOLEAN_STATES.get(brut, True)
    logger.debug("ignorer_certificat: {}", resultat)
    return resultat


def url(options: Mapping[str, str]) -> str:
    """URL à ouvrir : ``web_url`` sans espaces, ``https://`` ajouté s'il manque un schéma.

    Returns:
        Chaîne vide si l'option est absente ou vide.
    """
    logger.debug("url: entrée")
    brut = options.get(OPT_URL, "").strip()
    if brut and "://" not in brut and not _SCHEMA_SEUL.match(brut):
        brut = f"https://{brut}"
    logger.debug("url: {!r}", brut)
    return brut


def decouper(adresse: str) -> tuple[str, str]:
    """Schéma (minuscules) et nom d'hôte d'une URL ; ``("", "")`` si illisible."""
    logger.debug("decouper: {!r}", adresse)
    try:
        morceaux = urlsplit(adresse)
        resultat = (morceaux.scheme.lower(), morceaux.hostname or "")
    except ValueError:
        logger.debug("decouper: URL illisible")
        return ("", "")
    logger.debug("decouper: {}", resultat)
    return resultat


def valider(options: Mapping[str, str]) -> list[FieldError]:
    """Erreurs sur ``web_url`` : obligatoire, schéma ``http``/``https``, nom d'hôte présent."""
    logger.debug("valider: entrée")
    adresse = url(options)
    erreurs: list[FieldError] = []
    if not adresse:
        erreurs.append(FieldError(champ=OPT_URL, message=_("URL is required.")))
    elif (morceaux := decouper(adresse))[0] not in SCHEMAS or not morceaux[1]:
        erreurs.append(
            FieldError(champ=OPT_URL, message=_("The URL must start with http:// or https://"))
        )
    logger.debug("valider: {} erreur(s)", len(erreurs))
    return erreurs
