# SPDX-License-Identifier: GPL-3.0-or-later
"""Champs de protocole étendus de models.py.

Les boucles qui parcourent ``_protocol_field_defaults()`` (Host.__init__,
clone, load/save INI) ne s'exécutent que lorsque ``wMain`` et son registre
de plugins sont disponibles : ce test fournit un faux registre, en
réutilisant les stubs gi/cairo de test_gcm.py.
"""

from __future__ import annotations

import configparser

import test_gcm  # stubs gi/cairo puis importe gnome_connection_manager

import models  # noqa: E402 — les stubs gi sont déjà en place

gcm = test_gcm.gcm


class _FauxRegistre:
    """Registre de plugins qui déclare un champ étendu."""

    def all_host_fields(self):
        """Un champ de protocole additionnel, comme le ferait un plugin."""
        return [("serial_baud", "9600")]


class _FauxWMain:
    """Fenêtre principale minimale portant le registre."""

    plugin_registry = _FauxRegistre()


def test_host_avec_champs_de_protocole(monkeypatch):
    """Host absorbe les champs déclarés par le registre de plugins."""
    monkeypatch.setattr(gcm, "wMain", _FauxWMain(), raising=False)
    hote = models.Host(name="h1", tunnel="u@j:22:d:22", serial_baud="115200")
    assert hote.tunnel == ["u@j:22:d:22"]
    assert hote.serial_baud == "115200"


def test_host_clone_reprend_les_champs(monkeypatch):
    """clone() reporte les champs étendus sur la copie."""
    monkeypatch.setattr(gcm, "wMain", _FauxWMain(), raising=False)
    hote = models.Host(name="h1", serial_baud="115200")
    copie = hote.clone()
    assert copie.serial_baud == "115200"


def test_host_ini_aller_retour_avec_champs(monkeypatch):
    """Save puis load INI : champs étendus préservés."""
    monkeypatch.setattr(gcm, "wMain", _FauxWMain(), raising=False)
    hote = models.Host(name="h1", serial_baud="115200")
    cp = configparser.ConfigParser()
    cp.add_section("host h1")
    models.HostUtils.save_host_to_ini(cp, "host h1", hote)
    relu = models.HostUtils.load_host_from_ini(cp, "host h1")
    assert relu.serial_baud == "115200"
