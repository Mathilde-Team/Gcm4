# SPDX-License-Identifier: GPL-3.0-or-later
"""Onglets ouverts, enregistrés à la fermeture et restaurés au démarrage (#246).

Même clé et même format que l'application GTK3 (features.md §4.2) :
``[window] open-tabs = groupe/nom,groupe/nom``, dans l'ordre des onglets.
GTK3 et gcm4 découpent chaque entrée au dernier ``/`` (``gcm4_core.resolve_host_specs``,
``HostStore.find``) : un fichier passe de l'une à l'autre sans perte.

La liste est écrite à chaque fermeture, **même** si « Restaurer les onglets
ouverts » est désactivé (comportement GTK3) : activer l'option plus tard
restaure la dernière session plutôt qu'une liste vide.

Limite héritée du format : la virgule sépare les entrées. Un hôte dont le
chemin contient une virgule n'est pas enregistré (avertissement journalisé).
"""

from __future__ import annotations

from collections.abc import Iterable

from loguru import logger

from gcm4.core import window_state

#: Clé de ``[window]`` (GTK3 : ``conf.OPEN_TABS``).
CLE = "open-tabs"
SEPARATEUR = ","


def serialiser(chemins: Iterable[str]) -> str:
    """Chemins ``groupe/nom`` des onglets → valeur INI (ordre gardé, sans doublon)."""
    logger.debug("serialiser: entrée")
    vus: list[str] = []
    for chemin in chemins:
        chemin = chemin.strip()
        if not chemin or chemin in vus:
            continue
        if SEPARATEUR in chemin:
            logger.warning("serialiser: {!r} contient une virgule, non enregistré", chemin)
            continue
        vus.append(chemin)
    resultat = SEPARATEUR.join(vus)
    logger.debug("serialiser: {} onglet(s)", len(vus))
    return resultat


def analyser(brut: str) -> list[str]:
    """Valeur INI → chemins, dans l'ordre, sans entrée vide ni doublon."""
    logger.debug("analyser: {!r}", brut)
    resultat: list[str] = []
    for morceau in (brut or "").split(SEPARATEUR):
        chemin = morceau.strip()
        if chemin and chemin not in resultat:
            resultat.append(chemin)
    logger.debug("analyser: {} onglet(s)", len(resultat))
    return resultat


def lire(config_file: str) -> list[str]:
    """Onglets enregistrés à la dernière fermeture ; fichier absent ou illisible → ``[]``."""
    logger.debug("lire: {}", config_file)
    cp = window_state._lire(config_file)
    resultat = analyser(cp.get(window_state.SECTION, CLE, fallback=""))
    logger.debug("lire: {} onglet(s)", len(resultat))
    return resultat
