"""Cœur du plugin SFTP (``plugins/sftp/core.py``, #75) contre un vrai serveur paramiko.

Le serveur (``tests/serveur_sftp.py``) parle le vrai protocole SSH/SFTP sur
127.0.0.1 ; ``HOME`` est temporaire et l'agent SSH absent, pour que seules
les clés et mots de passe du test interviennent.
"""

# ruff: noqa: F811 (fixtures importées de serveur_sftp, redéclarées en paramètres)

from __future__ import annotations

import os
import stat
from pathlib import Path

import paramiko
import pytest
from serveur_sftp import (  # noqa: F401 (fixtures)
    ACCUEIL,
    MOT_DE_PASSE,
    UTILISATEUR,
    cles_sftp,
    maison,
    serveur,
)

import plugins.sftp.core as core


def _connexion(serveur, maison, utilisateur=UTILISATEUR) -> core.ConnexionSftp:
    """Connexion vers le serveur de test, ``known_hosts`` dans le ``HOME`` temporaire."""
    return core.ConnexionSftp(
        "127.0.0.1", serveur.port, utilisateur, maison / ".ssh" / "known_hosts"
    )


def _connue(serveur, maison, cles_sftp) -> core.ConnexionSftp:
    """Connexion dont la clé d'hôte est déjà dans ``known_hosts``."""
    connexion = _connexion(serveur, maison)
    core.ajouter_cle_connue(connexion.known_hosts, connexion.nom, cles_sftp[0])
    return connexion


@pytest.fixture
def ouverte(serveur, maison, cles_sftp):
    """Connexion ouverte par mot de passe ; fermée après le test."""
    connexion = _connue(serveur, maison, cles_sftp)
    assert connexion.ouvrir(MOT_DE_PASSE) == ACCUEIL
    yield connexion
    connexion.fermer()


def _racine(serveur) -> Path:
    """Dossier local qui correspond à ``/`` côté serveur."""
    return serveur.racine


# ── Fonctions pures ──────────────────────────────────────────────────────


def test_empreinte_au_format_openssh(cles_sftp):
    """``SHA256:`` + base64 sans remplissage, comme ``ssh-keygen -lf`` et paramiko."""
    texte = core.empreinte(cles_sftp[0])
    assert texte.startswith("SHA256:") and not texte.endswith("=")
    assert texte == cles_sftp[0].fingerprint


def test_nom_known_hosts():
    """Port 22 : nom seul ; autre port : ``[nom]:port`` (format d'OpenSSH)."""
    assert core.nom_known_hosts("srv", 22) == "srv"
    assert core.nom_known_hosts("srv", 2222) == "[srv]:2222"


def test_known_hosts_defaut(maison):
    """Le fichier d'OpenSSH, sous ``HOME``."""
    assert core.known_hosts_defaut() == maison / ".ssh" / "known_hosts"


def test_ajouter_cle_connue_nouveau_fichier(tmp_path, cles_sftp):
    """Dossier en 0700, fichier en 0600, ligne relue par paramiko."""
    fichier = tmp_path / "ssh" / "known_hosts"
    core.ajouter_cle_connue(fichier, "[srv]:2222", cles_sftp[0])
    assert stat.S_IMODE(fichier.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(fichier.stat().st_mode) == 0o600
    cles = paramiko.HostKeys(str(fichier))
    assert cles.lookup("[srv]:2222")["ssh-rsa"].asbytes() == cles_sftp[0].asbytes()


def test_ajouter_cle_connue_garde_le_contenu(tmp_path, cles_sftp):
    """Fichier existant sans fin de ligne : rien n'est réécrit, un saut de ligne est ajouté."""
    fichier = tmp_path / "known_hosts"
    fichier.write_text("# commentaire\nautre ssh-rsa AAAA", encoding="ascii")
    fichier.chmod(0o644)
    core.ajouter_cle_connue(fichier, "srv", cles_sftp[0])
    lignes = fichier.read_text(encoding="ascii").splitlines()
    assert lignes[:2] == ["# commentaire", "autre ssh-rsa AAAA"]
    assert lignes[2].startswith("srv ssh-rsa ")
    assert stat.S_IMODE(fichier.stat().st_mode) == 0o644


def test_ajouter_cle_connue_fichier_vide(tmp_path, cles_sftp):
    """Fichier vide : la ligne est la première, sans saut de ligne avant."""
    fichier = tmp_path / "known_hosts"
    fichier.write_text("", encoding="ascii")
    core.ajouter_cle_connue(fichier, "srv", cles_sftp[0])
    assert fichier.read_text(encoding="ascii").startswith("srv ssh-rsa ")


def test_ajouter_cle_connue_apres_fin_de_ligne(tmp_path, cles_sftp):
    """Fichier terminé par un saut de ligne : aucune ligne vide ajoutée."""
    fichier = tmp_path / "known_hosts"
    fichier.write_text("autre ssh-rsa AAAA\n", encoding="ascii")
    core.ajouter_cle_connue(fichier, "srv", cles_sftp[0])
    assert len(fichier.read_text(encoding="ascii").splitlines()) == 2


@pytest.mark.parametrize(
    ("chemin", "attendu"),
    [("/", "/"), ("/a", "/"), ("/a/b", "/a"), ("/a/b/", "/a")],
)
def test_parent(chemin, attendu):
    """``/`` reste ``/`` ; une barre finale est ignorée."""
    assert core.parent(chemin) == attendu


def test_joindre():
    """Séparateur POSIX."""
    assert core.joindre("/a", "b") == "/a/b"
    assert core.joindre("/", "b") == "/b"


@pytest.mark.parametrize(
    ("nom", "valide"),
    [
        ("doc", True),
        ("é t é", True),
        ("", False),
        (".", False),
        ("..", False),
        ("a/b", False),
        ("a\0", False),
    ],
)
def test_nom_valide(nom, valide):
    """Ni vide, ni ``.``/``..``, ni ``/``, ni octet nul."""
    assert core.nom_valide(nom) is valide


@pytest.mark.parametrize(
    ("octets", "texte"),
    [
        (0, "0 B"),
        (1023, "1023 B"),
        (1536, "1.5 KiB"),
        (3 * 1024**2, "3.0 MiB"),
        (5 * 1024**4, "5120.0 GiB"),
    ],
)
def test_taille_lisible(octets, texte):
    """Unités binaires, plafonnées au GiB."""
    assert core.taille_lisible(octets) == texte


def test_destination_libre(tmp_path):
    """Nom libre, puis ``nom (1).ext``, ``nom (2).ext``."""
    assert core.destination_libre(tmp_path, "a.txt") == tmp_path / "a.txt"
    (tmp_path / "a.txt").write_text("x")
    assert core.destination_libre(tmp_path, "a.txt") == tmp_path / "a (1).txt"
    (tmp_path / "a (1).txt").write_text("x")
    assert core.destination_libre(tmp_path, "a.txt") == tmp_path / "a (2).txt"


def test_entree_droits():
    """Comme ``ls -l``."""
    entree = core.Entree("d", "/d", 0, 0, True, False, stat.S_IFDIR | 0o755)
    assert entree.droits == "drwxr-xr-x"


def test_utilisateur_local(monkeypatch):
    """Sans utilisateur : celui de la session ; inconnu : vide."""
    monkeypatch.setattr(core.getpass, "getuser", lambda: "bob")
    assert core.ConnexionSftp("h", None, "", Path("/k")).utilisateur == "bob"

    def inconnu():
        raise KeyError("uid")

    monkeypatch.setattr(core.getpass, "getuser", inconnu)
    connexion = core.ConnexionSftp("h", None, "", Path("/k"))
    assert (connexion.utilisateur, connexion.port, connexion.nom) == ("", 22, "h")


def test_message_sans_strerror():
    """Raison du serveur absente : premier argument, sinon nom de l'exception."""
    assert core._message(OSError("refusé"), "/x") == "/x: refusé"
    assert core._message(OSError(), "/x") == "/x: OSError"


# ── Connexion ────────────────────────────────────────────────────────────


def test_cle_hote_inconnue_puis_acceptee(serveur, maison, cles_sftp):
    """Clé absente : refus avec l'empreinte ; une fois ajoutée, la connexion s'ouvre."""
    connexion = _connexion(serveur, maison)
    with pytest.raises(core.CleHoteInconnue) as info:
        connexion.ouvrir(MOT_DE_PASSE)
    assert info.value.nom == f"[127.0.0.1]:{serveur.port}"
    assert info.value.empreinte == core.empreinte(cles_sftp[0])
    assert not connexion.ouverte
    core.ajouter_cle_connue(connexion.known_hosts, info.value.nom, info.value.cle)
    assert connexion.ouvrir(MOT_DE_PASSE) == ACCUEIL
    assert connexion.ouverte
    connexion.fermer()
    connexion.fermer()
    assert not connexion.ouverte


def test_cle_hote_changee(serveur, maison, cles_sftp):
    """Clé différente de celle connue : refus, sans proposition d'accepter."""
    connexion = _connexion(serveur, maison)
    core.ajouter_cle_connue(connexion.known_hosts, connexion.nom, cles_sftp[2])
    with pytest.raises(core.CleHoteChangee) as info:
        connexion.ouvrir(MOT_DE_PASSE)
    assert info.value.empreinte == core.empreinte(cles_sftp[0])
    assert not connexion.ouverte


def test_mot_de_passe_refuse(serveur, maison, cles_sftp):
    """Mauvais mot de passe : :class:`AuthentificationRefusee`."""
    connexion = _connue(serveur, maison, cles_sftp)
    with pytest.raises(core.AuthentificationRefusee):
        connexion.ouvrir("mauvais")
    assert not connexion.ouverte


def test_sans_identifiants(serveur, maison, cles_sftp):
    """Ni mot de passe, ni clé, ni agent : :class:`AuthentificationRefusee`."""
    connexion = _connue(serveur, maison, cles_sftp)
    with pytest.raises(core.AuthentificationRefusee):
        connexion.ouvrir()


def test_fichier_de_cle(serveur, maison, cles_sftp, tmp_path):
    """Option ``identity-file`` : authentification par clé, sans mot de passe."""
    fichier = tmp_path / "id_rsa"
    cles_sftp[1].write_private_key_file(str(fichier))
    connexion = _connue(serveur, maison, cles_sftp)
    assert connexion.ouvrir(fichier_cle=str(fichier)) == ACCUEIL
    connexion.fermer()


def test_serveur_injoignable(maison, serveur):
    """Port fermé : :class:`ErreurSftp` lisible."""
    port = serveur.port
    serveur.arreter()
    connexion = core.ConnexionSftp("127.0.0.1", port, UTILISATEUR, maison / "kh")
    with pytest.raises(core.ErreurSftp) as info:
        connexion.ouvrir(MOT_DE_PASSE, delai=2)
    assert type(info.value) is core.ErreurSftp and str(info.value)


def test_operations_sans_connexion(maison):
    """Avant :meth:`ouvrir` : :class:`ErreurSftp`, pas d'``AttributeError``."""
    connexion = core.ConnexionSftp("h", 22, "u", maison / "kh")
    with pytest.raises(core.ErreurSftp):
        connexion.lister("/")


# ── Navigation et opérations ─────────────────────────────────────────────


def test_lister_trie_et_liens(ouverte, serveur):
    """Dossiers d'abord, puis par nom sans casse ; lien vers dossier = dossier ; lien cassé = fichier."""
    accueil = _racine(serveur) / "accueil"
    (accueil / "b.txt").write_bytes(b"12345")
    (accueil / "A.txt").write_bytes(b"")
    (accueil / "zdossier").mkdir()
    (accueil / "Cdossier").mkdir()
    os.symlink("zdossier", accueil / "lien")
    os.symlink("absent", accueil / "casse")
    entrees = ouverte.lister(ACCUEIL)
    assert [e.nom for e in entrees] == ["Cdossier", "lien", "zdossier", "A.txt", "b.txt", "casse"]
    par_nom = {e.nom: e for e in entrees}
    assert par_nom["lien"].dossier and par_nom["lien"].lien
    assert not par_nom["casse"].dossier and par_nom["casse"].lien
    assert par_nom["b.txt"].taille == 5 and par_nom["b.txt"].chemin == "/accueil/b.txt"
    assert par_nom["b.txt"].droits.startswith("-")
    assert par_nom["b.txt"].modifie > 0


def test_normaliser(ouverte, serveur):
    """Relatif au dossier d'accueil, ``..`` résolu par le serveur, chemin vide = accueil."""
    (_racine(serveur) / "accueil" / "sous").mkdir()
    assert ouverte.normaliser("sous") == "/accueil/sous"
    assert ouverte.normaliser("/accueil/sous/..") == "/accueil"
    assert ouverte.normaliser("") == ACCUEIL


def test_normaliser_echec(ouverte, monkeypatch):
    """Erreur du serveur : :class:`ErreurSftp` avec le chemin."""

    def refus(_chemin):
        raise OSError(2, "No such file")

    monkeypatch.setattr(ouverte._sftp, "normalize", refus)
    with pytest.raises(core.ErreurSftp, match="/x: No such file"):
        ouverte.normaliser("/x")


def test_lister_absent(ouverte):
    """Dossier absent : :class:`ErreurSftp` avec le chemin."""
    with pytest.raises(core.ErreurSftp, match="/absent"):
        ouverte.lister("/absent")


def test_telecharger(ouverte, serveur, tmp_path):
    """Contenu identique ; progression jusqu'au total."""
    contenu = os.urandom(200_000)
    (_racine(serveur) / "accueil" / "gros.bin").write_bytes(contenu)
    appels = []
    destination = ouverte.telecharger(
        "/accueil/gros.bin", tmp_path / "gros.bin", lambda f, t: appels.append((f, t))
    )
    assert destination.read_bytes() == contenu
    assert appels[-1] == (200_000, 200_000)


def test_telecharger_absent(ouverte, tmp_path):
    """Fichier absent : erreur, et aucun fichier local partiel ne reste."""
    with pytest.raises(core.ErreurSftp, match="/accueil/absent"):
        ouverte.telecharger("/accueil/absent", tmp_path / "x")
    assert not (tmp_path / "x").exists()


def test_envoyer(ouverte, serveur, tmp_path):
    """Même nom dans le dossier distant."""
    local = tmp_path / "local.txt"
    local.write_text("bonjour", encoding="utf-8")
    assert ouverte.envoyer(local, ACCUEIL) == "/accueil/local.txt"
    assert (_racine(serveur) / "accueil" / "local.txt").read_text(encoding="utf-8") == "bonjour"


def test_envoyer_echec(ouverte, tmp_path):
    """Dossier distant absent : :class:`ErreurSftp`."""
    local = tmp_path / "local.txt"
    local.write_text("x", encoding="utf-8")
    with pytest.raises(core.ErreurSftp, match="/absent/local.txt"):
        ouverte.envoyer(local, "/absent")


def test_creer_renommer_supprimer(ouverte, serveur):
    """Dossier créé, renommé, puis supprimé ; fichier et lien supprimés."""
    accueil = _racine(serveur) / "accueil"
    assert ouverte.creer_dossier(ACCUEIL, "neuf") == "/accueil/neuf"
    assert (accueil / "neuf").is_dir()
    dossier = next(e for e in ouverte.lister(ACCUEIL) if e.nom == "neuf")
    assert ouverte.renommer(dossier, "renomme") == "/accueil/renomme"
    renomme = next(e for e in ouverte.lister(ACCUEIL) if e.nom == "renomme")
    ouverte.supprimer(renomme)
    (accueil / "f").write_text("x")
    os.symlink("ailleurs", accueil / "l")
    for entree in ouverte.lister(ACCUEIL):
        ouverte.supprimer(entree)
    assert list(accueil.iterdir()) == []


def test_lien_vers_dossier_supprime_le_lien(ouverte, serveur):
    """Un lien vers un dossier est retiré comme un fichier : la cible reste."""
    accueil = _racine(serveur) / "accueil"
    (accueil / "cible").mkdir()
    (accueil / "cible" / "garde").write_text("x")
    os.symlink("cible", accueil / "lien")
    lien = next(e for e in ouverte.lister(ACCUEIL) if e.nom == "lien")
    ouverte.supprimer(lien)
    assert (accueil / "cible" / "garde").exists() and not (accueil / "lien").exists()


def test_echecs_des_operations(ouverte, serveur):
    """Dossier existant, renommage d'un absent, dossier non vide : :class:`ErreurSftp`."""
    accueil = _racine(serveur) / "accueil"
    (accueil / "plein").mkdir()
    (accueil / "plein" / "f").write_text("x")
    with pytest.raises(core.ErreurSftp, match="/accueil/plein"):
        ouverte.creer_dossier(ACCUEIL, "plein")
    plein = next(e for e in ouverte.lister(ACCUEIL) if e.nom == "plein")
    with pytest.raises(core.ErreurSftp, match="/accueil/plein"):
        ouverte.supprimer(plein)
    absent = core.Entree("absent", "/accueil/absent", 0, 0, False, False, 0)
    with pytest.raises(core.ErreurSftp, match="/accueil/absent"):
        ouverte.renommer(absent, "x")


def test_sftp_refuse_par_le_serveur(serveur, maison, cles_sftp, monkeypatch):
    """Sous-système SFTP indisponible (erreur SSH) : :class:`ErreurSftp`, connexion fermée."""

    def refus(_client):
        raise paramiko.SSHException("subsystem request failed")

    monkeypatch.setattr(paramiko.SSHClient, "open_sftp", refus)
    connexion = _connue(serveur, maison, cles_sftp)
    with pytest.raises(core.ErreurSftp, match="subsystem request failed") as info:
        connexion.ouvrir(MOT_DE_PASSE)
    assert type(info.value) is core.ErreurSftp and not connexion.ouverte
