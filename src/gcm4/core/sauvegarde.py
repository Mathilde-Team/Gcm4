# SPDX-License-Identifier: GPL-3.0-or-later
"""Aperçu d'import, bilan et copie de la configuration, sans GTK (#153).

Ce que l'interface d'import/export (``gcm4.ui.echange``) affiche ou
écrit, testable sans display :

- :func:`apercu` : ce que donnerait un import, magasin intact ;
- :func:`bilan` : phrase de bilan d'un import (aussi celle de
  ``gcm4 --import-json``, #154) ;
- :func:`avec_mots_de_passe` : le fichier lu contient-il des mots de passe
  (en clair) ;
- :func:`enregistrer_sous` : copie de ``gcm4.conf`` et de ``.gcm4.key``
  dans un autre dossier (« Enregistrer la configuration sous »).
"""

from __future__ import annotations

import copy
import os
import shutil
from collections.abc import Iterable
from pathlib import Path

from loguru import logger

from gcm4.core import chemins
from gcm4.core.chiffrement import FICHIER_CLE
from gcm4.core.exchange import Conflit, ExchangeError, Rapport, importer
from gcm4.core.i18n import _
from gcm4.core.model import Host, HostStore


def apercu(store: HostStore, hosts: Iterable[Host], conflit: Conflit) -> Rapport:
    """Bilan qu'aurait l'import de ``hosts`` dans ``store``, sans le modifier.

    Args:
        store (HostStore): Magasin actuel (laissé intact).
        hosts (Iterable[Host]): Hôtes lus dans le fichier.
        conflit (Conflit): Politique envisagée.

    Returns:
        Rapport: Hôtes qui seraient ajoutés, remplacés, renommés, ignorés.
    """
    logger.debug("apercu: conflit={}", conflit.value)
    rapport = importer(copy.deepcopy(store), hosts, conflit)
    logger.debug("apercu: {} ajouté(s)", len(rapport.ajoutes))
    return rapport


def bilan(fichier: str | Path, rapport: Rapport) -> str:
    """Phrase de bilan d'un import (traduite).

    Args:
        fichier (str | Path): Fichier importé.
        rapport (Rapport): Bilan de :func:`gcm4.core.exchange.importer`.

    Returns:
        str: « Import de f.json : ajoutés 2, remplacés 0… ».
    """
    logger.debug("bilan: {}", fichier)
    texte = _(
        "Import of {file}: added {added}, replaced {replaced}, renamed {renamed}, ignored {ignored}, new groups {groups}"
    ).format(
        file=fichier,
        added=len(rapport.ajoutes),
        replaced=len(rapport.remplaces),
        renamed=len(rapport.renommes),
        ignored=len(rapport.ignores),
        groups=len(rapport.groupes_crees),
    )
    logger.debug("bilan: fait")
    return texte


def avec_mots_de_passe(hosts: Iterable[Host]) -> bool:
    """Vrai si au moins un hôte porte un mot de passe (il était en clair dans le fichier).

    Args:
        hosts (Iterable[Host]): Hôtes lus.

    Returns:
        bool: Un mot de passe au moins.
    """
    logger.debug("avec_mots_de_passe: entrée")
    resultat = any(h.password for h in hosts)
    logger.debug("avec_mots_de_passe: {}", resultat)
    return resultat


def enregistrer_sous(config_dir: str, dossier: str | Path) -> list[Path]:
    """Copie ``gcm4.conf`` et ``.gcm4.key`` (si elle existe) dans ``dossier``.

    Les deux fichiers vont ensemble : sans la clé, les mots de passe de la
    copie ne se déchiffrent pas. Copies en 0600 ; rien n'est écrasé.

    Args:
        config_dir (str): Dossier de configuration en cours.
        dossier (str | Path): Dossier de destination (existant).

    Returns:
        list[Path]: Fichiers créés.

    Raises:
        ExchangeError: même dossier, fichier déjà présent, configuration
            absente ou écriture impossible (message traduit) ; rien n'est
            laissé à moitié copié.
    """
    logger.debug("enregistrer_sous: {} -> {}", config_dir, dossier)
    cible = Path(dossier)
    sources = [Path(chemins.fichier_conf(config_dir))]
    cle = Path(config_dir) / FICHIER_CLE
    if cle.exists():
        sources.append(cle)
    if not sources[0].is_file():
        logger.debug("enregistrer_sous: aucune configuration")
        raise ExchangeError(_("No configuration to save yet: %s") % sources[0])
    if cible.resolve() == Path(config_dir).resolve():
        logger.debug("enregistrer_sous: même dossier")
        raise ExchangeError(_("Choose a folder other than the configuration folder."))
    for source in sources:
        if (cible / source.name).exists():
            logger.debug("enregistrer_sous: {} existe déjà", source.name)
            raise ExchangeError(
                _("{folder} already contains {file}.").format(folder=cible, file=source.name)
            )
    crees: list[Path] = []
    try:
        for source in sources:
            destination = cible / source.name
            fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as sortie, source.open("rb") as entree:
                crees.append(destination)
                shutil.copyfileobj(entree, sortie)
    except OSError as exc:
        for fichier in crees:
            fichier.unlink(missing_ok=True)
        logger.debug("enregistrer_sous: écriture impossible ({})", exc)
        raise ExchangeError(_("Unable to write the file %s: %s") % (cible, exc)) from exc
    logger.debug("enregistrer_sous: {} fichier(s)", len(crees))
    return crees
