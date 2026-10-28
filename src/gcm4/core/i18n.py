# SPDX-License-Identifier: GPL-3.0-or-later
"""Traductions de gcm4 : domaine gettext ``gcm-lang`` de l'application GTK3.

Les libellés repris de l'ancienne interface gardent leurs msgids anglais,
déjà traduits dans les 29 langues de ``lang/``. La langue suit
``LANGUAGE``/``LC_ALL``/``LC_MESSAGES``/``LANG`` (règle de gettext).
"""

from __future__ import annotations

import gettext
import sys
from pathlib import Path

from loguru import logger

DOMAIN = "gcm-lang"
_translation: gettext.NullTranslations = gettext.NullTranslations()


def locale_dirs() -> list[Path]:
    """Dossiers de catalogues candidats : ``lang/`` du dépôt, puis ``<prefix>/share/locale``."""
    logger.debug("locale_dirs: entrée")
    candidats = [
        Path(__file__).resolve().parents[3] / "lang",
        Path(sys.prefix) / "share" / "locale",
    ]
    trouves = [d for d in candidats if d.is_dir()]
    logger.debug("locale_dirs: {}", trouves)
    return trouves


def install(locale_dir: Path | None = None) -> gettext.NullTranslations:
    """Charge le catalogue ``gcm-lang`` (anglais d'origine si aucun n'est trouvé)."""
    logger.debug("install: locale_dir={}", locale_dir)
    global _translation
    dossiers = [locale_dir] if locale_dir else locale_dirs()
    _translation = gettext.NullTranslations()
    for dossier in dossiers:
        trouve = gettext.translation(DOMAIN, str(dossier), fallback=True)
        if type(trouve) is not gettext.NullTranslations:  # catalogue réellement trouvé
            _translation = trouve
            break
    logger.debug("install: catalogue {}", type(_translation).__name__)
    return _translation


def _(message: str) -> str:
    """Traduit ``message`` (msgid anglais) avec le catalogue chargé."""
    logger.trace("_: {!r}", message)
    traduit = _translation.gettext(message)
    logger.trace("_: -> {!r}", traduit)
    return traduit


def N_(message: str) -> str:  # noqa: N802  (nom gettext standard, reconnu par xgettext)
    """Marque ``message`` pour l'extraction sans le traduire (traduit plus tard par ``_``)."""
    logger.trace("N_: {!r}", message)
    return message
