# SPDX-License-Identifier: GPL-3.0-or-later
"""Doublure SSH des imports hyperviseurs (#293, #383, #439).

``Hyperviseur`` remplace les accès SSH du socle partagé
(``gcm4_hv``, ex-``hypervisor_import_common`` : connexion paramiko,
exécution de commande, scan nmap, sondage de port) et rejoue des sorties
de commandes : les fonctions de collecte des plugins d'import tournent de
bout en bout sans réseau ni processus externe.

Le socle est chargé sous la même identité ``gcm4_hv`` que celle des
plugins (``partage()`` de ``plugins/import_libvirt/core.py``) : les
retouches ``monkeypatch`` sont vues par les collectes.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _charger_hv():
    """Charge ``plugins/import_hyperviseurs`` en paquet ``gcm4_hv`` (identité stable)."""
    if "gcm4_hv" not in sys.modules:
        dossier = Path(__file__).resolve().parent.parent / "plugins" / "import_hyperviseurs"
        spec = importlib.util.spec_from_file_location(
            "gcm4_hv", dossier / "__init__.py", submodule_search_locations=[str(dossier)]
        )
        if spec is None or spec.loader is None:  # pragma: no cover — chemin interne
            raise ImportError(str(dossier))
        module = importlib.util.module_from_spec(spec)
        sys.modules["gcm4_hv"] = module
        spec.loader.exec_module(module)
    return importlib.import_module("gcm4_hv.core")


hv = _charger_hv()


class ClientFaux:
    """Client SSH : seul ``close`` est appelé directement par la collecte."""

    def __init__(self):
        """Client ouvert."""
        self.ferme = False

    def close(self):
        """Ferme le client (vérifié par les tests)."""
        self.ferme = True


class Hyperviseur:
    """Rejoue les sorties de commandes ; la première clé contenue gagne."""

    def __init__(self, sorties, ports_ouverts=(), nmap=None, connexion=True):
        """Sorties par motif de commande, ports ouverts, table nmap."""
        self.sorties = sorties
        self.ports_ouverts = set(ports_ouverts)
        self.nmap = nmap or {}
        self.connexion = connexion
        self.commandes: list[str] = []
        self.clients: list[ClientFaux] = []
        self.connexions: list[tuple] = []

    def installer(self, monkeypatch):
        """Remplace les accès SSH du socle partagé ``gcm4_hv``."""
        monkeypatch.setattr(hv, "PARAMIKO_OK", True)
        monkeypatch.setattr(hv, "paramiko_connect", self.connecter)
        monkeypatch.setattr(hv, "libvirt_ssh_run", self.executer)
        monkeypatch.setattr(hv, "libvirt_nmap_scan", lambda client, run, log: self.nmap)
        monkeypatch.setattr(
            hv, "check_port_open", lambda client, run, ip, port: (ip, port) in self.ports_ouverts
        )

    def connecter(self, hote, port, utilisateur, log_fn):
        """Doublure de paramiko_connect : None si connexion refusée."""
        self.connexions.append((hote, port, utilisateur))
        if not self.connexion:
            return None
        client = ClientFaux()
        self.clients.append(client)
        return client

    def executer(self, client, commande, timeout=30):
        """Doublure de libvirt_ssh_run : sortie du premier motif contenu."""
        self.commandes.append(commande)
        for motif, sortie in self.sorties.items():
            if motif in commande:
                return sortie
        return ""
