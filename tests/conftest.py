# SPDX-License-Identifier: GPL-3.0-or-later
"""Configuration commune des tests.

Locale neutre : les messages testés sont les chaînes source (anglais). Sans ce
réglage, un runner en locale française (LANGUAGE/LANG=fr_FR) applique le
catalogue lang/fr_FR et les assertions sur « Import failed », « corrupted »…
échouent selon la machine qui exécute la CI. ``LANGUAGE=C`` désactive les
traductions pour Python (gettext lit LANGUAGE en premier) comme pour GNU gettext.
Les tests de traduction gardent la main : ils posent leur propre LANGUAGE avec
``monkeypatch.setenv`` ou dans l'environnement du sous-processus.

Dépendances externes (#160, ``tests/exigences.py``) : un test qui a besoin de gettext (``msgfmt``)
porte le marqueur ``requires_gettext`` ; les tests de ``tests/gtk4``
reçoivent ``requires_gtk4``. Un job qui installe ces dépendances les
déclare dans ``GCM_TESTS_EXIGES`` (``gettext``, ``gtk4``, séparés par des
virgules ; ``GCM_TESTS_GTK4=1`` vaut ``gtk4``) : un test marqué qui y est
sauté devient alors un échec. Ailleurs, le saut reste possible et
``-rs`` en donne la raison.
"""

import os

from exigences import pytest_collection_modifyitems, pytest_runtest_makereport

__all__ = ["pytest_collection_modifyitems", "pytest_runtest_makereport"]

os.environ["LANGUAGE"] = "C"
