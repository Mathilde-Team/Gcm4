# SPDX-License-Identifier: GPL-3.0-or-later
"""VNC sans dépendance cliente : contrat et refus explicite sur anciennes distributions."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from gcm4.core import registre
from gcm4.core.model import Host

RACINE = Path(__file__).resolve().parents[2]


@pytest.fixture
def adaptateur_vnc():
    """Charge le candidat natif du registre sans importer le composant VNC."""
    candidat = next(
        c for c in registre.candidats_dossier(RACINE / "plugins") if c.nom == "pluginvnc2"
    )
    classe = candidat.charger()
    import sys

    yield sys.modules[classe.__module__]
    registre._oublier_paquet("gcm4_plugin_pluginvnc2")


def test_options_sans_dependance_vnc(adaptateur_vnc):
    """Le vrai formulaire fonctionne sans AsyncVnc4 ni NumPy, y compris sur Rocky 9."""
    plugin = adaptateur_vnc.PLUGIN(SimpleNamespace())
    assert plugin.name == "vnc" and plugin.protocols == ("vnc",)
    assert plugin.default_port("vnc") == 5900
    options = plugin.default_options("vnc")
    changements = []
    host = Host(name="bureau", host="localhost", protocol="vnc")
    zone = plugin.build_host_options_widget(host, options, lambda: changements.append(True))
    zone.controles["vnc_shared"].set_active(False)
    assert options["vnc_shared"] == "False"
    zone.controles["vnc_display"].set_text("-1")
    assert plugin.validate_options("vnc", options)[0].champ == "vnc_display"
    assert len(changements) >= 2


def test_absence_boucle_native_refusee(adaptateur_vnc, monkeypatch):
    """La disponibilité réelle est vérifiée sur les anciennes distributions, sans saut."""
    plugin = adaptateur_vnc.PLUGIN(SimpleNamespace())
    host = Host(name="bureau", host="localhost", protocol="vnc")
    # Sur Debian 12/Rocky 9 ce chemin utilise le vrai détecteur. Sur le
    # runner récent, on simule aussi cette indisponibilité pour le garder testé.
    if adaptateur_vnc.boucle.asyncio_disponible():
        monkeypatch.setattr(adaptateur_vnc.boucle, "asyncio_disponible", lambda: False)
    with pytest.raises(RuntimeError, match="PyGObject 3.50"):
        plugin.open(host)
