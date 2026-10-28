# SPDX-License-Identifier: GPL-3.0-or-later
"""Glisser-déposer dans l'arbre, validé sans GTK (#141).

Portage de ``Wmain._on_tree_drag_data_get`` / ``_on_tree_drag_data_received``
(GTK3). L'arbre GTK4 est trié : on ne réordonne pas, on **change de groupe**.

- Élément déposé sur un **groupe** : il va dans ce groupe.
- Sur un **hôte** : il va dans le groupe de cet hôte. À la racine, il va
  donc à la racine ; GTK3 le refusait pour un groupe, ce qui empêchait de
  remonter un groupe à la racine.
- Sur la **zone vide** de la liste (``cible=None``) : racine.
- Refus : même groupe (rien à faire), groupe dans lui-même ou dans un
  descendant, nom déjà pris à l'arrivée (hôte de même nom, groupe de même
  chemin), élément disparu.

Les éléments glissés sont identifiés par une clé texte ``h:<chemin>`` ou
``g:<chemin>`` : un hôte et un sous-groupe peuvent porter le même chemin.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from loguru import logger

from gcm4.core.model import SEPARATEUR, HostStore, normaliser_chemin

PREFIXE_HOTE = "h:"
PREFIXE_GROUPE = "g:"


class Refus(enum.Enum):
    """Raison d'un dépôt refusé."""

    INTROUVABLE = "introuvable"
    MEME_GROUPE = "même groupe"
    DANS_LUI_MEME = "groupe dans lui-même ou un descendant"
    NOM_PRIS = "nom déjà pris"


@dataclass(frozen=True)
class Deplacement:
    """Déplacement validé : ``cle`` (``h:``/``g:``) vers le groupe ``destination``."""

    cle: str
    destination: str

    @property
    def est_groupe(self) -> bool:
        """Vrai pour un groupe."""
        return self.cle.startswith(PREFIXE_GROUPE)

    @property
    def chemin(self) -> str:
        """Chemin de l'élément déplacé, avant le déplacement."""
        return self.cle[2:]

    @property
    def nouveau_chemin(self) -> str:
        """Chemin de l'élément après le déplacement."""
        logger.trace("Deplacement.nouveau_chemin: {!r}", self.cle)
        feuille = self.chemin.rpartition(SEPARATEUR)[2]
        resultat = f"{self.destination}{SEPARATEUR}{feuille}" if self.destination else feuille
        logger.trace("Deplacement.nouveau_chemin: -> {!r}", resultat)
        return resultat


def cle(chemin: str, est_groupe: bool) -> str:
    """Clé d'un élément glissé."""
    logger.trace("cle: {!r} groupe={}", chemin, est_groupe)
    resultat = (PREFIXE_GROUPE if est_groupe else PREFIXE_HOTE) + chemin
    logger.trace("cle: -> {!r}", resultat)
    return resultat


def groupe_cible(store: HostStore, cible: str | None) -> str | None:
    """Groupe d'arrivée pour un dépôt sur ``cible`` (clé ``h:``/``g:``, ``None`` : zone vide).

    ``None`` si la cible n'existe plus.
    """
    logger.debug("groupe_cible: {!r}", cible)
    if cible is None:
        logger.debug("groupe_cible: racine (zone vide)")
        return ""
    chemin = cible[2:]
    if cible.startswith(PREFIXE_GROUPE):
        resultat = chemin if chemin in store.groups else None
    else:
        hote = store.find(chemin)
        resultat = hote.group if hote is not None else None
    logger.debug("groupe_cible: {!r}", resultat)
    return resultat


def valider(store: HostStore, source: str, cible: str | None) -> Deplacement | Refus:
    """Valide le dépôt de ``source`` sur ``cible`` sans rien modifier."""
    logger.debug("valider: {!r} -> {!r}", source, cible)
    destination = groupe_cible(store, cible)
    chemin = source[2:]
    if destination is None or not source.startswith((PREFIXE_HOTE, PREFIXE_GROUPE)):
        logger.debug("valider: refus (cible ou clé invalide)")
        return Refus.INTROUVABLE
    deplacement = Deplacement(source, normaliser_chemin(destination))
    if deplacement.est_groupe:
        resultat = _valider_groupe(store, deplacement)
    else:
        hote = store.find(chemin)
        if hote is None:
            resultat = Refus.INTROUVABLE
        elif hote.group == deplacement.destination:
            resultat = Refus.MEME_GROUPE
        elif store.find(deplacement.nouveau_chemin) is not None:
            resultat = Refus.NOM_PRIS
        else:
            resultat = deplacement
    logger.debug("valider: {}", resultat)
    return resultat


def _valider_groupe(store: HostStore, deplacement: Deplacement) -> Deplacement | Refus:
    """Règles propres aux groupes."""
    logger.trace("_valider_groupe: {}", deplacement)
    chemin, destination = deplacement.chemin, deplacement.destination
    resultat: Deplacement | Refus
    if chemin not in store.groups:
        resultat = Refus.INTROUVABLE
    elif destination == chemin or destination.startswith(chemin + SEPARATEUR):
        resultat = Refus.DANS_LUI_MEME
    elif chemin.rpartition(SEPARATEUR)[0] == destination:
        resultat = Refus.MEME_GROUPE
    elif deplacement.nouveau_chemin in store.groups:
        resultat = Refus.NOM_PRIS
    else:
        resultat = deplacement
    logger.trace("_valider_groupe: {}", resultat)
    return resultat


def appliquer(store: HostStore, deplacement: Deplacement) -> Deplacement:
    """Applique ``deplacement`` (validé) ; renvoie le déplacement inverse, pour annuler.

    Raises:
        KeyError, ValueError: le store a changé depuis la validation.
    """
    logger.debug("appliquer: {}", deplacement)
    ancien_parent = deplacement.chemin.rpartition(SEPARATEUR)[0]
    if deplacement.est_groupe:
        store.rename_group(deplacement.chemin, deplacement.nouveau_chemin)
        inverse = Deplacement(cle(deplacement.nouveau_chemin, True), ancien_parent)
    else:
        hote = store.find(deplacement.chemin)
        if hote is None:
            raise KeyError(deplacement.chemin)
        store.move(hote, deplacement.destination)
        inverse = Deplacement(cle(hote.path, False), ancien_parent)
    logger.debug("appliquer: inverse {}", inverse)
    return inverse
