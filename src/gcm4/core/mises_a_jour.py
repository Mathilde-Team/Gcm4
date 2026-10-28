# SPDX-License-Identifier: GPL-3.0-or-later
"""Vérification des mises à jour, sans GTK ni réseau dans les tests (#245).

Règle de #123 : **jamais d'accès réseau au démarrage sans accord**.

- ``[options] check-updates`` (GTK3, défaut ``True``) dit si l'on vérifie ;
- ``[window] check-updates-consent`` (nouvelle clé gcm4) garde l'accord
  explicite : ``True`` (accepté, ou option activée dans les préférences),
  ``False`` (refusé). Absente : l'accord n'a jamais été demandé, on demande
  une fois. ``[window]`` et non ``[options]`` : ``save_config`` réécrit
  ``[options]`` depuis le modèle, les clés d'état gcm4 vivent dans ``[window]``
  (comme ``theme-mode``) ;
- ``GCM_NO_UPDATE_CHECK=1`` désactive tout (paquets de distribution, tests).

Source : dernière version **publiée** du dépôt (API GitHub ``releases/latest``,
qui exclut brouillons et pré-versions). Toute erreur (hors ligne, délai, quota,
réponse inattendue) donne ``None`` et une ligne de journal, jamais d'exception.
"""

from __future__ import annotations

import json
import re
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from loguru import logger

from gcm4.core import window_state

URL_DERNIERE_VERSION = "https://api.github.com/repos/Mathilde-Team/Gcm4/releases/latest"
#: Page proposée si la réponse ne donne pas d'URL.
PAGE_VERSIONS = "https://github.com/Mathilde-Team/Gcm4/releases"
CLE_ACCORD = "check-updates-consent"
VARIABLE_DESACTIVATION = "GCM_NO_UPDATE_CHECK"
DELAI = 5.0  # secondes, comme GTK3

VERIFIER = "verifier"
DEMANDER = "demander"
NON = "non"

_VERSION = re.compile(
    r"^v?(?P<num>\d+(?:\.\d+)*)(?:[-._]?(?P<pre>a|alpha|b|beta|rc)[-._]?(?P<n>\d*))?$",
    re.IGNORECASE,
)
_RANG_PRE = {"a": 0, "alpha": 0, "b": 1, "beta": 1, "rc": 2}


@dataclass(frozen=True)
class MiseAJour:
    """Version publiée plus récente que la version installée."""

    version: str
    url: str


def cle_version(texte: str) -> tuple[tuple[int, ...], tuple[int, int]] | None:
    """``"v1.3.4-rc1"`` → clé comparable ; illisible (``0+inconnue``) → ``None``.

    Une pré-version précède la version finale : ``1.4rc1 < 1.4``.
    """
    logger.trace("cle_version: {!r}", texte)
    m = _VERSION.match(texte.strip())
    if m is None:
        logger.trace("cle_version: illisible")
        return None
    nombres = tuple(int(x) for x in m["num"].split("."))
    while len(nombres) > 1 and nombres[-1] == 0:  # 1.4.0 == 1.4
        nombres = nombres[:-1]
    pre = (_RANG_PRE[m["pre"].lower()], int(m["n"] or 0)) if m["pre"] else (3, 0)
    logger.trace("cle_version: {} {}", nombres, pre)
    return nombres, pre


def plus_recente(installee: str, publiee: str) -> bool:
    """``publiee`` est strictement plus récente ; version illisible → ``False``."""
    logger.debug("plus_recente: {!r} / {!r}", installee, publiee)
    a, b = cle_version(installee), cle_version(publiee)
    resultat = a is not None and b is not None and b > a
    logger.debug("plus_recente: {}", resultat)
    return resultat


def lire_reponse(texte: str) -> MiseAJour | None:
    """Réponse JSON de ``releases/latest`` → version et page ; invalide → ``None``."""
    logger.debug("lire_reponse: {} octet(s)", len(texte))
    try:
        donnees = json.loads(texte)
    except ValueError:
        logger.warning("lire_reponse: JSON invalide")
        return None
    if not isinstance(donnees, dict) or donnees.get("draft") or donnees.get("prerelease"):
        logger.debug("lire_reponse: pas une version publiée")
        return None
    version = donnees.get("tag_name")
    if not isinstance(version, str) or not version.strip():
        logger.debug("lire_reponse: tag_name absent")
        return None
    url = donnees.get("html_url")
    if not isinstance(url, str) or not url.startswith("https://"):
        url = PAGE_VERSIONS
    logger.debug("lire_reponse: {}", version)
    return MiseAJour(version.strip(), url)


def recuperer(url: str) -> str | None:
    """GET ``url`` (proxy de l'environnement respecté) ; échec → ``None``."""
    logger.debug("recuperer: {}", url)
    requete = urllib.request.Request(
        url,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "gcm4"},
    )
    try:
        with urllib.request.urlopen(requete, timeout=DELAI) as reponse:  # noqa: S310 (https fixe)
            texte = reponse.read(65536).decode("utf-8", "replace")
    except (OSError, ValueError) as exc:  # hors ligne, délai, 404, 403 (quota)…
        logger.info("recuperer: vérification impossible ({})", exc)
        return None
    logger.debug("recuperer: {} octet(s)", len(texte))
    return texte


def verifier(
    installee: str, recuperation: Callable[[str], str | None] = recuperer
) -> MiseAJour | None:
    """Version publiée plus récente que ``installee``, sinon ``None`` (jamais d'exception)."""
    logger.debug("verifier: installée {!r}", installee)
    texte = recuperation(URL_DERNIERE_VERSION)
    publiee = lire_reponse(texte) if texte is not None else None
    if publiee is None or not plus_recente(installee, publiee.version):
        logger.debug("verifier: rien de nouveau")
        return None
    logger.info("verifier: version {} disponible", publiee.version)
    return publiee


def lire_accord(config_file: str) -> bool | None:
    """Accord enregistré : ``True``, ``False``, ou ``None`` (jamais demandé ou illisible)."""
    logger.debug("lire_accord: {}", config_file)
    brut = window_state._lire(config_file).get(window_state.SECTION, CLE_ACCORD, fallback="")
    resultat = {"true": True, "false": False}.get(brut.strip().lower())
    logger.debug("lire_accord: {}", resultat)
    return resultat


def decision(check_updates: bool, accord: bool | None, environ: Mapping[str, str]) -> str:
    """:data:`VERIFIER`, :data:`DEMANDER` ou :data:`NON` au démarrage."""
    logger.debug("decision: option={} accord={}", check_updates, accord)
    if (
        environ.get(VARIABLE_DESACTIVATION, "").strip() not in ("", "0")
        or not check_updates
        or accord is False
    ):
        resultat = NON
    elif accord is True:
        resultat = VERIFIER
    else:
        resultat = DEMANDER
    logger.debug("decision: {}", resultat)
    return resultat
