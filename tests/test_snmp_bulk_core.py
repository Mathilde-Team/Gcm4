# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests unitaires pour snmp_bulk_core.py (cœur métier sans GTK).

Ce module n'a jamais dépendance à GTK/VTE (voir claude.md « Conventions du
projet ») ; il n'a en revanche pas de binding SNMP réel disponible dans cet
environnement de travail (``ezsnmp``/``easysnmp`` sont des extensions C liées
à libnetsnmp, non installées ici). On stub donc juste ``ezsnmp`` avant import
— uniquement ce dont ``snmp_bulk_core.py`` a besoin au niveau module (classe
``Session`` et les 4 exceptions), sans toucher au reste de l'environnement.

Couvre en particulier la régression #80-bis découverte le 2026-08-30 :
``SNMP_VENDORS`` (utilisé pour peupler le menu déroulant constructeur dans
``plugin_snmp_push.py``) proposait ``huawei_vrp`` sans qu'aucun driver ne soit
enregistré dans ``DRIVERS`` — un push Huawei échouait donc systématiquement
avec « Constructeur inconnu: huawei_vrp ». Le test
``test_every_declared_vendor_has_a_driver`` verrouille cet invariant pour
tout futur ajout de constructeur.
"""

import os
import sys
import types
import unittest


def _make_ezsnmp_stub():
    """Crée un stub minimal de ``ezsnmp``/``ezsnmp.exceptions``.

    Suffisant pour l'import de ``snmp_bulk_core.py`` (qui n'utilise que la
    classe ``Session`` et les 4 exceptions au niveau module) — aucune session
    SNMP réelle n'est ouverte par les tests de ce fichier.
    """
    ezsnmp = types.ModuleType("ezsnmp")
    ezsnmp.Session = object

    exceptions = types.ModuleType("ezsnmp.exceptions")

    class EasySNMPError(Exception):
        pass

    class EasySNMPNoSuchInstanceError(EasySNMPError):
        pass

    class EasySNMPNoSuchObjectError(EasySNMPError):
        pass

    class EasySNMPTimeoutError(EasySNMPError):
        pass

    exceptions.EasySNMPError = EasySNMPError
    exceptions.EasySNMPNoSuchInstanceError = EasySNMPNoSuchInstanceError
    exceptions.EasySNMPNoSuchObjectError = EasySNMPNoSuchObjectError
    exceptions.EasySNMPTimeoutError = EasySNMPTimeoutError
    ezsnmp.exceptions = exceptions
    return ezsnmp, exceptions


_ezsnmp, _ezsnmp_exceptions = _make_ezsnmp_stub()
sys.modules.setdefault("ezsnmp", _ezsnmp)
sys.modules.setdefault("ezsnmp.exceptions", _ezsnmp_exceptions)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import snmp_bulk_core as snmp_core  # noqa: E402


class TestDriverRegistryCompleteness(unittest.TestCase):
    """Chaque constructeur affiché dans l'UI doit avoir un driver enregistré."""

    def test_every_declared_vendor_has_a_driver(self):
        """Aucun vendor de SNMP_VENDORS ne doit manquer dans DRIVERS."""
        missing = set(snmp_core.SNMP_VENDORS) - set(snmp_core.DRIVERS)
        self.assertEqual(missing, set(), f"Vendeur(s) déclaré(s) sans driver : {missing}")

    def test_huawei_vrp_is_registered(self):
        """huawei_vrp doit résoudre vers HuaweiVrpDriver (régression #80-bis)."""
        self.assertIn("huawei_vrp", snmp_core.DRIVERS)
        self.assertIs(snmp_core.DRIVERS["huawei_vrp"], snmp_core.HuaweiVrpDriver)

    def test_no_orphan_driver(self):
        """Un driver enregistré sans entrée SNMP_VENDORS serait invisible dans l'UI."""
        orphans = set(snmp_core.DRIVERS) - set(snmp_core.SNMP_VENDORS)
        self.assertEqual(orphans, set(), f"Driver(s) sans entrée SNMP_VENDORS : {orphans}")


class TestPushToDeviceUnknownVendor(unittest.TestCase):
    """push_to_device doit rejeter proprement un vendor inconnu."""

    def test_unknown_vendor_is_rejected(self):
        """Un vendor absent de DRIVERS renvoie STATUS_REJECTED, sans exception."""
        row = snmp_core.InventoryRow(line_no=1, ip="10.0.0.1", profile_name="test")
        profile = snmp_core.SnmpProfile(
            name="test",
            vendor="constructeur_inexistant",
            auth=snmp_core.SnmpAuth(version="v2c", community="public"),
            port=161,
        )
        result = snmp_core.push_to_device(
            row, profile, "config", __import__("pathlib").Path("/tmp")
        )
        self.assertEqual(result.status, snmp_core.STATUS_REJECTED)


if __name__ == "__main__":
    unittest.main()
