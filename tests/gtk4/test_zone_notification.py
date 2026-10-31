# SPDX-License-Identifier: GPL-3.0-or-later
"""Icône de la zone de notification (#83) : StatusNotifierItem et dbusmenu sur un bus privé."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import time

import pytest
from gi.repository import Adw, Gio, GLib

import gcm4.ui  # noqa: F401  (require_version avant gi.repository)
from gcm4.core import chemins
from gcm4.core import preferences as prefs_core
from gcm4.ui import zone_notification as zn
from gcm4.ui.application import Gcm4Application

WATCHER_XML = """
<node><interface name="org.kde.StatusNotifierWatcher">
  <method name="RegisterStatusNotifierItem"><arg type="s" direction="in"/></method>
</interface></node>
"""


def _attendre(condition, delai=5.0):
    """Traite la boucle GLib jusqu'à ``condition()`` (ou échec après ``delai`` s)."""
    contexte = GLib.MainContext.default()
    fin = time.monotonic() + delai
    while not condition():
        assert time.monotonic() < fin, "délai dépassé"
        contexte.iteration(False)
        time.sleep(0.002)


@pytest.fixture
def bus_prive():
    """Adresse d'un ``dbus-daemon`` de session privé ; absent : erreur (#160)."""
    demon = shutil.which("dbus-daemon")
    assert demon, "dbus-daemon introuvable (paquet dbus-daemon)"
    processus = subprocess.Popen(
        [demon, "--session", "--nofork", "--print-address=1"], stdout=subprocess.PIPE, text=True
    )
    try:
        adresse = processus.stdout.readline().strip()
        assert adresse
        yield adresse
    finally:
        processus.terminate()
        processus.wait(timeout=10)


def _connexion(adresse):
    drapeaux = (
        Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
        | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION
    )
    return Gio.DBusConnection.new_for_address_sync(adresse, drapeaux, None, None)


def _appel(connexion, destination, chemin, interface, methode, parametres, type_reponse=None):
    """Appel D-Bus asynchrone (l'objet vit dans ce processus) ; renvoie la réponse ou l'erreur."""
    resultat = {}

    def fini(conn, res):
        try:
            resultat["ok"] = conn.call_finish(res)
        except GLib.Error as exc:
            resultat["erreur"] = exc

    connexion.call(
        destination,
        chemin,
        interface,
        methode,
        parametres,
        GLib.VariantType(type_reponse) if type_reponse else None,
        Gio.DBusCallFlags.NONE,
        5000,
        None,
        fini,
    )
    _attendre(lambda: resultat)
    if "erreur" in resultat:
        raise resultat["erreur"]
    return resultat["ok"].unpack()


class FauxWatcher:
    """``org.kde.StatusNotifierWatcher`` minimal ; ``refuser`` : répond par une erreur."""

    def __init__(self, adresse, refuser=False):
        """Publie l'objet watcher et prend son nom sur le bus ``adresse``."""
        self.connexion = _connexion(adresse)
        self.refuser = refuser
        self.enregistres = []
        interface = Gio.DBusNodeInfo.new_for_xml(WATCHER_XML).interfaces[0]
        self.objet = self.connexion.register_object(
            zn.CHEMIN_WATCHER, interface, self._appel, None, None
        )
        self.possede = {}
        self.nom = Gio.bus_own_name_on_connection(
            self.connexion,
            zn.WATCHER,
            Gio.BusNameOwnerFlags.NONE,
            lambda *_a: self.possede.update(ok=True),
            None,
        )
        _attendre(lambda: self.possede)

    def _appel(self, _c, _exp, _chemin, _iface, _methode, parametres, invocation):
        if self.refuser:
            invocation.return_dbus_error("org.kde.Erreur", "refus")
            return
        self.enregistres.append(parametres.unpack()[0])
        invocation.return_value(None)

    def fermer(self):
        """Libère le nom et retire l'objet (l'hôte SNI disparaît)."""
        Gio.bus_unown_name(self.nom)
        self.connexion.unregister_object(self.objet)
        self.connexion.flush_sync(None)


class Etat:
    """Fenêtre simulée : visibilité et compteurs des rappels de l'icône."""

    def __init__(self):
        """Fenêtre affichée, aucun rappel reçu."""
        self.visible = True
        self.bascules = 0
        self.quitte = 0

    def basculer(self):
        """Rappel « basculer » : inverse la visibilité."""
        self.bascules += 1
        self.visible = not self.visible

    def quitter(self):
        """Rappel « quitter »."""
        self.quitte += 1


@pytest.fixture
def icone(bus_prive):
    """Icône publiée sur le bus privé, et une connexion cliente distincte."""
    etat = Etat()
    connexion = _connexion(bus_prive)
    icone = zn.IconeNotification(
        connexion,
        "io.github.MathildeDec.gcm4",
        "/icones",
        lambda: etat.visible,
        etat.basculer,
        etat.quitter,
    )
    icone.demarrer()
    client = _connexion(bus_prive)
    _attendre(lambda: _proprietaire(client, icone.nom_bus))
    yield icone, etat, client
    icone.arreter()


def _proprietaire(client, nom):
    try:
        return _appel(
            client,
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus",
            "NameHasOwner",
            GLib.Variant("(s)", (nom,)),
            "(b)",
        )[0]
    except GLib.Error:
        return False


def _prop(client, icone, chemin, interface, nom):
    return _appel(
        client,
        icone.nom_bus,
        chemin,
        "org.freedesktop.DBus.Properties",
        "Get",
        GLib.Variant("(ss)", (interface, nom)),
        "(v)",
    )[0]


def _menu(client, icone, methode, parametres, type_reponse=None):
    return _appel(
        client, icone.nom_bus, zn.CHEMIN_MENU, zn.INTERFACE_MENU, methode, parametres, type_reponse
    )


def test_proprietes_sans_hote_sni(icone):
    """Sans StatusNotifierWatcher : objets publiés, rien d'enregistré, aucune erreur."""
    ic, _etat, client = icone
    assert not ic.enregistree
    assert (
        _prop(client, ic, zn.CHEMIN_ICONE, zn.INTERFACE_ICONE, "IconName")
        == "io.github.MathildeDec.gcm4"
    )
    assert _prop(client, ic, zn.CHEMIN_ICONE, zn.INTERFACE_ICONE, "Menu") == zn.CHEMIN_MENU
    assert _prop(client, ic, zn.CHEMIN_ICONE, zn.INTERFACE_ICONE, "Status") == "Active"
    assert _prop(client, ic, zn.CHEMIN_ICONE, zn.INTERFACE_ICONE, "IconThemePath") == "/icones"
    assert _prop(client, ic, zn.CHEMIN_MENU, zn.INTERFACE_MENU, "Version") == 3
    assert _prop(client, ic, zn.CHEMIN_MENU, zn.INTERFACE_MENU, "IconThemePath") == ["/icones"]
    assert ic._on_propriete(None, "", "", zn.INTERFACE_ICONE, "Inconnue") is None
    ic.demarrer()  # second appel sans effet
    assert len(ic._objets) == 2


def test_enregistrement_aupres_de_l_hote(icone, bus_prive):
    """Hôte SNI apparu après l'icône : elle s'enregistre sous son nom de bus ; il part : en attente."""
    ic, _etat, _client = icone
    watcher = FauxWatcher(bus_prive)
    _attendre(lambda: ic.enregistree)
    assert watcher.enregistres == [ic.nom_bus]
    watcher.fermer()
    _attendre(lambda: not ic.enregistree)


def test_refus_de_l_hote(icone, bus_prive):
    """Un refus du StatusNotifierWatcher est journalisé ; l'icône reste non enregistrée."""
    ic, _etat, _client = icone
    watcher = FauxWatcher(bus_prive, refuser=True)
    contexte = GLib.MainContext.default()
    fin = time.monotonic() + 0.5
    while time.monotonic() < fin:
        contexte.iteration(False)
    assert not ic.enregistree
    watcher.fermer()


def test_disposition_et_libelles(icone):
    """GetLayout : Masquer quand la fenêtre est affichée, Afficher sinon ; séparateur, Quitter."""
    ic, etat, client = icone
    revision, (racine, props, enfants) = _menu(
        client, ic, "GetLayout", GLib.Variant("(iias)", (0, -1, [])), "(u(ia{sv}av))"
    )
    assert racine == 0 and props == {"children-display": "submenu"}
    assert [e[0] for e in enfants] == [zn.BASCULER, zn.SEPARATEUR, zn.QUITTER]
    assert enfants[0][1]["label"] == "Hide window"
    assert enfants[1][1]["type"] == "separator"
    assert enfants[2][1]["label"] == "Quit"
    etat.visible = False
    _r, (_i, _p, enfants) = _menu(client, ic, "GetLayout", GLib.Variant("(iias)", (0, -1, [])))
    assert enfants[0][1]["label"] == "Show window"
    assert revision == ic.revision


def test_clic_et_menu(icone):
    """Activate et Event « clicked » basculent ou quittent ; LayoutUpdated est émis."""
    ic, etat, client = icone
    mises_a_jour = []
    client.signal_subscribe(
        ic.nom_bus,
        zn.INTERFACE_MENU,
        "LayoutUpdated",
        zn.CHEMIN_MENU,
        None,
        Gio.DBusSignalFlags.NONE,
        lambda *a: mises_a_jour.append(a[5].unpack()),
    )
    _appel(
        client,
        ic.nom_bus,
        zn.CHEMIN_ICONE,
        zn.INTERFACE_ICONE,
        "Activate",
        GLib.Variant("(ii)", (0, 0)),
    )
    assert etat.bascules == 1 and not etat.visible
    _attendre(lambda: mises_a_jour)
    assert mises_a_jour[-1] == (ic.revision, 0)
    _menu(
        client,
        ic,
        "Event",
        GLib.Variant("(isvu)", (zn.BASCULER, "clicked", GLib.Variant("s", ""), 0)),
    )
    _attendre(lambda: etat.bascules == 2)
    _menu(
        client,
        ic,
        "Event",
        GLib.Variant("(isvu)", (zn.BASCULER, "hovered", GLib.Variant("s", ""), 0)),
    )
    erreurs = _menu(
        client,
        ic,
        "EventGroup",
        GLib.Variant(
            "(a(isvu))",
            (
                [
                    (zn.QUITTER, "clicked", GLib.Variant("s", ""), 0),
                    (1, "opened", GLib.Variant("s", ""), 0),
                ],
            ),
        ),
        "(ai)",
    )
    assert erreurs == ([],)
    _attendre(lambda: etat.quitte == 1)
    for methode in ("SecondaryActivate", "ContextMenu"):
        _appel(
            client,
            ic.nom_bus,
            zn.CHEMIN_ICONE,
            zn.INTERFACE_ICONE,
            methode,
            GLib.Variant("(ii)", (0, 0)),
        )
    _appel(
        client,
        ic.nom_bus,
        zn.CHEMIN_ICONE,
        zn.INTERFACE_ICONE,
        "Scroll",
        GLib.Variant("(is)", (1, "vertical")),
    )
    ic.activer(zn.SEPARATEUR)
    assert etat.bascules == 2 and etat.quitte == 1


def test_proprietes_du_menu(icone):
    """GetGroupProperties, GetProperty (et propriété inconnue), AboutToShow(Group)."""
    ic, _etat, client = icone
    (tous,) = _menu(
        client, ic, "GetGroupProperties", GLib.Variant("(aias)", ([], [])), "(a(ia{sv}))"
    )
    assert [i for i, _p in tous] == [zn.BASCULER, zn.SEPARATEUR, zn.QUITTER]
    (un,) = _menu(client, ic, "GetGroupProperties", GLib.Variant("(aias)", ([zn.QUITTER], [])))
    assert [i for i, _p in un] == [zn.QUITTER]
    assert _menu(
        client, ic, "GetProperty", GLib.Variant("(is)", (zn.QUITTER, "label")), "(v)"
    ) == ("Quit",)
    with pytest.raises(GLib.Error, match="UnknownProperty"):
        _menu(client, ic, "GetProperty", GLib.Variant("(is)", (zn.QUITTER, "absente")))
    assert _menu(client, ic, "AboutToShow", GLib.Variant("(i)", (0,)), "(b)") == (False,)
    assert _menu(client, ic, "AboutToShowGroup", GLib.Variant("(ai)", ([0],)), "(aiai)") == (
        [],
        [],
    )


def test_arreter_retire_l_icone(icone):
    """Après arreter : nom de bus libéré, objets retirés ; rafraichir n'émet plus rien."""
    ic, _etat, client = icone
    ic.arreter()
    _attendre(lambda: not _proprietaire(client, ic.nom_bus))
    assert ic._objets == [] and ic._nom == 0 and ic._veille == 0
    ic.rafraichir()
    ic.arreter()  # second appel sans effet


# Intégration à l'application -------------------------------------------------


def _app(tmp_path, connexion, monkeypatch, pomper, tray=False):
    monkeypatch.setattr(Gcm4Application, "get_dbus_connection", lambda _self: connexion)
    if tray:
        prefs_core.save_preference(chemins.fichier_conf(str(tmp_path)), "tray_icon", True)
    app = Gcm4Application(
        argparse.Namespace(config_dir=str(tmp_path), debug=False, hotes=[]),
        flags=Gio.ApplicationFlags.NON_UNIQUE,
    )
    app.register(None)
    app.activate()
    pomper()
    return app


def _fermer(app, pomper):
    app.appliquer_zone_notification(False)
    for fenetre in list(app.get_windows()):
        fenetre.destroy()
    pomper()


def test_preference_au_demarrage(tmp_path, bus_prive, monkeypatch, pomper):
    """``tray-icon`` actif : l'icône est publiée dès l'ouverture de la fenêtre principale."""
    app = _app(tmp_path, _connexion(bus_prive), monkeypatch, pomper, tray=True)
    try:
        assert app.zone_notification is not None and app.zone_notification._objets
        app.appliquer_zone_notification(True)  # déjà affichée : inchangée
    finally:
        _fermer(app, pomper)
    assert app.zone_notification is None


def test_basculer_la_fenetre(tmp_path, bus_prive, monkeypatch, pomper):
    """Clic : la fenêtre se masque puis revient ; fermée, elle est recréée ; Quitter quitte."""
    app = _app(tmp_path, _connexion(bus_prive), monkeypatch, pomper)
    try:
        assert app.zone_notification is None  # désactivée par défaut
        app.appliquer_zone_notification(True)
        ic = app.zone_notification
        fenetre = app._fenetre_principale()
        assert app._principale_visible()
        ic.activer(zn.BASCULER)
        pomper()
        assert not fenetre.get_visible() and not app._principale_visible()
        ic.activer(zn.BASCULER)
        pomper()
        assert fenetre.get_visible()
        fenetre.destroy()
        pomper()
        assert app._fenetre_principale() is None and not app._principale_visible()
        app.basculer_fenetre()
        pomper()
        assert app._fenetre_principale() is not None
        quitte = []
        monkeypatch.setattr(app, "quit", lambda: quitte.append(True))
        app.lookup_action("quit").connect("activate", lambda *_a: quitte.append("action"))
        ic.activer(zn.QUITTER)
        pomper()
        assert quitte
    finally:
        _fermer(app, pomper)


def test_sans_bus_de_session(tmp_path, monkeypatch, pomper):
    """Aucun bus de session : l'option reste sans effet, sans erreur."""
    app = _app(tmp_path, None, monkeypatch, pomper)
    try:
        app.appliquer_zone_notification(True)
        assert app.zone_notification is None
    finally:
        _fermer(app, pomper)


def test_interrupteur_des_parametres(tmp_path, bus_prive, monkeypatch, pomper):
    """Paramètres > Général : l'interrupteur publie ou retire l'icône et enregistre ``tray-icon``."""
    app = _app(tmp_path, _connexion(bus_prive), monkeypatch, pomper)
    try:
        app.activate_action("preferences", None)
        pomper()
        d = app.preferences
        interrupteur = d.interrupteurs["tray_icon"]
        assert not interrupteur.get_active()
        assert interrupteur.get_ancestor(Adw.ActionRow).get_subtitle()
        interrupteur.set_active(True)
        pomper()
        assert app.zone_notification is not None
        assert prefs_core.load_preferences(app.config_file).tray_icon
        interrupteur.set_active(False)
        pomper()
        assert app.zone_notification is None
        assert not prefs_core.load_preferences(app.config_file).tray_icon
        d.close()
        pomper()
    finally:
        _fermer(app, pomper)
