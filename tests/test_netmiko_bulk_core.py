# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests unitaires pour netmiko_bulk_core.py (cœur métier sans GTK).

Le module n'importe ni GTK ni netmiko au niveau module (netmiko est importé
paresseusement dans ``push_to_device``) : la quasi-totalité se teste sans
aucune dépendance. Seul ``push_to_device`` a besoin d'un stub de ``netmiko``
installé dans ``sys.modules`` avant l'appel (aucune connexion réelle).

Couvre : le catalogue ``VENDOR_DEVICE_TYPES``, les profils
(``DeviceProfile``/``ProfileStore`` avec aller-retour .ini), le chargement
d'inventaire CSV (délimiteurs, alias de colonnes, erreurs), le rendu de
gabarits ``#var#``, la détection de rejets dans les sorties équipement,
``push_to_device`` (succès, rejet, authentification, délai, erreur générique,
sauvegarde), ``run_bulk_push`` (journal, manifeste, annulation, callbacks)
et ``summarize_results``.
"""

import configparser
import json
import os
import sys
import threading
import types
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import netmiko_bulk_core as nb  # noqa: E402

# ──────────────────────────────────────────────────────────────────────────
# Stub netmiko (aucune connexion réelle)
# ──────────────────────────────────────────────────────────────────────────


class _FakeNetmikoConnection:
    """Doublure de ``netmiko.ConnectHandler`` pilotée par les tests."""

    calls: dict[str, list] = {}

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.enable_called = False
        self.disconnected = False
        self.config_output = "interface GigabitEthernet1/0/1\ndescription test"
        self.save_output = "Saved"
        self.fail_on_disconnect = False
        _FakeNetmikoConnection.calls.setdefault("instances", []).append(self)

    def enable(self):
        self.enable_called = True

    def send_config_set(self, lines):
        _FakeNetmikoConnection.calls.setdefault("send_config_set", []).append(lines)
        return self.config_output

    def send_command_timing(self, command):
        _FakeNetmikoConnection.calls.setdefault("send_command_timing", []).append(command)
        return self.save_output

    def save_config(self):
        _FakeNetmikoConnection.calls.setdefault("save_config", []).append(True)
        return self.save_output

    def disconnect(self):
        self.disconnected = True
        if self.fail_on_disconnect:
            raise RuntimeError("déconnexion impossible")


def _install_netmiko_stub():
    """Installe un faux paquet ``netmiko`` dans ``sys.modules``.

    ``push_to_device`` fait ``from netmiko import ConnectHandler`` au moment
    de l'appel : le stub permet de piloter la connexion (sortie, exceptions)
    sans aucune dépendance réelle.
    """
    netmiko = types.ModuleType("netmiko")
    netmiko.ConnectHandler = _FakeNetmikoConnection

    exceptions = types.ModuleType("netmiko.exceptions")

    class NetmikoAuthenticationException(Exception):
        pass

    class NetmikoTimeoutException(Exception):
        pass

    exceptions.NetmikoAuthenticationException = NetmikoAuthenticationException
    exceptions.NetmikoTimeoutException = NetmikoTimeoutException
    netmiko.exceptions = exceptions
    sys.modules["netmiko"] = netmiko
    sys.modules["netmiko.exceptions"] = exceptions
    return netmiko, exceptions, NetmikoAuthenticationException, NetmikoTimeoutException


_netmiko, _exceptions, _AuthExc, _TimeoutExc = _install_netmiko_stub()


def _row(ip="10.0.0.1", profile_name="comware", variables=None, line_no=2):
    return nb.InventoryRow(
        ip=ip,
        profile_name=profile_name,
        variables=dict(variables or {}),
        line_no=line_no,
    )


def _profile(**kwargs):
    params = dict(
        name="comware",
        device_type="hp_comware",
        vendor="HPE / Aruba",
        username="admin",
        password="secret",
        port=22,
    )
    params.update(kwargs)
    return nb.DeviceProfile(**params)


class TestCatalogue(unittest.TestCase):
    """Le catalogue constructeurs doit rester exploitable par l'UI."""

    def test_iter_all_device_types_structure(self):
        """Chaque entrée est un triplet (constructeur, device_type, libellé)."""
        entries = nb.iter_all_device_types()
        self.assertGreater(len(entries), 50)
        for vendor, device_type, label in entries:
            self.assertTrue(vendor)
            self.assertTrue(device_type)
            self.assertTrue(label)

    def test_iter_all_device_types_coherent_avec_le_dict(self):
        """L'aplatissement reprend exactement VENDOR_DEVICE_TYPES."""
        attendu = [
            (vendor, dt, label)
            for vendor, entries in nb.VENDOR_DEVICE_TYPES.items()
            for dt, label in entries
        ]
        self.assertEqual(nb.iter_all_device_types(), attendu)

    def test_vendeurs_connus_presents(self):
        """Les grands constructeurs historiques du dépôt restent dans le catalogue."""
        self.assertIn("Cisco", nb.VENDOR_DEVICE_TYPES)
        self.assertIn("Huawei", nb.VENDOR_DEVICE_TYPES)
        self.assertIn("Serveurs / générique", nb.VENDOR_DEVICE_TYPES)


class TestDeviceProfile(unittest.TestCase):
    """``to_netmiko_kwargs`` bâtit les arguments de ConnectHandler."""

    def test_kwargs_sans_secret(self):
        """Sans secret, le dictionnaire n'expose pas la clé ``secret``."""
        kwargs = _profile(secret="").to_netmiko_kwargs("192.168.1.1")
        self.assertEqual(kwargs["device_type"], "hp_comware")
        self.assertEqual(kwargs["host"], "192.168.1.1")
        self.assertEqual(kwargs["username"], "admin")
        self.assertEqual(kwargs["password"], "secret")
        self.assertEqual(kwargs["port"], 22)
        self.assertEqual(kwargs["timeout"], 10)
        self.assertNotIn("secret", kwargs)

    def test_kwargs_avec_secret(self):
        """Avec un secret (enable), il est transmis à netmiko."""
        kwargs = _profile(secret="enable_me").to_netmiko_kwargs("h1")
        self.assertEqual(kwargs["secret"], "enable_me")

    def test_kwargs_port_et_timeout_personnalises(self):
        """Port et timeout du profil passent tels quels."""
        kwargs = _profile(port=2222, timeout=30).to_netmiko_kwargs("h1")
        self.assertEqual(kwargs["port"], 2222)
        self.assertEqual(kwargs["timeout"], 30)


class TestProfileStore(unittest.TestCase):
    """Magasin de profils : chargement, modification, sauvegarde .ini."""

    def test_init_fichier_absent_vide(self):
        """Sans fichier, le magasin démarre vide (pas d'exception)."""
        store = nb.ProfileStore("/tmp/inexistant-profiles.ini")
        self.assertEqual(store.names(), [])
        self.assertIsNone(store.get("comware"))

    def test_roundtrip_save_reload(self):
        """save() puis reload() restitue fidèlement chaque profil."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "profiles.ini"
            store = nb.ProfileStore(path)
            store.set(_profile(name="comware", secret="s3"))
            store.set(_profile(name="cisco", device_type="cisco_ios", secret=""))
            store.save()

            relu = nb.ProfileStore(path)  # __init__ recharge si le fichier existe
            self.assertEqual(relu.names(), ["cisco", "comware"])
            comware = relu.get("comware")
            self.assertEqual(comware.device_type, "hp_comware")
            self.assertEqual(comware.secret, "s3")
            self.assertEqual(comware.port, 22)
            self.assertEqual(comware.timeout, 10)
            cisco = relu.get("cisco")
            self.assertEqual(cisco.vendor, "HPE / Aruba")

    def test_save_permissions_0600(self):
        """Le fichier de profils est écrit avec des permissions restreintes."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "profiles.ini"
            store = nb.ProfileStore(path)
            store.set(_profile())
            store.save()
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_save_chmod_impossible_journalise(self):
        """Un chmod en échec (OSError) n'empêche pas la sauvegarde."""
        import tempfile
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "profiles.ini"
            store = nb.ProfileStore(path)
            store.set(_profile())
            with mock.patch.object(Path, "chmod", side_effect=OSError("lecture seule")):
                store.save()  # ne doit pas lever
            self.assertTrue(path.exists())

    def test_set_remplace_le_profil(self):
        """Un second set() du même nom remplace la première version."""
        store = nb.ProfileStore("/tmp/inexistant-profiles.ini")
        store.set(_profile(username="ancien"))
        store.set(_profile(username="nouveau"))
        self.assertEqual(store.get("comware").username, "nouveau")

    def test_delete_silencieux_si_absent(self):
        """delete() d'un profil inconnu ne lève pas (pop(..., None))."""
        store = nb.ProfileStore("/tmp/inexistant-profiles.ini")
        store.delete("jamais-cree")  # ne doit pas lever
        self.assertEqual(store.names(), [])

    def test_delete_nom_connu(self):
        """delete() retire bien le profil visé."""
        store = nb.ProfileStore("/tmp/inexistant-profiles.ini")
        store.set(_profile())
        store.delete("comware")
        self.assertIsNone(store.get("comware"))

    def test_all_trie_par_nom(self):
        """all() suit l'ordre alphabétique de names()."""
        store = nb.ProfileStore("/tmp/inexistant-profiles.ini")
        store.set(_profile(name="zz"))
        store.set(_profile(name="aa"))
        self.assertEqual([p.name for p in store.all()], ["aa", "zz"])

    def test_reload_ecrase_les_modifications_non_sauvegardees(self):
        """reload() repart du fichier : un set() non sauvegardé est perdu."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "profiles.ini"
            store = nb.ProfileStore(path)
            store.set(_profile(username="sauvegarde"))
            store.save()
            store.set(_profile(username="non-sauvegarde"))
            store.reload()
            self.assertEqual(store.get("comware").username, "sauvegarde")

    def test_write_template_cree_le_fichier(self):
        """write_template() écrit un exemple de profils si le fichier manque."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "profiles.ini"
            nb.ProfileStore.write_template(path)
            self.assertTrue(path.exists())
            cp = configparser.ConfigParser()
            cp.read(path)
            self.assertIn("comware", cp.sections())
            self.assertIn("cisco", cp.sections())
            self.assertEqual(cp["cisco"]["device_type"], "cisco_ios")

    def test_write_template_ne_reecrit_pas_un_fichier_existant(self):
        """Si le fichier existe déjà, write_template() ne le touche pas."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "profiles.ini"
            path.write_text("[perso]\ndevice_type = linux\n", encoding="utf-8")
            nb.ProfileStore.write_template(path)
            cp = configparser.ConfigParser()
            cp.read(path)
            self.assertIn("perso", cp.sections())
            self.assertNotIn("comware", cp.sections())


class TestLoadInventory(unittest.TestCase):
    """Chargement d'un inventaire CSV : délimiteurs, alias, erreurs."""

    def _write(self, tmp, content, name="inv.csv"):
        path = Path(tmp) / name
        path.write_text(content, encoding="utf-8")
        return path

    def test_csv_point_virgule(self):
        """Délimiteur par défaut : point-virgule."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, "ip;type;nom\n10.0.0.1;comware;sw1\n")
            rows = nb.load_inventory(path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].ip, "10.0.0.1")
            self.assertEqual(rows[0].profile_name, "comware")
            self.assertEqual(rows[0].variables, {"nom": "sw1"})
            self.assertEqual(rows[0].line_no, 2)

    def test_csv_virgule_et_tabulation(self):
        """Le délimiteur est reniflé sur la première ligne (virgule, tabulation)."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, "ip,type\n10.0.0.1,comware\n")
            rows = nb.load_inventory(path)
            self.assertEqual(rows[0].profile_name, "comware")

            path = self._write(tmp, "ip\ttype\n10.0.0.2\tcisco\n", name="inv-tsv.csv")
            rows = nb.load_inventory(path)
            self.assertEqual(rows[0].profile_name, "cisco")

    def test_csv_sans_delimiteur_connu(self):
        """Première ligne sans délimiteur connu : « ; » par défaut, en-tête
        non découpé donc colonne ip introuvable — ValueError explicite.
        """
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, "ip type\n10.0.0.7 comware\n")
            with self.assertRaises(ValueError) as ctx:
                nb.load_inventory(path)
            self.assertIn("colonne 'ip'", str(ctx.exception))

    def test_bom_utf8(self):
        """Un BOM UTF-8 (export Excel) ne casse pas la lecture."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, "\ufeffip;type\n10.0.0.1;comware\n")
            rows = nb.load_inventory(path)
            self.assertEqual(rows[0].ip, "10.0.0.1")

    def test_alias_host_et_profile(self):
        """Colonnes alias : host pour l'IP, profile pour le profil."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, "host;profile\n10.0.0.3;cisco\n")
            rows = nb.load_inventory(path)
            self.assertEqual(rows[0].ip, "10.0.0.3")
            self.assertEqual(rows[0].profile_name, "cisco")

    def test_en_tete_insensible_a_la_casse_avec_espaces(self):
        """« IP » et « Type » (espaces, casse) sont reconnus."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, "IP;Type\n10.0.0.4;comware\n")
            rows = nb.load_inventory(path)
            self.assertEqual(rows[0].ip, "10.0.0.4")

    def test_lignes_ip_vide_ignorees(self):
        """Une ligne sans IP est silencieusement ignorée."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, "ip;type\n;comware\n10.0.0.5;comware\n")
            rows = nb.load_inventory(path)
            self.assertEqual(len(rows), 1)

    def test_valeurs_decapees(self):
        """Les valeurs et noms de colonnes sont décapés."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, "ip;type;nom\n 10.0.0.6 ; comware ; sw  \n")
            rows = nb.load_inventory(path)
            self.assertEqual(rows[0].ip, "10.0.0.6")
            self.assertEqual(rows[0].profile_name, "comware")
            self.assertEqual(rows[0].variables, {"nom": "sw"})

    def test_fichier_vide_leve_valueerror(self):
        """Un inventaire vide est refusé explicitement."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, "")
            with self.assertRaises(ValueError):
                nb.load_inventory(path)

    def test_colonne_ip_manquante(self):
        """Sans colonne ip/host, ValueError explicite."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, "type;nom\ncomware;sw\n")
            with self.assertRaises(ValueError):
                nb.load_inventory(path)

    def test_colonne_type_manquante(self):
        """Sans colonne type/profile, ValueError explicite."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, "ip;nom\n10.0.0.1;sw\n")
            with self.assertRaises(ValueError):
                nb.load_inventory(path)

    def test_contexte_de_ligne(self):
        """render_context() complète ip/host depuis la ligne si absents."""
        row = _row(variables={"nom": "sw1", "ip": "10.9.9.9"})
        ctx = row.render_context()
        self.assertEqual(ctx["ip"], "10.9.9.9")  # la colonne CSV gagne
        self.assertEqual(ctx["host"], "10.0.0.1")
        self.assertEqual(ctx["nom"], "sw1")

    def test_contexte_defaut_host(self):
        """Sans colonnes ip/host, le contexte les positionne à row.ip."""
        row = _row()
        ctx = row.render_context()
        self.assertEqual(ctx["ip"], "10.0.0.1")
        self.assertEqual(ctx["host"], "10.0.0.1")


class TestGabarits(unittest.TestCase):
    """Substitution #var# et détection de variables manquantes."""

    def test_render_substitue(self):
        """Les placeholders présents au contexte sont remplacés."""
        rendered = nb.render_template("vlan #vlan#\ndesc #nom#", {"vlan": "10", "nom": "sw"})
        self.assertEqual(rendered, "vlan 10\ndesc sw")

    def test_render_laisse_les_inconnus(self):
        """Un placeholder sans valeur reste tel quel (pas d'exception)."""
        rendered = nb.render_template("vlan #vlan#", {})
        self.assertEqual(rendered, "vlan #vlan#")

    def test_render_occurrences_multiples(self):
        """Chaque occurrence est substituée."""
        rendered = nb.render_template("#a#/#a#", {"a": "x"})
        self.assertEqual(rendered, "x/x")

    def test_render_var_avec_chiffres(self):
        """Les noms de variables acceptent chiffres et tiret bas."""
        rendered = nb.render_template("#host_2#", {"host_2": "ok"})
        self.assertEqual(rendered, "ok")

    def test_find_missing_variables_tries(self):
        """Les manquantes sont listées, triées alphabétiquement."""
        missing = nb.find_missing_variables("#b# #a# #c#", {"c": "1"})
        self.assertEqual(missing, ["a", "b"])

    def test_find_missing_variables_aucune(self):
        """Tout résolvable : liste vide."""
        self.assertEqual(nb.find_missing_variables("#a#", {"a": "1"}), [])

    def test_find_missing_variables_texte_sans_variable(self):
        """Un gabarit sans placeholder ne signale rien."""
        self.assertEqual(nb.find_missing_variables("no-op", {}), [])


class TestScanErreurs(unittest.TestCase):
    """Détection générique des rejets dans les sorties équipement."""

    def test_sortie_propre(self):
        """Aucun marqueur : liste vide."""
        self.assertEqual(nb.scan_output_for_errors("interface Gi1/0/1\ndescription sw"), [])

    def test_marqueurs_reconnus(self):
        """Les marqueurs courants (casse indifférente) sont détectés."""
        output = "% Invalid input\nUnrecognized command at '^' marker\nok line"
        hits = nb.scan_output_for_errors(output)
        self.assertEqual(len(hits), 2)

    def test_une_entree_par_ligne(self):
        """Une ligne qui matche plusieurs motifs n'est comptée qu'une fois."""
        hits = nb.scan_output_for_errors("% Invalid input, syntax error")
        self.assertEqual(len(hits), 1)

    def test_marqueurs_minuscules(self):
        """Les motifs sont insensibles à la casse."""
        self.assertEqual(nb.scan_output_for_errors("permission denied"), ["permission denied"])

    def test_marqueurs_rejet_et_erreur_generale(self):
        """Command rejected / error: sont détectés."""
        hits = nb.scan_output_for_errors("command rejected\nerror: bad\nok")
        self.assertEqual(hits, ["command rejected", "error: bad"])


class TestPushResult(unittest.TestCase):
    """PushResult et sa vue booléenne ``ok``."""

    def test_ok_si_status_ok(self):
        """Ok est vrai seulement pour STATUS_OK."""
        self.assertTrue(nb.PushResult(row=_row(), status=nb.STATUS_OK).ok)
        self.assertFalse(nb.PushResult(row=_row(), status=nb.STATUS_REJECTED).ok)
        self.assertFalse(nb.PushResult(row=_row(), status=nb.STATUS_FAILED).ok)
        self.assertFalse(nb.PushResult(row=_row(), status=nb.STATUS_SKIPPED).ok)

    def test_champs_par_defaut(self):
        """Les champs optionnels ont des valeurs neutres."""
        result = nb.PushResult(row=_row(), status=nb.STATUS_OK)
        self.assertEqual(result.output, "")
        self.assertEqual(result.error, "")
        self.assertEqual(result.config_errors, [])
        self.assertEqual(result.log_path, "")
        self.assertEqual(result.duration_s, 0.0)


class TestPushToDevice(unittest.TestCase):
    """push_to_device avec le stub netmiko (aucune connexion réelle)."""

    def setUp(self):
        """Réinitialise le compteur d'appels du stub entre chaque test."""
        _FakeNetmikoConnection.calls = {}

    def test_succes_simple(self):
        """Connexion + envoi + sortie propre : STATUS_OK."""
        result = nb.push_to_device(_row(), _profile(), "interface Gi1/0/1")
        self.assertEqual(result.status, nb.STATUS_OK)
        self.assertTrue(result.ok)
        self.assertEqual(result.config_errors, [])
        self.assertGreaterEqual(result.duration_s, 0.0)
        instance = _FakeNetmikoConnection.calls["instances"][-1]
        self.assertTrue(instance.disconnected)

    def test_kwargs_passes_a_connecthandler(self):
        """Les paramètres de connexion viennent du profil et de la ligne."""
        nb.push_to_device(_row(ip="10.1.1.1"), _profile(port=2222), "cmd")
        instance = _FakeNetmikoConnection.calls["instances"][-1]
        self.assertEqual(instance.kwargs["host"], "10.1.1.1")
        self.assertEqual(instance.kwargs["port"], 2222)

    def test_secret_declenche_enable(self):
        """Un profil avec secret appelle conn.enable()."""
        nb.push_to_device(_row(), _profile(secret="s"), "cmd")
        instance = _FakeNetmikoConnection.calls["instances"][-1]
        self.assertTrue(instance.enable_called)

    def test_gabarit_vide_refuse(self):
        """Un gabarit rendu vide n'ouvre aucune connexion."""
        result = nb.push_to_device(_row(), _profile(), "  \n\n")
        self.assertEqual(result.status, nb.STATUS_FAILED)
        self.assertIn("vide", result.error)

    def test_rejet_dans_la_sortie(self):
        """Un marqueur d'erreur dans la sortie passe le statut en rejected."""

        class _RejectedConn(_FakeNetmikoConnection):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                self.config_output = "% Invalid input"

        sys.modules["netmiko"].ConnectHandler = _RejectedConn
        try:
            result = nb.push_to_device(_row(), _profile(), "bad cmd")
        finally:
            sys.modules["netmiko"].ConnectHandler = _FakeNetmikoConnection
        self.assertEqual(result.status, nb.STATUS_REJECTED)
        self.assertEqual(result.config_errors, ["% Invalid input"])

    def test_authentification_refusee(self):
        """NetmikoAuthenticationException : échec explicite, disconnect tenté."""

        def _raise_auth(**kwargs):
            raise _AuthExc("bad password")

        sys.modules["netmiko"].ConnectHandler = _raise_auth
        try:
            result = nb.push_to_device(_row(), _profile(), "cmd")
        finally:
            sys.modules["netmiko"].ConnectHandler = _FakeNetmikoConnection
        self.assertEqual(result.status, nb.STATUS_FAILED)
        self.assertIn("Authentification refusée", result.error)

    def test_delai_depasse(self):
        """NetmikoTimeoutException : échec « injoignable »."""

        def _raise_timeout(**kwargs):
            raise _TimeoutExc("host unreachable")

        sys.modules["netmiko"].ConnectHandler = _raise_timeout
        try:
            result = nb.push_to_device(_row(), _profile(), "cmd")
        finally:
            sys.modules["netmiko"].ConnectHandler = _FakeNetmikoConnection
        self.assertEqual(result.status, nb.STATUS_FAILED)
        self.assertIn("Délai dépassé / injoignable", result.error)

    def test_exception_generique(self):
        """Toute autre exception devient un échec par hôte, pas un crash."""

        def _raise_generic(**kwargs):
            raise RuntimeError("boom équipement")

        sys.modules["netmiko"].ConnectHandler = _raise_generic
        try:
            result = nb.push_to_device(_row(), _profile(), "cmd")
        finally:
            sys.modules["netmiko"].ConnectHandler = _FakeNetmikoConnection
        self.assertEqual(result.status, nb.STATUS_FAILED)
        self.assertIn("boom équipement", result.error)

    def test_save_avec_save_command(self):
        """save=True + save_command : send_command_timing utilisé, sortie concaténée."""
        result = nb.push_to_device(_row(), _profile(save_command="save force"), "cmd", save=True)
        self.assertEqual(result.status, nb.STATUS_OK)
        self.assertIn("save force", _FakeNetmikoConnection.calls["send_command_timing"])
        self.assertIn("Saved", result.output)

    def test_save_sans_save_command(self):
        """save=True sans save_command : save_config() natif utilisé."""
        result = nb.push_to_device(_row(), _profile(save_command=""), "cmd", save=True)
        self.assertEqual(result.status, nb.STATUS_OK)
        self.assertIn(True, _FakeNetmikoConnection.calls["save_config"])
        self.assertIn("Saved", result.output)

    def test_disconnect_en_erreur_non_fatale(self):
        """Une disconnect() qui lève n'occulte pas le résultat déjà établi."""

        class _BadDisconnectConn(_FakeNetmikoConnection):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                self.fail_on_disconnect = True

        sys.modules["netmiko"].ConnectHandler = _BadDisconnectConn
        try:
            result = nb.push_to_device(_row(), _profile(), "cmd")
        finally:
            sys.modules["netmiko"].ConnectHandler = _FakeNetmikoConnection
        self.assertEqual(result.status, nb.STATUS_OK)


class TestRunBulkPush(unittest.TestCase):
    """run_bulk_push : orchestration, journal, manifeste, annulation."""

    def _store(self, tmp):
        path = Path(tmp) / "profiles.ini"
        store = nb.ProfileStore(path)
        store.set(_profile())
        return store

    def test_profil_inconnu_echec_par_hote(self):
        """Une ligne au profil inconnu échoue sans interrompre les autres."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            rows = [_row(profile_name="inconnu"), _row(ip="10.0.0.2")]
            results = nb.run_bulk_push(rows, store, "desc #ip#", log_dir=Path(tmp) / "logs")
            self.assertEqual(len(results), 2)
            by_ip = {r.row.ip: r for r in results}
            self.assertEqual(by_ip["10.0.0.1"].status, nb.STATUS_FAILED)
            self.assertIn("Profil inconnu", by_ip["10.0.0.1"].error)
            self.assertEqual(by_ip["10.0.0.2"].status, nb.STATUS_OK)

    def test_variables_manquantes_echec(self):
        """Une variable de gabarit absente du CSV échoue avec la liste."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            results = nb.run_bulk_push([_row()], store, "vlan #vlan#", log_dir=tmp)
            self.assertEqual(results[0].status, nb.STATUS_FAILED)
            self.assertIn("Variables manquantes", results[0].error)
            self.assertIn("vlan", results[0].error)

    def test_succes_journal_et_manifeste(self):
        """Chaque hôte a son .log et le manifeste agrège tout."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            log_dir = Path(tmp) / "logs"
            results = nb.run_bulk_push([_row(line_no=7)], store, "desc #ip#", log_dir=log_dir)
            self.assertEqual(results[0].status, nb.STATUS_OK)
            log_path = Path(results[0].log_path)
            self.assertTrue(log_path.exists())
            self.assertEqual(log_path.name, "0007_10.0.0.1.log")
            content = log_path.read_text(encoding="utf-8")
            self.assertIn("# Hôte : 10.0.0.1", content)
            self.assertIn("# Statut : ok", content)

            manifest = json.loads((log_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["summary"]["total"], 1)
            self.assertEqual(manifest["summary"]["ok"], 1)
            self.assertEqual(manifest["hosts"][0]["ip"], "10.0.0.1")
            self.assertEqual(manifest["hosts"][0]["line_no"], 7)

    def test_ip_avec_caracteres_speciaux_sanitisee(self):
        """L'IP est assainie dans le nom de fichier de journal."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            results = nb.run_bulk_push([_row(ip="10.0.0.1/24")], store, "cmd", log_dir=tmp)
            self.assertIn("_", Path(results[0].log_path).name)

    def test_annulation_avant_envoi(self):
        """cancel_event armé : la ligne est marquée skipped, non tentée."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            cancel = threading.Event()
            cancel.set()
            results = nb.run_bulk_push([_row()], store, "cmd", log_dir=tmp, cancel_event=cancel)
            self.assertEqual(results[0].status, nb.STATUS_SKIPPED)
            self.assertIn("Annulé", results[0].error)

    def test_callbacks_appelles(self):
        """on_row_start et on_row_done sont appelés pour chaque ligne."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            starts, dones = [], []
            results = nb.run_bulk_push(
                [_row(ip="10.0.0.1"), _row(ip="10.0.0.2")],
                store,
                "cmd",
                log_dir=tmp,
                on_row_start=starts.append,
                on_row_done=dones.append,
            )
            self.assertEqual(sorted(r.ip for r in starts), ["10.0.0.1", "10.0.0.2"])
            self.assertEqual(len(dones), 2)

    def test_max_workers_zero_tolere(self):
        """max_workers=0 est ramené à 1 (max(1, ...)) sans erreur."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            results = nb.run_bulk_push([_row()], store, "cmd", log_dir=tmp, max_workers=0)
            self.assertEqual(len(results), 1)

    def test_log_contient_erreurs_de_config(self):
        """Les lignes rejetées détectées figurent dans le journal par hôte."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            row = _row()

            class _RejectedResult:
                pass

            result = nb.PushResult(
                row=row,
                status=nb.STATUS_REJECTED,
                output="% Invalid input",
                config_errors=["% Invalid input"],
                error="",
            )
            from netmiko_bulk_core import _write_log

            path = _write_log(Path(tmp), row, result)
            content = Path(path).read_text(encoding="utf-8")
            self.assertIn("# Lignes rejetées détectées :", content)
            self.assertIn("#   % Invalid input", content)
            self.assertIn("Sortie brute de l'équipement", content)

    def test_log_sans_sortie(self):
        """Une sortie vide produit la mention « (aucune sortie) »."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            row = _row()
            result = nb.PushResult(row=row, status=nb.STATUS_FAILED, error="x")
            from netmiko_bulk_core import _write_log

            path = _write_log(Path(tmp), row, result)
            content = Path(path).read_text(encoding="utf-8")
            self.assertIn("(aucune sortie)", content)
            self.assertIn("# Erreur de connexion : x", content)


class TestSummarizeResults(unittest.TestCase):
    """Statistiques d'une exécution pour l'UI de résultats."""

    def test_comptage_par_statut(self):
        """Le décompte par statut reflète les résultats fournis."""
        results = [
            nb.PushResult(row=_row(), status=nb.STATUS_OK),
            nb.PushResult(row=_row(), status=nb.STATUS_OK),
            nb.PushResult(row=_row(), status=nb.STATUS_REJECTED),
            nb.PushResult(
                row=_row(), status=nb.STATUS_FAILED, error="Authentification refusée : x"
            ),
            nb.PushResult(row=_row(), status=nb.STATUS_SKIPPED),
        ]
        summary = nb.summarize_results(results)
        self.assertEqual(summary["total"], 5)
        self.assertEqual(summary["ok"], 2)
        self.assertEqual(summary["rejected"], 1)
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(summary["skipped"], 1)

    def test_error_kinds_cle_avant_les_deux_points(self):
        """Les erreurs sont regroupées par leur préfixe avant « : »."""
        results = [
            nb.PushResult(
                row=_row(), status=nb.STATUS_FAILED, error="Authentification refusée : a"
            ),
            nb.PushResult(
                row=_row(), status=nb.STATUS_FAILED, error="Authentification refusée : b"
            ),
        ]
        summary = nb.summarize_results(results)
        self.assertEqual(summary["error_kinds"], {"Authentification refusée": 2})

    def test_vide(self):
        """Une exécution vide résume à des zéros."""
        summary = nb.summarize_results([])
        self.assertEqual(summary["total"], 0)
        self.assertEqual(summary["error_kinds"], {})


if __name__ == "__main__":
    unittest.main()
