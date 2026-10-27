"""VeNCrypt côté onglet VNC (Gcm4 #104, tâche 2).

asyncvnc2 négocie VeNCrypt seul (magasin système de CA, nom d'hôte
vérifié = ``host``) et accepte ``ssl_context=`` pour une CA privée.
Côté plugin : ``VncConnectionInfo.tls_ca_file``, la décision
``VncDisplay._resolve_tls_kwargs`` et des messages d'erreur TLS explicites
(``_describe_connection_error``). Les certificats sont générés pour de
vrai (cryptography) et la vérification passe par une vraie poignée de
main TLS en boucle locale, pas par un faux objet.
"""

from __future__ import annotations

import asyncio
import datetime
import ipaddress
import ssl

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from conftest import GTK4_AVAILABLE, make_test_display, requires_gtk4

if GTK4_AVAILABLE:
    import vnc_tab
    from vnc_tab import VncConnectionInfo, VncDisplay, VncStatus


def _nom(cn):
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])


def _ca(tmp_path, cn):
    cle = ec.generate_private_key(ec.SECP256R1())
    maintenant = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(_nom(cn))
        .issuer_name(_nom(cn))
        .public_key(cle.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(maintenant - datetime.timedelta(minutes=5))
        .not_valid_after(maintenant + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(cle.public_key()), critical=False)
        .sign(cle, hashes.SHA256())
    )
    chemin = tmp_path / f"{cn}.pem"
    chemin.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cle, cert, chemin


def _serveur(tmp_path, ca_cle, ca_cert):
    cle = ec.generate_private_key(ec.SECP256R1())
    maintenant = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(_nom("127.0.0.1"))
        .issuer_name(ca_cert.subject)
        .public_key(cle.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(maintenant - datetime.timedelta(minutes=5))
        .not_valid_after(maintenant + datetime.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_cle.public_key()), critical=False)
        .sign(ca_cle, hashes.SHA256())
    )
    cert_pem = tmp_path / "serveur.pem"
    cle_pem = tmp_path / "serveur.key"
    cert_pem.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    cle_pem.write_bytes(
        cle.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    contexte = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    contexte.load_cert_chain(cert_pem, cle_pem)
    return contexte


async def _poignee_de_main(contexte_serveur, contexte_client):
    async def _gerer(reader, writer):
        writer.close()

    serveur = await asyncio.start_server(_gerer, "127.0.0.1", 0, ssl=contexte_serveur)
    port = serveur.sockets[0].getsockname()[1]
    try:
        _r, w = await asyncio.open_connection("127.0.0.1", port, ssl=contexte_client, server_hostname="127.0.0.1")
        w.close()
    finally:
        serveur.close()
        await serveur.wait_closed()


@requires_gtk4
def test_default_uses_system_store():
    assert VncConnectionInfo(name="n", host="h").tls_ca_file is None
    assert VncDisplay._resolve_tls_kwargs(None) == {}
    assert VncDisplay._resolve_tls_kwargs("  ") == {}


@requires_gtk4
def test_private_ca_builds_a_verifying_context(tmp_path):
    _cle, _cert, chemin = _ca(tmp_path, "ca-privee")
    contexte = VncDisplay._resolve_tls_kwargs(f" {chemin} ")["ssl_context"]
    assert contexte.verify_mode == ssl.CERT_REQUIRED and contexte.check_hostname is True
    assert [c["subject"] for c in contexte.get_ca_certs()] == [((("commonName", "ca-privee"),),)]


@requires_gtk4
def test_tilde_is_expanded(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    _ca(tmp_path, "ca-home")
    assert "ssl_context" in VncDisplay._resolve_tls_kwargs("~/ca-home.pem")


@requires_gtk4
@pytest.mark.parametrize("contenu", [None, b"pas un certificat\n"])
def test_unreadable_ca_is_refused(tmp_path, contenu):
    chemin = tmp_path / "ca.pem"
    if contenu is not None:
        chemin.write_bytes(contenu)
    with pytest.raises(ValueError, match="autorité de certification illisible"):
        VncDisplay._resolve_tls_kwargs(str(chemin))


@requires_gtk4
def test_real_handshake_trusted_and_refused(tmp_path):
    ca_cle, ca_cert, ca_pem = _ca(tmp_path, "ca-serveur")
    _autre_cle, _autre_cert, autre_pem = _ca(tmp_path, "ca-etrangere")
    contexte_serveur = _serveur(tmp_path, ca_cle, ca_cert)

    bon = VncDisplay._resolve_tls_kwargs(str(ca_pem))["ssl_context"]
    asyncio.run(_poignee_de_main(contexte_serveur, bon))

    mauvais = VncDisplay._resolve_tls_kwargs(str(autre_pem))["ssl_context"]
    with pytest.raises(ssl.SSLCertVerificationError) as info:
        asyncio.run(_poignee_de_main(contexte_serveur, mauvais))
    message = VncDisplay._describe_connection_error(info.value)
    assert message.startswith("Certificat du serveur refusé (VeNCrypt) : ")
    assert "unable to get local issuer certificate" in message


@requires_gtk4
def test_other_errors_keep_their_message():
    assert VncDisplay._describe_connection_error(ssl.SSLError("bad record")).startswith(
        "Échec de la négociation TLS (VeNCrypt) : "
    )
    assert VncDisplay._describe_connection_error(ConnectionRefusedError("refusé")) == "Échec de connexion : refusé"


def _run_connect(monkeypatch, exc=None, **info_kwargs):
    appels = []

    def faux_connect(host, port, **kwargs):
        appels.append(kwargs)
        raise exc or ConnectionRefusedError("simulé")

    monkeypatch.setattr(vnc_tab.asyncvnc2, "connect", faux_connect)
    display = make_test_display(100, 100, 100, 100)
    for cle, valeur in info_kwargs.items():
        setattr(display.info, cle, valeur)
    statuts = []
    monkeypatch.setattr(display, "_set_status", lambda s, m="": statuts.append((s, m)))
    asyncio.run(display._connect_and_run())
    return appels, statuts


@requires_gtk4
def test_connect_receives_the_context(tmp_path, monkeypatch):
    _cle, _cert, chemin = _ca(tmp_path, "ca")
    appels, _statuts = _run_connect(monkeypatch, tls_ca_file=str(chemin))
    assert isinstance(appels[0]["ssl_context"], ssl.SSLContext)


@requires_gtk4
def test_connect_without_ca_passes_nothing(monkeypatch):
    appels, _statuts = _run_connect(monkeypatch)
    assert "ssl_context" not in appels[0]


@requires_gtk4
def test_unreadable_ca_stops_before_connect(tmp_path, monkeypatch):
    appels, statuts = _run_connect(monkeypatch, tls_ca_file=str(tmp_path / "absent.pem"))
    assert appels == []
    assert statuts[-1][0] == VncStatus.ERROR and statuts[-1][1].startswith("VeNCrypt : ")


@requires_gtk4
def test_tls_failure_status_names_vencrypt(monkeypatch):
    _appels, statuts = _run_connect(monkeypatch, exc=ssl.SSLError("bad record"))
    assert statuts[-1][0] == VncStatus.ERROR and "VeNCrypt" in statuts[-1][1]
