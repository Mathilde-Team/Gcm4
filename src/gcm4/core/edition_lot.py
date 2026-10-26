"""Édition de plusieurs hôtes en une fois, sans GTK (#100).

La sélection multiple de l'arbre (:class:`gcm4.ui.host_tree.HostTree`)
donne une liste de chemins ; seuls les hôtes comptent (un groupe
sélectionné est ignoré). :class:`EditionLot` décrit ce qui change :

- ``port`` : nouveau port, ``None`` pour le port par défaut du protocole,
  appliqué seulement si ``changer_port`` est vrai ;
- ``groupe`` : groupe de destination (``""`` : racine), ``None`` pour ne
  pas déplacer.

:func:`conflits` liste, avant toute modification, les noms qui
existeraient deux fois dans le groupe de destination (avec un hôte hors
de la sélection, ou entre deux hôtes sélectionnés venant de groupes
différents) : :func:`appliquer` refuse alors tout le lot, rien n'est
modifié à moitié.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from loguru import logger

from gcm4.core.model import SEPARATEUR, Host, HostStore, normaliser_chemin
from gcm4.core.validation import PORT_MAX, PORT_MIN


class LotInvalide(ValueError):
    """Lot refusé : port hors bornes ou noms en double dans le groupe cible."""


@dataclass(frozen=True)
class EditionLot:
    """Modifications communes à plusieurs hôtes ; un champ non demandé reste inchangé."""

    changer_port: bool = False
    port: int | None = None
    groupe: str | None = None

    @property
    def vide(self) -> bool:
        """Vrai si le lot ne change rien (bouton « Appliquer » inactif)."""
        logger.trace("EditionLot.vide: entrée")
        resultat = not self.changer_port and self.groupe is None
        logger.trace("EditionLot.vide: {}", resultat)
        return resultat


def hotes_de(store: HostStore, chemins: Iterable[str]) -> list[Host]:
    """Hôtes désignés par ``chemins``, dans l'ordre, sans doublon ; groupes ignorés."""
    logger.debug("hotes_de: entrée")
    resultat: list[Host] = []
    for chemin in chemins:
        hote = store.find(chemin)
        if hote is not None and all(hote is not h for h in resultat):
            resultat.append(hote)
    logger.debug("hotes_de: {} hôte(s)", len(resultat))
    return resultat


def conflits(store: HostStore, hotes: list[Host], edition: EditionLot) -> list[str]:
    """Chemins de destination déjà pris, triés ; liste vide si le déplacement est possible."""
    logger.debug("conflits: {} hôte(s) vers {!r}", len(hotes), edition.groupe)
    if edition.groupe is None:
        logger.debug("conflits: aucun déplacement")
        return []
    groupe = normaliser_chemin(edition.groupe)
    pris = {h.name for h in store.hosts if h.group == groupe and all(h is not s for s in hotes)}
    doublons: set[str] = set()
    for hote in hotes:
        if hote.name in pris:
            doublons.add(hote.name)
        pris.add(hote.name)
    resultat = sorted(f"{groupe}{SEPARATEUR}{nom}" if groupe else nom for nom in doublons)
    logger.debug("conflits: {}", resultat)
    return resultat


def appliquer(store: HostStore, hotes: list[Host], edition: EditionLot) -> int:
    """Applique ``edition`` à ``hotes`` ; renvoie le nombre d'hôtes modifiés.

    Raises:
        LotInvalide: port hors 1–65535 ou noms en double dans le groupe
            cible ; aucun hôte n'est alors modifié.
    """
    logger.debug("appliquer: entrée {} hôte(s) {}", len(hotes), edition)
    if edition.changer_port and edition.port is not None:
        if not PORT_MIN <= edition.port <= PORT_MAX:
            logger.debug("appliquer: port hors bornes {}", edition.port)
            raise LotInvalide(f"port {edition.port}")
    doublons = conflits(store, hotes, edition)
    if doublons:
        logger.debug("appliquer: conflits {}", doublons)
        raise LotInvalide(", ".join(doublons))
    modifies = 0
    for hote in hotes:
        avant = (hote.port, hote.group)
        if edition.changer_port:
            hote.port = edition.port
        if edition.groupe is not None:
            store.move(hote, edition.groupe)
        if (hote.port, hote.group) != avant:
            modifies += 1
    logger.debug("appliquer: {} hôte(s) modifié(s)", modifies)
    return modifies
