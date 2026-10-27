"""Dépôt du fichier rendu par ``snmp_bulk_core`` (SFTP, FTP, TFTP), issue #74.

Les transports repris de l'ancien ``snmp_push_core.py`` sont testés contre de
vrais serveurs lancés dans le test, sur 127.0.0.1 :

- SFTP : serveur SSH paramiko (``paramiko.ServerInterface`` + ``SFTPServer``) ;
- FTP : serveur minimal (USER, PASS, TYPE, PASV, STOR, QUIT) en TCP ;
- TFTP : serveur RFC 1350 en UDP qui répond depuis un port éphémère.

Seule la partie SNMP est simulée (``Session`` remplacée), faute d'équipement :
on vérifie les OID et valeurs écrits par le ``SET``.
"""

from __future__ import annotations

import socket
import socketserver
import struct
import threading
from pathlib import Path

import paramiko
import pytest
import test_snmp_bulk_core  # stub ezsnmp avant l'import du cœur

snmp_core = test_snmp_bulk_core.snmp_core

UTILISATEUR = "depot"
MOT_DE_PASSE = "secret-de-test"


# ── Serveur SFTP (paramiko) ─────────────────────────────────────────────


class _Handle(paramiko.SFTPHandle):
    """Fichier ouvert en écriture côté serveur."""

    def __init__(self, fichier):
        """Prépare l'état."""
        super().__init__()
        self.writefile = fichier


class _SftpRacine(paramiko.SFTPServerInterface):
    """SftpRacine."""

    racine: Path = Path("/nonexistent")

    def open(self, path, flags, attr):
        """Ouvre le fichier distant en écriture sous la racine."""
        chemin = self.racine / path.lstrip("/")
        try:
            fichier = open(chemin, "wb")  # noqa: SIM115 (fermé par SFTPHandle.close)
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)
        return _Handle(fichier)


class _Ssh(paramiko.ServerInterface):
    """Authentification par mot de passe du serveur de test."""

    def check_auth_password(self, username, password):
        """Accepte le seul couple utilisateur/mot de passe de test."""
        if (username, password) == (UTILISATEUR, MOT_DE_PASSE):
            return paramiko.AUTH_SUCCESSFUL
        return paramiko.AUTH_FAILED

    def get_allowed_auths(self, username):
        """Mot de passe seulement."""
        return "password"

    def check_channel_request(self, kind, chanid):
        """Accepte les canaux de session."""
        return paramiko.OPEN_SUCCEEDED


class ServeurSftp:
    """Serveur SSH/SFTP sur 127.0.0.1, un fil par connexion."""

    def __init__(self, racine: Path, cle: paramiko.PKey):
        """Prépare l'état."""
        self.racine, self.cle = racine, cle
        self.ecoute = socket.create_server(("127.0.0.1", 0))
        self.port = self.ecoute.getsockname()[1]
        self.transports: list[paramiko.Transport] = []
        threading.Thread(target=self._accepter, daemon=True).start()

    def _accepter(self):
        """Accepte les connexions SSH."""
        while True:
            try:
                sock, _ = self.ecoute.accept()
            except OSError:
                return
            transport = paramiko.Transport(sock)
            transport.add_server_key(self.cle)
            racine = self.racine

            class Racine(_SftpRacine):
                """Interface SFTP liée à la racine de ce serveur."""

                pass

            Racine.racine = racine
            transport.set_subsystem_handler("sftp", paramiko.SFTPServer, Racine)
            self.transports.append(transport)
            try:
                transport.start_server(server=_Ssh())
            except (paramiko.SSHException, EOFError, OSError):
                continue

    def ligne_known_hosts(self) -> str:
        """Ligne known_hosts de la clé du serveur."""
        return f"[127.0.0.1]:{self.port} {self.cle.get_name()} {self.cle.get_base64()}\n"

    def arreter(self):
        """Arrête le serveur."""
        self.ecoute.close()
        for transport in self.transports:
            transport.close()


@pytest.fixture(scope="module")
def cle_serveur():
    """Clé d'hôte Ed25519 du serveur SFTP, partagée par le module."""
    return paramiko.Ed25519Key.from_private_key_file(_cle_ed25519())


def _cle_ed25519() -> str:
    """Clé Ed25519 jetable (paramiko ne sait pas en générer)."""
    import tempfile

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519

    pem = ed25519.Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.OpenSSH,
        serialization.NoEncryption(),
    )
    fichier = Path(tempfile.mkdtemp()) / "cle"
    fichier.write_bytes(pem)
    return str(fichier)


@pytest.fixture
def maison(tmp_path, monkeypatch):
    """HOME vide : ``~/.ssh/known_hosts`` de l'utilisateur jamais lu."""
    dossier = tmp_path / "maison"
    dossier.mkdir()
    monkeypatch.setenv("HOME", str(dossier))
    return dossier


@pytest.fixture
def serveur_sftp(tmp_path, cle_serveur):
    """Serveur SFTP avec un dossier ``configs``."""
    racine = tmp_path / "sftp"
    (racine / "configs").mkdir(parents=True)
    serveur = ServeurSftp(racine, cle_serveur)
    yield serveur
    serveur.arreter()


def _sftp(serveur, tmp_path, **kwargs):
    """Configuration de dépôt SFTP vers *serveur*, clé d'hôte connue."""
    known = tmp_path / "known_hosts"
    known.write_text(serveur.ligne_known_hosts())
    options = {
        "protocol": "sftp",
        "host": "127.0.0.1",
        "port": serveur.port,
        "username": UTILISATEUR,
        "password": MOT_DE_PASSE,
        "remote_dir": "/configs",
        "known_hosts": str(known),
    }
    options.update(kwargs)
    return snmp_core.FileTransferConfig(**options)


def test_sftp_depose_le_fichier(serveur_sftp, tmp_path, maison):
    """Le fichier rendu (UTF-8) est écrit dans ``remote_dir``."""
    cfg = _sftp(serveur_sftp, tmp_path)
    chemin = snmp_core.upload_config_file(cfg, "sw1.cfg", "hostname sw1\nvlan 10 é\n")
    assert chemin == "/configs/sw1.cfg"
    depose = serveur_sftp.racine / "configs" / "sw1.cfg"
    assert depose.read_text(encoding="utf-8") == "hostname sw1\nvlan 10 é\n"


def test_sftp_cle_dans_known_hosts_systeme(serveur_sftp, tmp_path, maison):
    """``~/.ssh/known_hosts`` suffit, sans ``known_hosts`` dédié."""
    (maison / ".ssh").mkdir()
    (maison / ".ssh" / "known_hosts").write_text(serveur_sftp.ligne_known_hosts())
    cfg = _sftp(serveur_sftp, tmp_path, known_hosts="")
    snmp_core.upload_config_file(cfg, "sw2.cfg", "x")
    assert (serveur_sftp.racine / "configs" / "sw2.cfg").read_text() == "x"


def test_sftp_cle_inconnue_refusee(serveur_sftp, tmp_path, maison):
    """Clé d'hôte absente de known_hosts : refus, rien n'est écrit."""
    cfg = _sftp(serveur_sftp, tmp_path, known_hosts="")
    with pytest.raises(snmp_core.TransferError, match="inconnue"):
        snmp_core.upload_config_file(cfg, "sw1.cfg", "x")
    assert not (serveur_sftp.racine / "configs" / "sw1.cfg").exists()


def test_sftp_cle_changee_refusee(serveur_sftp, tmp_path, maison):
    """Clé d'hôte différente de celle connue : refus."""
    autre = paramiko.Ed25519Key.from_private_key_file(_cle_ed25519())
    cfg = _sftp(serveur_sftp, tmp_path)
    Path(cfg.known_hosts).write_text(
        f"[127.0.0.1]:{serveur_sftp.port} {autre.get_name()} {autre.get_base64()}\n"
    )
    with pytest.raises(snmp_core.TransferError, match="différente"):
        snmp_core.upload_config_file(cfg, "sw1.cfg", "x")


def test_sftp_mot_de_passe_refuse(serveur_sftp, tmp_path, maison):
    """Mot de passe refusé par le serveur : ``TransferError``."""
    cfg = _sftp(serveur_sftp, tmp_path, password="faux")
    with pytest.raises(snmp_core.TransferError, match="Erreur SSH"):
        snmp_core.upload_config_file(cfg, "sw1.cfg", "x")


def test_sftp_dossier_absent(serveur_sftp, tmp_path, maison):
    """Dossier distant inexistant : erreur SFTP remontée."""
    cfg = _sftp(serveur_sftp, tmp_path, remote_dir="/absent")
    with pytest.raises(snmp_core.TransferError, match="Dépôt SFTP"):
        snmp_core.upload_config_file(cfg, "sw1.cfg", "x")


def test_sftp_serveur_injoignable(tmp_path, maison):
    """Port fermé : erreur réseau remontée."""
    port = _port_libre()
    cfg = snmp_core.FileTransferConfig(protocol="sftp", host="127.0.0.1", port=port)
    with pytest.raises(snmp_core.TransferError, match="Dépôt SFTP"):
        snmp_core.upload_config_file(cfg, "sw1.cfg", "x")


def test_sftp_sans_paramiko(monkeypatch):
    """Sans paramiko, le dépôt SFTP échoue avec un message clair."""
    monkeypatch.setitem(__import__("sys").modules, "paramiko", None)
    cfg = snmp_core.FileTransferConfig(protocol="sftp", host="127.0.0.1")
    with pytest.raises(snmp_core.TransferError, match="paramiko"):
        snmp_core.upload_config_file(cfg, "sw1.cfg", "x")


# ── Serveur FTP minimal ─────────────────────────────────────────────────


class _Ftp(socketserver.StreamRequestHandler):
    """Serveur FTP réduit au dépôt d'un fichier."""

    def repondre(self, ligne: str):
        """Envoie une ligne de réponse FTP."""
        self.wfile.write((ligne + "\r\n").encode())

    def handle(self):
        """Boucle de commandes FTP d'une connexion."""
        racine: Path = self.server.racine  # type: ignore[attr-defined]
        donnees = None
        utilisateur = ""
        connecte = False
        self.repondre("220 test")
        for brut in self.rfile:
            commande, _, argument = brut.decode().strip().partition(" ")
            commande = commande.upper()
            if commande == "USER":
                utilisateur = argument
                self.repondre("331 mot de passe")
            elif commande == "PASS":
                connecte = (utilisateur, argument) == (UTILISATEUR, MOT_DE_PASSE)
                self.repondre("230 ok" if connecte else "530 refusé")
            elif not connecte:
                self.repondre("530 non connecté")
            elif commande == "TYPE":
                self.repondre("200 ok")
            elif commande == "PASV":
                donnees = socket.create_server(("127.0.0.1", 0))
                port = donnees.getsockname()[1]
                self.repondre(f"227 passif (127,0,0,1,{port >> 8},{port & 0xFF})")
            elif commande == "STOR" and donnees is not None:
                cible = racine / argument.lstrip("/")
                if not cible.parent.is_dir():
                    self.repondre("553 dossier absent")
                    continue
                self.repondre("150 envoi")
                conn, _ = donnees.accept()
                with conn, open(cible, "wb") as sortie:
                    while bloc := conn.recv(4096):
                        sortie.write(bloc)
                donnees.close()
                self.repondre("226 reçu")
            elif commande == "QUIT":
                self.repondre("221 au revoir")
                return
            else:
                self.repondre("502 non géré")


@pytest.fixture
def serveur_ftp(tmp_path):
    """Serveur FTP avec un dossier ``configs``."""
    racine = tmp_path / "ftp"
    (racine / "configs").mkdir(parents=True)
    serveur = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Ftp)
    serveur.daemon_threads = True
    serveur.racine = racine  # type: ignore[attr-defined]
    threading.Thread(target=serveur.serve_forever, daemon=True).start()
    yield serveur
    serveur.shutdown()
    serveur.server_close()


def _ftp(serveur, **kwargs):
    """Configuration de dépôt FTP vers *serveur*."""
    options = {
        "protocol": "ftp",
        "host": "127.0.0.1",
        "port": serveur.server_address[1],
        "username": UTILISATEUR,
        "password": MOT_DE_PASSE,
        "remote_dir": "/configs/",
    }
    options.update(kwargs)
    return snmp_core.FileTransferConfig(**options)


def test_ftp_depose_le_fichier(serveur_ftp):
    """Le fichier est stocké en binaire dans ``remote_dir``."""
    contenu = "interface Gi1\n" * 1000
    chemin = snmp_core.upload_config_file(_ftp(serveur_ftp), "sw1.cfg", contenu)
    assert chemin == "/configs/sw1.cfg"
    assert (serveur_ftp.racine / "configs" / "sw1.cfg").read_text() == contenu


def test_ftp_identifiants_refuses(serveur_ftp):
    """Identifiants refusés (530) : ``TransferError``."""
    with pytest.raises(snmp_core.TransferError, match="Dépôt FTP"):
        snmp_core.upload_config_file(_ftp(serveur_ftp, password="faux"), "sw1.cfg", "x")


def test_ftp_anonyme_refuse(serveur_ftp):
    """Sans utilisateur, ``anonymous`` est essayé (et refusé ici)."""
    cfg = _ftp(serveur_ftp, username="", password="")
    with pytest.raises(snmp_core.TransferError, match="530"):
        snmp_core.upload_config_file(cfg, "sw1.cfg", "x")


def test_ftp_dossier_absent(serveur_ftp):
    """Dossier distant inexistant (553) : ``TransferError``."""
    with pytest.raises(snmp_core.TransferError, match="553"):
        snmp_core.upload_config_file(_ftp(serveur_ftp, remote_dir="/absent"), "sw1.cfg", "x")


def test_ftp_serveur_injoignable():
    """Port fermé : erreur réseau remontée."""
    cfg = snmp_core.FileTransferConfig(protocol="ftp", host="127.0.0.1", port=_port_libre())
    with pytest.raises(snmp_core.TransferError, match="Dépôt FTP"):
        snmp_core.upload_config_file(cfg, "sw1.cfg", "x")


# ── Serveur TFTP (RFC 1350) ─────────────────────────────────────────────


class ServeurTftp:
    """WRQ reçu sur ``port`` ; la suite de l'échange depuis un port éphémère.

    ``mode`` : ``"normal"``, ``"erreur"`` (paquet ERROR au WRQ), ``"muet"``
    (aucune réponse), ``"bizarre"`` (opcode inattendu), ``"doublon"`` (ACK
    du bloc précédent avant le bon).
    """

    def __init__(self, racine: Path, mode: str = "normal"):
        """Prépare l'état."""
        self.racine, self.mode = racine, mode
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.recus: dict[str, bytes] = {}
        self.blocs = 0
        threading.Thread(target=self._servir, daemon=True).start()

    def _servir(self):
        """Sert un seul transfert."""
        try:
            paquet, client = self.sock.recvfrom(1024)
        except OSError:
            return
        opcode = struct.unpack("!H", paquet[:2])[0]
        nom = paquet[2:].split(b"\x00")[0].decode()
        if self.mode == "muet" or opcode != 2:
            return
        if self.mode == "erreur":
            self.sock.sendto(struct.pack("!HH", 5, 2) + b"Access violation\x00", client)
            return
        if self.mode == "bizarre":
            self.sock.sendto(struct.pack("!HH", 9, 0), client)
            return
        echange = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        echange.bind(("127.0.0.1", 0))
        echange.settimeout(5)
        contenu = b""
        with echange:
            echange.sendto(struct.pack("!HH", 4, 0), client)
            attendu = 1
            while True:
                paquet, client = echange.recvfrom(1024)
                _op, bloc = struct.unpack("!HH", paquet[:4])
                if bloc == attendu:
                    contenu += paquet[4:]
                    self.blocs += 1
                    attendu += 1
                if self.mode == "doublon":
                    echange.sendto(struct.pack("!HH", 4, bloc - 1), client)
                echange.sendto(struct.pack("!HH", 4, bloc), client)
                if len(paquet) - 4 < 512:
                    break
        self.recus[nom] = contenu

    def arreter(self):
        """Arrête le serveur."""
        self.sock.close()


@pytest.fixture
def tftp(tmp_path):
    """Fabrique de serveurs TFTP, arrêtés en fin de test."""
    serveurs: list[ServeurTftp] = []

    def lancer(mode="normal"):
        """Démarre un serveur TFTP dans le mode demandé."""
        serveur = ServeurTftp(tmp_path, mode)
        serveurs.append(serveur)
        return serveur

    yield lancer
    for serveur in serveurs:
        serveur.arreter()


def _attendre(condition, delai=5.0):
    """Attend *condition* (le serveur écrit après le dernier ACK)."""
    import time

    fin = time.monotonic() + delai
    while not condition() and time.monotonic() < fin:
        time.sleep(0.01)
    return condition()


def _tftp(serveur, **kwargs):
    """Configuration de dépôt TFTP vers *serveur*."""
    options = {"protocol": "tftp", "host": "127.0.0.1", "port": serveur.port}
    options.update(kwargs)
    return snmp_core.FileTransferConfig(**options)


@pytest.mark.parametrize("taille", [0, 100, 512, 1024, 1500])
def test_tftp_depose_le_fichier(tftp, taille):
    """Dernier bloc court, ou vide si la taille est un multiple de 512."""
    serveur = tftp()
    contenu = ("abcdefgh" * 200)[:taille]
    assert snmp_core.upload_config_file(_tftp(serveur), "sw1.cfg", contenu) == "sw1.cfg"
    assert _attendre(lambda: "sw1.cfg" in serveur.recus)
    assert serveur.recus["sw1.cfg"] == contenu.encode()
    assert serveur.blocs == taille // 512 + 1


def test_tftp_dossier_distant(tftp):
    """``remote_dir`` préfixe le nom demandé dans le WRQ."""
    serveur = tftp()
    chemin = snmp_core.upload_config_file(_tftp(serveur, remote_dir="gcm/"), "sw1.cfg", "x")
    assert chemin == "gcm/sw1.cfg"
    assert _attendre(lambda: "gcm/sw1.cfg" in serveur.recus)


def test_tftp_ack_en_double_ignore(tftp):
    """Un ACK du bloc précédent est ignoré, le transfert continue."""
    serveur = tftp("doublon")
    snmp_core.upload_config_file(_tftp(serveur), "sw1.cfg", "y" * 700)
    assert _attendre(lambda: "sw1.cfg" in serveur.recus)
    assert serveur.recus["sw1.cfg"] == b"y" * 700


def test_tftp_erreur_du_serveur(tftp):
    """Paquet ERROR du serveur : son message est remonté."""
    with pytest.raises(snmp_core.TransferError, match="Access violation"):
        snmp_core.upload_config_file(_tftp(tftp("erreur")), "sw1.cfg", "x")


def test_tftp_reponse_inattendue(tftp):
    """Opcode inconnu en réponse : ``TransferError``."""
    with pytest.raises(snmp_core.TransferError, match="opcode 9"):
        snmp_core.upload_config_file(_tftp(tftp("bizarre")), "sw1.cfg", "x")


def test_tftp_sans_reponse(tftp, monkeypatch):
    """Aucune réponse après les réémissions : ``TransferError``."""
    monkeypatch.setattr(snmp_core, "_TFTP_TIMEOUT_S", 0.05)
    with pytest.raises(snmp_core.TransferError, match="Pas d'ACK TFTP pour le bloc 0"):
        snmp_core.upload_config_file(_tftp(tftp("muet")), "sw1.cfg", "x")


def test_tftp_nom_non_ascii():
    """TFTP n'accepte que des noms ASCII."""
    cfg = snmp_core.FileTransferConfig(protocol="tftp", host="127.0.0.1")
    with pytest.raises(snmp_core.TransferError, match="non ASCII"):
        snmp_core.upload_config_file(cfg, "équipement.cfg", "x")


def test_tftp_hote_introuvable(monkeypatch):
    """Nom d'hôte non résolu : ``TransferError``."""

    def echec(*_args, **_kwargs):
        """Simule un échec de résolution."""
        raise socket.gaierror("Name or service not known")

    monkeypatch.setattr(snmp_core.socket, "getaddrinfo", echec)
    cfg = snmp_core.FileTransferConfig(protocol="tftp", host="depot.invalid")
    with pytest.raises(snmp_core.TransferError, match="introuvable"):
        snmp_core.upload_config_file(cfg, "sw1.cfg", "x")


def _port_libre() -> int:
    """Port TCP local sans service à l'écoute."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


# ── FileTransferConfig et dispatch ──────────────────────────────────────


def test_protocole_de_depot_inconnu():
    """Protocole de dépôt hors SFTP/FTP/TFTP : refus."""
    cfg = snmp_core.FileTransferConfig(protocol="scp", host="h")
    with pytest.raises(snmp_core.TransferError, match="inconnu"):
        snmp_core.upload_config_file(cfg, "a", "x")


@pytest.mark.parametrize(
    ("protocol", "port", "attendu"),
    [("sftp", 0, 22), ("ftp", 0, 21), ("tftp", 0, 69), ("tftp", 1069, 1069), ("scp", 0, 0)],
)
def test_port_effectif(protocol, port, attendu):
    """Port explicite, sinon celui du protocole, 0 si inconnu."""
    cfg = snmp_core.FileTransferConfig(protocol=protocol, host="h", port=port)
    assert cfg.effective_port() == attendu


@pytest.mark.parametrize(
    ("protocol", "fetch", "attendu"),
    [("sftp", "", "tftp"), ("ftp", "", "ftp"), ("tftp", "", "tftp"), ("sftp", "ftp", "ftp")],
)
def test_protocole_de_recuperation(protocol, fetch, attendu):
    """Récupération : explicite, sinon celui du dépôt, TFTP après SFTP."""
    cfg = snmp_core.FileTransferConfig(protocol=protocol, host="h", fetch_protocol=fetch)
    assert cfg.effective_fetch_protocol() == attendu


def test_chemins():
    """Chemin de dépôt et nom écrit dans le SET."""
    cfg = snmp_core.FileTransferConfig(protocol="sftp", host="h", remote_dir="/srv/tftp/")
    assert cfg.remote_path("a.cfg") == "/srv/tftp/a.cfg"
    assert cfg.device_filename("a.cfg") == "a.cfg"
    cfg.fetch_path = "gcm/"
    assert cfg.device_filename("a.cfg") == "gcm/a.cfg"
    assert snmp_core.FileTransferConfig("tftp", "h", remote_dir="").remote_path("a") == "a"


def test_mot_de_passe_absent_du_repr():
    """Le mot de passe n'apparaît pas dans ``repr`` (journaux)."""
    cfg = snmp_core.FileTransferConfig(protocol="ftp", host="h", password="s3cret")
    assert "s3cret" not in repr(cfg)


# ── Drivers : protocole de récupération dans le SET ─────────────────────


class FausseSession:
    """Remplace ``ezsnmp.Session`` : enregistre les SET, répond aux GET/WALK."""

    sets: list[list[tuple[str, str, str]]] = []
    etat = "3"

    def __init__(self, **kwargs):
        """Prépare l'état."""
        self.kwargs = kwargs

    def set_multiple(self, oid_values):
        """Enregistre les varbinds du SET."""
        FausseSession.sets.append(list(oid_values))

    def get(self, oid):
        """Répond l'état configuré."""
        return type("Var", (), {"value": FausseSession.etat})()

    def walk(self, oid):
        """Renvoie la ligne de résultat de la dernière opération."""
        index = FausseSession.sets[-1][0][0].rsplit(".", 1)[1]
        return [type("Var", (), {"value": index, "oid_index": "1"})()]


@pytest.fixture
def snmp(monkeypatch):
    """Remplace ``Session`` par ``FausseSession``."""
    FausseSession.sets = []
    FausseSession.etat = "3"
    monkeypatch.setattr(snmp_core, "Session", FausseSession)
    return FausseSession


def _driver(classe):
    """Driver *classe* sans attente entre deux sondages."""
    auth = snmp_core.SnmpAuth(version="v2c", community="private")
    return classe(auth=auth, poll_interval=0.0, poll_timeout=5.0)


def _valeurs(oid_values):
    """Colonne MIB -> valeur des varbinds d'un SET."""
    return {oid.rsplit(".", 2)[1]: valeur for oid, valeur, _type in oid_values}


@pytest.mark.parametrize(
    ("classe", "colonne", "protocole", "valeur", "etat"),
    [
        (snmp_core.H3cComwareDriver, "3", "ftp", "1", "2"),
        (snmp_core.H3cComwareDriver, "3", "tftp", "2", "2"),
        (snmp_core.CiscoConfigCopyDriver, "2", "tftp", "1", "3"),
        (snmp_core.CiscoConfigCopyDriver, "2", "ftp", "2", "3"),
        (snmp_core.CiscoConfigCopyDriver, "2", "sftp", "5", "3"),
        (snmp_core.HuaweiVrpDriver, "3", "ftp", "1", "2"),
        (snmp_core.HuaweiVrpDriver, "3", "tftp", "2", "2"),
        (snmp_core.HuaweiVrpDriver, "3", "sftp", "3", "2"),
    ],
)
def test_protocole_ecrit_dans_le_set(snmp, classe, colonne, protocole, valeur, etat):
    """La valeur MIB du protocole est écrite dans la bonne colonne."""
    snmp.etat = etat
    ok, detail = _driver(classe).push("10.0.0.1", "192.0.2.5", "a.cfg", protocol=protocole)
    assert (ok, detail) == (True, "succès")
    assert _valeurs(snmp.sets[0])[colonne] == valeur


def test_protocole_par_defaut_tftp(snmp):
    """Appel historique sans ``protocol`` : TFTP, comme avant #74."""
    snmp.etat = "2"
    _driver(snmp_core.H3cComwareDriver).push("10.0.0.1", "192.0.2.5", "a.cfg")
    assert _valeurs(snmp.sets[0])["3"] == "2"


def test_protocole_non_gere_sans_set(snmp):
    """Protocole non géré par le constructeur : échec sans SET."""
    ok, detail = _driver(snmp_core.H3cComwareDriver).push(
        "10.0.0.1", "192.0.2.5", "a.cfg", protocol="sftp"
    )
    assert not ok
    assert "H3C / HPE Comware ne récupère pas de fichier en sftp" in detail
    assert "ftp, tftp" in detail
    assert snmp.sets == []


@pytest.mark.parametrize("classe", [snmp_core.CiscoConfigCopyDriver, snmp_core.HuaweiVrpDriver])
def test_protocole_inconnu_sans_set(snmp, classe):
    """Protocole inconnu : échec sans SET."""
    ok, _detail = _driver(classe).push("10.0.0.1", "192.0.2.5", "a.cfg", protocol="scp")
    assert not ok
    assert snmp.sets == []


def test_protocol_value_du_driver_de_base():
    """Le driver de base ne gère aucun protocole."""
    assert _driver(snmp_core.SnmpDriver).protocol_value("tftp") is None


# ── push_to_device / run_bulk_push avec dépôt ───────────────────────────


def _profil(vendor="cisco_ios"):
    """Profil SNMP v2c du constructeur *vendor*."""
    return snmp_core.SnmpProfile(
        name="p",
        vendor=vendor,
        auth=snmp_core.SnmpAuth(version="v2c", community="private"),
        poll_interval=0.0,
    )


def _ligne(ip="10.0.0.1", line_no=3):
    """Ligne d'inventaire de test."""
    return snmp_core.InventoryRow(line_no=line_no, ip=ip, profile_name="p")


def test_push_depose_puis_declenche_en_tftp(snmp, tftp, tmp_path):
    """Dépôt TFTP puis SET : serveur, chemin, protocole, sans identifiants."""
    serveur = tftp()
    cfg = _tftp(serveur, username="ignoré", password="ignoré", fetch_path="gcm")
    resultat = snmp_core.push_to_device(
        _ligne(), _profil(), "hostname sw1\n", tmp_path, transfer=cfg
    )
    assert resultat.status == snmp_core.STATUS_OK, resultat.error
    assert _attendre(lambda: "gcm_push_10.0.0.1_3.cfg" in serveur.recus)
    assert serveur.recus["gcm_push_10.0.0.1_3.cfg"] == b"hostname sw1\n"
    valeurs = _valeurs(snmp.sets[0])
    assert valeurs["2"] == "1"  # ccCopyProtocol = tftp
    assert valeurs["5"] == "127.0.0.1"  # serveur de dépôt
    assert valeurs["6"] == "gcm/gcm_push_10.0.0.1_3.cfg"
    assert "7" not in valeurs and "8" not in valeurs  # pas d'identifiants en TFTP


def test_push_ftp_transmet_les_identifiants(snmp, serveur_ftp, tmp_path):
    """En FTP, les identifiants vont dans le SET mais pas dans le journal."""
    cfg = _ftp(serveur_ftp)
    resultat = snmp_core.push_to_device(_ligne(), _profil(), "conf", tmp_path, transfer=cfg)
    assert resultat.status == snmp_core.STATUS_OK, resultat.error
    assert (serveur_ftp.racine / "configs" / "gcm_push_10.0.0.1_3.cfg").read_text() == "conf"
    valeurs = _valeurs(snmp.sets[0])
    assert (valeurs["2"], valeurs["7"], valeurs["8"]) == ("2", UTILISATEUR, MOT_DE_PASSE)
    journal = Path(resultat.log_path).read_text()
    assert MOT_DE_PASSE not in journal


def test_push_sftp_puis_tftp(snmp, serveur_sftp, tmp_path, maison):
    """Dépôt SFTP, récupération TFTP par défaut (cas du serveur unique)."""
    snmp.etat = "2"
    cfg = _sftp(serveur_sftp, tmp_path)
    resultat = snmp_core.push_to_device(
        _ligne(ip="sw:1/a"), _profil("huawei_vrp"), "conf", tmp_path, transfer=cfg
    )
    assert resultat.status == snmp_core.STATUS_OK, resultat.error
    assert (serveur_sftp.racine / "configs" / "gcm_push_sw_1_a_3.cfg").exists()
    valeurs = _valeurs(snmp.sets[0])
    assert valeurs["3"] == "2" and "6" not in valeurs


def test_push_protocole_non_gere_ne_depose_rien(snmp, tftp, tmp_path):
    """Protocole de récupération non géré : ni dépôt ni SET."""
    serveur = tftp()
    cfg = _tftp(serveur, fetch_protocol="sftp")
    resultat = snmp_core.push_to_device(
        _ligne(), _profil("h3c_comware"), "conf", tmp_path, transfer=cfg
    )
    assert resultat.status == snmp_core.STATUS_FAILED
    assert "ne récupère pas de fichier en sftp" in resultat.error
    assert serveur.recus == {} and snmp.sets == []


def test_push_echec_du_depot(snmp, tftp, tmp_path):
    """Échec du dépôt : statut FAILED, aucun SET envoyé."""
    resultat = snmp_core.push_to_device(
        _ligne(), _profil(), "conf", tmp_path, transfer=_tftp(tftp("erreur"))
    )
    assert resultat.status == snmp_core.STATUS_FAILED
    assert resultat.error.startswith("Dépôt du fichier impossible : Erreur TFTP")
    assert snmp.sets == []


def test_push_sans_depot_inchange(snmp, tmp_path):
    """Sans ``transfer`` : aucun dépôt, ``config_<ip>.txt`` en TFTP sur ``server_ip``."""
    resultat = snmp_core.push_to_device(
        _ligne(), _profil(), "conf", tmp_path, server_ip="192.0.2.9"
    )
    assert resultat.status == snmp_core.STATUS_OK
    valeurs = _valeurs(snmp.sets[0])
    assert (valeurs["2"], valeurs["5"], valeurs["6"]) == ("1", "192.0.2.9", "config_10.0.0.1.txt")


def test_run_bulk_push_transmet_le_depot(snmp, tftp, tmp_path):
    """``run_bulk_push`` transmet ``transfer`` à chaque envoi."""
    serveur = tftp()
    magasin = snmp_core.SnmpProfileStore(tmp_path / "profils.ini")
    magasin.set("p", _profil())
    resultats = snmp_core.run_bulk_push(
        [_ligne()],
        magasin,
        "hostname #nom#",
        tmp_path / "journaux",
        transfer=_tftp(serveur),
    )
    assert [r.status for r in resultats] == [snmp_core.STATUS_OK]
    assert _attendre(lambda: "gcm_push_10.0.0.1_3.cfg" in serveur.recus)
