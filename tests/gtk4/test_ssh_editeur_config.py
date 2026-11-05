# SPDX-License-Identifier: GPL-3.0-or-later
"""Éditeur GTK4 de ``~/.ssh/config`` (``plugins/ssh/gtk4.py``, #97).

Les deux fichiers (personnel, partagé) sont dans ``tmp_path`` : le partagé
hors de ``/etc/ssh/ssh_config.d/`` est écrit sans élévation.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest
from gi.repository import Gdk, GLib, Gtk

from gcm4.core import registre

RACINE = Path(__file__).resolve().parents[2]

PERSONNEL = """Host web1 web
  HostName 192.168.1.10
  User root
  Port 2222
  IdentityFile {cle}
  LocalForward 8080 intranet:80
  DynamicForward 1080

Host db
  HostName db.example.com
"""

PARTAGE = """Host bastion
  HostName bastion.example.com
"""


@pytest.fixture(scope="module")
def module():
    """``gcm4_plugin_ssh.gtk4`` chargé par le registre, en mode source."""
    candidats = [c for c in registre.candidats_dossier(RACINE / "plugins") if c.nom == "ssh"]
    registre.decouvrir(lambda _nom: None, candidats)
    yield sys.modules["gcm4_plugin_ssh.gtk4"]
    registre._oublier_paquet("gcm4_plugin_ssh")


def _attendre(condition, delai=5.0):
    """Traite les événements GTK jusqu'à ``condition()`` ou ``delai`` secondes."""
    contexte = GLib.MainContext.default()
    fin = time.monotonic() + delai
    while not condition() and time.monotonic() < fin:
        contexte.iteration(False)
        time.sleep(0.005)
    return condition()


@pytest.fixture
def fichiers(tmp_path: Path):
    """Fichiers personnel et partagé ; la clé référencée existe."""
    cle = tmp_path / "id_a"
    cle.write_text("clé\n", encoding="utf-8")
    personnel = tmp_path / "config"
    personnel.write_text(PERSONNEL.format(cle=cle), encoding="utf-8")
    partage = tmp_path / "partage" / "config_gcm.conf"
    partage.parent.mkdir()
    partage.write_text(PARTAGE, encoding="utf-8")
    return personnel, partage


@pytest.fixture
def ouvrir(module, fichiers):
    """Fabrique d'éditeurs affichés sur les fichiers de test."""
    ouverts = []

    def fabrique(**options):
        """Ouvre un éditeur (chemins de test par défaut)."""
        options.setdefault("chemin_personnel", fichiers[0])
        options.setdefault("chemin_partage", fichiers[1])
        editeur = module.SshConfigEditor(**options)
        editeur.present()
        ouverts.append(editeur)
        return editeur

    yield fabrique
    for fenetre in ouverts:
        fenetre.destroy()


def _choisir(editeur, alias):
    """Sélectionne la ligne de l'hôte ``alias``."""
    (ligne,) = [ligne for ligne in editeur.lignes() if alias in ligne.hote.patterns]
    editeur.liste.select_row(ligne)
    return ligne


def test_liste_des_deux_fichiers(module, ouvrir):
    """Hôtes personnels puis partagés (🌐), infobulles, état initial."""
    editeur = ouvrir()
    lignes = editeur.lignes()
    assert [ligne.etiquette.get_label() for ligne in lignes] == ["web1 web", "db", "🌐 bastion"]
    assert [ligne.cible for ligne in lignes] == ["personal", "personal", "shared"]
    assert "192.168.1.10" in lignes[0].get_tooltip_text()
    assert lignes[1].get_tooltip_text().startswith("db\ndb.example.com")
    assert editeur.pile.get_visible_child_name() == "vide"
    assert not editeur.bouton_retirer.get_sensitive()
    assert not editeur.bandeau.get_reveal_child()
    assert not editeur.bouton_gestionnaire.get_visible()
    assert editeur.get_title() == module._("Edit SSH config (personal + shared)")


def test_parent_et_chemins_par_defaut(module, monkeypatch, tmp_path):
    """Fenêtre parente reprise ; fichier partagé par défaut ; fichiers absents."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(module.core, "SHARED_SSH_CONFIG_PATH", tmp_path / "absent.conf")
    parent = Gtk.Window()
    try:
        editeur = module.SshConfigEditor(parent=parent, chemin_personnel=tmp_path / "rien")
        assert editeur.get_transient_for() is parent
        assert editeur.analyseurs["shared"].config_path == tmp_path / "absent.conf"
        assert editeur.lignes() == []
        editeur.destroy()
    finally:
        parent.destroy()


def test_filtre(ouvrir):
    """La recherche porte sur les alias et le HostName, sans casse."""
    editeur = ouvrir()
    editeur.recherche.set_text("EXAMPLE")
    assert [editeur._filtrer(ligne) for ligne in editeur.lignes()] == [False, True, True]
    editeur.recherche.set_text("web")
    assert [editeur._filtrer(ligne) for ligne in editeur.lignes()] == [True, False, False]


def test_formulaire_et_enregistrement(module, ouvrir, fichiers):
    """Le formulaire est appliqué au changement d'hôte et à « Save », qui ferme."""
    editeur = ouvrir()
    _choisir(editeur, "web1")
    assert editeur.pile.get_visible_child_name() == "formulaire"
    assert editeur.champs["Port"].get_text() == "2222"
    assert editeur.choix_fichier.get_selected() == 0
    assert editeur.bouton_retirer.get_sensitive()
    assert editeur.liste_redirections.get_row_at_index(1) is not None
    editeur.champs["User"].set_text("admin")
    editeur.champs["Port"].set_text("")
    _choisir(editeur, "db")  # le formulaire de web1 est appliqué au changement d'hôte
    assert editeur.champs["HostName"].get_text() == "db.example.com"
    editeur.champs["User"].set_text("postgres")
    editeur._on_enregistrer(None)
    texte = fichiers[0].read_text(encoding="utf-8")
    assert "User admin" in texte and "Port" not in texte and "User postgres" in texte
    assert "LocalForward 8080 intranet:80" in texte
    assert not editeur.get_visible()


def test_erreurs_du_formulaire(module, ouvrir, fichiers):
    """Alias vide et port hors plage : messages sous les champs, rien d'écrit."""
    editeur = ouvrir()
    _choisir(editeur, "db")
    editeur.champs["Port"].set_text("70000")
    erreur = editeur.erreurs_champs["Port"]
    assert erreur.get_visible() and erreur.get_text() == module._(
        "Port must be an integer between 1 and 65535"
    )
    editeur.champs["Host"].set_text(" ")
    assert editeur.erreurs_champs["Host"].get_visible()
    assert not editeur.enregistrer()
    assert fichiers[0].read_text(encoding="utf-8") == PERSONNEL.format(
        cle=fichiers[0].with_name("id_a")
    )
    editeur.champs["Host"].set_text("db")
    editeur.champs["Port"].set_text("22")
    assert not editeur.erreurs_champs["Port"].get_visible()


def test_validation_des_fichiers(ouvrir, fichiers):
    """Une erreur de validation est affichée sans bloquer l'écriture."""
    fichiers[1].write_text("Host b\n  Port 99999\n", encoding="utf-8")
    editeur = ouvrir()
    assert editeur.bandeau.get_reveal_child() and "99999" in editeur.erreur.get_text()
    assert editeur.enregistrer()  # validate() signale sans empêcher l'écriture


def test_ajouter_et_retirer(module, ouvrir, fichiers):
    """« Add » crée new-host côté personnel ; « Remove » demande confirmation."""
    editeur = ouvrir()
    editeur.recherche.set_text("db")
    editeur._on_ajouter(None)
    assert editeur.recherche.get_text() == ""
    assert editeur.hote.patterns == ["new-host"] and editeur.cible_hote == "personal"
    assert [ligne.cible for ligne in editeur.lignes()] == ["personal"] * 3 + ["shared"]
    editeur.champs["Host"].set_text("nouveau")
    editeur.champs["HostName"].set_text("n.example.com")
    editeur._on_retirer(None)
    editeur.confirmation.emit("response", "cancel")
    assert editeur.hote is not None
    editeur._on_retirer(None)
    editeur.confirmation.emit("response", "remove")
    assert editeur.hote is None and editeur.pile.get_visible_child_name() == "vide"
    assert len(editeur.lignes()) == 3
    assert editeur.enregistrer()
    assert "new-host" not in fichiers[0].read_text(encoding="utf-8")


def test_deselection(ouvrir):
    """Sans hôte, « File: » est sans effet ; désélectionner applique puis vide le formulaire."""
    editeur = ouvrir()
    editeur.choix_fichier.set_selected(1)
    assert [ligne.cible for ligne in editeur.lignes()] == ["personal", "personal", "shared"]
    editeur.choix_fichier.set_selected(0)
    _choisir(editeur, "bastion")
    assert editeur.choix_fichier.get_selected() == 1 and editeur.cible_hote == "shared"
    editeur.champs["User"].set_text("ops")
    editeur.liste.unselect_all()
    assert editeur.hote is None and editeur.pile.get_visible_child_name() == "vide"
    assert not editeur.bouton_tester.get_sensitive()
    assert editeur.analyseurs["shared"].config.hosts[0].get_option("User") == "ops"


def test_deplacer_vers_le_fichier_partage(ouvrir, fichiers):
    """« File: » déplace l'hôte vers le fichier partagé, qui le reçoit à l'écriture."""
    editeur = ouvrir()
    _choisir(editeur, "db")
    editeur.choix_fichier.set_selected(1)
    assert editeur.cible_hote == "shared"
    assert [ligne.etiquette.get_label() for ligne in editeur.lignes()] == [
        "web1 web",
        "🌐 bastion",
        "🌐 db",
    ]
    assert editeur.liste.get_selected_row().hote is editeur.hote
    editeur._on_fichier_change(editeur.choix_fichier, None)  # même fichier : rien
    assert len(editeur.lignes()) == 3
    assert editeur.enregistrer()
    assert "Host db" in fichiers[1].read_text(encoding="utf-8")
    assert "Host db" not in fichiers[0].read_text(encoding="utf-8")


def test_redirections(module, ouvrir, fichiers):
    """Lecture, saisie (hôte cible obligatoire), retrait et écriture des redirections."""
    editeur = ouvrir()
    _choisir(editeur, "web1")
    assert [r.type for r in editeur.redirections] == ["dynamic", "local"]
    assert not editeur.hote_distant.get_sensitive()  # SOCKS par défaut
    editeur.type_redirection.set_selected(2)
    assert editeur.hote_distant.get_sensitive()
    assert editeur.port_local.get_tooltip_text() == module._("Remote listen port")
    editeur.type_redirection.set_selected(1)
    assert editeur.port_local.get_tooltip_text() == module._("Local Port")
    editeur.hote_distant.set_text("")
    editeur._on_ajouter_redirection(None)
    assert len(editeur.redirections) == 2  # hôte cible manquant
    editeur.type_redirection.set_selected(2)
    editeur.port_local.set_value(9000)
    editeur.hote_distant.set_text("localhost")
    editeur.port_distant.set_value(22)
    editeur._on_ajouter_redirection(None)
    assert editeur.redirections[-1].brut == "9000 localhost:22"
    etiquettes = editeur.liste_redirections.get_row_at_index(2).get_child()
    assert etiquettes.get_first_child().get_label() == module._("Remote")
    editeur._on_retirer_redirection(None)  # aucune sélection : rien
    editeur.liste_redirections.select_row(editeur.liste_redirections.get_row_at_index(0))
    editeur._on_retirer_redirection(None)
    assert [r.type for r in editeur.redirections] == ["local", "remote"]
    assert editeur.enregistrer()
    texte = fichiers[0].read_text(encoding="utf-8")
    assert "DynamicForward" not in texte and "RemoteForward 9000 localhost:22" in texte


def test_onglet_brut_et_diff(module, ouvrir, fichiers):
    """Visual ↔ Raw synchronisés ; diff coloré ; fichier partagé éditable en brut."""
    editeur = ouvrir()
    assert editeur._texte_brut() == fichiers[0].read_text(encoding="utf-8")

    def diff():
        """Texte affiché dans la zone diff."""
        tampon = editeur.texte_diff.get_buffer()
        return tampon.get_text(tampon.get_start_iter(), tampon.get_end_iter(), False)

    assert diff() == module._("No changes.")
    _choisir(editeur, "db")
    editeur.champs["User"].set_text("postgres")
    editeur.onglets.set_current_page(1)
    assert "\n+    User postgres\n" in diff() and diff().startswith("--- original")
    tampon = editeur.texte_brut.get_buffer()
    tampon.set_text(editeur._texte_brut().replace("Host db", "Host base"))
    assert "-Host db" in diff()
    editeur.onglets.set_current_page(0)
    assert [ligne.etiquette.get_label() for ligne in editeur.lignes()] == [
        "web1 web",
        "base",
        "🌐 bastion",
    ]
    assert editeur.hote is None  # l'hôte courant a été relu depuis le texte
    editeur.onglets.set_current_page(1)
    editeur.choix_brut.set_selected(1)
    assert editeur.cible_brute == "shared" and "Host bastion" in editeur._texte_brut()
    editeur.texte_brut.get_buffer().set_text("Host bastion\n  HostName b2\n")
    assert editeur.enregistrer()
    assert "HostName b2" in fichiers[1].read_text(encoding="utf-8")
    assert "Host base" in fichiers[0].read_text(encoding="utf-8")


def test_texte_brut_illisible(module, ouvrir, monkeypatch, fichiers):
    """Analyse du texte Raw impossible : erreur affichée, rien d'écrit ni de basculé."""
    editeur = ouvrir()
    editeur.onglets.set_current_page(1)

    def refus(_texte):
        """Analyse impossible."""
        raise OSError("disque plein")

    monkeypatch.setattr(module.core, "config_depuis_texte", refus)
    assert not editeur.enregistrer()
    assert editeur.bandeau.get_reveal_child() and "disque plein" in editeur.erreur.get_text()
    editeur.choix_brut.set_selected(1)
    assert editeur.cible_brute == "personal"
    editeur.onglets.set_current_page(0)
    assert len(editeur.lignes()) == 3
    assert fichiers[0].read_text(encoding="utf-8") == PERSONNEL.format(
        cle=fichiers[0].with_name("id_a")
    )


def test_echec_d_ecriture(module, ouvrir, monkeypatch):
    """Écriture refusée : erreurs des deux fichiers, fenêtre laissée ouverte."""
    editeur = ouvrir()

    def refus(_analyseur, backup=True):
        """Écriture impossible."""
        raise PermissionError("lecture seule")

    monkeypatch.setattr(module.core.SSHConfigParser, "write", refus)
    editeur._on_enregistrer(None)
    assert editeur.get_visible()
    assert editeur.erreur.get_text().count("lecture seule") == 2
    editeur.bandeau.get_child().get_last_child().emit("clicked")
    assert not editeur.bandeau.get_reveal_child()


def test_fichier_personnel_absent(module, ouvrir, tmp_path):
    """~/.ssh/config absent : liste partielle, fichier créé à l'écriture."""
    editeur = ouvrir(chemin_personnel=tmp_path / "nouveau")
    assert [ligne.cible for ligne in editeur.lignes()] == ["shared"]
    assert editeur.enregistrer() and (tmp_path / "nouveau").exists()


def test_copier_la_commande(module, ouvrir):
    """« Copy ssh cmd » met la commande du formulaire dans le presse-papiers."""
    editeur = ouvrir()
    _choisir(editeur, "web1")
    editeur.champs["User"].set_text("admin")
    editeur._on_copier(None)
    lu = []
    presse_papiers = Gdk.Display.get_default().get_clipboard()
    presse_papiers.read_text_async(None, lambda pp, res: lu.append(pp.read_text_finish(res)))
    assert _attendre(lambda: lu)
    assert lu == ["ssh -p 2222 admin@192.168.1.10"]


def test_choisir_une_cle(module, ouvrir, tmp_path, monkeypatch):
    """« ⚙ » ouvre le gestionnaire ; « 🔑 Pick… » ajoute les clés sans doublon."""
    dossier = tmp_path / ".ssh"
    dossier.mkdir()
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(dossier / "id_b")], check=True
    )
    appels = []
    editeur = ouvrir(gestionnaire=lambda: appels.append(1))
    assert editeur.bouton_gestionnaire.get_visible()
    editeur.bouton_gestionnaire.emit("clicked")
    assert appels == [1]
    _choisir(editeur, "web1")
    editeur._on_choisir_cle(None)
    assert editeur.selecteur.get_transient_for() is editeur
    avant = editeur.champs["IdentityFile"].get_text()
    editeur.selecteur.emit("key-selected", f"{avant}, {dossier / 'id_b'}")
    assert editeur.champs["IdentityFile"].get_text() == f"{avant}, {dossier / 'id_b'}"


def test_tester_la_connexion(module, ouvrir, monkeypatch):
    """« ⚡ Test » affiche la sortie de la commande et son code de retour."""
    monkeypatch.setattr(module.core, "commande_test", lambda alias: ["echo", f"ok {alias}"])
    editeur = ouvrir()
    _choisir(editeur, "db")
    editeur._on_tester(None)
    test = editeur.test
    assert test.get_title() == module._("Test connection — {alias}").format(alias="db")
    assert _attendre(lambda: test.termine)
    assert "ok db" in test.contenu() and test.contenu().endswith(
        module._("\nExit code: {rc}").format(rc=0)
    )
    test.close()


def test_test_sans_sortie(module, ouvrir, monkeypatch):
    """Commande muette : « (no output) »."""
    monkeypatch.setattr(module.core, "commande_test", lambda _alias: ["true"])
    test = module.SshTestDialog(ouvrir(), "db")
    assert _attendre(lambda: test.termine)
    assert module._("(no output)\n") in test.contenu()
    test.destroy()


def test_test_trop_long(module, ouvrir, monkeypatch):
    """Au-delà du délai, la commande est interrompue."""
    monkeypatch.setattr(module.core, "commande_test", lambda _alias: ["sleep", "30"])
    monkeypatch.setattr(module.SshTestDialog, "DELAI", 1)
    test = module.SshTestDialog(ouvrir(), "db")
    assert _attendre(lambda: test.termine, delai=5)
    assert test.contenu().endswith(module._("\n[Timeout after 10 s]"))
    assert _attendre(lambda: test.processus.get_if_exited() or test.processus.get_if_signaled())
    test.destroy()


def test_test_ferme_avant_la_fin(module, ouvrir, monkeypatch):
    """Fermer la fenêtre interrompt la commande en cours."""
    monkeypatch.setattr(module.core, "commande_test", lambda _alias: ["sleep", "30"])
    test = module.SshTestDialog(ouvrir(), "db")
    test.present()
    test.close()
    assert test.termine
    assert _attendre(lambda: test.processus.get_if_signaled())
    _attendre(lambda: False, delai=0.3)  # rappel d'annulation de communicate
    test.close()  # déjà terminé : rien à interrompre


def test_test_commande_introuvable(module, ouvrir, monkeypatch):
    """Commande impossible à lancer : erreur affichée, rien en attente."""
    monkeypatch.setattr(module.core, "commande_test", lambda _alias: ["/nexiste/pas"])
    test = module.SshTestDialog(ouvrir(), "db")
    assert test.termine and test.processus is None
    assert "/nexiste/pas" in test.contenu().split("\n\n", 1)[1]
    test.destroy()
