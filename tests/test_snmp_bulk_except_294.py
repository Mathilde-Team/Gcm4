# SPDX-License-Identifier: GPL-3.0-or-later
"""``except`` journalisés de ``snmp_bulk_core.py`` (issue #294, lot 2).

Mêmes replis qu'avant (``(False, message)``, journal de poussée non écrit),
mais chacun laisse une trace qui nomme l'hôte et la cause. Le module est
importé avec le stub ``ezsnmp`` de ``tests/test_snmp_bulk_core.py`` ; on
intercepte les méthodes du logger du module (pas de puits loguru ajouté).
"""

from __future__ import annotations

import pytest

from tests.test_snmp_bulk_core import snmp_core


@pytest.fixture
def journal(monkeypatch):
    """Messages journalisés par snmp_bulk_core : (niveau, texte)."""
    recus = []
    for niveau in ("debug", "warning"):
        monkeypatch.setattr(
            snmp_core.logger,
            niveau,
            lambda msg, *args, _n=niveau, **_kw: recus.append((_n, msg.format(*args))),
        )
    return recus


class _SessionEnPanne:
    def set_multiple(self, _oid_values):
        raise snmp_core.EasySNMPTimeoutError("pas de réponse")

    def get(self, _oid):
        raise snmp_core.EasySNMPNoSuchObjectError("objet absent")


def _driver(monkeypatch):
    driver = snmp_core.H3cComwareDriver(auth=None)
    monkeypatch.setattr(driver, "_session", lambda _host: _SessionEnPanne())
    return driver


def test_failed_set_is_a_warning_naming_the_host(monkeypatch, journal):
    """SET en échec : même retour, avertissement avec hôte et cause."""
    assert _driver(monkeypatch)._set_multiple("192.0.2.7", []) == (False, "pas de réponse")
    assert ("warning", "SNMP SET vers 192.0.2.7 échoué : pas de réponse") in journal


def test_failed_get_is_traced(monkeypatch, journal):
    """GET en échec : même retour, trace debug avec OID, hôte et cause."""
    assert _driver(monkeypatch)._get("192.0.2.7", "1.3.6.1") == (False, "objet absent")
    assert ("debug", "SNMP GET 1.3.6.1 sur 192.0.2.7 échoué : objet absent") in journal


def test_unwritable_push_log_is_a_warning(tmp_path, monkeypatch, journal):
    """Journal de poussée impossible à écrire : résultat inchangé, avertissement."""

    class _DriverOk:
        def __init__(self, **_kw):
            pass

        def push(self, _ip, _server, _filename):
            return True, "succès"

    monkeypatch.setitem(snmp_core.DRIVERS, "faux", _DriverOk)
    log_dir = tmp_path / "journaux"
    log_dir.mkdir()
    (log_dir / "3_192.0.2.9.log").mkdir()  # un répertoire à la place du fichier
    profil = snmp_core.SnmpProfile(name="p", vendor="faux", auth=None)
    ligne = snmp_core.InventoryRow(line_no=3, ip="192.0.2.9", profile_name="p")
    resultat = snmp_core.push_to_device(ligne, profil, "conf", log_dir)
    assert resultat.status == snmp_core.STATUS_OK
    assert any(
        n == "warning" and m.startswith(f"journal de poussée {log_dir}") for n, m in journal
    )
