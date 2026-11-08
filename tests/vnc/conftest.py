# SPDX-License-Identifier: GPL-3.0-or-later
"""Suite VNC native du job GTK4 requis, avec les dépendances du composant.

Lancée explicitement avec GCM_TESTS_VNC=1 : une dépendance absente est
alors une erreur de collecte, jamais un saut. La suite tests/gtk4 garde
les tests du contrat v1 sans dépendance VNC, également exécutés sur les
anciennes distributions où open() doit refuser l'absence de boucle native.
"""

import os

if os.environ.get("GCM_TESTS_VNC") != "1":
    collect_ignore_glob = ["test_*.py"]
