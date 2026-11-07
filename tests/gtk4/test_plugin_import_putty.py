# SPDX-License-Identifier: GPL-3.0-or-later
"""Plugin outil « Import from PuTTY sessions » GTK4 (#436, portage #179).

L'onglet du plugin est piloté comme l'utilisateur : activation de l'action
du menu Fichier, scan d'un vrai dossier de sessions PuTTY factices, choix
de la stratégie de conflit, import via le contexte du plugin (service
``import_hosts``) et vérification dans ``gcm4.conf`` relu du disque. Le
sélecteur de dossier n'est pas ouvert : le scan est déclenché par son
rappel directement.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import gcm4.ui  # noqa: F401  (require_version avant gi.repository)
from gcm4.core import registre
from gcm4.core.chiffrement import ChiffrementGcm
from gcm4.core.config import ConfigError, load_config
from gcm4.core.exchange import Conflit, ExchangeError, dict_vers_hote
from gcm4.ui.main_window import MainWindow

RACINE = Path(__file__).resolve().parents[2]


def _fenetre(app):
    return next(f for f in app.get_windows() if isinstance(f, MainWindow))


@pytest.fixture
def app_putty(tmp_path, monkeypatch, pomper):
    """Application sur la fixture aes avec le seul plugin import_putty."""
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
        c for c in registre.candidats_dossier(RACINE / "plugins") if c.nom == "import_putty"
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


def _sessions(dossier: Path) -> Path:
    """Dossier de sessions PuTTY factices : deux SSH, une Raw ignorée, Default Settings."""
    dossier.mkdir(parents=True, exist_ok=True)
    (dossier / "Default%20Settings").write_text("Protocol=ssh\nHostName=perdu\n")
    (dossier / "srv-a").write_text(
        "Protocol=ssh\nHostName=s1.example\nUserName=alice\nPortNumber=2222\n"
    )
    (dossier / "telnet-b").write_text("Protocol=telnet\nHostName=t2.example\n")
    (dossier / "brute").write_text("Protocol=raw\nHostName=inconnu\n")
    return dossier


def _libelles_fichier(fenetre):
    """Libellés des entrées du menu Fichier (sections comprises)."""
    modele = fenetre.bouton_menu.get_menu_model()
    resultat = []
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
                    resultat.append(ligne.get_string())
    return resultat


def _onglet(fenetre):
    """Onglet du plugin (dernier onglet ouvert)."""
    import gcm4_plugin_import_putty.gtk4 as plugin  # noqa: PLC0415

    onglet = fenetre.onglets.get_nth_page(fenetre.onglets.get_n_pages() - 1)
    assert isinstance(onglet, plugin.OngletImportPutty)
    return onglet


def _relire(dossier):
    return load_config(str(dossier / "gcm4.conf"), ChiffrementGcm(str(dossier)))


def test_entree_de_menu_et_onglet(app_putty, pomper):
    """L'outil est dans le menu Fichier (section import) et ouvre son onglet."""
    app, _dossier = app_putty
    fenetre = _fenetre(app)
    assert "Import from PuTTY sessions…" in _libelles_fichier(fenetre)
    fenetre.activate_action("win.outil-import-putty", None)
    pomper()
    onglet = _onglet(fenetre)
    assert fenetre.titres_onglets[onglet] == "Import from PuTTY sessions"
    assert not onglet.bouton_importer.get_sensitive()
    assert onglet.conflit_choisi() == Conflit.IGNORER


def test_scan_resume_et_import(app_putty, pomper, tmp_path):
    """Scan du dossier, résumé, import : hôtes dans le magasin et gcm4.conf relu."""
    app, dossier = app_putty
    fenetre = _fenetre(app)
    sessions = _sessions(tmp_path / "putty")
    fenetre.activate_action("win.outil-import-putty", None)
    pomper()
    onglet = _onglet(fenetre)
    messages: list[str] = []
    fenetre.notifier = messages.append
    onglet._dossier_choisi(str(sessions))
    pomper()
    assert len(onglet.hotes) == 2
    assert len(onglet.ignores) == 1
    assert onglet.bouton_importer.get_sensitive()
    onglet._on_importer(None)
    pomper()
    noms = {h.name for h in fenetre.config.store.hosts}
    assert {"srv-a", "telnet-b"} <= noms
    relu = _relire(dossier)
    assert {"srv-a", "telnet-b"} <= {h.name for h in relu.store.hosts}
    assert messages and "added 2" in messages[0]


def test_import_conflit_renommer(app_putty, pomper, tmp_path):
    """Un hôte homonyme existe : RENOMMER l'importe en « nom (2) »."""
    app, _dossier = app_putty
    fenetre = _fenetre(app)
    sessions = _sessions(tmp_path / "putty")
    fenetre.config.store.hosts.append(
        dict_vers_hote({"name": "srv-a", "host": "ailleurs", "protocol": "ssh"}, ou="test")
    )
    fenetre.activate_action("win.outil-import-putty", None)
    pomper()
    onglet = _onglet(fenetre)
    onglet.boutons_conflit[Conflit.RENOMMER].set_active(True)
    onglet._dossier_choisi(str(sessions))
    pomper()
    onglet._on_importer(None)
    pomper()
    noms = [h.name for h in fenetre.config.store.hosts]
    assert noms.count("srv-a") == 1 and "srv-a (2)" in noms


def test_import_erreur_enregistrement_magasin_intact(app_putty, pomper, tmp_path, monkeypatch):
    """gcm4.conf non inscriptible : ExchangeError notifiée, magasin inchangé."""
    app, _dossier = app_putty
    fenetre = _fenetre(app)
    sessions = _sessions(tmp_path / "putty")
    fenetre.activate_action("win.outil-import-putty", None)
    pomper()
    onglet = _onglet(fenetre)
    messages: list[str] = []
    fenetre.notifier = messages.append

    def _echoue():
        raise ConfigError("lecture seule")

    monkeypatch.setattr(fenetre, "enregistrer_config", _echoue)
    onglet._dossier_choisi(str(sessions))
    pomper()
    onglet._on_importer(None)
    pomper()
    assert len(messages) == 1 and "PuTTY import error" in messages[0]
    assert {"srv-a", "telnet-b"}.isdisjoint({h.name for h in fenetre.config.store.hosts})
    assert onglet.bouton_importer.get_sensitive()  # l'import reste possible


def test_import_hosts_sans_configuration(application, tmp_path, pomper):
    """Le service du contexte refuse l'import sans configuration ouverte (#436)."""
    from gcm4.ui.contexte import ContexteApplication

    (tmp_path / "illisible").mkdir()
    (tmp_path / "illisible" / "gcm4.conf").write_text("[options\n", encoding="utf-8")
    fenetre = MainWindow(application, str(tmp_path / "illisible"))
    contexte = ContexteApplication(application, "import-putty")
    with pytest.raises(ExchangeError):
        contexte.import_hosts(
            [dict_vers_hote({"name": "x", "host": "h", "protocol": "ssh"}, ou="test")]
        )
    fenetre.destroy()
    pomper()


def test_choix_de_dossier_via_le_selecteur(app_putty, pomper, tmp_path, monkeypatch):
    """Le bouton ouvre le sélecteur de dossier (mode DOSSIER) et le rappel scanne."""
    from gcm4.ui import fichiers

    app, _dossier = app_putty
    fenetre = _fenetre(app)
    sessions = _sessions(tmp_path / "putty")
    fenetre.activate_action("win.outil-import-putty", None)
    pomper()
    onglet = _onglet(fenetre)

    appels = []

    def faux_choisir(parent, mode, titre, rappel, nom="", filtrer=True):
        appels.append((mode, titre))
        rappel(str(sessions))

    monkeypatch.setattr(fichiers, "choisir", faux_choisir)
    onglet._on_choisir_dossier(None)
    pomper()
    assert appels == [("dossier", "Select PuTTY sessions folder")]
    assert len(onglet.hotes) == 2 and onglet.dossier == sessions


def test_annulation_du_selecteur_et_gardes(app_putty, pomper):
    """Sélecteur annulé : rien n'est scanné ; importer sans scan ne fait rien."""
    app, _dossier = app_putty
    fenetre = _fenetre(app)
    fenetre.activate_action("win.outil-import-putty", None)
    pomper()
    onglet = _onglet(fenetre)
    onglet._dossier_choisi(None)
    pomper()
    assert onglet.dossier is None and not onglet.hotes
    assert onglet.etiquette_resume.get_label() == ""
    onglet._on_importer(None)  # garde : rien à importer
    pomper()
    assert not onglet.bouton_importer.get_sensitive()


def test_scan_vide_message_et_erreur_de_scan(app_putty, pomper, tmp_path, monkeypatch):
    """Dossier sans session importable : message dédié ; scan en échec : notifié."""
    import gcm4_plugin_import_putty.core as core_putty

    app, _dossier = app_putty
    fenetre = _fenetre(app)
    vide = tmp_path / "vide"
    vide.mkdir()
    (vide / "Default%20Settings").write_text("Protocol=ssh\nHostName=perdu\n")
    fenetre.activate_action("win.outil-import-putty", None)
    pomper()
    onglet = _onglet(fenetre)
    messages: list[str] = []
    fenetre.notifier = messages.append
    onglet._dossier_choisi(str(vide))
    pomper()
    assert not onglet.hotes and not onglet.ignores
    assert (
        onglet.etiquette_resume.get_label()
        == "No importable SSH/Telnet session found in this folder."
    )
    assert not onglet.bouton_importer.get_sensitive()

    def _echoue(_dossier):
        raise OSError("disque illisible")

    monkeypatch.setattr(core_putty, "scan_putty_sessions", _echoue)
    onglet._dossier_choisi(str(vide))
    pomper()
    assert len(messages) == 1 and "PuTTY import error" in messages[0]
    assert not onglet.hotes


def test_dossier_par_defaut_propose(app_putty, pomper, tmp_path, monkeypatch):
    """Sans choix, le libellé propose ~/.putty/sessions s'il existe."""
    import gcm4_plugin_import_putty.core as core_putty

    app, _dossier = app_putty
    fenetre = _fenetre(app)
    (tmp_path / ".putty" / "sessions").mkdir(parents=True)
    monkeypatch.setattr(core_putty, "DEFAULT_SESSIONS_DIR", tmp_path / ".putty" / "sessions")
    fenetre.activate_action("win.outil-import-putty", None)
    pomper()
    onglet = _onglet(fenetre)
    assert str(tmp_path / ".putty" / "sessions") in onglet.etiquette_dossier.get_label()


def test_resume_sans_session_ignoree(app_putty, pomper, tmp_path):
    """Un dossier sans session ignorée : résumé sans la ligne des ignorées."""
    app, _dossier = app_putty
    fenetre = _fenetre(app)
    sessions = tmp_path / "seules"
    sessions.mkdir()
    (sessions / "srv-a").write_text("Protocol=ssh\nHostName=s1.example\n")
    fenetre.activate_action("win.outil-import-putty", None)
    pomper()
    onglet = _onglet(fenetre)
    onglet._maj_resume()  # avant tout choix : résumé vide
    assert onglet.etiquette_resume.get_label() == ""
    onglet._dossier_choisi(str(sessions))
    pomper()
    assert onglet.etiquette_resume.get_label() == "Sessions found: 1."
