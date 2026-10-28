# SPDX-License-Identifier: GPL-3.0-or-later
"""Arbre des groupes et des hôtes, sans GTK (#139).

Construit depuis :class:`~gcm4.core.model.HostStore` la structure affichée
par ``gcm4.ui.host_tree`` : groupes d'abord, puis hôtes, triés selon la
locale (``locale.strxfrm``, casse ignorée).

Compatibilité avec l'application GTK3 :

- **couleurs** : la couleur d'un groupe s'hérite de l'ancêtre le plus
  proche qui en a une (``gcm4_core.resolve_group_color``) ; un hôte prend
  celle de son groupe. Classes CSS ``gcm-couleur-rrggbb``
  (:func:`gcm4.core.theme.color_class`) ;
- **groupes repliés** : format GTK3 de ``collapsed-folders``, converti par
  :mod:`gcm4.core.replies` ;
- **info-bulle** : nom, ``protocole:utilisateur@hôte``, description, comme
  ``on_treeServers_tooltip``, avec échappement du balisage (absent en
  GTK3 : un ``<`` dans une description cassait l'info-bulle).

Différence voulue : les hôtes sans groupe sont affichés à la racine ;
``updateTree`` les ignorait (ils étaient invisibles).
"""

from __future__ import annotations

import locale
from collections.abc import Iterable
from dataclasses import dataclass, field
from html import escape

from loguru import logger

import gcm4_core
from gcm4.core.model import Group, Host, HostStore, parent
from gcm4.core.replies import ecrire_replies, gtk3_tree_paths, lire_replies
from gcm4.core.theme import color_class


def cle_de_tri(texte: str) -> str:
    """Clé de tri selon la locale, casse ignorée."""
    logger.trace("cle_de_tri: {!r}", texte)
    try:
        resultat = locale.strxfrm(texte.casefold())
    except (OSError, ValueError):  # locale invalide ou caractère nul
        logger.warning("cle_de_tri: strxfrm indisponible, ordre des points de code")
        resultat = texte.casefold()
    logger.trace("cle_de_tri: fait")
    return resultat


@dataclass
class Noeud:
    """Ligne de l'arbre : groupe (``host`` vaut ``None``) ou hôte."""

    chemin: str
    nom: str
    host: Host | None = None
    couleur: str = ""
    enfants: list[Noeud] = field(default_factory=list)

    @property
    def est_groupe(self) -> bool:
        """Vrai pour un groupe."""
        logger.trace("Noeud.est_groupe: {!r}", self.chemin)
        resultat = self.host is None
        logger.trace("Noeud.est_groupe: -> {}", resultat)
        return resultat

    @property
    def classe_css(self) -> str | None:
        """Classe ``gcm-couleur-rrggbb`` de la couleur effective, ou ``None``."""
        logger.trace("Noeud.classe_css: {!r}", self.chemin)
        resultat = color_class(self.couleur) if self.couleur else None
        logger.trace("Noeud.classe_css: -> {}", resultat)
        return resultat


def _carte_couleurs(groupes: Iterable[Group]) -> dict[str, str]:
    """``{chemin: couleur}`` des groupes qui ont une couleur propre."""
    logger.trace("_carte_couleurs")
    resultat = {g.path: g.color for g in groupes if g.color}
    logger.trace("_carte_couleurs: {}", len(resultat))
    return resultat


def couleur_effective(chemin: str, couleurs: dict[str, str]) -> str:
    """Couleur du groupe ``chemin`` ou de son ancêtre coloré le plus proche."""
    logger.trace("couleur_effective: {!r}", chemin)
    resultat: str = gcm4_core.resolve_group_color(chemin, couleurs) or ""
    logger.trace("couleur_effective: -> {!r}", resultat)
    return resultat


def construire(store: HostStore) -> list[Noeud]:
    """Nœuds de premier niveau : groupes triés, puis hôtes sans groupe triés."""
    logger.debug("construire: {} groupe(s), {} hôte(s)", len(store.groups), len(store.hosts))
    couleurs = _carte_couleurs(store.groups.values())

    def niveau(chemin: str) -> list[Noeud]:
        logger.trace("construire.niveau: {!r}", chemin)
        groupes = sorted(store.subgroups(chemin), key=lambda g: cle_de_tri(g.name))
        hotes = sorted(store.hosts_in(chemin), key=lambda h: cle_de_tri(h.name))
        noeuds = [
            Noeud(g.path, g.name, None, couleur_effective(g.path, couleurs), niveau(g.path))
            for g in groupes
        ]
        couleur = couleur_effective(chemin, couleurs) if chemin else ""
        noeuds += [Noeud(h.path, h.name, h, couleur) for h in hotes]
        logger.trace("construire.niveau: {} nœud(s)", len(noeuds))
        return noeuds

    resultat = niveau("")
    logger.debug("construire: {} nœud(s) de premier niveau", len(resultat))
    return resultat


def infobulle(host: Host) -> str:
    """Balisage Pango de l'info-bulle d'un hôte (texte échappé)."""
    logger.trace("infobulle: {!r}", host.path)
    ligne = (
        f"{host.protocol}:{host.user}@{host.host}" if host.protocol else f"{host.user}@{host.host}"
    )
    resultat = f"<b>{escape(host.name)}</b>\n{escape(ligne)}"
    if host.description:
        resultat += f"\n<small>{escape(host.description)}</small>"
    logger.trace("infobulle: fait")
    return resultat


def correspondances(store: HostStore, texte: str) -> set[str] | None:
    """Chemins à garder pour ``texte`` : hôtes trouvés et tous leurs groupes ancêtres.

    ``None`` si ``texte`` est vide (pas de filtre). Recherche de
    ``HostStore.search`` : nom, adresse, description ; casse et accents
    ignorés. Les groupes sans hôte trouvé disparaissent.
    """
    logger.debug("correspondances: {!r}", texte)
    if not texte.strip():
        logger.debug("correspondances: pas de filtre")
        return None
    garder: set[str] = set()
    for hote in store.search(texte):
        garder.add(hote.path)
        garder.update(ancetres(hote.path))
    logger.debug("correspondances: {} chemin(s)", len(garder))
    return garder


def ancetres(chemin: str) -> list[str]:
    """Groupes parents de ``chemin``, du plus haut au plus proche (``a/b/c`` → ``a``, ``a/b``)."""
    logger.trace("ancetres: {!r}", chemin)
    resultat: list[str] = []
    courant = parent(chemin)
    while courant:
        resultat.insert(0, courant)
        courant = parent(courant)
    logger.trace("ancetres: {}", resultat)
    return resultat


__all__ = [
    "correspondances",
    "Noeud",
    "ancetres",
    "cle_de_tri",
    "construire",
    "couleur_effective",
    "ecrire_replies",
    "gtk3_tree_paths",
    "infobulle",
    "lire_replies",
]
