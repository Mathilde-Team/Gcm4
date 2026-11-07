# SPDX-License-Identifier: GPL-3.0-or-later
"""Plugin outil « Import from libvirt » GTK4 (#439, portage #179).

L'onglet est piloté comme l'utilisateur : activation de l'action du menu
Fichier, scan à travers la vraie logique ``core.collecter_hotes`` (client
SSH factice répondant aux commandes ``virsh``), prévisualisation à cases
à cocher, import via le contexte du plugin (``import_hosts``) et
vérification dans ``gcm4.conf`` relu du disque. Le client SSH factice
remplace ``paramiko_connect`` du paquet partagé ``gcm4_hv``.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

import gcm4.ui  # noqa: F401  (require_version avant gi.repository)
from gcm4.core import registre
from gcm4.core.chiffrement import ChiffrementGcm
from gcm4.core.config import load_config
from gcm4.core.exchange import Conflit, dict_vers_hote
from gcm4.ui.main_window import MainWindow

RACINE = Path(__file__).resolve().parents[2]

#: XML factice de la VM linux : MAC + port SPICE.
_XML_PROD = (
    "<domain><name>vm-prod-a</name>"
    "<devices><interface><mac address='52:54:00:aa:bb:cc'/></interface>"
    "<graphics type='spice' port='5930'/></devices></domain>"
)
#: XML factice de la VM windows : MAC, pas de SPICE.
_XML_WIN = (
    "<domain><name>vm-win-b</name>"
    "<devices><interface><mac address='52:54:00:dd:ee:ff'/></interface>"
    "</devices></domain>"
)

#: Réponses du client SSH factice, par commande exacte.
REPONSES = {
    "virsh list --all --name": "vm-prod-a\nvm-win-b",
    "virsh domstate vm-prod-a": "running",
    "virsh domstate vm-win-b": "shut off",
    "virsh dumpxml vm-prod-a": _XML_PROD,
    "virsh dumpxml vm-win-b": _XML_WIN,
    "virsh net-list --name": "",
    "ip neigh show 2>/dev/null || arp -n 2>/dev/null": "",
    "ip -o -f inet addr show": "",
    "virsh domifaddr vm-prod-a --source agent 2>/dev/null": " 52:54:00:aa:bb:cc/0 ipv4 192.168.1.10/24",
    "virsh domifaddr vm-prod-a --source arp 2>/dev/null": "",
    "virsh domifaddr vm-win-b --source agent 2>/dev/null": "",
    "virsh domifaddr vm-win-b --source arp 2>/dev/null": "",
    "which nmap 2>/dev/null": "",
}


class FauxSortie:
    """Sortie de commande SSH factice (mêmes méthodes que celle de paramiko)."""

    def __init__(self, texte: str) -> None:
        """Mémorise la sortie brute de la commande."""
        self._octets = texte.encode()

    def read(self) -> bytes:
        """Rend la sortie de la commande."""
        return self._octets


class FauxClient:
    """Client SSH factice : chaque commande a sa réponse dans ``REPONSES``."""

    def __init__(self, reponses: dict[str, str]) -> None:
        """Mémorise les réponses par commande exacte."""
        self.reponses = reponses
        self.ferme = False

    def exec_command(self, cmd: str, timeout: int = 30):
        """Rend ``(None, sortie, None)`` comme paramiko."""
        return None, FauxSortie(self.reponses.get(cmd, "")), None

    def close(self) -> None:
        """Marque le client fermé (assertion de nettoyage)."""
        self.ferme = True


@pytest.fixture
def app_libvirt(tmp_path, monkeypatch, pomper):
    """Application sur la fixture aes avec le seul plugin import_libvirt."""
    import shutil

    from gi.repository import Gio

    from gcm4.core import chemins
    from gcm4.core import config as config_mod
    from gcm4.ui.application import Gcm4Application

    monkeypatch.setenv("USER", "utilisateur")
    monkeypatch.setattr(config_mod, "_sauvegardes", set())
    dossier = tmp_path / "gcm"
    shutil.copytree(RACINE / "tests" / "data" / "config" / "aes", dossier)
    chemins.preparer(str(dossier))
    candidats = [
        c for c in registre.candidats_dossier(RACINE / "plugins") if c.nom == "import_libvirt"
    ]
    app = Gcm4Application(
        _args(tmp_path),
        flags=Gio.ApplicationFlags.NON_UNIQUE,
        candidats=candidats,
    )
    app.register(None)
    app.activate()
    pomper()
    yield app, dossier
    for fenetre in list(app.get_windows()):
        fenetre.destroy()
    pomper()


def _args(tmp_path):
    import argparse

    return argparse.Namespace(config_dir=str(tmp_path / "gcm"), debug=False, hotes=[])


def _fenetre(app):
    return next(f for f in app.get_windows() if isinstance(f, MainWindow))


def _onglet(fenetre):
    """Onglet du plugin (dernier onglet ouvert)."""
    import gcm4_plugin_import_libvirt.gtk4 as plugin  # noqa: PLC0415

    onglet = fenetre.onglets.get_nth_page(fenetre.onglets.get_n_pages() - 1)
    assert isinstance(onglet, plugin.OngletImportLibvirt)
    return onglet


def _ouvrir(app, pomper):
    """Ouvre l'onglet d'import libvirt et le rend."""
    fenetre = _fenetre(app)
    fenetre.activate_action("win.outil-import-libvirt", None)
    pomper()
    return fenetre, _onglet(fenetre)


def _attendre_resultats(onglet, pomper):
    """Pompe la boucle GTK jusqu'à l'affichage des résultats (scan en thread)."""
    for _ in range(300):
        pomper()
        if onglet._stack.get_visible_child_name() == "results":
            return True
        time.sleep(0.02)
    return False


def _relire(dossier):
    return load_config(str(dossier / "gcm4.conf"), ChiffrementGcm(str(dossier)))


def _brancher_client_faux(monkeypatch):
    """Remplace ``paramiko_connect`` du paquet partagé par le client factice."""
    import gcm4_plugin_import_libvirt.core as plugin_core  # noqa: PLC0415

    client = FauxClient(REPONSES)

    def _connecter(hostname, port, username, log_fn=None):
        return client

    monkeypatch.setattr(plugin_core.hv_common, "paramiko_connect", _connecter)
    return client


def test_entree_de_menu_et_onglet(app_libvirt, pomper):
    """L'outil est dans le menu Fichier (section import) et ouvre son onglet."""
    app, _dossier = app_libvirt
    fenetre = _fenetre(app)
    libelles = []
    modele = fenetre.bouton_menu.get_menu_model()
    for i in range(modele.get_n_items()):
        titre = modele.get_item_attribute_value(i, "label", None).get_string()
        if titre != "File":
            continue
        sous_menu = modele.get_item_link(i, "submenu")
        for j in range(sous_menu.get_n_items()):
            section = sous_menu.get_item_link(j, "section")
            if section is None:
                continue
            for k in range(section.get_n_items()):
                ligne = section.get_item_attribute_value(k, "label", None)
                if ligne is not None:
                    libelles.append(ligne.get_string())
    assert "Import from libvirt" in libelles
    fenetre.activate_action("win.outil-import-libvirt", None)
    pomper()
    onglet = _onglet(fenetre)
    assert fenetre.titres_onglets[onglet] == "Import from libvirt"
    assert onglet._stack.get_visible_child_name() == "config"
    assert not onglet.bouton_importer.get_sensitive()
    assert onglet.conflit_choisi() == Conflit.IGNORER


def test_scan_collecte_et_import(app_libvirt, pomper, monkeypatch):
    """Scan via le vrai ``collecter_hotes`` (client SSH factice) puis import."""
    app, dossier = app_libvirt
    client = _brancher_client_faux(monkeypatch)
    fenetre, onglet = _ouvrir(app, pomper)
    onglet.zone_cibles.definir(["qemu+ssh://root@hv1.example/system"])
    onglet._on_scan(None)
    assert _attendre_resultats(onglet, pomper)
    assert client.ferme

    # vm-prod-a : SSH (IP résolue par domifaddr) + SPICE (port 5930) ;
    # vm-win-b : SSH via l'hyperviseur (IP inconnue, shut off, Windows).
    decouvertes = onglet.decouvertes
    assert len(decouvertes) == 3
    ssh_prod = next(d for d in decouvertes if d["protocol"] == "ssh" and d["name"] == "prod-a")
    assert ssh_prod["host"] == "192.168.1.10"
    assert ssh_prod["extra_params"] == "-J root@hv1.example"
    assert ssh_prod["group"] == "VM/ssh"
    spice = next(d for d in decouvertes if d["protocol"] == "spice")
    assert spice["port"] == 5930
    ssh_win = next(d for d in decouvertes if d["protocol"] == "ssh" and d["name"] == "win-b")
    assert ssh_win["user"] == "root"  # SSH vers l'hyperviseur, VM jointe par -t
    assert ssh_win["host"] == "hv1.example"
    assert ssh_win["extra_params"] == "-t ssh Administrator@vm-win-b"

    # état extrait de la description (« [running] … »)
    assert next(d for d in decouvertes if d["name"] == "prod-a")["state"] == "running"
    assert "Connections found: 3." in onglet.etiquette_resume.get_label()

    # décoche la ligne SPICE : seules les deux SSH sont importées
    onglet.vue.tout_cocher(True)
    for element in onglet.vue._elements():
        if element.valeur.get("protocol") == "spice":
            element.coche = False
    onglet.vue.modele.splice(0, 3, onglet.vue._elements())
    onglet._on_importer(None)
    pomper()
    noms = {h.name for h in fenetre.config.store.hosts}
    assert {"prod-a", "win-b"} <= noms
    relu = _relire(dossier)
    assert {"prod-a", "win-b"} <= {h.name for h in relu.store.hosts}
    assert not onglet.bouton_importer.get_sensitive()


def test_scan_conflit_renommer(app_libvirt, pomper, monkeypatch):
    """Un hôte homonyme existe : RENOMMER l'importe en « nom (2) »."""
    app, _dossier = app_libvirt
    fenetre = _fenetre(app)
    fenetre.config.store.hosts.append(
        dict_vers_hote(
            {"name": "prod-a", "host": "ailleurs", "protocol": "ssh", "group": "VM/ssh"}, ou="test"
        )
    )
    _brancher_client_faux(monkeypatch)
    fenetre, onglet = _ouvrir(app, pomper)
    onglet.zone_cibles.definir(["qemu+ssh://root@hv1.example/system"])
    onglet.boutons_conflit[Conflit.RENOMMER].set_active(True)
    onglet._on_scan(None)
    assert _attendre_resultats(onglet, pomper)
    onglet._on_importer(None)
    pomper()
    noms = [h.name for h in fenetre.config.store.hosts]
    # « prod-a » : l'existant (VM/ssh) + le SPICE (VM/spice) ; le SSH homonyme est renommé
    assert noms.count("prod-a") == 2 and "prod-a (2)" in noms


def test_zone_cibles_ajout_doublon_et_invalide(app_libvirt, pomper, monkeypatch):
    """Saisie manuelle : normalisation, doublon refusé, saisie vide refusée."""
    app, _dossier = app_libvirt
    _fenetre, onglet = _ouvrir(app, pomper)
    onglet.zone_cibles.definir([])
    messages: list[str] = []

    def _log(message):
        messages.append(message)

    monkeypatch.setattr(onglet, "log", _log)
    monkeypatch.setattr(onglet.zone_cibles, "log_fn", _log)
    onglet.zone_cibles.saisie.set_text("root@192.168.0.5:2222")
    onglet.zone_cibles.ajouter()
    assert onglet.zone_cibles.cibles() == ["qemu+ssh://root@192.168.0.5:2222/system"]
    assert any("Target added" in m for m in messages)

    onglet.zone_cibles.saisie.set_text("root@192.168.0.5:2222")
    onglet.zone_cibles.ajouter()
    assert len(onglet.zone_cibles.cibles()) == 1
    assert any("already present" in m for m in messages)

    onglet.zone_cibles.saisie.set_text("   ")
    onglet.zone_cibles.ajouter()
    assert any("Invalid libvirt target" in m for m in messages)


def test_scan_gardes_sans_cible_ni_protocole(app_libvirt, pomper, monkeypatch):
    """Sans cible cochée ou sans protocole coché, le scan ne démarre pas."""
    app, _dossier = app_libvirt
    _fenetre, onglet = _ouvrir(app, pomper)
    messages: list[str] = []

    def _log(message):
        messages.append(message)

    monkeypatch.setattr(onglet, "log", _log)
    onglet.zone_cibles.definir(["qemu+ssh://root@hv1.example/system"])
    onglet.zone_cibles.vue.tout_cocher(False)
    onglet._on_scan(None)
    pomper()
    assert any("No URI selected" in m for m in messages)
    assert onglet._stack.get_visible_child_name() == "config"

    for case in onglet.cases_protocole.values():
        case.set_active(False)
    onglet.zone_cibles.vue.tout_cocher(True)
    onglet._on_scan(None)
    pomper()
    assert any("No connection type selected" in m for m in messages)
    assert onglet._stack.get_visible_child_name() == "config"


def test_echec_de_scan_notifie_et_restaure(app_libvirt, pomper, monkeypatch):
    """Une exception dans le scan est journalisée : aucun résultat, onglet vivant."""
    app, _dossier = app_libvirt
    fenetre, onglet = _ouvrir(app, pomper)
    messages: list[str] = []
    fenetre.notifier = messages.append

    def _plante(*_args, **_kwargs):
        raise RuntimeError("connexion refusée")

    import gcm4_plugin_import_libvirt.core as plugin_core  # noqa: PLC0415

    monkeypatch.setattr(plugin_core, "collecter_hotes", _plante)
    onglet.zone_cibles.definir(["qemu+ssh://root@hv1.example/system"])
    onglet._on_scan(None)
    assert _attendre_resultats(onglet, pomper)
    assert onglet.decouvertes == []
    assert "Connections found: 0." in onglet.etiquette_resume.get_label()
    assert not onglet.bouton_importer.get_sensitive()
    assert onglet.bouton_scan.get_sensitive()
    journal = onglet._journal_tampon.get_text(
        onglet._journal_tampon.get_start_iter(), onglet._journal_tampon.get_end_iter(), False
    )
    assert "Scan failed" in journal


def test_import_hosts_sans_configuration(application, tmp_path, pomper):
    """Le service du contexte refuse l'import sans configuration ouverte (#436)."""
    from gcm4.ui.contexte import ContexteApplication

    (tmp_path / "illisible").mkdir()
    (tmp_path / "illisible" / "gcm4.conf").write_text("[options\n", encoding="utf-8")
    fenetre = MainWindow(application, str(tmp_path / "illisible"))
    contexte = ContexteApplication(application, "import-libvirt")
    with pytest.raises(Exception, match="open"):
        contexte.import_hosts(
            [dict_vers_hote({"name": "x", "host": "h", "protocol": "ssh"}, ou="test")]
        )
    fenetre.destroy()
    pomper()


def test_import_sans_selection_et_echec_notifies(app_libvirt, pomper, monkeypatch):
    """Import sans case cochée : notification ; échec de conversion : notifié."""
    app, _dossier = app_libvirt
    fenetre = _fenetre(app)
    notifie: list[str] = []
    monkeypatch.setattr(fenetre, "notifier", lambda message, *a, **k: notifie.append(str(message)))
    _brancher_client_faux(monkeypatch)
    fenetre, onglet = _ouvrir(app, pomper)
    monkeypatch.setattr(fenetre, "notifier", lambda message, *a, **k: notifie.append(str(message)))
    onglet.zone_cibles.definir(["qemu+ssh://root@hv1.example/system"])
    onglet._on_scan(None)
    assert _attendre_resultats(onglet, pomper)

    # Rien de coché : notification explicite, rien d'importé
    onglet.vue.tout_cocher(False)
    onglet._on_importer(None)
    pomper()
    assert any("No connection to import" in n for n in notifie)

    # Conversion impossible : l'échec est notifié, l'onglet reste utilisable
    import gcm4_hv.ui as hv_ui  # noqa: PLC0415

    def _explose(d, ou=""):
        raise RuntimeError("conversion impossible")

    monkeypatch.setattr(hv_ui, "dict_vers_hote", _explose)
    onglet.vue.tout_cocher(True)
    onglet._on_importer(None)
    pomper()
    assert any("Scan failed" in n for n in notifie)
