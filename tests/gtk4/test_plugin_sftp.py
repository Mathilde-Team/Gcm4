# SPDX-License-Identifier: GPL-3.0-or-later
"""Plugin SFTP GTK4 (``plugins/sftp/gtk4.py``, #75) contre un vrai serveur SSH/SFTP paramiko.

Chaque test ouvre un onglet sur le serveur de ``tests/serveur_sftp.py``
(127.0.0.1, racine temporaire) et pilote l'interface comme l'utilisateur :
boutons, barre de question, lignes de la liste. Le fil de travail tourne
pour de bon ; :func:`_attendre` traite les événements GTK jusqu'à la fin de
l'opération.
"""

# ruff: noqa: F811 (fixtures importées de serveur_sftp, redéclarées en paramètres)

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest
from gi.repository import Gio, GLib, Gtk
from serveur_sftp import (  # noqa: F401 (fixtures)
    ACCUEIL,
    MOT_DE_PASSE,
    UTILISATEUR,
    cles_sftp,
    maison,
    serveur,
)

from gcm4.core import registre
from gcm4.core.model import Host

RACINE = Path(__file__).resolve().parents[2]


class Journal:
    """Journal du contexte : garde avertissements et informations."""

    def __init__(self):
        """Aucun message."""
        self.messages = []

    def warning(self, message, *args):
        """Avertissement formaté comme loguru."""
        self.messages.append(message.format(*args))

    info = warning

    def debug(self, *_args):
        """Ignoré."""


class Contexte:
    """Contexte du contrat v1 : mots de passe demandés et notifications notés."""

    def __init__(self):
        """Configuration vide."""
        self.log = Journal()
        self.configuration = {}
        self.demandes = []
        self.notifications = []

    def config(self):
        """Section ``[plugin sftp]``."""
        return self.configuration

    def ask_password(self, host, done):
        """Demande notée ; le test répond en appelant ``done``."""
        self.demandes.append((host, done))

    def notify(self, message):
        """Notification notée."""
        self.notifications.append(message)


@pytest.fixture
def contexte():
    """Contexte neuf."""
    return Contexte()


@pytest.fixture
def plugin(contexte):
    """Plugin sftp chargé depuis ``plugins/`` du dépôt, comme en mode source."""
    candidats = registre.candidats_dossier(RACINE / "plugins")
    etats = {e.name: e for e in registre.decouvrir(lambda _nom: contexte, candidats).etats}
    assert etats["sftp"].erreur is None
    yield etats["sftp"].plugin
    registre._oublier_paquet("gcm4_plugin_sftp")


@pytest.fixture
def module(plugin):
    """Module ``gcm4_plugin_sftp.gtk4`` chargé."""
    return sys.modules["gcm4_plugin_sftp.gtk4"]


@pytest.fixture
def telechargements(tmp_path, contexte):
    """``download-dir`` de ``[plugin sftp]`` (créé à la demande par le plugin)."""
    dossier = tmp_path / "telechargements"
    contexte.configuration = {"download-dir": str(dossier)}
    return dossier


@pytest.fixture
def connue(maison, serveur, cles_sftp, plugin):
    """Clé du serveur déjà dans ``~/.ssh/known_hosts``."""
    core = sys.modules["gcm4_plugin_sftp.core"]
    fichier = maison / ".ssh" / "known_hosts"
    core.ajouter_cle_connue(fichier, f"[127.0.0.1]:{serveur.port}", cles_sftp[0])
    return fichier


@pytest.fixture
def onglet(plugin, serveur, connue, telechargements):
    """Onglet connecté par mot de passe, dans une fenêtre ; fermé après le test."""
    session = plugin.open(_hote(serveur))
    fenetre = _afficher(session)
    assert _attendre(lambda: session.connecte and not session.occupe)
    yield session
    session.close()
    fenetre.destroy()


def _hote(serveur, **champs):
    """Hôte sftp du serveur de test."""
    valeurs = {
        "name": "fichiers",
        "host": "127.0.0.1",
        "port": serveur.port,
        "user": UTILISATEUR,
        "password": MOT_DE_PASSE,
        "protocol": "sftp",
    }
    valeurs.update(champs)
    return Host(**valeurs)


def _afficher(session):
    """Fenêtre 800×600 qui montre l'onglet."""
    fenetre = Gtk.Window(child=session.widget, default_width=800, default_height=600)
    fenetre.present()
    return fenetre


def _attendre(condition, delai=20.0):
    """Traite les événements GTK jusqu'à ``condition()`` ou ``delai`` secondes."""
    contexte = GLib.MainContext.default()
    fin = time.monotonic() + delai
    while not condition() and time.monotonic() < fin:
        contexte.iteration(False)
        time.sleep(0.005)
    return condition()


def _libre(session):
    """Attend la fin de l'opération en cours."""
    return _attendre(lambda: not session.occupe)


def _accueil(serveur) -> Path:
    """Dossier local qui correspond à ``/accueil``."""
    return serveur.racine / "accueil"


def _noms(session):
    """Noms des lignes affichées, dans l'ordre."""
    noms = []
    ligne = session.liste.get_first_child()
    while ligne is not None:
        noms.append(ligne.entree.nom)
        ligne = ligne.get_next_sibling()
    return noms


# ── Plugin ───────────────────────────────────────────────────────────────


def test_decouvert_depuis_les_sources(plugin):
    """Mode source : ``plugins/sftp`` est chargé, port 22, options vides par défaut."""
    assert (plugin.name, plugin.protocols, plugin.version) == ("sftp", ("sftp",), "1.0")
    assert plugin.default_port("sftp") == 22
    assert plugin.default_options("sftp") == {"remote-dir": "", "identity-file": ""}
    assert "gcm4_plugin_sftp.core" in sys.modules


def test_validation_du_fichier_de_cle(plugin, tmp_path):
    """Fichier de clé absent : erreur sur ``identity-file`` ; vide ou présent : rien."""
    cle = tmp_path / "id"
    cle.write_text("x")
    assert plugin.validate_options("sftp", {"identity-file": ""}) == []
    assert plugin.validate_options("sftp", {"identity-file": str(cle)}) == []
    erreurs = plugin.validate_options("sftp", {"identity-file": str(tmp_path / "absent")})
    assert [(e.champ, e.message) for e in erreurs] == [("identity-file", "File not found")]


def test_zone_options(plugin, serveur):
    """Deux champs : dossier distant et fichier de clé, écrits dans la copie des options."""
    options = {"remote-dir": "/srv", "identity-file": ""}
    appels = []
    grille = plugin.build_host_options_widget(_hote(serveur), options, lambda: appels.append(1))
    saisies = [grille.get_child_at(1, ligne) for ligne in (0, 1)]
    assert saisies[0].get_text() == "/srv"
    saisies[0].set_text(" /data ")
    saisies[1].set_text("~/.ssh/id_test")
    assert options == {"remote-dir": "/data", "identity-file": "~/.ssh/id_test"}
    assert appels  # changed() à chaque modification (set_text : effacement puis insertion)


def test_sans_adresse(plugin, serveur):
    """Hôte sans adresse : ``open`` lève, la base n'ouvre pas d'onglet."""
    with pytest.raises(ValueError, match="No address"):
        plugin.open(_hote(serveur, host=""))


def test_dossier_des_telechargements(plugin, contexte, tmp_path, monkeypatch):
    """``download-dir`` (``~`` développé), sinon le dossier XDG, sinon le dossier personnel."""
    contexte.configuration = {"download-dir": "~/dl"}
    assert plugin._telechargements() == Path.home() / "dl"
    contexte.configuration = {}
    monkeypatch.setattr(GLib, "get_user_special_dir", lambda _d: str(tmp_path / "xdg"))
    assert plugin._telechargements() == tmp_path / "xdg"
    monkeypatch.setattr(GLib, "get_user_special_dir", lambda _d: None)
    monkeypatch.setattr(GLib, "get_home_dir", lambda: str(tmp_path))
    assert plugin._telechargements() == tmp_path


# ── Connexion ────────────────────────────────────────────────────────────


def test_cle_inconnue_acceptee(plugin, serveur, maison, cles_sftp, telechargements):
    """Clé inconnue : question avec l'empreinte ; acceptée, elle est ajoutée et l'onglet se connecte."""
    core = sys.modules["gcm4_plugin_sftp.core"]
    (_accueil(serveur) / "lisez-moi.txt").write_text("x")
    session = plugin.open(_hote(serveur))
    fenetre = _afficher(session)
    assert _attendre(lambda: session.barre.get_visible())
    assert core.empreinte(cles_sftp[0]) in session.question.get_text()
    assert not session.connecte and session.etat.get_text() == "Unknown host key"
    session.bouton_oui.emit("clicked")
    assert _attendre(lambda: session.connecte and not session.occupe)
    assert not session.barre.get_visible()
    assert f"[127.0.0.1]:{serveur.port} ssh-rsa" in (maison / ".ssh" / "known_hosts").read_text()
    assert session.dossier == ACCUEIL and _noms(session) == ["lisez-moi.txt"]
    assert session.chemin.get_text() == ACCUEIL
    assert session.etat.get_text() == "/accueil: 1 items"
    session.close()
    fenetre.destroy()


def test_cle_inconnue_refusee(plugin, serveur, maison, telechargements):
    """« Annuler » : rien n'est écrit, l'onglet reste déconnecté."""
    session = plugin.open(_hote(serveur))
    fenetre = _afficher(session)
    assert _attendre(lambda: session.barre.get_visible())
    session.bouton_non.emit("clicked")
    assert not session.barre.get_visible() and not session.connecte
    assert not (maison / ".ssh" / "known_hosts").exists()
    session.close()
    fenetre.destroy()


def test_cle_inconnue_known_hosts_inutilisable(plugin, serveur, maison, telechargements):
    """``~/.ssh`` est un fichier : l'ajout échoue, message d'erreur, pas de nouvel essai."""
    (maison / ".ssh").write_text("pas un dossier")
    session = plugin.open(_hote(serveur))
    fenetre = _afficher(session)
    assert _attendre(lambda: session.barre.get_visible())
    connexions = serveur.connexions
    session.bouton_oui.emit("clicked")
    assert session.etat.get_text().startswith("Error: ")
    assert not session.occupe and serveur.connexions == connexions
    session.close()
    fenetre.destroy()


def test_cle_changee(plugin, serveur, maison, cles_sftp, telechargements):
    """Clé différente : refus expliqué, aucune question, avertissement journalisé."""
    core = sys.modules["gcm4_plugin_sftp.core"]
    core.ajouter_cle_connue(
        maison / ".ssh" / "known_hosts", f"[127.0.0.1]:{serveur.port}", cles_sftp[2]
    )
    contexte = plugin.context
    session = plugin.open(_hote(serveur))
    fenetre = _afficher(session)
    assert _attendre(lambda: "has changed" in session.etat.get_text())
    assert core.empreinte(cles_sftp[0]) in session.etat.get_text()
    assert not session.barre.get_visible() and not session.connecte
    assert any("clé d'hôte changée" in m for m in contexte.log.messages)
    session.close()
    fenetre.destroy()


def test_mot_de_passe_demande(plugin, contexte, serveur, connue, telechargements):
    """Sans mot de passe enregistré (et sans clé) : ``ask_password``, puis connexion."""
    session = plugin.open(_hote(serveur, password=""))
    fenetre = _afficher(session)
    assert _attendre(lambda: contexte.demandes)
    assert session.etat.get_text() == "Authentication failed"
    hote, done = contexte.demandes.pop()
    assert hote is session.host
    done("mauvais")
    assert _attendre(lambda: contexte.demandes)
    contexte.demandes.pop()[1](MOT_DE_PASSE)
    assert _attendre(lambda: session.connecte and not session.occupe)
    assert session.dossier == ACCUEIL
    session.close()
    fenetre.destroy()


def test_mot_de_passe_annule_puis_reessai(plugin, contexte, serveur, connue, telechargements):
    """Saisie annulée : « Réessayer » redemande le mot de passe."""
    session = plugin.open(_hote(serveur, password="mauvais"))
    fenetre = _afficher(session)
    assert _attendre(lambda: contexte.demandes)
    contexte.demandes.pop()[1](None)
    assert session.etat.get_text() == "Authentication cancelled"
    assert session.barre.get_visible() and session.bouton_oui.get_label() == "Retry"
    session.bouton_oui.emit("clicked")
    assert _attendre(lambda: contexte.demandes)
    session.close()
    contexte.demandes.pop()[1](MOT_DE_PASSE)
    assert not session.connecte
    fenetre.destroy()


def test_serveur_injoignable_puis_reessai(plugin, contexte, serveur, connue, telechargements):
    """Serveur arrêté : erreur et « Réessayer » ; l'erreur est journalisée."""
    hote = _hote(serveur)
    serveur.arreter()
    session = plugin.open(hote)
    fenetre = _afficher(session)
    assert _attendre(lambda: session.barre.get_visible())
    assert session.question.get_text().startswith("Connection failed: ")
    assert session.etat.get_text().startswith("Error: ")
    assert any(m.startswith("SFTP ") for m in contexte.log.messages)
    session.bouton_oui.emit("clicked")
    assert session.occupe
    assert _attendre(lambda: session.barre.get_visible())
    session.close()
    fenetre.destroy()


def test_fichier_de_cle_et_dossier_initial(
    plugin, serveur, connue, telechargements, cles_sftp, tmp_path
):
    """Options ``identity-file`` (avec ``~``) et ``remote-dir`` : connexion par clé, dossier ouvert."""
    cles_sftp[1].write_private_key_file(str(Path.home() / "id_test"))
    (_accueil(serveur) / "projet").mkdir()
    hote = _hote(
        serveur,
        password="",
        protocol_options={"sftp": {"identity-file": "~/id_test", "remote-dir": "projet"}},
    )
    session = plugin.open(hote)
    fenetre = _afficher(session)
    assert _attendre(lambda: session.connecte and not session.occupe)
    assert session.dossier == "/accueil/projet" and _noms(session) == []
    session.close()
    fenetre.destroy()


# ── Navigation ───────────────────────────────────────────────────────────


def test_navigation(onglet, serveur):
    """Ligne de dossier activée, parent, actualiser, chemin saisi ; boutons selon la sélection."""
    accueil = _accueil(serveur)
    (accueil / "sous").mkdir()
    (accueil / "sous" / "f.txt").write_text("x")
    (accueil / "z.txt").write_text("x")
    onglet.actualiser()
    assert _libre(onglet) and _noms(onglet) == ["sous", "z.txt"]
    assert not onglet.bouton_supprimer.get_sensitive()
    assert onglet.selectionner("sous") and onglet.selection().nom == "sous"
    assert onglet.bouton_supprimer.get_sensitive()
    assert not onglet.bouton_telecharger.get_sensitive()
    assert not onglet.selectionner("absent")
    onglet.liste.get_row_at_index(0).emit("activate")
    assert _libre(onglet) and onglet.dossier == "/accueil/sous" and _noms(onglet) == ["f.txt"]
    onglet.bouton_parent.emit("clicked")
    assert _libre(onglet) and onglet.dossier == ACCUEIL
    onglet.chemin.set_text("/")
    onglet.chemin.emit("activate")
    assert _libre(onglet) and onglet.dossier == "/" and _noms(onglet) == ["accueil"]
    onglet.monter()
    assert _libre(onglet) and onglet.dossier == "/"


def test_dossier_absent(onglet):
    """Chemin inexistant : erreur dans la ligne d'état, la liste ne change pas."""
    avant = _noms(onglet)
    onglet.aller("/absent")
    assert _libre(onglet)
    assert onglet.etat.get_text().startswith("Error: /absent")
    assert _noms(onglet) == avant and onglet.dossier == ACCUEIL


def test_activer_pendant_une_operation(onglet, serveur):
    """Occupé : l'activation d'une ligne est ignorée."""
    (_accueil(serveur) / "d").mkdir()
    onglet.actualiser()
    assert _libre(onglet)
    entree = onglet.entrees[0]
    onglet.occupe = True
    onglet.activer(entree)
    onglet.occupe = False
    assert onglet.dossier == ACCUEIL


def test_lignes(onglet, serveur):
    """Icône, nom (→ pour un lien), taille lisible, date et droits."""
    accueil = _accueil(serveur)
    (accueil / "gros.bin").write_bytes(b"x" * 2048)
    os.symlink("gros.bin", accueil / "lien")
    onglet.actualiser()
    assert _libre(onglet)
    textes = []
    for index in (0, 1):
        boite = onglet.liste.get_row_at_index(index).get_child()
        enfant = boite.get_first_child().get_next_sibling()
        ligne = []
        while enfant is not None:
            ligne.append(enfant.get_label())
            enfant = enfant.get_next_sibling()
        textes.append(ligne)
    assert textes[0][:2] == ["gros.bin", "2.0 KiB"] and textes[0][3].startswith("-rw")
    assert textes[1][0] == "lien →"


# ── Transferts ───────────────────────────────────────────────────────────


def test_telechargement(onglet, serveur, contexte, telechargements):
    """Bouton puis activation : copie dans ``download-dir``, sans écraser ; notification."""
    contenu = os.urandom(300_000)
    (_accueil(serveur) / "image.bin").write_bytes(contenu)
    onglet.actualiser()
    assert _libre(onglet)
    onglet.selectionner("image.bin")
    assert onglet.bouton_telecharger.get_sensitive()
    onglet.bouton_telecharger.emit("clicked")
    assert _libre(onglet)
    assert (telechargements / "image.bin").read_bytes() == contenu
    assert contexte.notifications == [f"Downloaded to {telechargements / 'image.bin'}"]
    onglet.liste.get_selected_row().emit("activate")
    assert _libre(onglet)
    assert (telechargements / "image (1).bin").read_bytes() == contenu
    assert onglet.etat.get_text() == f"Downloaded to {telechargements / 'image (1).bin'}"


def test_telecharger_un_dossier_ne_fait_rien(onglet, serveur, telechargements):
    """Dossier sélectionné : « Télécharger » est inactif et ne fait rien."""
    (_accueil(serveur) / "d").mkdir()
    onglet.actualiser()
    assert _libre(onglet)
    onglet.selectionner("d")
    onglet.telecharger_selection()
    assert not onglet.occupe and not telechargements.exists()


def test_progression(onglet):
    """Barre affichée pendant une opération, au plus tous les 0,1 s, masquée à la fin."""
    onglet.occupe = True
    onglet._derniere_progression = 0.0
    onglet._progression(50, 100)
    onglet._progression(60, 100)
    assert _attendre(lambda: onglet.progres.get_visible())
    assert onglet.progres.get_fraction() == pytest.approx(0.5)
    onglet._progression(100, 100)
    assert _attendre(lambda: onglet.progres.get_fraction() == pytest.approx(1.0))
    onglet._derniere_progression = 0.0
    onglet._progression(0, 0)
    onglet.occupe = False
    _attendre(lambda: False, 0.05)


def test_envoi(onglet, serveur, contexte, tmp_path):
    """Fichiers envoyés au dossier courant, dossiers ignorés et signalés, liste relue."""
    fichier = tmp_path / "rapport.txt"
    fichier.write_text("contenu", encoding="utf-8")
    dossier = tmp_path / "photos"
    dossier.mkdir()
    onglet.envoyer([fichier, dossier])
    assert _attendre(lambda: "rapport.txt" in _noms(onglet) and not onglet.occupe)
    assert (_accueil(serveur) / "rapport.txt").read_text(encoding="utf-8") == "contenu"
    assert contexte.notifications == ["Folders not uploaded: photos"]
    onglet.envoyer([dossier])
    assert onglet.etat.get_text() == "Nothing to upload (folders are not uploaded)"
    assert not onglet.occupe


def test_envoi_echec(onglet, serveur, tmp_path):
    """Dossier courant supprimé entre-temps : erreur dans la ligne d'état."""
    fichier = tmp_path / "f.txt"
    fichier.write_text("x")
    onglet.dossier = "/disparu"
    onglet.envoyer([fichier])
    assert _libre(onglet) and onglet.etat.get_text().startswith("Error: /disparu/f.txt")


class _Fichiers:
    """``Gdk.FileList`` minimal : ``get_files()``."""

    def __init__(self, chemins):
        self.chemins = chemins

    def get_files(self):
        return [Gio.File.new_for_path(str(c)) for c in self.chemins]


def test_depot(onglet, serveur, tmp_path):
    """Glisser-déposer : fichiers envoyés ; occupé : refusé avec un message."""
    fichier = tmp_path / "depose.txt"
    fichier.write_text("x")
    onglet.occupe = True
    assert onglet._sur_depot(None, _Fichiers([fichier]), 0, 0) is False
    assert onglet.etat.get_text().startswith("Busy")
    onglet.occupe = False
    assert onglet._sur_depot(None, _Fichiers([fichier]), 0, 0) is True
    assert _attendre(lambda: "depose.txt" in _noms(onglet) and not onglet.occupe)
    assert onglet._sur_depot(None, _Fichiers([]), 0, 0) is False


class _FileDialog:
    """``Gtk.FileDialog`` simulé : note l'appel, le test appelle la fin."""

    derniers = []

    def __init__(self, **proprietes):
        self.proprietes = proprietes
        _FileDialog.derniers.append(self)

    def open_multiple(self, fenetre, annulation, rappel):
        self.appel = (fenetre, annulation, rappel)

    def open_multiple_finish(self, resultat):
        if resultat is None:
            raise GLib.Error("Dismissed by user")
        return resultat


def test_selecteur_gtk_recent(onglet, serveur, tmp_path, monkeypatch, module):
    """GTK ≥ 4.10 : ``Gtk.FileDialog.open_multiple`` ; annulation sans effet, fichiers envoyés."""
    monkeypatch.setattr(module.Gtk, "FileDialog", _FileDialog, raising=False)
    onglet.bouton_envoyer.emit("clicked")
    dialogue = _FileDialog.derniers[-1]
    assert dialogue.proprietes["title"] == "Upload files"
    assert dialogue.appel[0] is onglet.widget.get_root()
    rappel = dialogue.appel[2]
    rappel(dialogue, None)
    assert not onglet.occupe
    fichier = tmp_path / "choisi.txt"
    fichier.write_text("x")
    rappel(dialogue, [Gio.File.new_for_path(str(fichier))])
    assert _attendre(lambda: "choisi.txt" in _noms(onglet) and not onglet.occupe)


class _Liste:
    """``Gio.ListModel`` minimal de ``Gio.File``."""

    def __init__(self, fichiers):
        self.fichiers = fichiers

    def get_n_items(self):
        return len(self.fichiers)

    def get_item(self, index):
        return self.fichiers[index]


class _FileChooserNative:
    """``Gtk.FileChooserNative`` simulé (GTK < 4.10)."""

    dernier = None

    def __init__(self, *arguments):
        self.arguments = arguments
        self.signaux = {}
        self.montre = self.detruit = False
        self.fichiers = []

    @classmethod
    def new(cls, *arguments):
        cls.dernier = cls(*arguments)
        return cls.dernier

    def set_select_multiple(self, valeur):
        self.multiple = valeur

    def connect(self, signal, rappel):
        self.signaux[signal] = rappel

    def show(self):
        self.montre = True

    def get_files(self):
        return _Liste(self.fichiers)

    def destroy(self):
        self.detruit = True


def test_selecteur_gtk_ancien(onglet, serveur, tmp_path, monkeypatch, module):
    """GTK < 4.10 : ``Gtk.FileChooserNative`` ; refus sans effet, acceptation envoie."""
    monkeypatch.setattr(module.Gtk, "FileDialog", None, raising=False)
    monkeypatch.setattr(module.Gtk, "FileChooserNative", _FileChooserNative)
    onglet.choisir_envois()
    dialogue = _FileChooserNative.dernier
    assert dialogue.montre and dialogue.multiple and dialogue.arguments[0] == "Upload files"
    dialogue.signaux["response"](dialogue, Gtk.ResponseType.CANCEL)
    assert dialogue.detruit and not onglet.occupe
    fichier = tmp_path / "ancien.txt"
    fichier.write_text("x")
    dialogue.fichiers = [Gio.File.new_for_path(str(fichier))]
    dialogue.signaux["response"](dialogue, Gtk.ResponseType.ACCEPT)
    assert _attendre(lambda: "ancien.txt" in _noms(onglet) and not onglet.occupe)


# ── Dossiers, renommage, suppression ─────────────────────────────────────


def test_nouveau_dossier(onglet, serveur):
    """Barre avec champ : nom validé, dossier créé et listé ; nom invalide refusé."""
    onglet.bouton_dossier.emit("clicked")
    assert onglet.barre.get_visible() and onglet.saisie.get_visible()
    assert onglet.bouton_oui.get_label() == "Create"
    onglet.saisie.set_text("../evasion")
    onglet.saisie.emit("activate")
    assert onglet.etat.get_text() == "Invalid name: ../evasion" and not onglet.occupe
    onglet.demander_dossier()
    onglet.saisie.set_text(" neuf ")
    onglet.bouton_oui.emit("clicked")
    assert _attendre(lambda: "neuf" in _noms(onglet) and not onglet.occupe)
    assert (_accueil(serveur) / "neuf").is_dir()
    onglet.creer_dossier("neuf")
    assert _libre(onglet) and onglet.etat.get_text().startswith("Error: /accueil/neuf")


def test_renommer(onglet, serveur):
    """Barre préremplie avec le nom ; même nom : rien ; nouveau nom : renommé."""
    (_accueil(serveur) / "ancien.txt").write_text("x")
    onglet.actualiser()
    assert _libre(onglet)
    onglet.demander_renommage()
    assert not onglet.barre.get_visible()
    onglet.selectionner("ancien.txt")
    onglet.bouton_renommer.emit("clicked")
    assert onglet.saisie.get_text() == "ancien.txt"
    onglet.bouton_oui.emit("clicked")
    assert not onglet.occupe
    onglet.demander_renommage()
    onglet.saisie.set_text("nouveau.txt")
    onglet.bouton_oui.emit("clicked")
    assert _attendre(lambda: _noms(onglet) == ["nouveau.txt"] and not onglet.occupe)


def test_supprimer(onglet, serveur):
    """Confirmation en rouge ; fichier puis dossier vide supprimés ; dossier plein : erreur."""
    accueil = _accueil(serveur)
    (accueil / "f.txt").write_text("x")
    (accueil / "vide").mkdir()
    (accueil / "plein").mkdir()
    (accueil / "plein" / "x").write_text("x")
    onglet.actualiser()
    assert _libre(onglet)
    onglet.demander_suppression()
    assert not onglet.barre.get_visible()
    onglet.selectionner("f.txt")
    onglet.bouton_supprimer.emit("clicked")
    assert onglet.question.get_text() == "Delete f.txt?"
    assert onglet.bouton_oui.has_css_class("destructive-action")
    onglet.bouton_oui.emit("clicked")
    assert _attendre(lambda: "f.txt" not in _noms(onglet) and not onglet.occupe)
    onglet.selectionner("vide")
    onglet.demander_suppression()
    assert onglet.question.get_text() == "Delete the empty folder vide?"
    onglet.bouton_oui.emit("clicked")
    assert _attendre(lambda: "vide" not in _noms(onglet) and not onglet.occupe)
    onglet.selectionner("plein")
    onglet.demander_suppression()
    onglet.bouton_oui.emit("clicked")
    assert _libre(onglet) and onglet.etat.get_text().startswith("Error: /accueil/plein")
    onglet.demander_dossier()
    assert not onglet.bouton_oui.has_css_class("destructive-action")


# ── Fermeture ────────────────────────────────────────────────────────────


def test_fermeture(plugin, serveur, connue, telechargements):
    """Fermer arrête le fil et la connexion ; un résultat tardif est ignoré ; deux fermetures sans effet."""
    session = plugin.open(_hote(serveur))
    fenetre = _afficher(session)
    assert _attendre(lambda: session.connecte and not session.occupe)
    session.close()
    session.close()
    session._fil.join(10)
    assert not session._fil.is_alive() and not session.connexion.ouverte
    appels = []
    assert session._terminer(appels.append, 1) is False and appels == []
    session._mot_de_passe_saisi(MOT_DE_PASSE)
    assert session.occupe is False
    assert session._afficher_progression(0.5) is False and not session.progres.get_visible()
    fenetre.destroy()
