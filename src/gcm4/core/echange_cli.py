# SPDX-License-Identifier: GPL-3.0-or-later
"""Import et export des hôtes en ligne de commande, sans fenêtre (#154).

``gcm4 --export-json FICHIER`` (ou ``--export-csv``) écrit les hôtes de
``gcm4.conf`` ; ``gcm4 --import-json FICHIER`` (ou ``--import-csv``) les
ajoute à ``gcm4.conf`` puis s'arrête. Aucun import GTK : scriptable sans
display (cron, ssh, paquets). Bilan sur la sortie standard, erreur sur la
sortie d'erreur avec le code :data:`CODE_ECHANGE_IMPOSSIBLE`.

Ne pas lancer d'import pendant que l'interface tourne sur la même
configuration : elle réécrirait ``gcm4.conf`` avec son propre état.
"""

from __future__ import annotations

import argparse
import sys
from typing import TextIO

from loguru import logger

from gcm4.core import chemins, sauvegarde
from gcm4.core.chiffrement import ChiffrementGcm
from gcm4.core.config import ConfigError, load_config, save_config
from gcm4.core.exchange import Conflit, ExchangeError, exporter_fichier, importer_fichier
from gcm4.core.i18n import _

#: Code de sortie : configuration, fichier d'échange ou chiffrement en erreur.
CODE_ECHANGE_IMPOSSIBLE = 5

#: Valeurs de ``--on-conflict`` → politique de :mod:`gcm4.core.exchange`.
CONFLITS = {"ignore": Conflit.IGNORER, "rename": Conflit.RENOMMER, "replace": Conflit.REMPLACER}


def ajouter_arguments(parser: argparse.ArgumentParser) -> None:
    """Ajoute ``--export-json``/``--export-csv``/``--import-json``/``--import-csv`` et leurs options.

    Args:
        parser (argparse.ArgumentParser): Analyseur de la commande ``gcm4``.
    """
    logger.debug("ajouter_arguments: entrée")
    groupe = parser.add_argument_group("import/export sans fenêtre")
    actions = groupe.add_mutually_exclusive_group()
    for option, aide in (
        ("--export-json", "écrit tous les hôtes dans FICHIER (JSON) puis quitte"),
        ("--export-csv", "écrit tous les hôtes dans FICHIER (CSV) puis quitte"),
        ("--import-json", "ajoute les hôtes de FICHIER (JSON) à la configuration puis quitte"),
        ("--import-csv", "ajoute les hôtes de FICHIER (CSV) à la configuration puis quitte"),
    ):
        actions.add_argument(option, metavar="FICHIER", default=None, help=aide)
    groupe.add_argument(
        "--on-conflict",
        choices=sorted(CONFLITS),
        default="ignore",
        help="import : hôte de même nom dans le même groupe (défaut : ignore)",
    )
    groupe.add_argument(
        "--include-passwords",
        action="store_true",
        help="export : inclure les mots de passe EN CLAIR (exclus par défaut)",
    )
    logger.debug("ajouter_arguments: fait")


def demande(args: argparse.Namespace) -> tuple[str, str, str] | None:
    """Action d'échange demandée : ``(sens, format, fichier)``, ou ``None``.

    Args:
        args (argparse.Namespace): Arguments analysés.

    Returns:
        tuple[str, str, str] | None: ``("export", "json", "f.json")``… ou
        ``None`` si aucune option d'échange n'est donnée.
    """
    logger.debug("demande: entrée")
    for sens in ("export", "import"):
        for fmt in ("json", "csv"):
            fichier = getattr(args, f"{sens}_{fmt}", None)
            if fichier:
                logger.debug("demande: {} {} {}", sens, fmt, fichier)
                return sens, fmt, fichier
    logger.debug("demande: aucune")
    return None


def _cible(fichier: str, fmt: str) -> str:
    """Nom de fichier avec l'extension du format (``hotes`` → ``hotes.json``).

    :mod:`gcm4.core.exchange` choisit le format d'après l'extension : un
    ``--export-csv x.json`` doit rester du CSV, d'où l'extension ajoutée si
    elle ne correspond pas.

    Args:
        fichier (str): Fichier donné sur la ligne de commande.
        fmt (str): ``"json"`` ou ``"csv"``.

    Returns:
        str: Fichier à lire ou écrire.
    """
    logger.trace("_cible: {} {}", fichier, fmt)
    resultat = fichier if fichier.lower().endswith("." + fmt) else f"{fichier}.{fmt}"
    logger.trace("_cible: -> {}", resultat)
    return resultat


def executer(
    args: argparse.Namespace, sortie: TextIO | None = None, erreur: TextIO | None = None
) -> int | None:
    """Exécute l'import ou l'export demandé ; ``None`` si aucun (l'interface démarre).

    Args:
        args (argparse.Namespace): Arguments analysés (``config_dir`` compris).
        sortie (TextIO | None): Flux du bilan (défaut : ``sys.stdout``).
        erreur (TextIO | None): Flux des erreurs (défaut : ``sys.stderr``).

    Returns:
        int | None: 0 si réussi, :data:`CODE_ECHANGE_IMPOSSIBLE` en cas
        d'erreur (configuration jamais écrite), ``None`` sans option d'échange.
    """
    logger.debug("executer: entrée")
    action = demande(args)
    if action is None:
        logger.debug("executer: rien à faire")
        return None
    sens, fmt, fichier = action
    sortie = sortie or sys.stdout
    erreur = erreur or sys.stderr
    conf = chemins.fichier_conf(args.config_dir)
    chiffrement = ChiffrementGcm(args.config_dir)
    try:
        cfg = load_config(conf, chiffrement)
        if sens == "export":
            cible = _cible(fichier, fmt)
            hotes = cfg.store.hosts_in("", recursive=True)
            n = exporter_fichier(cible, hotes, mots_de_passe=args.include_passwords)
            print(_("Hosts exported: {count} ({file})").format(count=n, file=cible), file=sortie)
        else:
            source = fichier
            rapport = importer_fichier(cfg.store, source, CONFLITS[args.on_conflict])
            save_config(conf, cfg, chiffrement)
            print(sauvegarde.bilan(source, rapport), file=sortie)
    except (ConfigError, ExchangeError) as exc:  # ChiffrementError dérive de ConfigError
        logger.info("executer: {} {} impossible ({})", sens, fmt, exc)
        print(f"gcm4: {exc}", file=erreur)
        logger.debug("executer: sortie {}", CODE_ECHANGE_IMPOSSIBLE)
        return CODE_ECHANGE_IMPOSSIBLE
    logger.debug("executer: {} {} réussi", sens, fmt)
    return 0
