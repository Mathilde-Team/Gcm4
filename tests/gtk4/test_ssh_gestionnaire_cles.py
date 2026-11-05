# SPDX-License-Identifier: GPL-3.0-or-later
"""Gestionnaire de clés SSH GTK4 (``plugins/ssh/gtk4.py``, #97, 3/3).

Clés réelles (``ssh-keygen``), ``ssh-agent`` isolé, et un faux ``ssh`` sur
le ``PATH`` qui exécute la commande distante localement dans une maison de
test : aucun réseau. Les dialogues sont pilotés en émettant ``response``
sur ``fenetre.dialogue``.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from gi.repository import GLib, Gtk

from gcm4.core import registre

RACINE = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def module():
    """``gcm4_plugin_ssh.gtk4`` chargé par le registre, en mode source."""
    candidats = [c for c in registre.candidats_dossier(RACINE / "plugins") if c.nom == "ssh"]
    registre.decouvrir(lambda _nom: None, candidats)
    yield sys.modules["gcm4_plugin_ssh.gtk4"]
    registre._oublier_paquet("gcm4_plugin_ssh")


def _attendre(condition, delai=10.0):
    """Traite les événements GTK jusqu'à ``condition()`` ou ``delai`` secondes."""
    contexte = GLib.MainContext.default()
    fin = time.monotonic() + delai
    while not condition() and time.monotonic() < fin:
        contexte.iteration(False)
        time.sleep(0.005)
    return condition()


def _paire(dossier: Path, nom: str = "id_test", phrase: str = "") -> Path:
    """Paire ed25519 générée par ssh-keygen dans ``dossier``."""
    dossier.mkdir(parents=True, exist_ok=True)
    chemin = dossier / nom
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", phrase, "-C", "test", "-f", str(chemin)],
        check=True,
    )
    return chemin


@pytest.fixture
def agent(monkeypatch):
    """ssh-agent isolé pour le test (arrêté à la fin)."""
    sortie = subprocess.run(["ssh-agent", "-s"], capture_output=True, text=True, check=True)
    valeurs = dict(
        morceau.split("=", 1)
        for ligne in sortie.stdout.splitlines()
        for morceau in [ligne.split(";", 1)[0]]
        if "=" in morceau
    )
    monkeypatch.setenv("SSH_AUTH_SOCK", valeurs["SSH_AUTH_SOCK"])
    monkeypatch.setenv("SSH_AGENT_PID", valeurs["SSH_AGENT_PID"])
    yield
    subprocess.run(["ssh-agent", "-k"], capture_output=True, check=False)


@pytest.fixture
def faux_ssh(tmp_path, monkeypatch):
    """``ssh`` qui exécute localement la commande distante, ``HOME`` = maison de test."""
    maison = tmp_path / "distant"
    maison.mkdir()
    binaires = tmp_path / "bin"
    binaires.mkdir()
    script = binaires / "ssh"
    script.write_text(
        '#!/bin/sh\nfor dernier; do :; done\nHOME="$FAUX_HOME" exec sh -c "$dernier"\n',
        encoding="utf-8",
    )
    script.chmod(0o755)
    monkeypatch.setenv("FAUX_HOME", str(maison))
    monkeypatch.setenv("PATH", f"{binaires}{os.pathsep}{os.environ['PATH']}")
    return maison


@pytest.fixture
def dossier(tmp_path, monkeypatch):
    """``~/.ssh`` de test (``HOME`` isolé) avec une paire ``id_test``."""
    monkeypatch.setenv("HOME", str(tmp_path))
    ssh = tmp_path / ".ssh"
    _paire(ssh)
    return ssh


@pytest.fixture
def ouvrir(module, dossier):
    """Fabrique de gestionnaires affichés, chargements terminés."""
    ouverts = []

    def fabrique(**options):
        options.setdefault("dossier", dossier)
        fenetre = module.SshKeyManager(**options)
        fenetre.present()
        ouverts.append(fenetre)
        assert _attendre(lambda: fenetre.travaux == 0)
        return fenetre

    yield fabrique
    for fenetre in ouverts:
        # Une fenêtre déjà fermée par le test est détruite : la détruire une
        # seconde fois fait planter GTK 4.8 (Debian 12).
        if not fenetre.ferme:
            fenetre.close()
    _attendre(lambda: False, 0.05)


def test_ouverture(module, ouvrir):
    """Les quatre onglets sont chargés ; la clé de test est listée."""
    fenetre = ouvrir()
    assert fenetre.get_title() == module._("SSH Key Manager")
    assert fenetre.onglets.get_n_pages() == 4
    assert [ligne.cle.nom for ligne in fenetre.privees.valeurs()] == ["id_test"]
    assert fenetre.orphelines.valeurs() == []
    assert fenetre.dernier_message == module._(
        "{priv} private key(s), {pub} Orphan Public key(s) loaded from ~/.ssh/"
    ).format(priv=1, pub=0)
    assert fenetre.etat_cible.get_text() == module._("local machine")
    assert fenetre.etat_ca.get_text() == module._("(none — keys are generated unsigned)")
    assert fenetre.actions_cles.get_visible()
    assert not fenetre.actions_known_hosts.get_visible()
    assert not fenetre.bouton_copier.get_sensitive()
    assert not fenetre.bouton_supprimer.get_sensitive()
    fenetre.privees.selection.set_selected(0)
    assert fenetre.bouton_copier.get_sensitive() and fenetre.bouton_agent.get_sensitive()


# -- outils de test ------------------------------------------------------------


def _repondre(fenetre, reponse):
    """Répond ``reponse`` au dialogue ouvert, puis le ferme."""
    dialogue = fenetre.dialogue
    dialogue.emit("response", reponse)
    if hasattr(dialogue, "force_close"):
        dialogue.force_close()
    else:  # libadwaita < 1.5 : Adw.MessageDialog
        dialogue.destroy()


def _raccourci(fenetre, declencheur):
    """Déclenche le raccourci ``declencheur`` de ``fenetre``."""
    controleurs = fenetre.observe_controllers()
    for i in range(controleurs.get_n_items()):
        controleur = controleurs.get_item(i)
        if not isinstance(controleur, Gtk.ShortcutController):
            continue
        for j in range(controleur.get_n_items()):
            raccourci = controleur.get_item(j)
            if raccourci.get_trigger().to_string() == declencheur:
                raccourci.get_action().activate(Gtk.ShortcutActionFlags(0), fenetre, None)
                return
    raise AssertionError(declencheur)


def _descendants(widget):
    """Tous les widgets sous ``widget``."""
    enfant = widget.get_first_child()
    while enfant is not None:
        yield enfant
        yield from _descendants(enfant)
        enfant = enfant.get_next_sibling()


def _presse_papiers(widget):
    """Texte du presse-papiers de ``widget``."""
    recu = []
    widget.get_clipboard().read_text_async(None, lambda c, r: recu.append(c.read_text_finish(r)))
    assert _attendre(lambda: recu)
    return recu[0]


def _choisir(tableau, nom):
    """Sélectionne la clé ``nom`` dans ``tableau``."""
    noms = [ligne.cle.nom for ligne in tableau.valeurs()]
    tableau.selection.set_selected(noms.index(nom))


@pytest.fixture
def selecteur(module, monkeypatch):
    """``fichiers.choisir`` factice : enregistre ``(titre, rappel, filtrer)``."""
    appels = []

    def choisir(_parent, mode, titre, rappel, nom="", filtrer=True):
        appels.append((mode, titre, rappel, filtrer))
        return "sélecteur"

    monkeypatch.setattr(module.fichiers, "choisir", choisir)
    return appels


@pytest.fixture(autouse=True)
def pas_root(module, monkeypatch):
    """Compte local ordinaire (le garde-fou root est testé à part)."""
    monkeypatch.setattr(module.core.FichiersLocaux, "utilisateur", lambda _self: "moi")


def _erreur(module, code, detail=""):
    """Fonction qui lève ``ErreurCle(code, detail)``."""

    def lever(*_args, **_kwargs):
        raise module.core.ErreurCle(code, detail)

    return lever


# -- ouverture, onglets, raccourcis -------------------------------------------


def test_onglets_et_messages(module, ouvrir, dossier):
    """Barre d'actions par onglet ; message de la cible à l'affichage de l'onglet."""
    (dossier / "known_hosts").write_text("", encoding="utf-8")
    fenetre = ouvrir()
    fenetre.onglets.set_current_page(module.PAGE_KNOWN_HOSTS)
    assert fenetre.actions_known_hosts.get_visible() and not fenetre.actions_cles.get_visible()
    assert fenetre.dernier_message == module._("{n} known_hosts entries loaded from {t}").format(
        n=0, t=module._("local machine")
    )
    fenetre.onglets.set_current_page(module.PAGE_AUTHORIZED_KEYS)
    assert fenetre.actions_authorized_keys.get_visible()
    assert fenetre.dernier_message == module._("authorized_keys not found on {t}").format(
        t=module._("local machine")
    )
    fenetre.charger_cible()  # notifier=True sur l'onglet affiché
    assert _attendre(lambda: fenetre.travaux == 0)
    assert fenetre.dernier_message.startswith("authorized_keys not found")
    fenetre.onglets.set_current_page(module.PAGE_ORPHELINES)
    assert fenetre.actions_cles.get_visible()
    assert fenetre.cle_choisie() is None
    fenetre.onglets.set_current_page(module.PAGE_KNOWN_HOSTS)
    assert fenetre.cle_choisie() is None  # pas un onglet de clés


def test_parent_et_dossier_par_defaut(module, dossier):
    """Fenêtre parente reprise ; ``~/.ssh`` par défaut ; fermeture par Close."""
    parent = Gtk.Window()
    try:
        fenetre = module.SshKeyManager(parent=parent)
        assert fenetre.get_transient_for() is parent
        assert fenetre.dossier == dossier
        assert _attendre(lambda: fenetre.travaux == 0)
        fenetre.present()
        fenetre.bouton_fermer.emit("clicked")
        assert fenetre.ferme
    finally:
        parent.destroy()


def test_dossier_absent(module, ouvrir, tmp_path):
    """Sans ``~/.ssh/`` : onglets de clés vides, message."""
    fenetre = ouvrir(dossier=tmp_path / "rien")
    assert fenetre.privees.valeurs() == [] and fenetre.orphelines.valeurs() == []
    assert fenetre.dernier_message == module._("~/.ssh/ not found — no keys loaded")


def test_raccourcis(module, ouvrir, selecteur):
    """Ctrl+N, Ctrl+I, Suppr et Échap."""
    fenetre = ouvrir()
    _raccourci(fenetre, "<Control>n")
    assert isinstance(fenetre.dialogue, module.GenererCleDialog)
    _raccourci(fenetre.dialogue, "Escape")
    _raccourci(fenetre, "<Control>i")
    assert selecteur[-1][1] == module._("Import Private Key") and selecteur[-1][3] is False
    _choisir(fenetre.privees, "id_test")
    _raccourci(fenetre, "Delete")
    assert fenetre.dialogue is not None
    _repondre(fenetre, "cancel")
    _raccourci(fenetre, "Escape")
    assert fenetre.ferme


def test_fermeture_pendant_un_travail(module, ouvrir):
    """Un travail fini après la fermeture n'appelle plus sa suite."""
    fenetre = ouvrir()
    fins = []
    verrou = __import__("threading").Event()
    fenetre.en_fond(verrou.wait, lambda r, e: fins.append(r))
    fenetre.close()
    verrou.set()
    assert _attendre(lambda: fenetre.travaux == 0)
    assert fins == []


# -- lignes et tableaux -------------------------------------------------------


def _ligne(module, tmp_path, certificat=None, creation=None, publique=True):
    """:class:`LigneGestionCle` sur une paire de ``tmp_path``."""
    import tempfile

    lignes = Path(tempfile.mkdtemp(dir=tmp_path))
    chemin = _paire(lignes, "id_l")
    if not publique:
        chemin.with_name("id_l.pub").unlink()
    (cle,) = module.core.lister_cles(lignes)
    details = module.core.DetailsCle("ED25519", "SHA256:x", creation, certificat)
    return module.LigneGestionCle(cle, details)


def test_ligne_gestion_cle(module, tmp_path):
    """Colonnes Validity, Signed by, Created, Perms ; clé expirée."""
    from datetime import datetime

    info = module.core.InfoCertificat
    sans = _ligne(module, tmp_path)
    assert (sans.validite(), sans.signataire(), sans.creation()) == ("—", "—", "?")
    assert not sans.expiree and sans.publique == sans.cle.publique
    assert sans.permissions() == "600 ✓"
    toujours = _ligne(module, tmp_path, info(None, False, None), datetime(2026, 11, 5, 9, 30))
    assert toujours.validite() == module._("forever")
    assert toujours.signataire() == module._("unknown CA")
    assert toujours.creation() == "2026-11-05 09:30"
    inconnue = _ligne(module, tmp_path, info("?", False, "ED25519 SHA256:ca"))
    assert inconnue.validite() == "?" and inconnue.signataire() == "ED25519 SHA256:ca"
    finie = _ligne(module, tmp_path, info("2020-01-02T00:00:00", True, None))
    assert finie.validite() == module._("until {end}").format(end="2020-01-02T00:00:00")
    assert finie.expiree


def test_tableau_tri_alerte_et_coche(module, ouvrir):
    """Tri des colonnes, classe ``error`` des lignes signalées, cases à cocher."""
    resultats = [("b", False, ""), ("a", True, "a ssh-ed25519 …")]
    parent = ouvrir()
    dialogue = module.ResultatsKnownHostsDialog(parent, "local", resultats)
    dialogue.present()
    _attendre(lambda: False, 0.3)
    etiquettes = [w for w in _descendants(dialogue.tableau.vue) if isinstance(w, Gtk.Label)]
    assert any(w.has_css_class("error") for w in etiquettes)
    assert any(w.get_text() == "a" and not w.has_css_class("error") for w in etiquettes)
    colonnes = dialogue.tableau.vue.get_columns()
    dialogue.tableau.vue.sort_by_column(colonnes.get_item(0), Gtk.SortType.ASCENDING)
    assert [r[0] for r in dialogue.tableau.valeurs()] == ["a", "b"]
    assert dialogue.tableau.choisie() is None
    dialogue.tableau.selection.set_selected(1)
    assert dialogue.tableau.choisie() == ("b", False, "")
    _raccourci(dialogue, "Escape")

    coches = []
    tableau = module.Tableau([("T", str, True)], coche=("C", bool, coches.append))
    tableau.remplir([1, 0])
    fenetre = Gtk.Window(child=tableau.defilement, default_width=300, default_height=200)
    fenetre.present()
    _attendre(lambda: False, 0.3)
    tableau.vue.sort_by_column(tableau.vue.get_columns().get_item(0), Gtk.SortType.ASCENDING)
    _attendre(lambda: False, 0.1)
    cases = [w for w in _descendants(tableau.vue) if isinstance(w, Gtk.CheckButton)]
    assert sorted(c.get_active() for c in cases) == [False, True]
    (eteinte,) = [c for c in cases if not c.get_active()]
    eteinte.set_active(True)  # le rappel décide ; la case reprend son état
    assert coches == [0] and not eteinte.get_active()
    fenetre.destroy()


# -- génération ---------------------------------------------------------------


def test_formulaire_generation(module, ouvrir):
    """Type, taille RSA, champs de signature, valeurs par défaut."""
    recus = []
    fenetre = ouvrir()
    dialogue = module.GenererCleDialog(fenetre, None, recus.append)
    assert not dialogue.signer.get_visible() and not dialogue.taille.get_visible()
    assert dialogue.nom.get_text() == "id_ed25519"
    dialogue.type.set_selected(module.TYPES_CLE.index("rsa"))
    assert dialogue.taille.get_visible() and dialogue.nom.get_text() == "id_rsa"
    dialogue.nom.set_text("")
    dialogue.commentaire.set_text("")
    options = dialogue.options()
    assert options["nom"] == "id_rsa" and options["taille"] == 4096
    assert options["commentaire"] == module.core.commentaire_par_defaut()
    assert options["signer"] is False
    dialogue.destroy()

    signe = module.GenererCleDialog(fenetre, "ca_test", recus.append)
    assert signe.signer.get_visible() and not signe.identite.get_visible()
    signe.signer.set_active(True)
    assert signe.identite.get_visible()
    signe.commentaire.set_text("alice@poste")
    for champ in (signe.identite, signe.principaux, signe.validite):
        champ.set_text("")
    options = signe.options()
    assert options["signer"] is True and options["taille"] == 0
    assert (options["identite"], options["principaux"], options["validite"]) == (
        "alice@poste",
        "alice",
        "+52w",
    )
    signe.bouton_generer.emit("clicked")
    assert recus == [options]


def test_generer_une_cle(module, ouvrir, dossier):
    """« ⚙ Generate » : la clé est créée en arrière-plan puis listée."""
    fenetre = ouvrir()
    fenetre.bouton_generer.emit("clicked")
    dialogue = fenetre.dialogue
    dialogue.nom.set_text("id_test")  # déjà pris : id_test_1
    dialogue.bouton_generer.emit("clicked")
    assert _attendre(lambda: fenetre.travaux == 0)
    assert (dossier / "id_test_1").exists()
    assert sorted(ligne.cle.nom for ligne in fenetre.privees.valeurs()) == ["id_test", "id_test_1"]


def _ca(dossier: Path) -> Path:
    """CA de test hors de ``~/.ssh``."""
    return _paire(dossier, "ca_test")


def test_generer_et_signer(module, ouvrir, dossier, tmp_path):
    """Clé signée par la CA désignée ; certificat expiré signalé."""
    fenetre = ouvrir()
    ca = _ca(dossier)
    fenetre.designer_ca(str(ca))
    options = module.GenererCleDialog(fenetre, ca.name, lambda _o: None).options()
    options.update(nom="id_signee", signer=True, identite="alice", principaux="alice")
    fenetre.generer(options)
    assert _attendre(lambda: fenetre.travaux == 0)
    assert fenetre.dernier_message == module._("Key signed by CA {ca} (identity={id})").format(
        ca="ca_test", id="alice"
    )
    assert (dossier / "id_signee-cert.pub").exists()
    options.update(nom="id_perimee", validite="20200101:20200102")
    fenetre.generer(options)
    assert _attendre(lambda: fenetre.travaux == 0)
    (perimee,) = [ligne for ligne in fenetre.privees.valeurs() if ligne.cle.nom == "id_perimee"]
    assert perimee.expiree
    _attendre(lambda: False, 0.3)  # rendu des lignes signalées


@pytest.mark.parametrize(
    ("code", "attendu"),
    [
        ("absent", "ssh-keygen not found — install openssh-client"),
        ("echec", "ssh-keygen failed: {err}"),
        ("systeme", "Generation failed: {err}"),
    ],
)
def test_generation_refusee(module, ouvrir, monkeypatch, code, attendu):
    """Échec de ssh-keygen : message selon le code."""
    fenetre = ouvrir()
    monkeypatch.setattr(module.core, "generer_cle", _erreur(module, code, "boum"))
    fenetre.generer(module.GenererCleDialog(fenetre, None, lambda _o: None).options())
    assert _attendre(lambda: fenetre.travaux == 0)
    assert fenetre.dernier_message == module._(attendu).format(
        err="boum" if code == "echec" else code
    )


@pytest.mark.parametrize("code", ["delai", "echec"])
def test_signature_refusee(module, ouvrir, dossier, monkeypatch, code):
    """Clé générée mais signature impossible."""
    fenetre = ouvrir()
    fenetre.designer_ca(str(_ca(dossier)))
    monkeypatch.setattr(module.core, "signer_cle", _erreur(module, code, "refus"))
    options = module.GenererCleDialog(fenetre, "ca_test", lambda _o: None).options()
    options.update(nom="id_x", signer=True)
    fenetre.generer(options)
    assert _attendre(lambda: fenetre.travaux == 0)
    if code == "delai":
        assert fenetre.dernier_message.startswith("CA signing timed out")
    else:
        assert fenetre.dernier_message == module._("CA signing failed: {err}").format(err="refus")
    assert (dossier / "id_x").exists()


# -- import -------------------------------------------------------------------


def test_importer(module, ouvrir, dossier, tmp_path, selecteur, monkeypatch):
    """Import d'une paire ; confirmation si le nom existe ; annulation ; échec."""
    fenetre = ouvrir()
    fenetre.bouton_importer.emit("clicked")
    assert selecteur[-1][2] == fenetre.importer
    fenetre.importer(None)
    source = _paire(tmp_path / "ailleurs", "id_ext")
    fenetre.importer(str(source))
    assert (dossier / "id_ext").exists()
    assert fenetre.dernier_message == module._("Key imported: {name}").format(name="id_ext")
    fenetre.importer(str(source))  # existe : confirmation
    _repondre(fenetre, "cancel")
    fenetre.importer(str(source))
    monkeypatch.setattr(module.core, "importer_paire", _erreur(module, "systeme", "disque"))
    _repondre(fenetre, "ok")
    assert fenetre.dernier_message == module._("Import failed: {err}").format(err="disque")


# -- clé publique -------------------------------------------------------------


def test_copier_et_voir_la_publique(module, ouvrir, dossier, tmp_path):
    """Copie et affichage de la .pub ; aucune sélection ; .pub illisible."""
    fenetre = ouvrir()
    fenetre._on_copier_publique(None)
    assert fenetre.dernier_message == module._("No Public key available for selected entry")
    fenetre._on_voir_publique(None)
    assert fenetre.dernier_message == module._("No public key available for selected entry")
    _choisir(fenetre.privees, "id_test")
    contenu = (dossier / "id_test.pub").read_text(encoding="utf-8").strip()
    fenetre.bouton_copier.emit("clicked")
    assert fenetre.dernier_message == module._("Public key copied to clipboard")
    assert _presse_papiers(fenetre) == contenu
    fenetre.bouton_voir.emit("clicked")
    vue = fenetre.dialogue
    assert isinstance(vue, module.VuePubliqueDialog)
    assert module._texte_tampon(vue.texte) == contenu
    vue.get_clipboard().set("autre")
    vue.bouton_copier.emit("clicked")
    assert _presse_papiers(vue) == contenu
    _raccourci(vue, "Escape")

    ligne = _ligne(module, tmp_path)
    ligne.cle.publique.unlink()
    ligne.cle.publique.mkdir()  # IsADirectoryError
    assert fenetre._lire_publique(ligne, "absente") is None
    assert fenetre.dernier_message.startswith("Cannot read public key")


def test_cle_publique_orpheline(module, ouvrir, dossier):
    """Une .pub seule : onglet Orphan Public keys, copiable, pas de phrase ni d'agent."""
    (dossier / "id_test").unlink()
    fenetre = ouvrir()
    fenetre.onglets.set_current_page(module.PAGE_ORPHELINES)
    _choisir(fenetre.orphelines, "id_test.pub")
    assert fenetre.bouton_copier.get_sensitive() and not fenetre.bouton_phrase.get_sensitive()
    fenetre._on_phrase(None)
    assert fenetre.dernier_message == module._("No private key selected")
    fenetre._on_agent(None)
    assert fenetre.dernier_message == module._("No private key selected")


# -- renommage ----------------------------------------------------------------


def test_renommer(module, ouvrir, dossier, monkeypatch):
    """Renommage ; nom inchangé ; nom invalide, existant ; échec."""
    fenetre = ouvrir()
    fenetre._on_renommer(None)
    assert fenetre.dernier_message == module._("No key selected")
    _choisir(fenetre.privees, "id_test")
    fenetre.bouton_renommer.emit("clicked")
    fenetre.saisie_nom.set_text(" id_neuf ")
    _repondre(fenetre, "ok")
    assert (dossier / "id_neuf").exists() and (dossier / "id_neuf.pub").exists()
    assert fenetre.dernier_message == module._("Renamed {old} to {new}").format(
        old="id_test", new="id_neuf"
    )
    (ligne,) = fenetre.privees.valeurs()
    message = fenetre.dernier_message
    fenetre.renommer(ligne, "id_neuf")
    fenetre.renommer(ligne, "")
    assert fenetre.dernier_message == message
    fenetre.renommer(ligne, "a/b")
    assert fenetre.dernier_message == module._("Invalid name — must not contain a path separator")
    _paire(dossier, "id_autre")
    fenetre.renommer(ligne, "id_autre")
    assert fenetre.dernier_message == module._("A file named {name} already exists").format(
        name="id_autre"
    )
    monkeypatch.setattr(module.core, "renommer_cle", _erreur(module, "systeme", "ro"))
    fenetre.renommer(ligne, "id_z")
    assert fenetre.dernier_message == module._("Rename failed: {err}").format(err="ro")


# -- phrase de passe ----------------------------------------------------------


def test_phrase_de_passe(module, ouvrir, dossier):
    """Changement de phrase ; confirmation différente ; ancienne phrase fausse."""
    fenetre = ouvrir()
    _choisir(fenetre.privees, "id_test")
    fenetre.bouton_phrase.emit("clicked")
    ancienne, nouvelle, confirmation = fenetre.phrases
    nouvelle.set_text("secret")
    confirmation.set_text("autre")
    _repondre(fenetre, "ok")
    assert fenetre.dernier_message == module._("New passphrase and confirmation do not match")
    fenetre._on_phrase(None)
    fenetre.phrases[1].set_text("secret")
    fenetre.phrases[2].set_text("secret")
    _repondre(fenetre, "ok")
    assert fenetre.dernier_message == module._("Passphrase changed for {name}").format(
        name="id_test"
    )
    (ligne,) = fenetre.privees.valeurs()
    fenetre.changer_phrase(ligne, "fausse", "x", "x")
    assert fenetre.dernier_message.startswith("Passphrase change failed")
    fenetre._on_phrase(None)
    _repondre(fenetre, "cancel")


# -- ssh-agent ----------------------------------------------------------------


def test_agent(module, ouvrir, dossier, agent):
    """Ajout puis retrait de ssh-agent, après confirmation."""
    fenetre = ouvrir()
    _choisir(fenetre.privees, "id_test")
    fenetre.bouton_agent.emit("clicked")
    _repondre(fenetre, "ok")
    assert fenetre.dernier_message == module._("Added to ssh-agent")
    fenetre._on_agent(None)
    _repondre(fenetre, "ok")
    assert fenetre.dernier_message == module._("Removed from ssh-agent")


def test_agent_sans_publique(module, ouvrir, dossier):
    """Sans .pub, la clé ne peut pas être identifiée dans l'agent."""
    (dossier / "id_test.pub").unlink()
    fenetre = ouvrir()
    _choisir(fenetre.privees, "id_test")
    fenetre._on_agent(None)
    assert fenetre.dernier_message == module._(
        "No public key available to identify this key in the agent"
    )


@pytest.mark.parametrize(
    ("code", "debut"),
    [("delai", "ssh-add timed out"), ("echec", "Failed to add"), ("absent", "ssh-add failed")],
)
def test_agent_ajout_refuse(module, ouvrir, monkeypatch, code, debut):
    """``ssh-add`` refusé : message selon le code."""
    fenetre = ouvrir()
    monkeypatch.setattr(module.core, "ajouter_agent", _erreur(module, code, "phrase"))
    (ligne,) = fenetre.privees.valeurs()
    fenetre.ajouter_agent(ligne)
    assert fenetre.dernier_message.startswith(debut)


@pytest.mark.parametrize(("code", "debut"), [("echec", "Failed to remove"), ("absent", "ssh-add")])
def test_agent_retrait_refuse(module, ouvrir, monkeypatch, code, debut):
    """``ssh-add -d`` refusé : message selon le code."""
    fenetre = ouvrir()
    monkeypatch.setattr(module.core, "retirer_agent", _erreur(module, code, "absente"))
    (ligne,) = fenetre.privees.valeurs()
    fenetre.retirer_agent(ligne)
    assert fenetre.dernier_message.startswith(debut)


# -- révéler, supprimer -------------------------------------------------------


def test_reveler(module, ouvrir, dossier, monkeypatch):
    """« 📂 Reveal » ouvre ``~/.ssh/`` ; message si impossible."""
    fenetre = ouvrir()
    uris = []
    monkeypatch.setattr(
        module.Gio.AppInfo, "launch_default_for_uri", lambda uri, _ctx: uris.append(uri)
    )
    fenetre.bouton_reveler.emit("clicked")
    assert uris == [module.Gio.File.new_for_path(str(dossier)).get_uri()]

    def refus(_uri, _ctx):
        raise GLib.Error("aucune application")

    monkeypatch.setattr(module.Gio.AppInfo, "launch_default_for_uri", refus)
    fenetre._on_reveler(None)
    assert fenetre.dernier_message.startswith("Cannot open file manager")


def test_supprimer(module, ouvrir, dossier, monkeypatch):
    """Suppression confirmée ; aucune sélection ; échec."""
    _paire(dossier, "id_b")
    fenetre = ouvrir()
    fenetre._on_supprimer(None)
    assert fenetre.dialogue is None
    _choisir(fenetre.privees, "id_b")
    fenetre.bouton_supprimer.emit("clicked")
    _repondre(fenetre, "ok")
    assert not (dossier / "id_b").exists()
    assert fenetre.dernier_message == module._("Key deleted: {name}").format(name="id_b")
    monkeypatch.setattr(module.core, "supprimer_cle", _erreur(module, "systeme", "ro"))
    (ligne,) = fenetre.privees.valeurs()
    fenetre.supprimer(ligne)
    assert fenetre.dernier_message == module._("Delete failed: {err}").format(err="ro")


# -- CA de signature ----------------------------------------------------------


def test_designer_la_ca(module, ouvrir, dossier, tmp_path, selecteur):
    """Désignation : annulée, .pub choisie, sans .pub, puis acceptée et relue."""
    fenetre = ouvrir()
    fenetre.bouton_designer_ca.emit("clicked")
    assert selecteur[-1][1:] == (module._("Designate Signing CA"), fenetre.designer_ca, False)
    fenetre.designer_ca(None)
    ca = _ca(tmp_path / "cas")
    fenetre.designer_ca(str(ca) + ".pub")
    assert fenetre.dernier_message == module._("Select the private key file, not the .pub file")
    seule = tmp_path / "cas" / "seule"
    seule.write_text("x", encoding="utf-8")
    fenetre.designer_ca(str(seule))
    assert fenetre.dernier_message.startswith("No matching .pub file found next to seule")
    fenetre.designer_ca(str(ca))
    assert fenetre.ca == ca
    assert fenetre.etat_ca.get_text().startswith("ca_test — ED25519 SHA256:")
    assert module.core.lire_config_ca(dossier / ".gcm_ca_config.json") == ca
    ca.with_name("ca_test.pub").unlink()  # empreinte lue sur la privée
    fenetre.afficher_ca()
    assert fenetre.etat_ca.get_text().startswith("ca_test")
    autre = ouvrir()
    assert autre.ca == ca
    autre._on_generer(None)
    assert autre.dialogue.signer.get_visible()


def test_importer_la_ca(module, ouvrir, dossier, tmp_path, selecteur, monkeypatch):
    """Import de CA : annulé, sans .pub, accepté, confirmation, échec."""
    fenetre = ouvrir()
    fenetre.bouton_importer_ca.emit("clicked")
    assert selecteur[-1][2] == fenetre.importer_ca
    fenetre.importer_ca(None)
    seule = tmp_path / "seule"
    seule.write_text("x", encoding="utf-8")
    fenetre.importer_ca(str(seule))
    assert fenetre.dernier_message.startswith("No matching .pub file found next to seule")
    ca = _ca(tmp_path / "cas")
    fenetre.importer_ca(str(ca))
    assert fenetre.ca == dossier / "ca_test"
    assert fenetre.dernier_message == module._("CA imported and designated: {name}").format(
        name="ca_test"
    )
    monkeypatch.setattr(module.core, "importer_paire", _erreur(module, "systeme", "ro"))
    fenetre.importer_ca(str(ca))  # existe : confirmation
    _repondre(fenetre, "ok")
    assert fenetre.dernier_message == module._("CA import failed: {err}").format(err="ro")


def test_supprimer_la_ca(module, ouvrir, dossier, tmp_path):
    """Suppression de la CA : aucune, confirmée, échec."""
    fenetre = ouvrir()
    fenetre.bouton_supprimer_ca.emit("clicked")
    assert fenetre.dernier_message == module._("No CA currently designated")
    ca = _ca(dossier)
    fenetre.designer_ca(str(ca))
    fenetre._on_supprimer_ca(None)
    _repondre(fenetre, "ok")
    assert not ca.exists() and not ca.with_name("ca_test.pub").exists()
    assert fenetre.ca is None
    assert fenetre.dernier_message == module._("CA deleted: {name}").format(name="ca_test")
    repertoire = tmp_path / "pas_un_fichier"
    repertoire.mkdir()
    fenetre.ca = repertoire
    fenetre.supprimer_ca()
    assert fenetre.dernier_message.startswith("CA deletion failed")


# -- cible distante -----------------------------------------------------------


def _publique(dossier: Path, nom: str = "id_test") -> str:
    """Ligne de la clé publique ``nom``."""
    return (dossier / f"{nom}.pub").read_text(encoding="utf-8").strip()


def test_connexion_distante(module, ouvrir, dossier, faux_ssh):
    """Connexion : vide, invalide, réussie (fichiers distants), retour en local."""
    fenetre = ouvrir()
    fenetre.saisie_cible.set_text("")
    fenetre.bouton_connecter.emit("clicked")
    assert fenetre.dernier_message == module._("Switched back to local machine")
    fenetre.saisie_cible.set_text("pas une cible")
    fenetre._on_connecter(None)
    assert fenetre.dernier_message == module._(
        "Invalid target — expected user@host or user@host:port"
    )
    distant_ssh = faux_ssh / ".ssh"
    distant_ssh.mkdir()
    (distant_ssh / "known_hosts").write_text(
        f"hote1 {_publique(dossier)}\n|1|abc=|def= {_publique(dossier)}\n", encoding="utf-8"
    )
    fenetre.saisie_cible.set_text("alice@serveur:2222")
    fenetre.saisie_cible.emit("activate")
    assert not fenetre.bouton_connecter.get_sensitive()
    assert _attendre(lambda: fenetre.travaux == 0)
    assert fenetre.bouton_connecter.get_sensitive()
    assert fenetre.distant is not None
    assert fenetre.etat_cible.get_text() == "alice@serveur:2222"
    assert [e.hotes for e in fenetre.known_hosts.valeurs()] == ["hote1", ""]
    fenetre.onglets.set_current_page(module.PAGE_KNOWN_HOSTS)
    _attendre(lambda: False, 0.3)  # rendu : « (hashed) »
    etiquettes = [
        w.get_text() for w in _descendants(fenetre.known_hosts.vue) if isinstance(w, Gtk.Label)
    ]
    assert module._("(hashed)") in etiquettes
    fenetre.onglets.set_current_page(module.PAGE_AUTHORIZED_KEYS)
    assert fenetre.dernier_message == module._("authorized_keys not found on {t}").format(
        t="alice@serveur:2222"
    )
    fenetre.bouton_local.emit("clicked")
    assert fenetre.distant is None and fenetre.saisie_cible.get_text() == ""
    assert _attendre(lambda: fenetre.travaux == 0)
    assert fenetre.known_hosts.valeurs() == []


def test_connexion_impossible(module, ouvrir, monkeypatch):
    """Cible injoignable : message, la cible locale reste active."""
    fenetre = ouvrir()
    monkeypatch.setattr(module.core.FichiersDistants, "tester", lambda _self: "refusée")
    fenetre.saisie_cible.set_text("alice@serveur")
    fenetre._on_connecter(None)
    assert _attendre(lambda: fenetre.travaux == 0)
    assert fenetre.distant is None
    assert fenetre.dernier_message == module._("Connection failed: {err}").format(err="refusée")


def test_cible_changee_entre_temps(module, ouvrir, tmp_path):
    """Une lecture d'une ancienne cible n'écrase pas les onglets."""
    fenetre = ouvrir()
    ancienne = module.core.FichiersLocaux(tmp_path)
    fenetre._cible_lue(ancienne, ("hote ssh-ed25519 AAAA\n", None), True)
    assert fenetre.known_hosts.valeurs() == []


# -- known_hosts --------------------------------------------------------------


def test_known_hosts_empreinte_et_suppression(module, ouvrir, dossier, monkeypatch):
    """Copie d'empreinte ; suppression confirmée ; ligne changée ; écriture refusée."""
    (dossier / "known_hosts").write_text(
        f"hote1 {_publique(dossier)}\nhote2 ssh-ed25519 pas-du-base64\n", encoding="utf-8"
    )
    fenetre = ouvrir()
    fenetre.onglets.set_current_page(module.PAGE_KNOWN_HOSTS)
    fenetre.bouton_copier_empreinte.emit("clicked")
    assert fenetre.dernier_message == module._("No known_hosts entry selected")
    premiere, seconde = fenetre.known_hosts.valeurs()
    fenetre.known_hosts.selection.set_selected(1)
    fenetre._on_copier_empreinte(None)
    assert fenetre.dernier_message == module._("No fingerprint available for this entry")
    fenetre.known_hosts.selection.set_selected(0)
    fenetre._on_copier_empreinte(None)
    assert _presse_papiers(fenetre) == premiere.empreinte
    fenetre.bouton_supprimer_kh.emit("clicked")
    _repondre(fenetre, "ok")
    assert _attendre(lambda: fenetre.travaux == 0)
    assert fenetre.dernier_message == module._("known_hosts entry removed")
    assert "hote1" not in (dossier / "known_hosts").read_text(encoding="utf-8")
    assert [e.hotes for e in fenetre.known_hosts.valeurs()] == ["hote2"]

    fenetre._retrait(premiere, "known_hosts", 0o644, ("a", "q", "act", "ok", "ko"))
    _repondre(fenetre, "ok")  # la ligne n'existe plus
    assert _attendre(lambda: fenetre.travaux == 0)
    assert fenetre.dernier_message == module._(
        "Entry not found (file may have changed) — refreshing"
    )
    monkeypatch.setattr(module.core.FichiersLocaux, "ecrire", lambda *_a: False)
    fenetre.known_hosts.selection.set_selected(0)
    _raccourci(fenetre, "Delete")
    _repondre(fenetre, "ok")
    assert _attendre(lambda: fenetre.travaux == 0)
    assert fenetre.dernier_message == module._("Failed to update known_hosts")


def test_tester_known_hosts(module, ouvrir, dossier):
    """Test des hôtes candidats : aucun, sans known_hosts, puis résultats."""
    fenetre = ouvrir()
    fenetre.bouton_tester_kh.emit("clicked")
    assert fenetre.dernier_message.startswith("No candidate hosts found")
    (dossier / "config").write_text("Host hote1 *.lan\nHost hote2\n", encoding="utf-8")
    fenetre._on_tester_known_hosts(None)
    assert _attendre(lambda: fenetre.travaux == 0)
    assert fenetre.dernier_message == module._("known_hosts not found on {t}").format(
        t=module._("local machine")
    )
    (dossier / "known_hosts").write_text(f"hote1 {_publique(dossier)}\n", encoding="utf-8")
    fenetre._on_tester_known_hosts(None)
    assert _attendre(lambda: fenetre.travaux == 0)
    resultats = fenetre.dialogue
    assert isinstance(resultats, module.ResultatsKnownHostsDialog)
    assert [(h, t) for h, t, _i in resultats.tableau.valeurs()] == [
        ("hote1", True),
        ("hote2", False),
    ]
    assert "1/2" in resultats.get_title()


def test_hotes_candidats(module, ouvrir, dossier):
    """Alias de ~/.ssh/config puis hôtes de gcm4, sans doublon ; rappel en échec."""
    (dossier / "config").write_text("Host a b\n", encoding="utf-8")
    assert ouvrir(hotes_gcm=lambda: ["b", "", "c"]).hotes_candidats() == ["a", "b", "c"]
    assert ouvrir(hotes_gcm=lambda: None).hotes_candidats() == ["a", "b"]

    def panne():
        raise RuntimeError("base indisponible")

    assert ouvrir(hotes_gcm=panne).hotes_candidats() == ["a", "b"]


# -- authorized_keys ----------------------------------------------------------


def test_authorized_keys(module, ouvrir, dossier):
    """Ajout (vide, invalide, valide), bascule par la case, suppression."""
    fenetre = ouvrir()
    fenetre.onglets.set_current_page(module.PAGE_AUTHORIZED_KEYS)
    fenetre.bouton_ajouter_ak.emit("clicked")
    _repondre(fenetre, "ok")  # rien de saisi
    assert fenetre.travaux == 0
    fenetre._on_ajouter_authorized_key(None)
    fenetre.saisie_cle.get_buffer().set_text("pas une clé")
    _repondre(fenetre, "ok")
    assert fenetre.dernier_message == module._("This does not look like a valid public key line")
    fenetre._on_ajouter_authorized_key(None)
    fenetre.saisie_cle.get_buffer().set_text(f"  {_publique(dossier)}\n")
    _repondre(fenetre, "ok")
    assert _attendre(lambda: fenetre.travaux == 0)
    assert fenetre.dernier_message == module._("Key added to authorized_keys")
    fichier = dossier / "authorized_keys"
    assert fichier.read_text(encoding="utf-8") == _publique(dossier) + "\n"
    assert oct(fichier.stat().st_mode & 0o777) == "0o600"

    _attendre(lambda: False, 0.3)  # rendu de la case « Enabled »
    (case,) = [
        w for w in _descendants(fenetre.authorized_keys.vue) if isinstance(w, Gtk.CheckButton)
    ]
    assert case.get_active()
    case.set_active(False)
    assert case.get_active()  # l'état ne change qu'après écriture
    _repondre(fenetre, "ok")
    assert _attendre(lambda: fenetre.travaux == 0)
    assert fenetre.dernier_message == module._("authorized_keys entry updated")
    assert fichier.read_text(encoding="utf-8").startswith("#")
    (entree,) = fenetre.authorized_keys.valeurs()
    assert not entree.active
    fenetre._on_basculer_authorized_key(entree)
    _repondre(fenetre, "ok")
    assert _attendre(lambda: fenetre.travaux == 0)
    assert fichier.read_text(encoding="utf-8") == _publique(dossier) + "\n"

    fenetre._on_supprimer_authorized_key(None)
    assert fenetre.dernier_message == module._("No authorized_keys entry selected")
    fenetre.authorized_keys.selection.set_selected(0)
    _raccourci(fenetre, "Delete")
    _repondre(fenetre, "ok")
    assert _attendre(lambda: fenetre.travaux == 0)
    assert fenetre.dernier_message == module._("authorized_keys entry removed")
    assert fichier.read_text(encoding="utf-8") == ""


def test_garde_fou_root(module, ouvrir, dossier, faux_ssh):
    """Sur un compte root, une confirmation de plus ; refusée, rien n'est écrit."""
    fenetre = ouvrir()
    fenetre.saisie_cible.set_text("root@serveur")
    fenetre._on_connecter(None)
    assert _attendre(lambda: fenetre.travaux == 0)
    fenetre.ajouter_authorized_key(_publique(dossier))
    assert "root" in fenetre.dialogue.get_body()
    _repondre(fenetre, "cancel")
    assert not (faux_ssh / ".ssh" / "authorized_keys").exists()
    fenetre.ajouter_authorized_key(_publique(dossier))
    _repondre(fenetre, "ok")
    assert _attendre(lambda: fenetre.travaux == 0)
    fichier = faux_ssh / ".ssh" / "authorized_keys"
    assert fichier.read_text(encoding="utf-8") == _publique(dossier) + "\n"
    (entree,) = fenetre.authorized_keys.valeurs()
    fenetre._on_basculer_authorized_key(entree)
    _repondre(fenetre, "ok")  # « Disable this authorized_keys entry? »
    _repondre(fenetre, "ok")  # garde-fou root
    assert _attendre(lambda: fenetre.travaux == 0)
    assert fichier.read_text(encoding="utf-8").startswith("#")
