# SPDX-License-Identifier: GPL-3.0-or-later
"""Menu contextuel de l'arbre et règles de ses actions, sans GTK (#142).

La disposition est celle de ``folder_context_menu_core.build_folder_context_menu_layout``
(GTK3, ``popupMenuFolder``), portée ici : ce module racine n'est pas dans le
paquet gcm4 et disparaît avec le cœur GTK3 (#182). La parité est testée tant
qu'il existe. Elle est ensuite filtrée :

- ``connect`` : visible seulement si un plugin gère le protocole (registre,
  #124) ; aucune entrée en mode zéro plugin.

``add`` / ``edit`` ouvrent le dialogue d'hôte (#145).

Libellés : msgids de l'interface GTK3 (``widgets.FolderContextMenu``),
marqués ``N_`` et traduits à l'affichage.
"""

from __future__ import annotations

from dataclasses import dataclass

from loguru import logger

from gcm4.core.host_tree import Noeud
from gcm4.core.i18n import N_
from gcm4.core.model import SEPARATEUR, HostStore, normaliser_chemin

LIBELLES: dict[str, str] = {
    "connect": N_("Connect"),
    "copy-address": N_("Copy Address"),
    "add": N_("Add Host"),
    "new-group": N_("New Group…"),
    "rename-group": N_("Rename Group…"),
    "group-color": N_("Choose Color…"),
    "group-color-reset": N_("Reset Color"),
    "edit": N_("Edit"),
    "delete": N_("Remove"),
    "duplicate": N_("Duplicate Host"),
    "expand": N_("Expand all"),
    "collapse": N_("Collapse all"),
    "edit-selection": N_("Edit Selected Hosts…"),
}

#: Entrées en attente d'un lot ultérieur (voir le module) ; aucune depuis #145.
EN_ATTENTE: frozenset[str] = frozenset()


def disposition_gtk3(click_target: str) -> list[list[str]]:
    """Disposition complète de ``popupMenuFolder`` pour ``"empty"``/``"folder"``/``"host"``."""
    logger.trace("disposition_gtk3: {}", click_target)
    if click_target not in ("empty", "folder", "host"):
        raise ValueError(click_target)
    premiere = ["connect", "add", "new-group"]
    if click_target == "host":
        premiere.insert(1, "copy-address")
    if click_target == "folder":
        premiere.append("rename-group")
    premiere += ["group-color", "group-color-reset"]
    edition = []
    if click_target == "host":
        edition += ["edit", "duplicate"]
    if click_target != "empty":
        edition.append("delete")
    resultat = [premiere, *([edition] if edition else []), ["expand", "collapse"]]
    logger.trace("disposition_gtk3: {}", resultat)
    return resultat


def cible(noeud: Noeud | None) -> str:
    """Cible du clic : ``"empty"``, ``"folder"`` ou ``"host"`` (valeurs GTK3)."""
    logger.trace("cible: {!r}", noeud.chemin if noeud else None)
    resultat = "empty" if noeud is None else ("folder" if noeud.est_groupe else "host")
    logger.trace("cible: -> {}", resultat)
    return resultat


def sections(noeud: Noeud | None, connexion_possible: bool = False) -> list[list[str]]:
    """Sections d'actions du menu pour ``noeud`` (``None`` : zone vide).

    Args:
        noeud: ligne visée par le clic droit.
        connexion_possible: un plugin gère le protocole de l'hôte (#124).
    """
    logger.debug(
        "sections: {!r} connexion={}", noeud.chemin if noeud else None, connexion_possible
    )
    exclues = set(EN_ATTENTE)
    if not connexion_possible or noeud is None or noeud.est_groupe:
        exclues.add("connect")
    if groupe_de_travail(noeud) == "":
        # La racine n'est pas un groupe : pas de couleur à choisir.
        exclues.update({"group-color", "group-color-reset"})
    resultat = []
    for section in disposition_gtk3(cible(noeud)):
        gardees = [a for a in section if a not in exclues]
        if gardees:
            resultat.append(gardees)
    logger.debug("sections: {}", resultat)
    return resultat


def sections_lot(nombre_hotes: int) -> list[list[str]]:
    """Sections du menu d'une sélection de plusieurs lignes (#100).

    Avec au moins deux hôtes sélectionnés : édition en lot ; sinon (groupes
    seuls, ou un seul hôte parmi des groupes) seules restent les actions
    d'affichage.
    """
    logger.debug("sections_lot: {} hôte(s)", nombre_hotes)
    resultat = [["edit-selection"]] if nombre_hotes >= 2 else []
    resultat.append(["expand", "collapse"])
    logger.debug("sections_lot: {}", resultat)
    return resultat


def groupe_de_travail(noeud: Noeud | None) -> str:
    """Groupe visé : le groupe cliqué, le groupe de l'hôte cliqué, ou la racine ``""``."""
    logger.trace("groupe_de_travail")
    if noeud is None:
        resultat = ""
    elif noeud.est_groupe:
        resultat = noeud.chemin
    else:
        resultat = noeud.chemin.rpartition(SEPARATEUR)[0]
    logger.trace("groupe_de_travail: {!r}", resultat)
    return resultat


@dataclass(frozen=True)
class Decompte:
    """Ce qu'une suppression de groupe emporte."""

    hotes: int
    sous_groupes: int

    @property
    def vide(self) -> bool:
        """Vrai si le groupe ne contient rien."""
        return not self.hotes and not self.sous_groupes


def decompte(store: HostStore, chemin: str) -> Decompte:
    """Hôtes (récursivement) et sous-groupes de ``chemin``, pour la confirmation."""
    logger.debug("decompte: {!r}", chemin)
    prefixe = chemin + SEPARATEUR
    resultat = Decompte(
        hotes=len(store.hosts_in(chemin, recursive=True)),
        sous_groupes=sum(1 for p in store.groups if p.startswith(prefixe)),
    )
    logger.debug("decompte: {}", resultat)
    return resultat


def chemin_de_groupe(
    store: HostStore, parent: str, nom: str, actuel: str | None = None
) -> str | None:
    """Chemin ``parent/nom`` d'un groupe créé ou renommé, ``None`` si le nom est refusé.

    Refus : nom vide, contenant ``/`` (le séparateur), ou chemin déjà pris
    par un autre groupe que ``actuel``.
    """
    logger.debug("chemin_de_groupe: {!r} {!r} actuel={!r}", parent, nom, actuel)
    propre = nom.strip()
    if not propre or SEPARATEUR in propre:
        logger.debug("chemin_de_groupe: nom refusé")
        return None
    chemin = normaliser_chemin(f"{parent}{SEPARATEUR}{propre}" if parent else propre)
    if chemin in store.groups and chemin != actuel:
        logger.debug("chemin_de_groupe: déjà pris")
        return None
    logger.debug("chemin_de_groupe: {!r}", chemin)
    return chemin
