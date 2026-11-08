# SPDX-License-Identifier: GPL-3.0-or-later
"""Collecte séparée : le script Wayland exige le vrai backend, jamais un saut."""

import os

if os.environ.get("GCM_TESTS_WAYLAND") != "1":
    collect_ignore_glob = ["test_*.py"]
