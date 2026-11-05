# SPDX-License-Identifier: GPL-3.0-or-later
"""Logique sans GTK du gestionnaire de clés (``plugins/ssh/core.py``, #97).

Les opérations passent par les vrais ``ssh-keygen``/``ssh-add``/``ssh-agent`` ;
le ssh distant est remplacé par un faux ``ssh`` qui exécute la commande
localement, avec ``HOME`` pointant sur un dossier de test.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def core():
    """``plugins/ssh/core.py`` chargé hors registre."""
    spec = importlib.util.spec_from_file_location(
        "ssh_core_gestion_cles_test", RACINE / "plugins" / "ssh" / "core.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    yield module
    del sys.modules[spec.name]


def _paire(dossier: Path, nom: str = "id_test", phrase: str = "") -> Path:
    """Paire ed25519 générée par ssh-keygen dans ``dossier``."""
    dossier.mkdir(parents=True, exist_ok=True)
    chemin = dossier / nom
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", phrase, "-C", "test", "-f", str(chemin)],
        check=True,
    )
    return chemin


def _empreinte_officielle(chemin: Path) -> str:
    """Empreinte lue par ssh-keygen -lf."""
    sortie = subprocess.run(
        ["ssh-keygen", "-lf", str(chemin)], capture_output=True, text=True, check=True
    )
    return sortie.stdout.split()[1]


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


# -- exécution, erreurs ----------------------------------------------------


def test_executer_erreurs(core):
    """Outil absent, code retour non nul et délai dépassé deviennent des ErreurCle."""
    with pytest.raises(core.ErreurCle) as absent:
        core._executer(["/nexiste/pas"], 1)
    assert absent.value.code == "absent" and absent.value.detail == "/nexiste/pas"
    with pytest.raises(core.ErreurCle) as echec:
        core._executer(["sh", "-c", "echo oups >&2; exit 3"], 5)
    assert (echec.value.code, echec.value.detail) == ("echec", "oups")
    with pytest.raises(core.ErreurCle) as delai:
        core._executer(["sleep", "5"], 0.2)
    assert delai.value.code == "delai"
    assert core._executer(["cat"], 5, entree="abc").stdout == "abc"


def test_commentaire_par_defaut(core, monkeypatch):
    """utilisateur@machine, avec repli sur $USER."""
    assert "@" in core.commentaire_par_defaut()

    def refus():
        raise KeyError("pas d'utilisateur")

    monkeypatch.setattr(core.getpass, "getuser", refus)
    monkeypatch.setenv("USER", "alice")
    assert core.commentaire_par_defaut().startswith("alice@")


# -- clés, certificats -----------------------------------------------------


def test_empreinte_et_type(core, tmp_path):
    """Empreinte complète et type ; vide pour un fichier qui n'est pas une clé."""
    cle = _paire(tmp_path)
    assert core.empreinte_et_type(cle) == (_empreinte_officielle(cle), "ED25519")
    autre = tmp_path / "texte"
    autre.write_text("rien\n", encoding="utf-8")
    assert core.empreinte_et_type(autre) == ("", "")


def test_empreinte_et_type_sortie_atypique(core, monkeypatch, tmp_path):
    """Sortie sans type entre parenthèses : type vide."""

    class Resultat:
        """Sortie simulée de ssh-keygen."""

        stdout = "256\n"

    monkeypatch.setattr(core, "_executer", lambda *_a, **_k: Resultat())
    assert core.empreinte_et_type(tmp_path) == ("", "")


def test_date_creation(core, tmp_path, monkeypatch):
    """Date de naissance, sinon ctime, sinon None."""
    fichier = tmp_path / "f"
    fichier.write_text("x", encoding="utf-8")
    date = core.date_creation(fichier)
    assert isinstance(date, datetime) and abs((datetime.now() - date).total_seconds()) < 120
    monkeypatch.setenv("PATH", str(tmp_path / "vide"))  # stat introuvable : ctime
    assert core.date_creation(fichier) == datetime.fromtimestamp(fichier.stat().st_ctime)
    assert core.date_creation(tmp_path / "absent") is None


def test_certificats(core, tmp_path):
    """Certificat perpétuel, daté, expiré ; fichier illisible."""
    ca = _paire(tmp_path, "ca")
    cle = _paire(tmp_path, "id_a")
    certificat = core.signer_cle(cle.with_name("id_a.pub"), ca, "moi", "alice", "always:forever")
    assert certificat == tmp_path / "id_a-cert.pub" and certificat.exists()
    info = core.info_certificat(certificat)
    assert info.fin is None and not info.expire
    assert info.ca == f"ED25519 {_empreinte_officielle(ca)[:24]}"
    core.signer_cle(cle.with_name("id_a.pub"), ca, "moi", "alice", "20200101:20200102")
    passe = core.info_certificat(certificat)
    assert passe.fin == "2020-01-02T00:00:00" and passe.expire
    core.signer_cle(cle.with_name("id_a.pub"), ca, "moi", "alice", "+52w")
    futur = core.info_certificat(certificat)
    assert futur.fin > "2026" and not futur.expire
    assert core.info_certificat(ca) == core.InfoCertificat("?", False, None)


def test_certificat_sortie_atypique(core, monkeypatch, tmp_path):
    """Sorties de ssh-keygen -L sans validité, ou à date illisible."""

    class Resultat:
        """Sortie simulée de ssh-keygen -L."""

        stdout = "Type: ssh-ed25519-cert-v01\n"

    monkeypatch.setattr(core, "_executer", lambda *_a, **_k: Resultat())
    assert core.info_certificat(tmp_path) == core.InfoCertificat("?", False, None)
    Resultat.stdout = "Signing CA: RSA SHA256:abc\nValid: from x to demain\n"
    assert core.info_certificat(tmp_path) == core.InfoCertificat("demain", False, "RSA SHA256:abc")


def test_details_cle(core, tmp_path):
    """Détails d'une privée certifiée, d'une privée simple et d'un certificat orphelin."""
    ca = _paire(tmp_path / "ca")
    cle = _paire(tmp_path / "ssh", "id_a")
    _paire(tmp_path / "ssh", "id_b")
    core.signer_cle(cle.with_name("id_a.pub"), ca, "moi", "alice", "always:forever")
    orphelin = tmp_path / "ssh" / "vieux-cert.pub"
    (tmp_path / "ssh" / "id_a-cert.pub").rename(orphelin)
    core.signer_cle(cle.with_name("id_a.pub"), ca, "moi", "alice", "always:forever")
    cles = {c.nom: c for c in core.lister_cles(tmp_path / "ssh")}
    assert sorted(cles) == ["id_a", "id_a-cert.pub", "id_b", "vieux-cert.pub"]
    details = core.details_cle(cles["id_a"])
    assert details.type == "ED25519" and details.certificat.fin is None
    assert details.empreinte == _empreinte_officielle(cle)
    assert core.details_cle(cles["id_b"]).certificat is None
    assert core.details_cle(cles["vieux-cert.pub"]).certificat.fin is None
    assert core.details_cle(cles["id_a-cert.pub"]).certificat is not None


def test_lister_cles_ignore_les_sauvegardes(core, tmp_path):
    """known_hosts.old, config.bak, authorized_keys.1 ne sont pas des clés."""
    for nom in ("known_hosts.old", "config.bak", "authorized_keys.1", "id_x"):
        (tmp_path / nom).write_text("x", encoding="utf-8")
    assert [c.nom for c in core.lister_cles(tmp_path)] == ["id_x"]


# -- known_hosts, authorized_keys -----------------------------------------


def test_empreinte_ligne(core, tmp_path):
    """Même empreinte que ssh-keygen ; vide si base64 invalide."""
    cle = _paire(tmp_path)
    type_, base64_cle = cle.with_name("id_test.pub").read_text(encoding="utf-8").split()[:2]
    assert core.empreinte_ligne(type_, base64_cle) == _empreinte_officielle(cle)
    assert core.empreinte_ligne("ssh-rsa", "pas du base64!") == ""


def test_lire_known_hosts(core, tmp_path):
    """Entrées normales, hachées, marquées ; commentaires et lignes courtes ignorés."""
    cle = _paire(tmp_path)
    type_, b64 = cle.with_name("id_test.pub").read_text(encoding="utf-8").split()[:2]
    texte = (
        f"# commentaire\n\nweb,10.0.0.1 {type_} {b64}\n"
        f"|1|abc=|def= {type_} {b64} note finale\n"
        f"@cert-authority *.example.com {type_} {b64}\nincomplet ssh-rsa\n"
    )
    entrees = core.lire_known_hosts(texte)
    assert [(e.ligne, e.hotes, e.hache, e.marqueur) for e in entrees] == [
        (3, "web,10.0.0.1", False, ""),
        (4, "", True, ""),
        (5, "*.example.com", False, "@cert-authority"),
    ]
    assert entrees[0].empreinte == _empreinte_officielle(cle)
    assert entrees[1].commentaire == "note finale" and entrees[0].commentaire == ""
    assert entrees[0].brut == f"web,10.0.0.1 {type_} {b64}"


def test_lire_authorized_keys(core, tmp_path):
    """Clés actives, désactivées, avec options ; lignes sans type ignorées."""
    cle = _paire(tmp_path)
    type_, b64 = cle.with_name("id_test.pub").read_text(encoding="utf-8").split()[:2]
    texte = (
        f"{type_} {b64} alice@pc\n# {type_} {b64} ancienne clé\n"
        f'from="10.0.0.0/8",no-pty {type_} {b64}\n# simple commentaire\n\n#\n'
        "ssh-rsa\n"
    )
    entrees = core.lire_authorized_keys(texte)
    assert [(e.ligne, e.active, e.options, e.commentaire) for e in entrees] == [
        (1, True, "", "alice@pc"),
        (2, False, "", "ancienne clé"),
        (3, True, 'from="10.0.0.0/8",no-pty', ""),
        (7, True, "", ""),
    ]
    assert entrees[0].empreinte == _empreinte_officielle(cle) and entrees[3].empreinte == ""


def test_est_ligne_cle_publique(core, tmp_path):
    """Une seule clé active au base64 valide."""
    ligne = _paire(tmp_path).with_name("id_test.pub").read_text(encoding="utf-8")
    assert core.est_ligne_cle_publique(ligne)
    assert core.est_ligne_cle_publique("no-pty " + ligne)
    assert not core.est_ligne_cle_publique("# " + ligne)
    assert not core.est_ligne_cle_publique("ssh-ed25519 pasbase64!")
    assert not core.est_ligne_cle_publique("bonjour")
    assert not core.est_ligne_cle_publique(ligne + ligne)


def test_retirer_basculer_ajouter(core):
    """Modifications ligne à ligne, refusées si le fichier a changé."""
    texte = "a\nb\na\n"
    assert core.retirer_ligne(texte, "a") == "b\n"
    assert core.retirer_ligne("a\n", "a") == ""
    assert core.retirer_ligne(texte, "z") is None
    assert core.basculer_ligne(texte, 2, "b") == "a\n# b\na\n"
    assert core.basculer_ligne("x\n#  b\n", 2, "#  b") == "x\nb\n"
    assert core.basculer_ligne(texte, 2, "autre") is None
    assert core.basculer_ligne(texte, 9, "b") is None
    assert core.basculer_ligne(texte, 0, "b") is None
    assert core.ajouter_ligne("", " k \n") == "k\n"
    assert core.ajouter_ligne("a", "k") == "a\nk\n"
    assert core.ajouter_ligne("a\n", "k") == "a\nk\n"


def test_hotes_ssh_config(core, tmp_path):
    """Alias concrets, sans motifs ni doublons ; fichier absent : liste vide."""
    config = tmp_path / "config"
    config.write_text(
        "Host web web.example.com *.lan\n  HostName w\nhost db !bad web\nHost ?x\n",
        encoding="utf-8",
    )
    assert core.hotes_ssh_config(config) == ["web", "web.example.com", "db"]
    assert core.hotes_ssh_config(tmp_path / "absent") == []


def test_tester_known_hosts(core, tmp_path, monkeypatch):
    """ssh-keygen -F : trouvé avec la ligne, absent, outil manquant."""
    cle = _paire(tmp_path)
    type_, b64 = cle.with_name("id_test.pub").read_text(encoding="utf-8").split()[:2]
    texte = f"web {type_} {b64}\n"
    resultats = core.tester_known_hosts(texte, ["web", "db"])
    assert resultats[0][:2] == ("web", True) and f"web {type_} {b64}" in resultats[0][2]
    assert resultats[1] == ("db", False, "")
    monkeypatch.setenv("PATH", str(tmp_path / "vide"))
    assert core.tester_known_hosts(texte, ["web"]) == [("web", False, "absent")]


# -- cible distante, adaptateurs -------------------------------------------


@pytest.mark.parametrize(
    ("texte", "attendu"),
    [
        ("root@h", ("root", "h", 22, "root@h")),
        (" admin@10.0.0.1:2222 ", ("admin", "10.0.0.1", 2222, "admin@10.0.0.1:2222")),
        ("h", None),
        ("a@b@c", None),
        ("u@h:0", None),
        ("u@h:70000", None),
    ],
)
def test_analyser_cible(core, texte, attendu):
    """user@host[:port], port de 1 à 65535."""
    cible = core.analyser_cible(texte)
    if attendu is None:
        assert cible is None
    else:
        assert (cible.utilisateur, cible.hote, cible.port, cible.affichage) == attendu


def test_commande_cible(core):
    """Commande ssh non interactive, -p hors port 22."""
    assert core.CibleSsh("u", "h").commande("ls") == [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=6",
        "u@h",
        "ls",
    ]
    assert core.CibleSsh("u", "h", 2222).commande("ls")[5:7] == ["-p", "2222"]


def test_fichiers_locaux(core, tmp_path, monkeypatch):
    """Lecture, écriture avec mode, échec d'écriture, utilisateur."""
    fichiers = core.FichiersLocaux(tmp_path / "ssh")
    assert not fichiers.distant
    assert fichiers.lire("known_hosts") is None
    assert fichiers.ecrire("known_hosts", "x\n", 0o644)
    assert fichiers.lire("known_hosts") == "x\n"
    assert oct((tmp_path / "ssh" / "known_hosts").stat().st_mode & 0o777) == "0o644"
    (tmp_path / "ssh" / "dossier").mkdir()
    assert not fichiers.ecrire("dossier", "x")
    assert fichiers.utilisateur()

    def refus():
        raise OSError("pas d'utilisateur")

    monkeypatch.setattr(core.getpass, "getuser", refus)
    monkeypatch.setenv("USER", "bob")
    assert fichiers.utilisateur() == "bob"


def test_fichiers_distants(core, faux_ssh):
    """Lecture et écriture atomique via ssh ; fichier absent."""
    fichiers = core.FichiersDistants(core.CibleSsh("root", "h"))
    assert fichiers.distant and fichiers.utilisateur() == "root"
    assert fichiers.tester() is None
    assert fichiers.lire("authorized_keys") is None
    texte = "ssh-ed25519 AAAA 'guillemets' \"doubles\" $HOME\n"
    assert fichiers.ecrire("authorized_keys", texte)
    chemin = faux_ssh / ".ssh" / "authorized_keys"
    assert chemin.read_text(encoding="utf-8") == texte
    assert oct(chemin.stat().st_mode & 0o777) == "0o600"
    assert oct((faux_ssh / ".ssh").stat().st_mode & 0o777) == "0o700"
    assert fichiers.lire("authorized_keys") == texte


def test_fichiers_distants_injoignables(core, faux_ssh, monkeypatch, tmp_path):
    """Cible en échec : message d'erreur, lecture None, écriture refusée."""
    script = tmp_path / "bin" / "ssh"
    script.write_text("#!/bin/sh\necho 'Permission denied' >&2\nexit 255\n", encoding="utf-8")
    fichiers = core.FichiersDistants(core.CibleSsh("u", "h"))
    assert fichiers.tester() == "Permission denied"
    assert fichiers.lire("known_hosts") is None
    assert not fichiers.ecrire("known_hosts", "x")
    script.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    assert fichiers.tester() == "echec"
    script.write_text("#!/bin/sh\necho bonjour\n", encoding="utf-8")
    assert fichiers.tester() == "echec"
    monkeypatch.setenv("PATH", str(tmp_path / "vide"))
    assert fichiers.tester() == "absent"


# -- génération, signature, phrase, agent ----------------------------------


def test_nom_libre(core, tmp_path):
    """Suffixe _1, _2… si la privée ou la publique existe."""
    assert core.nom_libre(tmp_path, "id") == tmp_path / "id"
    (tmp_path / "id").write_text("x", encoding="utf-8")
    (tmp_path / "id_1.pub").write_text("x", encoding="utf-8")
    assert core.nom_libre(tmp_path, "id") == tmp_path / "id_2"


@pytest.mark.parametrize(
    ("type_", "attendu"), [("ed25519", "ED25519"), ("ecdsa", "ECDSA"), ("rsa", "RSA")]
)
def test_generer_cle(core, tmp_path, type_, attendu):
    """Les trois types, dans un dossier créé à la volée."""
    chemin = tmp_path / "nouveau" / f"id_{type_}"
    core.generer_cle(chemin, type_, 2048, "moi@pc", "")
    empreinte, lu = core.empreinte_et_type(chemin.with_name(chemin.name + ".pub"))
    assert lu == attendu and empreinte.startswith("SHA256:")
    assert "moi@pc" in chemin.with_name(chemin.name + ".pub").read_text(encoding="utf-8")
    if type_ == "rsa":
        assert subprocess.run(
            ["ssh-keygen", "-lf", str(chemin)], capture_output=True, text=True
        ).stdout.startswith("2048 ")


def test_generer_cle_refusee(core, tmp_path):
    """Type inconnu : ErreurCle echec."""
    with pytest.raises(core.ErreurCle) as erreur:
        core.generer_cle(tmp_path / "k", "inconnu", 0, "c", "")
    assert erreur.value.code == "echec"


def test_changer_phrase(core, tmp_path):
    """Nouvelle phrase posée ; mauvaise ancienne phrase refusée."""
    cle = _paire(tmp_path, phrase="ancienne1")
    core.changer_phrase(cle, "ancienne1", "nouvelle1")
    with pytest.raises(core.ErreurCle):
        core.changer_phrase(cle, "ancienne1", "x")
    core.changer_phrase(cle, "nouvelle1", "")
    assert (
        subprocess.run(
            ["ssh-keygen", "-y", "-P", "", "-f", str(cle)], capture_output=True
        ).returncode
        == 0
    )


def test_agent(core, tmp_path, agent):
    """Ajout, présence, retrait ; clé à phrase refusée sans terminal."""
    cle = _paire(tmp_path)
    publique = cle.with_name("id_test.pub")
    assert not core.cle_dans_agent(publique)
    core.ajouter_agent(cle)
    assert core.cle_dans_agent(publique)
    core.retirer_agent(publique)
    assert not core.cle_dans_agent(publique)
    protegee = _paire(tmp_path, "id_p", phrase="secret12")
    with pytest.raises(core.ErreurCle):
        core.ajouter_agent(protegee)
    texte = tmp_path / "pas_une_cle"
    texte.write_text("x", encoding="utf-8")
    assert not core.cle_dans_agent(texte)


def test_agent_injoignable(core, tmp_path, monkeypatch):
    """Sans agent : la clé n'est pas chargée."""
    monkeypatch.setenv("SSH_AUTH_SOCK", str(tmp_path / "aucun.sock"))
    assert not core.cle_dans_agent(_paire(tmp_path).with_name("id_test.pub"))


# -- import, renommage, suppression ----------------------------------------


def test_importer_paire(core, tmp_path):
    """Paire copiée en 600/644 ; .pub exigée pour une CA ; copie impossible."""
    source = _paire(tmp_path / "ailleurs", "ma_cle")
    (tmp_path / "ailleurs" / "seule").write_text("x", encoding="utf-8")
    dossier = tmp_path / "ssh"
    cible = core.importer_paire(source, dossier)
    assert cible == dossier / "ma_cle"
    assert oct(cible.stat().st_mode & 0o777) == "0o600"
    assert oct((dossier / "ma_cle.pub").stat().st_mode & 0o777) == "0o644"
    assert core.importer_paire(tmp_path / "ailleurs" / "seule", dossier) == dossier / "seule"
    assert not (dossier / "seule.pub").exists()
    with pytest.raises(core.ErreurCle) as sans_pub:
        core.importer_paire(tmp_path / "ailleurs" / "seule", dossier, exiger_publique=True)
    assert (sans_pub.value.code, sans_pub.value.detail) == ("nom", "seule.pub")
    with pytest.raises(core.ErreurCle) as absente:
        core.importer_paire(tmp_path / "ailleurs" / "absente", dossier)
    assert absente.value.code == "systeme"


def test_renommer_cle(core, tmp_path):
    """Privée + .pub + certificat renommés ensemble ; conflits et noms invalides."""
    ca = _paire(tmp_path / "ca")
    cle = _paire(tmp_path / "ssh", "id_a")
    core.signer_cle(cle.with_name("id_a.pub"), ca, "moi", "alice", "always:forever")
    (tmp_path / "ssh" / "orpheline.pub").write_text("x", encoding="utf-8")
    (tmp_path / "ssh" / "privee_seule").write_text("x", encoding="utf-8")
    cles = {c.nom: c for c in core.lister_cles(tmp_path / "ssh")}
    core.renommer_cle(cles["privee_seule"], "renommee")
    assert (tmp_path / "ssh" / "renommee").exists()
    for nom in ("", "a/b", "a\\b", ".", ".."):
        with pytest.raises(core.ErreurCle) as invalide:
            core.renommer_cle(cles["id_a"], nom)
        assert invalide.value.code == "nom"
    (tmp_path / "ssh" / "pris-cert.pub").write_text("x", encoding="utf-8")
    with pytest.raises(core.ErreurCle) as conflit:
        core.renommer_cle(cles["id_a"], "pris")
    assert (conflit.value.code, conflit.value.detail) == ("existe", "pris-cert.pub")
    assert cle.exists()
    core.renommer_cle(cles["id_a"], "id_b")
    assert sorted(p.name for p in (tmp_path / "ssh").iterdir() if p.name.startswith("id_")) == [
        "id_b",
        "id_b-cert.pub",
        "id_b.pub",
    ]
    core.renommer_cle(cles["orpheline.pub"], "autre.pub")
    assert (tmp_path / "ssh" / "autre.pub").exists()
    with pytest.raises(core.ErreurCle) as disparue:
        core.renommer_cle(cles["orpheline.pub"], "encore.pub")
    assert disparue.value.code == "systeme"


def test_supprimer_cle(core, tmp_path, monkeypatch):
    """Privée, .pub et certificat supprimés ; orpheline seule ; échec système."""
    ca = _paire(tmp_path / "ca")
    cle = _paire(tmp_path / "ssh", "id_a")
    core.signer_cle(cle.with_name("id_a.pub"), ca, "moi", "alice", "always:forever")
    (tmp_path / "ssh" / "orpheline.pub").write_text("x", encoding="utf-8")
    cles = {c.nom: c for c in core.lister_cles(tmp_path / "ssh")}
    core.supprimer_cle(cles["id_a"])
    core.supprimer_cle(cles["orpheline.pub"])
    assert list((tmp_path / "ssh").iterdir()) == []
    (tmp_path / "ssh" / "id_a").mkdir()
    with pytest.raises(core.ErreurCle) as erreur:
        core.supprimer_cle(cles["id_a"])
    assert erreur.value.code == "systeme"


# -- CA désignée -----------------------------------------------------------


def test_config_ca(core, tmp_path):
    """Écriture en 600, relecture, CA disparue, fichier absent ou invalide."""
    config = tmp_path / "ssh" / ".gcm_ca_config.json"
    ca = _paire(tmp_path, "ca")
    assert core.lire_config_ca(config) is None
    assert core.ecrire_config_ca(config, ca)
    assert oct(config.stat().st_mode & 0o777) == "0o600"
    assert core.lire_config_ca(config) == ca
    assert core.ecrire_config_ca(config, None) and core.lire_config_ca(config) is None
    config.write_text('{"ca_priv": "/nexiste/pas"}', encoding="utf-8")
    assert core.lire_config_ca(config) is None
    config.write_text("[1, 2]", encoding="utf-8")
    assert core.lire_config_ca(config) is None
    config.write_text("{pas du json", encoding="utf-8")
    assert core.lire_config_ca(config) is None
    assert not core.ecrire_config_ca(tmp_path / "ca" / "config.json", ca)
