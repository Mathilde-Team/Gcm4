# SPDX-License-Identifier: GPL-3.0-or-later
"""Emplacement de la configuration de gcm4 et migration depuis ``~/.gcm`` (#99).

Décision de l'autrice (12/10/2026, remplace D3 de #118) : le nom ``gcm4``
partout.

- Dossier : ``$XDG_CONFIG_HOME/gcm4`` (``~/.config/gcm4`` si la variable est
  absente ou relative, comme le veut la spécification XDG) ; ``-c/--config``
  reste prioritaire.
- Fichiers : ``gcm4.conf`` et ``.gcm4.key``.
- Migration au démarrage, avant tout journal ou écriture :

  1. dossier par défaut absent et ``~/.gcm`` présent : copie complète
     (``gcm4_core.migrate_legacy_config_dir`` : droits conservés, jamais
     d'écrasement, ancien dossier jamais supprimé, copie partielle nettoyée) ;
  2. dans le dossier retenu, ``gcm.conf`` / ``.gcm.key`` / ``gcm.conf.bak``
     sans leurs équivalents ``gcm4`` : copie sous le nouveau nom en ``0600``,
     sans écrasement (``os.link`` d'un temporaire). Dans un dossier qui vient
     d'être copié, l'ancien nom est ensuite retiré (l'original reste dans
     ``~/.gcm``) ; ailleurs (``-c ~/.gcm`` explicite), il est conservé.

Les anciens fichiers restent en place : l'application GTK3 les utilise
jusqu'à son retrait (#177), et un retour en arrière retrouve tout. Les deux
configurations évoluent ensuite séparément.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass, field

from loguru import logger

import gcm4_core

NOM_DOSSIER = "gcm4"
FICHIER_CONF = "gcm4.conf"
FICHIER_CLE = ".gcm4.key"
ANCIEN_DOSSIER = ".gcm"
ANCIENS_FICHIERS = {
    FICHIER_CONF: "gcm.conf",
    FICHIER_CLE: ".gcm.key",
    FICHIER_CONF + ".bak": "gcm.conf.bak",
}


class MigrationError(RuntimeError):
    """La configuration n'a pas pu être migrée ; rien n'a été supprimé."""


@dataclass
class Migration:
    """Ce que :func:`preparer` a fait."""

    dossier_copie_depuis: str | None = None
    fichiers_copies: list[str] = field(default_factory=list)

    @property
    def effectuee(self) -> bool:
        """Vrai si quelque chose a été copié."""
        return self.dossier_copie_depuis is not None or bool(self.fichiers_copies)


def dossier_par_defaut(home: str, environ: dict[str, str] | None = None) -> str:
    """``$XDG_CONFIG_HOME/gcm4``, ou ``home/.config/gcm4`` (variable absente, vide ou relative)."""
    logger.debug("dossier_par_defaut: entrée")
    env = os.environ if environ is None else environ
    xdg = env.get("XDG_CONFIG_HOME", "")
    base = xdg if os.path.isabs(xdg) else os.path.join(home, ".config")
    resultat = os.path.join(base, NOM_DOSSIER)
    logger.debug("dossier_par_defaut: {}", resultat)
    return resultat


def ancien_dossier(home: str) -> str:
    """Dossier de l'application GTK3 : ``home/.gcm``."""
    logger.debug("ancien_dossier: {}", home)
    resultat = os.path.join(home, ANCIEN_DOSSIER)
    logger.debug("ancien_dossier: {}", resultat)
    return resultat


def fichier_conf(config_dir: str) -> str:
    """Chemin de ``gcm4.conf`` dans ``config_dir``."""
    logger.debug("fichier_conf: {}", config_dir)
    resultat = os.path.join(config_dir, FICHIER_CONF)
    logger.debug("fichier_conf: {}", resultat)
    return resultat


def _copier_sans_ecraser(source: str, destination: str) -> bool:
    """Copie ``source`` en ``destination`` (0600) ; ``False`` si ``destination`` existe déjà."""
    logger.debug("_copier_sans_ecraser: {} -> {}", source, destination)
    dossier = os.path.dirname(destination)
    descripteur, temporaire = tempfile.mkstemp(prefix=".gcm4-migration.", dir=dossier)
    try:
        with os.fdopen(descripteur, "wb") as sortie, open(source, "rb") as entree:
            shutil.copyfileobj(entree, sortie)
            sortie.flush()
            os.fsync(sortie.fileno())
        os.chmod(temporaire, 0o600)
        os.link(temporaire, destination)  # échoue si destination existe : jamais d'écrasement
    except FileExistsError:
        logger.debug("_copier_sans_ecraser: {} déjà présent", destination)
        return False
    finally:
        os.unlink(temporaire)
    logger.debug("_copier_sans_ecraser: copié")
    return True


def preparer(config_dir: str, ancien: str | None = None) -> Migration:
    """Migre si besoin vers ``config_dir`` (voir le module) ; idempotent.

    Args:
        config_dir: dossier retenu (défaut XDG ou ``-c``).
        ancien: dossier GTK3 à copier si ``config_dir`` est absent ; ``None``
            avec ``-c`` (le dossier donné n'est jamais rempli depuis ailleurs).

    Raises:
        MigrationError: copie impossible (droits, disque plein) ; l'ancien
            dossier est intact et un dossier partiel est supprimé.
    """
    logger.debug("preparer: config_dir={} ancien={}", config_dir, ancien)
    migration = Migration()
    try:
        if ancien is not None and gcm4_core.migrate_legacy_config_dir(ancien, config_dir):
            migration.dossier_copie_depuis = ancien
            logger.info("preparer: configuration copiée de {} vers {}", ancien, config_dir)
        if os.path.isdir(config_dir):
            for nouveau, vieux in ANCIENS_FICHIERS.items():
                source = os.path.join(config_dir, vieux)
                destination = os.path.join(config_dir, nouveau)
                if os.path.isfile(source) and not os.path.lexists(destination):
                    if _copier_sans_ecraser(source, destination):
                        migration.fichiers_copies.append(nouveau)
                        logger.info("preparer: {} copié en {}", vieux, nouveau)
                        if migration.dossier_copie_depuis is not None:
                            os.unlink(source)  # doublon : l'original est dans l'ancien dossier
    except (OSError, RuntimeError) as exc:
        # gcm4_core enveloppe l'OSError : sa cause (« No space left… ») est
        # ce que l'utilisateur doit lire.
        cause = exc.__cause__
        message = f"{exc} : {cause}" if cause is not None else str(exc)
        logger.error("preparer: migration impossible ({})", message)
        raise MigrationError(message) from exc
    logger.debug("preparer: {}", migration)
    return migration
