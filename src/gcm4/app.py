"""Point d'entrée ``gcm4`` (``[project.scripts]``, #133).

Ordre imposé : ligne de commande et journalisation d'abord, GTK ensuite.
Si PyGObject, GTK4 ou libadwaita manquent, l'utilisateur reçoit la
commande d'installation de sa distribution, sans trace Python, et un code
de sortie non nul.
"""

from __future__ import annotations

import os
import sys

from loguru import logger

import gcm4_core
import logging_config
from gcm4.core import chemins, cli, echange_cli, i18n, preferences
from gcm4.core.i18n import _

# Paquets système de PyGObject, GTK4 et libadwaita (D1 de #118).
# Fedora : le typelib cairo-1.0, exigé par celui de GTK 4, est dans
# gobject-introspection, que ni gtk4 ni python3-gobject n'installent
# (constaté sur Fedora 44, #157).
PAQUETS_GTK = {
    "debian": "python3-gi gir1.2-gtk-4.0 gir1.2-adw-1",
    "fedora": "python3-gobject gobject-introspection gtk4 libadwaita",
    "arch": "python-gobject gtk4 libadwaita",
}
CODE_GTK_ABSENT = 3
CODE_MIGRATION_IMPOSSIBLE = 4


def main(argv: list[str] | None = None) -> int:
    """Lance gcm4 ; renvoie le code de sortie du processus."""
    logger.debug("main: entrée")  # muet : gcm4 est désactivé jusqu'à configure_logging
    # Journalisation configurée avant tout : niveau de GCM_LOG_LEVEL /
    # GCM_DEBUG, sans quoi loguru écrirait du DEBUG sur stderr par défaut.
    logging_config.configure_logging()
    argv = sys.argv[1:] if argv is None else argv
    args = cli.parse_args(argv)
    i18n.install()
    # Avant le journal permanent (config_dir/log) : il créerait le dossier et
    # bloquerait la copie de ~/.gcm (#99).
    try:
        chemins.preparer(args.config_dir, args.ancien_dossier)
    except chemins.MigrationError as exc:
        logger.error("main: migration de la configuration impossible ({})", exc)
        print(
            _("gcm4: unable to copy the configuration {old} to {new} ({error}).").format(
                old=args.ancien_dossier, new=args.config_dir, error=exc
            ),
            _(
                "The old configuration is intact: fix the problem (permissions, disk space), then start gcm4 again."
            ),
            sep="\n",
            file=sys.stderr,
        )
        logger.debug("main: sortie {}", CODE_MIGRATION_IMPOSSIBLE)
        return CODE_MIGRATION_IMPOSSIBLE
    if args.debug:
        logging_config.enable_debug(config_dir=args.config_dir)
    else:
        # Préférences > Journalisation (#148) ; GCM_LOG_LEVEL / GCM_DEBUG priment.
        preference = preferences.load_preferences(chemins.fichier_conf(args.config_dir))
        niveau, _source = preferences.niveau_journal(False, preference.log_level, os.environ)
        logging_config.configure_logging(niveau or None, config_dir=args.config_dir, force=True)
    # Import/export sans fenêtre (#154) : avant GTK, scriptable sans display.
    code_echange = echange_cli.executer(args)
    if code_echange is not None:
        logger.debug("main: sortie {} (import/export)", code_echange)
        return code_echange
    try:
        from gcm4.ui import application
    except (ImportError, ValueError) as exc:
        logger.warning("main: interface GTK4 indisponible ({})", exc)
        conseil = gcm4_core.dep_install_hint(
            PAQUETS_GTK["debian"], PAQUETS_GTK["fedora"], PAQUETS_GTK["arch"]
        )
        print(
            f"gcm4 : GTK4, libadwaita ou PyGObject est introuvable ({exc}).\n"
            f"Installez-les avec : {conseil}",
            file=sys.stderr,
        )
        logger.debug("main: sortie {} (GTK absent)", CODE_GTK_ABSENT)
        return CODE_GTK_ABSENT
    code = application.run(args, argv)
    logger.debug("main: sortie {}", code)
    return code
