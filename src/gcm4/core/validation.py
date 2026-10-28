# SPDX-License-Identifier: GPL-3.0-or-later
"""Validation d'un hôte avant enregistrement, sans GTK (#144).

Seuls les champs communs sont vérifiés (inventaire dans
``docs/architecture.md``, « Champs du dialogue d'hôte ») : les options de
protocole appartiennent au plugin (#146) et sont conservées telles quelles.

Le dialogue d'hôte (#145) affiche chaque :class:`FieldError` sur son champ
(classe CSS ``error``) et garde « Enregistrer » inactif tant que la liste
n'est pas vide.
"""

from __future__ import annotations

from dataclasses import dataclass

from loguru import logger

from gcm4.core.i18n import _
from gcm4.core.model import SEPARATEUR, Host, HostStore, normaliser_chemin

PORT_MIN, PORT_MAX = 1, 65535

#: Champs vérifiés (attributs de :class:`~gcm4.core.model.Host`).
NOM, GROUPE, ADRESSE, PORT = "name", "group", "host", "port"


@dataclass(frozen=True)
class FieldError:
    """Erreur sur un champ : attribut de ``Host`` et message traduit."""

    champ: str
    message: str


def lire_port(texte: str) -> int | None:
    """Saisie du port → entier, ``None`` si vide (port par défaut du protocole, #146).

    Raises:
        ValueError: texte non numérique (« 22a », « -1 », « 2.5 »).
    """
    logger.trace("lire_port: {!r}", texte)
    propre = texte.strip()
    if not propre:
        logger.trace("lire_port: vide")
        return None
    if not propre.isdigit() or not propre.isascii():
        raise ValueError(propre)
    resultat = int(propre)
    logger.trace("lire_port: {}", resultat)
    return resultat


def _port_hors_bornes() -> FieldError:
    """Erreur unique du port (non numérique ou hors bornes)."""
    logger.trace("_port_hors_bornes")
    resultat = FieldError(
        PORT,
        _("Port must be a number between {min} and {max}.").format(min=PORT_MIN, max=PORT_MAX),
    )
    logger.trace("_port_hors_bornes: fait")
    return resultat


def erreur_de_port(texte: str) -> FieldError | None:
    """Erreur de la saisie du port (non numérique ou hors 1–65535), ``None`` si valide."""
    logger.trace("erreur_de_port: {!r}", texte)
    try:
        port = lire_port(texte)
    except ValueError:
        logger.debug("erreur_de_port: saisie non numérique {!r}", texte)
        port = -1
    resultat = None
    if port is not None and not PORT_MIN <= port <= PORT_MAX:
        resultat = _port_hors_bornes()
    logger.trace("erreur_de_port: {}", resultat)
    return resultat


def validate_host(
    host: Host,
    store: HostStore,
    original: Host | None = None,
    *,
    adresse_requise: bool = True,
) -> list[FieldError]:
    """Erreurs des champs communs de ``host`` ; liste vide si l'hôte peut être enregistré.

    Args:
        host: hôte saisi (pas encore dans ``store`` en création ou duplication).
        store: hôtes existants, pour l'unicité du nom dans le groupe.
        original: hôte modifié (mode modification) ; il ne compte pas comme doublon.
        adresse_requise: faux pour un protocole sans adresse (terminal local),
            indiqué par son plugin (#124).

    Règles : nom obligatoire, sans ``/`` et unique dans son groupe
    (comparaison exacte, comme GTK3 et ``HostStore`` : « Web » et « web »
    peuvent coexister) ; adresse non vide ; port vide (défaut du protocole)
    ou de 1 à 65535.
    """
    logger.debug(
        "validate_host: {!r} original={!r}", host.path, original.path if original else None
    )
    erreurs: list[FieldError] = []
    nom = host.name.strip()
    if not nom:
        erreurs.append(FieldError(NOM, _("A name is required.")))
    elif SEPARATEUR in nom:
        erreurs.append(FieldError(NOM, _("The name must not contain «/».")))
    else:
        groupe = normaliser_chemin(host.group)
        if any(
            h is not original and h.group == groupe and h.name.strip() == nom for h in store.hosts
        ):
            erreurs.append(
                FieldError(
                    NOM,
                    _("A host named «{name}» already exists in this group.").format(name=nom),
                )
            )
    if adresse_requise and not host.host.strip():
        erreurs.append(FieldError(ADRESSE, _("An address is required.")))
    if host.port is not None and not PORT_MIN <= host.port <= PORT_MAX:
        erreurs.append(_port_hors_bornes())
    logger.debug("validate_host: {} erreur(s) {}", len(erreurs), [e.champ for e in erreurs])
    return erreurs
