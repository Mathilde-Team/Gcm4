# SPDX-License-Identifier: GPL-3.0-or-later
"""Plugin web GTK4 (``plugins/web/gtk4.py``, #397) avec un vrai ``WebKit.WebView``.

Demande WebKitGTK 6.0 (``gir1.2-webkit-6.0``), installé dans le job « Tests
GTK4 (Xvfb) » ; écarté nommément sur Debian 12 et Rocky 9, qui testent le
repli navigateur avec ``test_plugin_web.py``. Les pages viennent de
serveurs HTTP et HTTPS locaux (127.0.0.1), le second avec un certificat
auto-signé généré par ``cryptography`` (dépendance de paramiko).
"""

from __future__ import annotations

import datetime
import http.server
import ssl
import sys
import threading
import time
from pathlib import Path

import gi
import pytest

gi.require_version("WebKit", "6.0")

from gi.repository import GLib, WebKit  # noqa: E402

from gcm4.core import registre  # noqa: E402
from gcm4.core.model import Host  # noqa: E402

RACINE = Path(__file__).resolve().parents[2]
PAGE = b"<!doctype html><html><head><title>Console BMC</title></head><body>ok</body></html>"


class Page(http.server.BaseHTTPRequestHandler):
    """Rend toujours la même page, titrée « Console BMC »."""

    def do_GET(self):  # noqa: N802 (nom imposé par http.server)
        """Page HTML."""
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(PAGE)))
        self.end_headers()
        self.wfile.write(PAGE)

    def log_message(self, *_args):
        """Silencieux."""


class Journal:
    """Journal du contexte : garde les avertissements."""

    def __init__(self):
        """Aucun avertissement."""
        self.avertissements = []

    def warning(self, message, *args):
        """Avertissement formaté comme loguru."""
        self.avertissements.append(message.format(*args))

    def debug(self, *_args):
        """Ignoré."""


class Contexte:
    """Contexte minimal du contrat v1."""

    def __init__(self):
        """Journal vide."""
        self.log = Journal()

    def config(self):
        """Section ``[plugin web]`` vide."""
        return {}

    def notify(self, _message):
        """Ignoré."""


def _certificat(dossier):
    """Certificat auto-signé pour 127.0.0.1 ; rend (certificat, clé)."""
    import ipaddress

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    cle = ec.generate_private_key(ec.SECP256R1())
    nom = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    maintenant = datetime.datetime.now(datetime.UTC)
    certificat = (
        x509.CertificateBuilder()
        .subject_name(nom)
        .issuer_name(nom)
        .public_key(cle.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(maintenant - datetime.timedelta(days=1))
        .not_valid_after(maintenant + datetime.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            critical=False,
        )
        .sign(cle, hashes.SHA256())
    )
    chemin_cert, chemin_cle = dossier / "cert.pem", dossier / "cle.pem"
    chemin_cert.write_bytes(certificat.public_bytes(serialization.Encoding.PEM))
    chemin_cle.write_bytes(
        cle.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return chemin_cert, chemin_cle


def _serveur(tls_dossier=None):
    """Serveur local dans un fil ; rend (serveur, URL)."""
    serveur = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Page)
    schema = "http"
    if tls_dossier is not None:
        contexte = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        contexte.load_cert_chain(*_certificat(tls_dossier))
        serveur.socket = contexte.wrap_socket(serveur.socket, server_side=True)
        schema = "https"
    threading.Thread(target=serveur.serve_forever, daemon=True).start()
    return serveur, f"{schema}://127.0.0.1:{serveur.server_port}/"


@pytest.fixture
def http_local():
    """URL d'un serveur HTTP local."""
    serveur, url = _serveur()
    yield url
    serveur.shutdown()


@pytest.fixture
def https_local(tmp_path):
    """URL d'un serveur HTTPS local à certificat auto-signé."""
    serveur, url = _serveur(tmp_path)
    yield url
    serveur.shutdown()


@pytest.fixture
def plugin():
    """Plugin web chargé depuis ``plugins/`` du dépôt."""
    candidats = registre.candidats_dossier(RACINE / "plugins")
    etats = {e.name: e for e in registre.decouvrir(lambda _nom: Contexte(), candidats).etats}
    assert etats["web"].erreur is None
    yield etats["web"].plugin
    registre._oublier_paquet("gcm4_plugin_web")


def _attendre(condition, delai=20.0):
    """Traite les événements GTK jusqu'à ``condition()`` ou ``delai`` secondes."""
    contexte = GLib.MainContext.default()
    fin = time.monotonic() + delai
    while not condition() and time.monotonic() < fin:
        contexte.iteration(False)
        time.sleep(0.005)
    return condition()


def _hote(url, ignorer="True"):
    """Hôte ``web`` de test."""
    return Host(
        name="bmc",
        protocol="web",
        protocol_options={"web": {"web_url": url, "web_ignore_cert": ignorer}},
    )


def _ouvrir(plugin, hote):
    """Session ouverte, son widget placé dans une fenêtre affichée."""
    from gi.repository import Gtk

    session = plugin.open(hote)
    fenetre = Gtk.Window(child=session.widget, default_width=800, default_height=600)
    fenetre.present()
    return session, fenetre


def test_charger_webkit_present(plugin):
    """WebKitGTK 6.0 installé : le module est rendu."""
    assert sys.modules[type(plugin).__module__].charger_webkit() is WebKit


def test_page_chargee_dans_l_onglet(plugin, http_local):
    """La page s'ouvre dans l'onglet ; l'état affiche son titre ; Recharger relance."""
    session, fenetre = _ouvrir(plugin, _hote(http_local))
    assert isinstance(session.webview, WebKit.WebView)
    assert _attendre(lambda: session.etat.get_text() == "Console BMC")
    assert session.webview.get_uri() == http_local
    session.bouton_recharger.emit("clicked")
    assert session.etat.get_text() == "Connecting…"
    assert _attendre(lambda: session.etat.get_text() == "Console BMC")
    session.close()
    assert session.ferme
    fenetre.destroy()


def test_echec_de_connexion(plugin):
    """Serveur absent : état d'erreur et avertissement journalisé."""
    serveur, url = _serveur()
    serveur.shutdown()
    serveur.server_close()  # port fermé : connexion refusée
    session, fenetre = _ouvrir(plugin, _hote(url))
    assert _attendre(lambda: session.en_erreur)
    assert session.etat.get_text().startswith("Error: ")
    assert url in session.context.log.avertissements[0]
    session.close()
    fenetre.destroy()


def test_certificat_auto_signe_accepte(plugin, https_local):
    """``web_ignore_cert`` vrai : certificat accepté pour 127.0.0.1 seulement, page chargée."""
    session, fenetre = _ouvrir(plugin, _hote(https_local, "True"))
    assert _attendre(lambda: session.etat.get_text() == "Console BMC")
    assert session.exceptions_tls == {"127.0.0.1"}
    assert any("127.0.0.1" in a for a in session.context.log.avertissements)
    session.close()
    fenetre.destroy()


def test_certificat_auto_signe_refuse(plugin, https_local):
    """``web_ignore_cert`` faux : certificat refusé, aucune exception enregistrée."""
    session, fenetre = _ouvrir(plugin, _hote(https_local, "False"))
    assert _attendre(lambda: session.en_erreur)
    assert session.exceptions_tls == set()
    assert session.etat.get_text().startswith("Error: ")
    session.close()
    fenetre.destroy()


def test_certificat_deja_accepte_puis_refuse(plugin, http_local):
    """Deuxième erreur TLS pour un hôte déjà accepté : refusée (pas de boucle de rechargement)."""
    session, fenetre = _ouvrir(plugin, _hote(http_local))
    session.exceptions_tls.add("127.0.0.1")
    assert session._erreur_tls(session.webview, "https://127.0.0.1/", None, None) is False
    assert session._erreur_tls(session.webview, "https://", None, None) is False  # sans hôte
    assert session.en_erreur
    session.close()
    fenetre.destroy()
