# SPDX-License-Identifier: GPL-3.0-or-later
"""Groupes repliés de ``[window] collapsed-folders``, au format GTK3 (#139).

L'application GTK3 y enregistre des chemins ``Gtk.TreePath`` de son arbre
(``0``, ``1:2``), pas des noms de groupe (``Wmain.get_collapsed_nodes`` /
``set_collapsed_nodes``). :func:`gtk3_tree_paths` reproduit l'ordre de
``Wmain.updateTree`` pour convertir dans les deux sens : une configuration
écrite par gcm4 reste correcte pour l'application GTK3, et inversement.

gcm4 1.3.4-rc1 écrivait des noms de groupe : ces valeurs, qui ne sont
jamais des index GTK3, sont encore acceptées à la lecture.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from loguru import logger

from gcm4.core.model import SEPARATEUR

_INDICE_GTK3 = re.compile(r"^\d+(?::\d+)*$")


def gtk3_tree_paths(groupes: Iterable[str]) -> dict[str, str]:
    """``{chemin de groupe: Gtk.TreePath GTK3}`` (``"0"``, ``"1:2"``…).

    Reproduit ``Wmain.updateTree`` : clés de groupe triées à l'envers
    (``sorted(..., reverse=True)``, ordre des points de code), chaque
    dossier absent **ajouté en tête** de son parent (``prepend``). Les
    hôtes étant ajoutés après (``append``), les dossiers précèdent toujours
    les hôtes et leur index ne dépend que des dossiers. Le groupe racine
    ``""`` est ignoré, comme dans ``updateTree``.
    """
    logger.debug("gtk3_tree_paths: entrée")
    enfants: dict[str, list[str]] = {"": []}
    for cle in sorted({g for g in groupes if g}, reverse=True):
        chemin = ""
        for dossier in cle.split(SEPARATEUR):
            precedent = chemin
            chemin = f"{chemin}{SEPARATEUR}{dossier}" if chemin else dossier
            if chemin not in enfants:
                enfants[chemin] = []
                enfants[precedent].insert(0, chemin)
    resultat: dict[str, str] = {}

    def parcourir(chemin: str, prefixe: str) -> None:
        logger.trace("gtk3_tree_paths.parcourir: {!r}", chemin)
        for index, enfant in enumerate(enfants[chemin]):
            indice = f"{prefixe}:{index}" if prefixe else str(index)
            resultat[enfant] = indice
            parcourir(enfant, indice)
        logger.trace("gtk3_tree_paths.parcourir: fait")

    parcourir("", "")
    logger.debug("gtk3_tree_paths: {} groupe(s)", len(resultat))
    return resultat


def lire_replies(valeur: str, groupes: Iterable[str]) -> set[str]:
    """``collapsed-folders`` (chemins GTK3 ``0:1``) → chemins de groupe repliés.

    Un index qui ne correspond à aucun groupe (arbre modifié depuis) est
    ignoré.
    """
    logger.debug("lire_replies: {!r}", valeur)
    groupes = set(groupes)
    inverse = {indice: chemin for chemin, indice in gtk3_tree_paths(groupes).items()}
    resultat: set[str] = set()
    for entree in (e.strip() for e in valeur.split(",")):
        if _INDICE_GTK3.match(entree):
            if entree in inverse:
                resultat.add(inverse[entree])
        elif entree in groupes:  # nom de groupe écrit par gcm4 1.3.4-rc1
            resultat.add(entree)
    logger.debug("lire_replies: {} groupe(s)", len(resultat))
    return resultat


def ecrire_replies(replies: Iterable[str], groupes: Iterable[str]) -> str:
    """Chemins de groupe repliés → ``collapsed-folders`` au format GTK3, dans l'ordre de l'arbre."""
    logger.debug("ecrire_replies: entrée")
    indices = gtk3_tree_paths(groupes)
    choisis = [indices[c] for c in set(replies) if c in indices]
    resultat = ",".join(sorted(choisis, key=lambda i: [int(n) for n in i.split(":")]))
    logger.debug("ecrire_replies: {!r}", resultat)
    return resultat
