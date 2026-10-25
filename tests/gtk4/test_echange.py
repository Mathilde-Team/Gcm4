"""Import/export des hôtes et copie de la configuration depuis le menu Fichier (#153).

Vrais dialogues sous Xvfb ; seul le sélecteur de fichiers est remplacé
(``Echange.choisir``) : il répond aussitôt avec le chemin du test.
"""

from __future__ import annotations

import json

from gi.repository import Adw, Gtk

import gcm4.ui  # noqa: F401  (require_version avant gi.repository)
from gcm4.core.chiffrement import ChiffrementGcm
from gcm4.core.config import ConfigError, load_config
from gcm4.core.exchange import Conflit, exporter_fichier
from gcm4.core.model import Host
from gcm4.ui import fichiers


def _fenetre(app):
    from gcm4.ui.main_window import MainWindow

    return next(f for f in app.get_windows() if isinstance(f, MainWindow))


class _Selecteur:
    """Faux sélecteur : note l'appel et répond ``reponse`` tout de suite."""

    def __init__(self, reponse):
        self.reponse = reponse
        self.appels = []

    def __call__(self, parent, mode, titre, rappel, nom=""):
        self.appels.append((mode, titre, nom))
        rappel(self.reponse)


def _preparer(fenetre, monkeypatch, reponse):
    """Faux sélecteur et toasts relevés dans une liste."""
    selecteur = _Selecteur(reponse)
    fenetre.echange.choisir = selecteur
    messages: list[str] = []
    monkeypatch.setattr(fenetre, "notifier", messages.append)
    return selecteur, messages


def _repondre(echange, reponse, pomper):
    dialogue = echange.dialogue
    assert dialogue is not None
    dialogue.emit("response", reponse)
    dialogue.close()  # un vrai clic ferme le dialogue ; emit() seul ne le fait pas
    pomper()


def _relire(dossier):
    return load_config(str(dossier / "gcm4.conf"), ChiffrementGcm(str(dossier)))


def test_menu_fichier_actions_actives(app_fixture):
    """Configuration chargée : les trois actions du menu Fichier sont actives."""
    app, _dossier = app_fixture
    fenetre = _fenetre(app)
    for nom in ("import-hosts", "export-hosts", "save-config-as"):
        assert fenetre.lookup_action(nom).get_enabled(), nom


def test_configuration_illisible_import_export_desactives(application, tmp_path, pomper):
    """gcm4.conf illisible : importer/exporter désactivés, la copie reste possible."""
    from gcm4.ui.main_window import MainWindow

    (tmp_path / "illisible").mkdir()
    (tmp_path / "illisible" / "gcm4.conf").write_text("[options\n", encoding="utf-8")
    fenetre = MainWindow(application, str(tmp_path / "illisible"))
    assert fenetre.config is None
    assert not fenetre.lookup_action("import-hosts").get_enabled()
    assert not fenetre.lookup_action("export-hosts").get_enabled()
    assert fenetre.lookup_action("save-config-as").get_enabled()
    fenetre.destroy()
    pomper()


def test_export_sans_mots_de_passe(app_fixture, tmp_path, monkeypatch, pomper):
    """Exporter : dialogue d'options, avertissement caché, fichier sans mot de passe."""
    app, _dossier = app_fixture
    fenetre = _fenetre(app)
    cible = tmp_path / "export"
    selecteur, messages = _preparer(fenetre, monkeypatch, str(cible))
    fenetre.activate_action("win.export-hosts", None)
    pomper()
    echange = fenetre.echange
    # Adw.AlertDialog (libadwaita ≥ 1.5), sinon Adw.MessageDialog (Debian 12 : 1.2).
    attendu = Adw.AlertDialog if hasattr(Adw, "AlertDialog") else Adw.MessageDialog
    assert isinstance(echange.dialogue, attendu)
    assert not echange.alerte_mots_de_passe.get_visible()
    _repondre(echange, "exporter", pomper)
    assert selecteur.appels == [(fichiers.ENREGISTRER, "Export servers", "gcm4-hosts.json")]
    donnees = json.loads((tmp_path / "export.json").read_text(encoding="utf-8"))
    assert sorted(h["name"] for h in donnees["hosts"]) == ["api", "db", "web"]
    assert all("password" not in h for h in donnees["hosts"])
    assert messages == [f"Hosts exported: 3 ({cible}.json)"]


def test_export_avec_mots_de_passe_avertit(app_fixture, tmp_path, monkeypatch, pomper):
    """Case cochée : avertissement visible, mots de passe en clair dans le CSV."""
    app, _dossier = app_fixture
    fenetre = _fenetre(app)
    _preparer(fenetre, monkeypatch, str(tmp_path / "h.csv"))
    fenetre.activate_action("win.export-hosts", None)
    pomper()
    echange = fenetre.echange
    echange.case_mots_de_passe.set_active(True)
    assert echange.alerte_mots_de_passe.get_visible()
    echange.case_mots_de_passe.set_active(False)
    assert not echange.alerte_mots_de_passe.get_visible()
    echange.case_mots_de_passe.set_active(True)
    _repondre(echange, "exporter", pomper)
    texte = (tmp_path / "h.csv").read_text(encoding="utf-8")
    assert texte.splitlines()[0].count("password") == 1
    assert fenetre.config.store.find("PROD/eu/api").password in texte


def test_export_annule(app_fixture, tmp_path, monkeypatch, pomper):
    """Annuler les options, puis annuler le sélecteur : rien n'est écrit."""
    app, _dossier = app_fixture
    fenetre = _fenetre(app)
    selecteur, messages = _preparer(fenetre, monkeypatch, None)
    fenetre.activate_action("win.export-hosts", None)
    pomper()
    _repondre(fenetre.echange, "annuler", pomper)
    assert selecteur.appels == []
    fenetre.activate_action("win.export-hosts", None)
    pomper()
    _repondre(fenetre.echange, "exporter", pomper)
    assert len(selecteur.appels) == 1
    assert messages == []
    assert list(tmp_path.glob("*.json")) == []


def test_export_impossible(app_fixture, tmp_path, monkeypatch):
    """Dossier absent : message d'erreur, aucune exception."""
    app, _dossier = app_fixture
    fenetre = _fenetre(app)
    _selecteur, messages = _preparer(fenetre, monkeypatch, None)
    assert fenetre.echange.exporter_vers(str(tmp_path / "absent" / "h.json"), False) is False
    assert len(messages) == 1 and "absent" in messages[0]


def _fichier(tmp_path, *hotes, nom="import.json"):
    chemin = tmp_path / nom
    exporter_fichier(chemin, hotes, mots_de_passe=True)
    return chemin


def test_import_apercu_puis_renommer(app_fixture, tmp_path, monkeypatch, pomper):
    """Aperçu : compte, déjà présents, bilan prévu selon la stratégie ; import enregistré."""
    app, dossier = app_fixture
    fenetre = _fenetre(app)
    chemin = _fichier(
        tmp_path,
        Host(name="web", group="LAB", host="10.9.9.9"),
        Host(name="nouveau", group="NEUF", host="n.example.org"),
    )
    selecteur, messages = _preparer(fenetre, monkeypatch, str(chemin))
    fenetre.activate_action("win.import-hosts", None)
    pomper()
    assert selecteur.appels == [(fichiers.OUVRIR, "Import servers", "")]
    echange = fenetre.echange
    assert echange.dialogue.get_body() == "Hosts in import.json: 2, already present: 1"
    assert echange.alerte_mots_de_passe is None
    assert echange.conflit_choisi() is Conflit.IGNORER
    assert "added 1, replaced 0, renamed 0, ignored 1" in echange.etiquette_apercu.get_label()
    echange.boutons_conflit[Conflit.RENOMMER].set_active(True)
    assert "added 1, replaced 0, renamed 1, ignored 0" in echange.etiquette_apercu.get_label()
    assert fenetre.config.store.find("NEUF/nouveau") is None  # aperçu : rien d'importé
    _repondre(echange, "importer", pomper)
    assert "renamed 1" in echange.dialogue.get_body()
    assert "new groups 1" in echange.dialogue.get_body()
    _repondre(echange, "fermer", pomper)
    assert echange.dialogue is None
    relu = _relire(dossier).store
    assert relu.find("LAB/web (2)").host == "10.9.9.9"
    assert relu.find("LAB/web").host == "10.0.0.1"
    assert relu.find("NEUF/nouveau") is not None
    assert fenetre.arbre.ligne("NEUF") is not None  # arbre rechargé
    assert messages == []


def test_import_remplacer_avec_mots_de_passe(app_fixture, tmp_path, monkeypatch, pomper):
    """Fichier avec mots de passe : avertissement ; « remplacer » écrase l'hôte (chiffré)."""
    app, dossier = app_fixture
    fenetre = _fenetre(app)
    chemin = _fichier(tmp_path, Host(name="web", group="LAB", host="w2", password="s3cret"))
    _preparer(fenetre, monkeypatch, str(chemin))
    fenetre.activate_action("win.import-hosts", None)
    pomper()
    echange = fenetre.echange
    assert isinstance(echange.alerte_mots_de_passe, Gtk.Label)
    assert "clear text" in echange.alerte_mots_de_passe.get_label()
    echange.boutons_conflit[Conflit.REMPLACER].set_active(True)
    _repondre(echange, "importer", pomper)
    assert "replaced 1" in echange.dialogue.get_body()
    assert "s3cret" not in (dossier / "gcm4.conf").read_text(encoding="utf-8")
    assert _relire(dossier).store.find("LAB/web").password == "s3cret"


def test_import_annule_rien_ne_change(app_fixture, tmp_path, monkeypatch, pomper):
    """Annuler l'aperçu : magasin et fichier intacts ; sélecteur annulé : aucun aperçu."""
    app, dossier = app_fixture
    fenetre = _fenetre(app)
    avant = (dossier / "gcm4.conf").read_bytes()
    chemin = _fichier(tmp_path, Host(name="x", group="G", host="x"))
    _preparer(fenetre, monkeypatch, str(chemin))
    fenetre.activate_action("win.import-hosts", None)
    pomper()
    _repondre(fenetre.echange, "annuler", pomper)
    assert fenetre.config.store.find("G/x") is None
    assert (dossier / "gcm4.conf").read_bytes() == avant
    _preparer(fenetre, monkeypatch, None)
    fenetre.activate_action("win.import-hosts", None)
    pomper()
    assert fenetre.echange.dialogue is None


def test_import_fichier_invalide(app_fixture, tmp_path, monkeypatch, pomper):
    """Fichier mal formé : message, aucun aperçu, rien d'importé."""
    app, _dossier = app_fixture
    fenetre = _fenetre(app)
    mauvais = tmp_path / "mauvais.json"
    mauvais.write_text("{pas du json", encoding="utf-8")
    _selecteur, messages = _preparer(fenetre, monkeypatch, str(mauvais))
    fenetre.activate_action("win.import-hosts", None)
    pomper()
    assert fenetre.echange.dialogue is None
    assert len(messages) == 1 and "mauvais.json" in messages[0]


def test_import_ecriture_impossible_annule(app_fixture, tmp_path, monkeypatch, pomper):
    """gcm4.conf non enregistrable : message, magasin revenu à l'état d'avant."""
    app, _dossier = app_fixture
    fenetre = _fenetre(app)
    chemin = _fichier(tmp_path, Host(name="x", group="G", host="x"))
    _selecteur, messages = _preparer(fenetre, monkeypatch, str(chemin))

    def refus():
        raise ConfigError("disque plein")

    monkeypatch.setattr(fenetre, "enregistrer_config", refus)
    nb = len(fenetre.config.store.hosts)
    assert (
        fenetre.echange.appliquer_import(str(chemin), [Host(name="x", group="G")], Conflit.IGNORER)
        is False
    )
    assert messages == ["disque plein"]
    assert len(fenetre.config.store.hosts) == nb
    assert "G" not in fenetre.config.store.groups


def test_enregistrer_la_configuration_sous(app_fixture, tmp_path, monkeypatch, pomper):
    """Copie de gcm4.conf et .gcm4.key ; deuxième copie au même endroit refusée."""
    app, dossier = app_fixture
    fenetre = _fenetre(app)
    copie = tmp_path / "copie"
    copie.mkdir()
    selecteur, messages = _preparer(fenetre, monkeypatch, str(copie))
    fenetre.activate_action("win.save-config-as", None)
    pomper()
    assert selecteur.appels == [(fichiers.DOSSIER, "Save configuration as…", "")]
    assert (copie / "gcm4.conf").read_bytes() == (dossier / "gcm4.conf").read_bytes()
    assert (copie / ".gcm4.key").read_bytes() == (dossier / ".gcm4.key").read_bytes()
    assert _relire(copie).store.find("PROD/eu/api").password  # la copie se déchiffre
    fenetre.activate_action("win.save-config-as", None)
    pomper()
    assert messages[0] == f"Configuration saved to {copie}"
    assert messages[1] == f"{copie} already contains gcm4.conf."
