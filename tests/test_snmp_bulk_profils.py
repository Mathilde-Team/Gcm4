# SPDX-License-Identifier: GPL-3.0-or-later
"""Profils, inventaire et gabarits de snmp_bulk_core.py (cœur sans GTK).

Complète les tests existants (test_snmp_bulk_core.py, test_snmp_bulk_transferts.py)
sur les chemins restés sans couverture : authentification v3, magasin de
profils sur disque, inventaire CSV, variables de gabarit manquantes et
chemins d'erreur des sondages de résultat des drivers.
"""

from __future__ import annotations

import textwrap

import pytest
import test_snmp_bulk_core  # stub ezsnmp avant l'import du cœur

snmp_core = test_snmp_bulk_core.snmp_core


# ── SnmpAuth.session_kwargs ────────────────────────────────────────────────


def test_session_kwargs_v2c():
    """v2c : communauté, hôte, port, timeout et retries."""
    auth = snmp_core.SnmpAuth(version="v2c", community="publique")
    kwargs = auth.session_kwargs("10.0.0.1", port=1161, timeout=4, retries=1)
    assert kwargs["hostname"] == "10.0.0.1"
    assert kwargs["port"] == 1161
    assert kwargs["version"] == 2
    assert kwargs["community"] == "publique"
    assert kwargs["timeout"] == 4
    assert kwargs["retries"] == 1


def test_session_kwargs_v3():
    """v3 : paramètres USM complets repris du profil."""
    auth = snmp_core.SnmpAuth(
        version="v3",
        username="admin",
        auth_password="auth-secret",
        auth_protocol="SHA",
        priv_password="priv-secret",
        priv_protocol="AES",
        security_level="auth_with_privacy",
    )
    kwargs = auth.session_kwargs("10.0.0.2")
    assert kwargs["version"] == 3
    assert kwargs["security_username"] == "admin"
    assert kwargs["auth_protocol"] == "SHA"
    assert kwargs["auth_password"] == "auth-secret"
    assert kwargs["privacy_protocol"] == "AES"
    assert kwargs["privacy_password"] == "priv-secret"
    assert kwargs["security_level"] == "auth_with_privacy"


def test_session_kwargs_v3_sans_optionnels():
    """v3 sans mots de passe : chaînes vides, protocoles par défaut."""
    auth = snmp_core.SnmpAuth(version="v3")
    kwargs = auth.session_kwargs("10.0.0.3")
    assert kwargs["security_username"] == ""
    assert kwargs["auth_password"] == ""
    assert kwargs["privacy_password"] == ""
    assert kwargs["auth_protocol"] == "SHA"
    assert kwargs["privacy_protocol"] == "AES"


# ── SnmpProfileStore ──────────────────────────────────────────────────────


@pytest.fixture
def fichier_profils(tmp_path):
    """Fichier INI de deux profils (un v2c, un v3)."""
    chemin = tmp_path / "snmp_profiles.ini"
    chemin.write_text(
        textwrap.dedent(
            """\
            [profile:cisco-v2]
            vendor = cisco
            snmp_version = v2c
            community = publique
            port = 1161
            timeout = 6
            retries = 3
            poll_interval = 1.5
            poll_timeout = 90.0

            [profile:hp-v3]
            vendor = h3c_comware
            snmp_version = v3
            username = admin
            auth_password = auth-secret
            auth_protocol = SHA
            priv_password = priv-secret
            priv_protocol = AES
            security_level = auth_with_privacy
            """
        ),
        encoding="utf-8",
    )
    return chemin


def test_store_charge_les_profils(fichier_profils):
    """load() relit le fichier : noms, authentification et minuteries."""
    store = snmp_core.SnmpProfileStore(fichier_profils)
    assert len(store.all()) == 2

    cisco = store.get("cisco-v2")
    assert cisco is not None
    assert cisco.vendor == "cisco"
    assert cisco.auth.version == "v2c"
    assert cisco.auth.community == "publique"
    assert cisco.port == 1161
    assert cisco.timeout == 6
    assert cisco.retries == 3
    assert cisco.poll_interval == 1.5
    assert cisco.poll_timeout == 90.0

    hp = store.get("hp-v3")
    assert hp is not None
    assert hp.auth.version == "v3"
    assert hp.auth.username == "admin"
    assert hp.auth.security_level == "auth_with_privacy"


def test_store_get_absent_rend_none(fichier_profils):
    """get() d'un nom inconnu : None, pas d'exception."""
    store = snmp_core.SnmpProfileStore(fichier_profils)
    assert store.get("inconnu") is None


def test_store_set_et_delete(fichier_profils):
    """set() écrit sur disque puis delete() retire et réécrit."""
    store = snmp_core.SnmpProfileStore(fichier_profils)
    profil = snmp_core.SnmpProfile(
        name="provisoire",
        vendor="cisco",
        auth=snmp_core.SnmpAuth(version="v2c", community="x"),
    )
    store.set("provisoire", profil)
    relu = snmp_core.SnmpProfileStore(fichier_profils)
    assert relu.get("provisoire") is not None

    store.delete("provisoire")
    relu2 = snmp_core.SnmpProfileStore(fichier_profils)
    assert relu2.get("provisoire") is None
    assert len(relu2.all()) == 2


def test_store_delete_absent_silencieux(fichier_profils):
    """delete() d'un nom inconnu : sans erreur, fichier inchangé."""
    store = snmp_core.SnmpProfileStore(fichier_profils)
    store.delete("n-existe-pas")
    assert len(store.all()) == 2


# ── Inventaire CSV ────────────────────────────────────────────────────────


def test_load_inventory(tmp_path):
    """Colonnes ip/type et variables supplémentaires reprises telles quelles."""
    csv = tmp_path / "inventaire.csv"
    csv.write_text(
        textwrap.dedent(
            """\
            ip, type, nom, vlan
            10.0.0.1, cisco, sw1, 10
            10.0.0.2, h3c_comware, sw2, 20
            """
        ),
        encoding="utf-8",
    )
    rows = snmp_core.load_inventory(csv)
    assert [r.ip for r in rows] == ["10.0.0.1", "10.0.0.2"]
    assert rows[0].profile_name == "cisco"
    assert rows[0].variables == {"nom": "sw1", "vlan": "10"}
    assert rows[0].line_no == 2


def test_load_inventory_lignes_incompletes(tmp_path):
    """Ligne sans ip ou sans type : ignorée, sans lever."""
    csv = tmp_path / "inventaire.csv"
    csv.write_text(
        textwrap.dedent(
            """\
            ip, type, nom
            10.0.0.1, cisco, sw1
            , cisco, sans-ip
            10.0.0.2, , sans-type
            """
        ),
        encoding="utf-8",
    )
    rows = snmp_core.load_inventory(csv)
    assert len(rows) == 1


def test_load_inventory_alias_host_profile(tmp_path):
    """En-têtes alias host/profile acceptés à la place d'ip/type."""
    csv = tmp_path / "inventaire.csv"
    csv.write_text(
        textwrap.dedent(
            """\
            host, profile, nom
            10.0.0.9, huawei_vrp, sw9
            """
        ),
        encoding="utf-8",
    )
    rows = snmp_core.load_inventory(csv)
    assert rows[0].ip == "10.0.0.9"
    assert rows[0].profile_name == "huawei_vrp"


# ── Gabarit ──────────────────────────────────────────────────────────────


def test_find_missing_variables():
    """Seules les variables du gabarit absentes du CSV sont signalées."""
    template = "hostname #nom#\nvlan #vlan#\n#description#"
    manquantes = snmp_core.find_missing_variables(template, {"nom", "vlan"})
    assert manquantes == {"description"}


def test_find_missing_variables_toutes_presentes():
    """Aucune variable manquante : ensemble vide."""
    template = "#nom# #vlan#"
    assert snmp_core.find_missing_variables(template, {"nom", "vlan"}) == set()


def test_render_template():
    """Substitution des variables présentes, les absentes restent telles quelles."""
    rendu = snmp_core.render_template("hostname #nom#\nvlan #vlan#", {"nom": "sw1"})
    assert rendu == "hostname sw1\nvlan #vlan#"


# ── Chemins d'erreur des drivers ─────────────────────────────────────────


class SessionWalkErreur:
    """Remplace ``ezsnmp.Session`` : le walk de la table lève un timeout."""

    def __init__(self, **kwargs):
        """Prépare l'état."""
        self.kwargs = kwargs

    def set_multiple(self, oid_values):
        """Avale les varbinds du SET initial."""
        pass

    def walk(self, oid):
        """Lève un timeout SNMP, comme un équipement injoignable."""
        raise snmp_core.EasySNMPTimeoutError("timeout de test")

    def get(self, oid):
        """Ne doit jamais être appelé dans ce scénario."""
        raise AssertionError("ne doit pas être appelée")


class SessionEtatTerminal:
    """Session dont la table de résultat renvoie un état d'échec.

    Le stockage est au niveau classe : le driver crée une nouvelle instance
    par requête (SET puis sondage), comme la FausseSession des tests de
    transferts.
    """

    _sets = []  # (oid, valeur, type) de chaque SET initial

    def __init__(self, **kwargs):
        """Prépare l'état."""
        self.kwargs = kwargs

    def set_multiple(self, oid_values):
        """Enregistre les varbinds du SET initial, au niveau classe."""
        type(self)._sets = list(oid_values)

    def walk(self, oid):
        """Renvoie la ligne de l'opération en cours, indexée sur le SET."""
        index = type(self)._sets[0][0].rsplit(".", 1)[1]  # BASE.2.{idx}
        return [type("Var", (), {"value": index, "oid_index": "1"})()]

    def get(self, oid):
        """Renvoie l'état terminal (5) ou la raison selon la colonne sondée."""
        col = oid.rsplit(".", 2)[-2]  # colonne sondée : 4=State, 7=raison
        if col == "4":
            return type("Var", (), {"value": "5"})()  # état terminal non-succès
        return type("Var", (), {"value": "raison simulée"})()


def _driver(classe, session_cls, monkeypatch):
    monkeypatch.setattr(snmp_core, "Session", session_cls)
    auth = snmp_core.SnmpAuth(version="v2c", community="private")
    return classe(auth=auth, poll_interval=0.0, poll_timeout=5.0)


def test_push_walk_en_echec(monkeypatch):
    """Walk de la table de résultat en timeout : échec signalé, pas de crash."""
    driver = _driver(snmp_core.H3cComwareDriver, SessionWalkErreur, monkeypatch)
    ok, detail = driver.push("10.0.0.1", "192.0.2.5", "a.cfg")
    assert not ok
    assert "walk OperateResultTable échoué" in detail


def test_push_etat_terminal_echec_avec_raison(monkeypatch):
    """État terminal non-succès : détail enrichi de la raison lue."""
    driver = _driver(snmp_core.H3cComwareDriver, SessionEtatTerminal, monkeypatch)
    ok, detail = driver.push("10.0.0.1", "192.0.2.5", "a.cfg")
    assert not ok
    assert "état terminal non-succès" in detail
    assert "raison simulée" in detail


def test_push_timeout_de_sondage(monkeypatch):
    """Aucune ligne dans la table : timeout après poll_timeout écoulé."""

    class SessionVide:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def set_multiple(self, oid_values):
            pass

        def walk(self, oid):
            return []

    monkeypatch.setattr(snmp_core, "Session", SessionVide)
    auth = snmp_core.SnmpAuth(version="v2c", community="private")
    driver = snmp_core.H3cComwareDriver(auth=auth, poll_interval=0.0, poll_timeout=0.05)
    ok, detail = driver.push("10.0.0.1", "192.0.2.5", "a.cfg")
    assert not ok
    assert "timeout en attente du résultat" in detail


def test_push_set_initial_echoue(monkeypatch):
    """SET initial qui lève : échec immédiat, message de l'exception repris."""

    class SessionSetErreur:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def set_multiple(self, oid_values):
            raise snmp_core.EasySNMPError("SET refusé par l'équipement")

    monkeypatch.setattr(snmp_core, "Session", SessionSetErreur)
    auth = snmp_core.SnmpAuth(version="v2c", community="private")
    driver = snmp_core.HuaweiVrpDriver(auth=auth, poll_interval=0.0, poll_timeout=5.0)
    ok, detail = driver.push("10.0.0.1", "192.0.2.5", "a.cfg")
    assert not ok
    assert "SET initial échoué" in detail


def test_push_ligne_illisible_ignoree(monkeypatch):
    """Ligne de table non numérique : ignorée, sondage continue jusqu'au timeout."""

    class SessionLigneBizarre:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def set_multiple(self, oid_values):
            pass

        def walk(self, oid):
            return [type("Var", (), {"value": "pas-un-nombre", "oid_index": "9"})()]

        def get(self, oid):
            raise AssertionError("ne doit pas être appelée")

    monkeypatch.setattr(snmp_core, "Session", SessionLigneBizarre)
    auth = snmp_core.SnmpAuth(version="v2c", community="private")
    driver = snmp_core.H3cComwareDriver(auth=auth, poll_interval=0.0, poll_timeout=0.05)
    ok, detail = driver.push("10.0.0.1", "192.0.2.5", "a.cfg")
    assert not ok
    assert "timeout en attente du résultat" in detail


def test_push_base_driver_non_implemente():
    """SnmpDriver.push de base : NotImplementedError, pour le socle abstrait."""
    auth = snmp_core.SnmpAuth(version="v2c", community="private")
    driver = snmp_core.SnmpDriver(auth=auth)
    with pytest.raises(NotImplementedError):
        driver.push("10.0.0.1", "192.0.2.5", "a.cfg")


def test_push_h3c_avec_identifiants(monkeypatch):
    """Push H3C avec identifiants : SET complété des colonnes username/password."""
    driver = _driver(snmp_core.H3cComwareDriver, SessionEtatTerminal, monkeypatch)
    ok, detail = driver.push("10.0.0.1", "192.0.2.5", "a.cfg", username="depot", password="secret")
    assert not ok
    oids = [oid for oid, _, _ in SessionEtatTerminal._sets]
    assert any(".6." in oid for oid in oids)  # colonne username
    assert any(".7." in oid for oid in oids)  # colonne password


def test_push_h3c_erreur_lecture_state(monkeypatch):
    """Lecture de State qui lève : échec signalé « erreur lecture State »."""

    class SessionStateErreur(SessionEtatTerminal):
        def get(self, oid):
            col = oid.rsplit(".", 2)[-2]
            if col == "4":
                raise snmp_core.EasySNMPError("State illisible")
            return type("Var", (), {"value": "raison"})()

    driver = _driver(snmp_core.H3cComwareDriver, SessionStateErreur, monkeypatch)
    ok, detail = driver.push("10.0.0.1", "192.0.2.5", "a.cfg")
    assert not ok
    assert "erreur lecture State" in detail


def test_push_h3c_raison_illisible(monkeypatch):
    """État d'échec dont la raison lève : détail sans raison, pas de crash."""

    class SessionRaisonErreur(SessionEtatTerminal):
        def get(self, oid):
            col = oid.rsplit(".", 2)[-2]
            if col == "4":
                return type("Var", (), {"value": "5"})()
            raise snmp_core.EasySNMPNoSuchObjectError("raison absente")

    driver = _driver(snmp_core.H3cComwareDriver, SessionRaisonErreur, monkeypatch)
    ok, detail = driver.push("10.0.0.1", "192.0.2.5", "a.cfg")
    assert not ok
    assert "état terminal non-succès" in detail
    assert "raison:" not in detail


def test_push_huawei_avec_identifiants_etat_echec(monkeypatch):
    """Push Huawei avec identifiants : sondage table, état non-succès + raison."""
    driver = _driver(snmp_core.HuaweiVrpDriver, SessionEtatTerminal, monkeypatch)
    ok, detail = driver.push("10.0.0.1", "192.0.2.5", "a.cfg", username="depot", password="secret")
    assert not ok
    oids = [oid for oid, _, _ in SessionEtatTerminal._sets]
    assert any(".6." in oid for oid in oids)
    assert any(".7." in oid for oid in oids)
    assert "état terminal non-succès" in detail
    assert "raison simulée" in detail


def test_push_huawei_walk_en_echec(monkeypatch):
    """Walk de la table Huawei en timeout : échec signalé."""
    driver = _driver(snmp_core.HuaweiVrpDriver, SessionWalkErreur, monkeypatch)
    ok, detail = driver.push("10.0.0.1", "192.0.2.5", "a.cfg")
    assert not ok
    assert "walk OperateResultTable échoué" in detail


def test_push_huawei_timeout_de_sondage(monkeypatch):
    """Table Huawei vide : timeout après poll_timeout."""

    class SessionVide:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def set_multiple(self, oid_values):
            pass

        def walk(self, oid):
            return []

    monkeypatch.setattr(snmp_core, "Session", SessionVide)
    auth = snmp_core.SnmpAuth(version="v2c", community="private")
    driver = snmp_core.HuaweiVrpDriver(auth=auth, poll_interval=0.0, poll_timeout=0.05)
    ok, detail = driver.push("10.0.0.1", "192.0.2.5", "a.cfg")
    assert not ok
    assert "timeout en attente du résultat" in detail


def test_push_cisco_succes(monkeypatch):
    """Push Cisco : SET initial puis ccCopyState=successful → succès."""

    class SessionCisco:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def set_multiple(self, oid_values):
            pass

        def get(self, oid):
            return type("Var", (), {"value": "3"})()  # successful

    monkeypatch.setattr(snmp_core, "Session", SessionCisco)
    auth = snmp_core.SnmpAuth(version="v2c", community="private")
    driver = snmp_core.CiscoConfigCopyDriver(auth=auth, poll_interval=0.0, poll_timeout=5.0)
    ok, detail = driver.push("10.0.0.1", "192.0.2.5", "a.cfg")
    assert ok
    assert detail == "succès"


def test_push_cisco_echec_cote_equipement(monkeypatch):
    """ccCopyState=failed : échec signalé, pas de crash."""

    class SessionCisco:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def set_multiple(self, oid_values):
            pass

        def get(self, oid):
            return type("Var", (), {"value": "4"})()  # failed

    monkeypatch.setattr(snmp_core, "Session", SessionCisco)
    auth = snmp_core.SnmpAuth(version="v2c", community="private")
    driver = snmp_core.CiscoConfigCopyDriver(auth=auth, poll_interval=0.0, poll_timeout=5.0)
    ok, detail = driver.push("10.0.0.1", "192.0.2.5", "a.cfg")
    assert not ok
    assert "la copie a échoué" in detail


def test_push_cisco_state_invalide(monkeypatch):
    """CcCopyState non numérique : échec « state invalide »."""

    class SessionCisco:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def set_multiple(self, oid_values):
            pass

        def get(self, oid):
            return type("Var", (), {"value": "pas-un-nombre"})()

    monkeypatch.setattr(snmp_core, "Session", SessionCisco)
    auth = snmp_core.SnmpAuth(version="v2c", community="private")
    driver = snmp_core.CiscoConfigCopyDriver(auth=auth, poll_interval=0.0, poll_timeout=5.0)
    ok, detail = driver.push("10.0.0.1", "192.0.2.5", "a.cfg")
    assert not ok
    assert "state invalide" in detail


def test_push_cisco_etat_inconnu(monkeypatch):
    """CcCopyState hors énumération : échec « état inconnu »."""

    class SessionCisco:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def set_multiple(self, oid_values):
            pass

        def get(self, oid):
            return type("Var", (), {"value": "9"})()

    monkeypatch.setattr(snmp_core, "Session", SessionCisco)
    auth = snmp_core.SnmpAuth(version="v2c", community="private")
    driver = snmp_core.CiscoConfigCopyDriver(auth=auth, poll_interval=0.0, poll_timeout=5.0)
    ok, detail = driver.push("10.0.0.1", "192.0.2.5", "a.cfg")
    assert not ok
    assert "état inconnu: 9" in detail


def test_push_cisco_set_initial_echoue(monkeypatch):
    """SET initial Cisco qui lève : échec immédiat."""

    class SessionSetErreur:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def set_multiple(self, oid_values):
            raise snmp_core.EasySNMPError("SET refusé")

    monkeypatch.setattr(snmp_core, "Session", SessionSetErreur)
    auth = snmp_core.SnmpAuth(version="v2c", community="private")
    driver = snmp_core.CiscoConfigCopyDriver(auth=auth, poll_interval=0.0, poll_timeout=5.0)
    ok, detail = driver.push("10.0.0.1", "192.0.2.5", "a.cfg")
    assert not ok
    assert "SET initial échoué" in detail


def test_push_cisco_timeout_de_sondage(monkeypatch):
    """Lecture de ccCopyState en erreur : sondage jusqu'au timeout."""

    class SessionCiscoMuet:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def set_multiple(self, oid_values):
            pass

        def get(self, oid):
            raise snmp_core.EasySNMPTimeoutError("muet")

    monkeypatch.setattr(snmp_core, "Session", SessionCiscoMuet)
    auth = snmp_core.SnmpAuth(version="v2c", community="private")
    driver = snmp_core.CiscoConfigCopyDriver(auth=auth, poll_interval=0.0, poll_timeout=0.05)
    ok, detail = driver.push("10.0.0.1", "192.0.2.5", "a.cfg")
    assert not ok
    assert "timeout en attente de ccCopyState" in detail


# ── Orchestration : run_bulk_push, push_to_device, résumé ────────────────


def _ligne(ip: str, profil: str = "h3c_comware") -> snmp_core.InventoryRow:
    return snmp_core.InventoryRow(line_no=1, ip=ip, profile_name=profil)


def _profil(vendor: str = "h3c_comware") -> snmp_core.SnmpProfile:
    return snmp_core.SnmpProfile(
        name="test",
        vendor=vendor,
        auth=snmp_core.SnmpAuth(version="v2c", community="private"),
        poll_interval=0.0,
        poll_timeout=0.05,
    )


def test_run_bulk_push_profil_inexistant(tmp_path):
    """Ligne dont le profil manque : rejetée, callbacks appelés, résultat écrit."""
    demarres, finis = [], []

    def on_row_start(row):
        demarres.append(row.ip)

    def on_row_done(result):
        finis.append(result)

    rows = [_ligne("10.0.0.1", "profil-absent")]
    results = snmp_core.run_bulk_push(
        rows,
        {},
        "#nom#",
        tmp_path,
        on_row_start=on_row_start,
        on_row_done=on_row_done,
    )
    assert len(results) == 1
    assert results[0].status == snmp_core.STATUS_REJECTED
    assert "Profil 'profil-absent' inexistant" in results[0].error
    assert demarres == []  # rejet avant le démarrage
    assert len(finis) == 1


def test_push_to_device_transfer_error(tmp_path, monkeypatch):
    """driver.push qui lève TransferError : statut failed, journal écrit."""

    def push_ko(self, *args, **kwargs):
        raise snmp_core.TransferError("dépôt impossible")

    monkeypatch.setattr(snmp_core.H3cComwareDriver, "push", push_ko)
    result = snmp_core.push_to_device(_ligne("10.0.0.1"), _profil(), "config", tmp_path)
    assert result.status == snmp_core.STATUS_FAILED
    assert "Dépôt du fichier impossible" in result.error
    journal = tmp_path / "1_10.0.0.1.log"
    assert journal.exists()
    assert journal.read_text(encoding="utf-8").startswith("failed:")


def test_push_to_device_exception_generique(tmp_path, monkeypatch):
    """driver.push qui lève une exception imprévue : failed, journal écrit."""

    def push_ko(self, *args, **kwargs):
        raise ValueError("bug simulé")

    monkeypatch.setattr(snmp_core.H3cComwareDriver, "push", push_ko)
    result = snmp_core.push_to_device(_ligne("10.0.0.1"), _profil(), "config", tmp_path)
    assert result.status == snmp_core.STATUS_FAILED
    assert "bug simulé" in result.error


def test_push_to_device_constructeur_inconnu(tmp_path):
    """Profil au vendor inconnu : rejeté sans appeler de driver."""
    result = snmp_core.push_to_device(
        _ligne("10.0.0.1"), _profil(vendor="vendor_factice"), "config", tmp_path
    )
    assert result.status == snmp_core.STATUS_REJECTED
    assert "Constructeur inconnu" in result.error


def test_summarize_results():
    """Résumé des compteurs sur un mélange de statuts."""
    rows = [_ligne("10.0.0.1")]
    results = [
        snmp_core.PushResult(row=rows[0], status=snmp_core.STATUS_OK),
        snmp_core.PushResult(row=rows[0], status=snmp_core.STATUS_OK),
        snmp_core.PushResult(row=rows[0], status=snmp_core.STATUS_FAILED, error="x"),
        snmp_core.PushResult(row=rows[0], status=snmp_core.STATUS_REJECTED, error="y"),
    ]
    resume = snmp_core.summarize_results(results)
    assert resume["total"] == 4
    assert resume["ok"] == 2
    assert resume["failed"] == 1
    assert resume["rejected"] == 1


def test_store_charge_fichier_sans_profils(tmp_path):
    """INI sans section profile: : aucun profil, pas d'erreur."""
    chemin = tmp_path / "vide.ini"
    chemin.write_text("[autre]\ncle = valeur\n", encoding="utf-8")
    store = snmp_core.SnmpProfileStore(chemin)
    assert store.all() == []


def test_push_h3c_set_initial_echoue(monkeypatch):
    """SET initial H3C qui lève : échec immédiat avec le message."""

    class SessionSetErreur:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def set_multiple(self, oid_values):
            raise snmp_core.EasySNMPError("SET refusé par l'équipement")

    monkeypatch.setattr(snmp_core, "Session", SessionSetErreur)
    auth = snmp_core.SnmpAuth(version="v2c", community="private")
    driver = snmp_core.H3cComwareDriver(auth=auth, poll_interval=0.0, poll_timeout=5.0)
    ok, detail = driver.push("10.0.0.1", "192.0.2.5", "a.cfg")
    assert not ok
    assert "SET initial échoué" in detail


def test_push_cisco_waiting_puis_succes(monkeypatch):
    """CcCopyState waiting puis successful : attente puis succès."""
    appels = {"n": 0}

    class SessionCisco:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def set_multiple(self, oid_values):
            pass

        def get(self, oid):
            appels["n"] += 1
            valeur = "1" if appels["n"] == 1 else "3"  # waiting puis successful
            return type("Var", (), {"value": valeur})()

    monkeypatch.setattr(snmp_core, "Session", SessionCisco)
    auth = snmp_core.SnmpAuth(version="v2c", community="private")
    driver = snmp_core.CiscoConfigCopyDriver(auth=auth, poll_interval=0.0, poll_timeout=5.0)
    ok, detail = driver.push("10.0.0.1", "192.0.2.5", "a.cfg")
    assert ok
    assert detail == "succès"
    assert appels["n"] >= 2


def test_push_huawei_ligne_illisible_ignoree(monkeypatch):
    """Ligne de table Huawei non numérique : ignorée, timeout final."""

    class SessionLigneBizarre:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def set_multiple(self, oid_values):
            pass

        def walk(self, oid):
            return [type("Var", (), {"value": "pas-un-nombre", "oid_index": "9"})()]

        def get(self, oid):
            raise AssertionError("ne doit pas être appelée")

    monkeypatch.setattr(snmp_core, "Session", SessionLigneBizarre)
    auth = snmp_core.SnmpAuth(version="v2c", community="private")
    driver = snmp_core.HuaweiVrpDriver(auth=auth, poll_interval=0.0, poll_timeout=0.05)
    ok, detail = driver.push("10.0.0.1", "192.0.2.5", "a.cfg")
    assert not ok
    assert "timeout en attente du résultat" in detail


def test_push_huawei_raison_illisible(monkeypatch):
    """État d'échec Huawei dont la raison lève : détail sans raison."""

    class SessionRaisonErreur(SessionEtatTerminal):
        def get(self, oid):
            col = oid.rsplit(".", 2)[-2]
            if col == "4":
                return type("Var", (), {"value": "5"})()
            raise snmp_core.EasySNMPNoSuchObjectError("raison absente")

    driver = _driver(snmp_core.HuaweiVrpDriver, SessionRaisonErreur, monkeypatch)
    ok, detail = driver.push("10.0.0.1", "192.0.2.5", "a.cfg")
    assert not ok
    assert "état terminal non-succès" in detail
    assert "raison:" not in detail


def test_push_huawei_erreur_lecture_state(monkeypatch):
    """Lecture de State Huawei qui lève : « erreur lecture State »."""

    class SessionStateErreur(SessionEtatTerminal):
        def get(self, oid):
            col = oid.rsplit(".", 2)[-2]
            if col == "4":
                raise snmp_core.EasySNMPError("State illisible")
            return type("Var", (), {"value": "raison"})()

    driver = _driver(snmp_core.HuaweiVrpDriver, SessionStateErreur, monkeypatch)
    ok, detail = driver.push("10.0.0.1", "192.0.2.5", "a.cfg")
    assert not ok
    assert "erreur lecture State" in detail


def test_run_bulk_push_nominal(monkeypatch, tmp_path):
    """Chemin nominal : profil connu, push OK, callbacks démarrage/fin."""
    demarres, finis = [], []

    def push_ok(self, *args, **kwargs):
        return True, "succès"

    monkeypatch.setattr(snmp_core.H3cComwareDriver, "push", push_ok)
    monkeypatch.setattr(snmp_core, "Session", SessionEtatTerminal)

    def on_row_start(row):
        demarres.append(row.ip)

    def on_row_done(result):
        finis.append(result)

    rows = [_ligne("10.0.0.1"), _ligne("10.0.0.2")]
    results = snmp_core.run_bulk_push(
        rows,
        {"h3c_comware": _profil()},
        "hostname #nom#",
        tmp_path,
        on_row_start=on_row_start,
        on_row_done=on_row_done,
    )
    assert len(results) == 2
    assert all(r.status == snmp_core.STATUS_OK for r in results)
    assert sorted(demarres) == ["10.0.0.1", "10.0.0.2"]
    assert len(finis) == 2
