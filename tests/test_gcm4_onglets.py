# SPDX-License-Identifier: GPL-3.0-or-later
"""Onglets ouverts : format [window] open-tabs partagé avec GTK3 (#246)."""

from __future__ import annotations

from types import SimpleNamespace

import gcm4_core
from gcm4.core import onglets


def test_serialiser_ordre_doublons_et_virgule():
    """Ordre gardé, doublons et entrées vides retirés, chemin à virgule ignoré."""
    chemins = ["LAB/web", " PROD/eu/api ", "LAB/web", "", "LAB/a,b", "racine"]
    assert onglets.serialiser(chemins) == "LAB/web,PROD/eu/api,racine"
    assert onglets.serialiser([]) == ""


def test_analyser():
    """Valeur INI → chemins, sans vide ni doublon."""
    assert onglets.analyser(" LAB/web, ,PROD/eu/api,LAB/web,") == ["LAB/web", "PROD/eu/api"]
    assert onglets.analyser("") == [] and onglets.analyser(None) == []


def test_compatible_gtk3():
    """GTK3 relit ce que gcm4 écrit, et inversement (découpe au dernier /)."""
    ecrit = onglets.serialiser(["PROD/eu/api", "LAB/bases/db"])
    assert gcm4_core.parse_open_tabs(ecrit) == onglets.analyser(ecrit)
    hotes = {
        "PROD/eu": [SimpleNamespace(group="PROD/eu", name="api")],
        "LAB/bases": [SimpleNamespace(group="LAB/bases", name="db")],
    }
    relus = gcm4_core.resolve_host_specs(gcm4_core.parse_open_tabs(ecrit), hotes)
    assert [f"{h.group}/{h.name}" for h in relus] == ["PROD/eu/api", "LAB/bases/db"]
    gtk3 = ",".join(gcm4_core.serialize_open_tabs(relus))
    assert onglets.analyser(gtk3) == ["PROD/eu/api", "LAB/bases/db"]


def test_lire(tmp_path):
    """Fichier absent ou sans clé : liste vide ; sinon la liste enregistrée."""
    conf = tmp_path / "gcm4.conf"
    assert onglets.lire(str(conf)) == []
    conf.write_text("[window]\nshow-panel = True\n", encoding="utf-8")
    assert onglets.lire(str(conf)) == []
    conf.write_text("[window]\nopen-tabs = LAB/web,PROD/eu/api\n", encoding="utf-8")
    assert onglets.lire(str(conf)) == ["LAB/web", "PROD/eu/api"]
