"""Dialogue d'hôte sous Xvfb : création, modification, duplication, annulation (#145)."""

from __future__ import annotations

import configparser

from gi.repository import Gdk, Gtk
from loguru import logger

import gcm4.ui  # noqa: F401  (require_version avant gi.repository)
from gcm4.core.chiffrement import ChiffrementGcm
from gcm4.core.config import ConfigError, load_config
from gcm4.core.model import Host
from gcm4.core.validation import FieldError
from gcm4.ui.host_dialog import CREATION, DUPLICATION, MODIFICATION, HostDialog


def _relire(dossier):
    return load_config(str(dossier / "gcm4.conf"), ChiffrementGcm(str(dossier)))


def _brut(dossier):
    cp = configparser.RawConfigParser()
    cp.read(dossier / "gcm4.conf", encoding="utf-8")
    return {cp.get(s, "name"): dict(cp.items(s)) for s in cp.sections() if s.startswith("host ")}


def _ouvrir(arbre, action, chemin):
    noeud = arbre.ligne(chemin).get_item().noeud if chemin else None
    arbre.menu.cible = noeud
    arbre.menu.executer(action)
    return arbre.menu.dialogue_hote


def test_creation_dans_le_groupe_vise(app_fixture, pomper):
    """Nouvel hôte dans LAB : vierge sans message, invalide tant que nom et adresse manquent, puis écrit."""
    app, dossier = app_fixture
    arbre = app.get_active_window().arbre
    d = _ouvrir(arbre, "add", "LAB/bases")
    pomper()
    assert d.mode == CREATION and d.get_title() and d.groupe.get_text() == "LAB/bases"
    assert not d.bouton_enregistrer.get_sensitive() and not d.message.get_visible()
    assert not d.enregistrer()
    d.nom.set_text("neuf")
    assert d.message.get_visible() and d.adresse.has_css_class("error")
    d.adresse.set_text("10.9.9.9")
    d.port.set_text("2200")
    d.utilisateur.set_text("root")
    d.mot_de_passe.set_text("S3cret-unique")
    assert d.bouton_enregistrer.get_sensitive() and not d.message.get_visible()
    assert d.enregistrer()
    pomper()
    assert not d.get_visible()
    h = _relire(dossier).store.find("LAB/bases/neuf")
    assert (h.host, h.port, h.user, h.password) == ("10.9.9.9", 2200, "root", "S3cret-unique")
    assert arbre.chemin_selectionne() == "LAB/bases/neuf"


def test_erreurs_sur_les_champs(app_fixture, pomper):
    """Doublon, « / », port invalide : classe error sur le champ, Enregistrer inactif."""
    app, _ = app_fixture
    arbre = app.get_active_window().arbre
    d = _ouvrir(arbre, "add", "LAB")
    d.adresse.set_text("h")
    for nom in ("web", "a/b", ""):
        d.nom.set_text(nom)
        assert d.nom.has_css_class("error") and not d.bouton_enregistrer.get_sensitive(), nom
    d.nom.set_text("libre")
    assert not d.nom.has_css_class("error")
    for port in ("abc", "0", "70000"):
        d.port.set_text(port)
        assert d.port.has_css_class("error") and "65535" in d.message.get_label(), port
        assert not d.bouton_enregistrer.get_sensitive()
    d.port.set_text("")
    assert d.bouton_enregistrer.get_sensitive()
    d.enregistre = True
    d.close()


def test_modification_garde_les_options_de_protocole(app_fixture, pomper):
    """PROD/eu/api modifié et déplacé, sans plugin : options proto-a (tunnel, x11) et mot de passe intacts."""
    app, dossier = app_fixture
    arbre = app.get_active_window().arbre
    avant = _brut(dossier)["api"]
    mdp = _relire(dossier).store.find("PROD/eu/api").password
    rang = [h.path for h in arbre.store.hosts].index("PROD/eu/api")
    arbre.menu.executer("expand")
    pomper()
    d = _ouvrir(arbre, "edit", "PROD/eu/api")
    assert d.mode == MODIFICATION and d.nom.get_text() == "api" and d.port.get_text() == "2222"
    assert d.mot_de_passe.get_text()  # déchiffré, éditable
    assert d.protocole.get_subtitle() and d.protocole.get_sensitive()  # proto-a inconnu, conservé
    assert d.bouton_enregistrer.get_sensitive() and not d.modifie()
    d.description.set_text("Passerelle")
    d.groupe.set_text("PROD")
    assert d.enregistrer()
    pomper()
    relu = _relire(dossier)
    h = relu.store.find("PROD/api")
    assert relu.store.find("PROD/eu/api") is None
    assert arbre.store.hosts[rang].path == "PROD/api"  # même rang en mémoire
    assert h.description == "Passerelle" and h.protocol == "proto-a" and h.password == mdp
    apres = _brut(dossier)["api"]
    for cle in ("tunnel", "x11", "port", "user", "protocol"):
        assert apres[cle] == avant[cle], cle


def test_modification_nom_inchange_n_est_pas_un_doublon(app_fixture, pomper):
    """Modifier sans renommer : pas d'erreur de doublon."""
    app, _ = app_fixture
    arbre = app.get_active_window().arbre
    d = _ouvrir(arbre, "edit", "LAB/web")
    assert d.erreurs == [] and d.bouton_enregistrer.get_sensitive()
    d.nom.set_text("db")  # db est dans LAB/bases : libre dans LAB
    assert d.erreurs == []
    d.groupe.set_text("LAB/bases")
    assert d.nom.has_css_class("error")
    d.enregistre = True
    d.close()


def test_duplication_original_intact(app_fixture, pomper):
    """Dupliquer web : « web (2) » proposé, renommé « web-bis » ; l'original et ses options restent."""
    app, dossier = app_fixture
    arbre = app.get_active_window().arbre
    d = _ouvrir(arbre, "duplicate", "LAB/web")
    assert d.mode == DUPLICATION and d.nom.get_text() == "web (2)"
    d.nom.set_text("web")
    assert not d.bouton_enregistrer.get_sensitive()
    d.nom.set_text("web-bis")
    assert d.enregistrer()
    pomper()
    brut = _brut(dossier)
    assert brut["web"]["resolution"] == brut["web-bis"]["resolution"] == "1280x800"
    assert brut["web-bis"]["group"] == "LAB"


def test_annulation_avec_et_sans_modification(app_fixture, pomper):
    """Sans modification : fermeture directe. Avec : confirmation ; continuer garde, abandonner ferme sans écrire."""
    app, dossier = app_fixture
    arbre = app.get_active_window().arbre
    d = _ouvrir(arbre, "edit", "LAB/web")
    pomper()
    d.close()
    pomper()
    assert not d.get_visible() and d.confirmation is None
    contenu = (dossier / "gcm4.conf").read_bytes()
    d = _ouvrir(arbre, "edit", "LAB/web")
    d.description.set_text("brouillon")
    d.close()
    pomper()
    assert d.get_visible() and d.confirmation is not None
    d.confirmation.emit("response", "continuer")
    pomper()
    assert d.get_visible() and d.description.get_text() == "brouillon"
    d.close()
    pomper()
    d.confirmation.emit("response", "abandonner")
    pomper()
    assert not d.get_visible()
    assert (dossier / "gcm4.conf").read_bytes() == contenu


def test_couleurs_espace_de_travail_fermeture(app_fixture, pomper):
    """Couleurs choisies (hex), espace de travail, fermeture « Toujours », commandes : écrits."""
    app, dossier = app_fixture
    arbre = app.get_active_window().arbre
    d = _ouvrir(arbre, "edit", "LAB/web")
    assert d.couleurs_par_defaut.get_active()
    d.couleurs_par_defaut.set_active(False)
    rouge = Gdk.RGBA()
    rouge.parse("#ff0000")
    d.premier_plan.set_rgba(rouge)
    d.espace_de_travail.set_active(True)
    d.fermeture.set_selected(2)
    d.avant.set_text("echo {name}")
    d.apres.set_text("logger fin")
    d.parametres.set_text("-v")
    assert d.enregistrer()
    pomper()
    h = _relire(dossier).store.find("LAB/web")
    assert (h.font_color, h.back_color) == ("#ff0000", "#000000")
    assert h.workspace and h.auto_close_tab == "1"
    assert (h.pre_connect_command, h.post_connect_command, h.extra_params) == (
        "echo {name}",
        "logger fin",
        "-v",
    )


def test_creation_sans_plugin(app_fixture, pomper):
    """Zéro plugin : protocole non modifiable en création, l'hôte s'enregistre quand même."""
    app, dossier = app_fixture
    arbre = app.get_active_window().arbre
    d = _ouvrir(arbre, "add", None)
    assert not d.protocole.get_sensitive() and d.protocole.get_subtitle()
    d.nom.set_text("racine")
    d.adresse.set_text("h")
    assert d.enregistrer()
    pomper()
    assert _relire(dossier).store.find("racine") is not None


def test_protocoles_du_registre(app_fixture, pomper):
    """Protocoles fournis par le registre : liste triée, choix enregistré dans l'hôte lu."""
    app, _ = app_fixture
    fenetre = app.get_active_window()
    retenus = []
    d = HostDialog(
        fenetre,
        fenetre.arbre.store,
        CREATION,
        lambda h, _o: retenus.append(h) or True,
        protocoles=("zeta", "alpha", ""),
    )
    assert d.protocole.get_model().get_n_items() == 2 and d.protocole.get_sensitive()
    d.protocole.set_selected(1)
    d.nom.set_text("x")
    d.adresse.set_text("h")
    assert d.enregistrer() and retenus[0].protocol == "zeta"


def test_echec_d_ecriture_garde_le_dialogue(app_fixture, pomper):
    """Écriture impossible : dialogue ouvert, saisie conservée, arbre rétabli."""
    app, _ = app_fixture
    arbre = app.get_active_window().arbre
    arbre.notifier = lambda _m: None

    def echoue():
        raise ConfigError("lecture seule")

    arbre.enregistrer = echoue
    d = _ouvrir(arbre, "edit", "LAB/web")
    d.description.set_text("perdu ?")
    assert not d.enregistrer()
    pomper()
    assert d.get_visible() and d.description.get_text() == "perdu ?"
    assert arbre.store.find("LAB/web").description == ""
    d.enregistre = True
    d.close()


def test_mot_de_passe_jamais_journalise(app_fixture, pomper):
    """Journal TRACE pendant création et modification : le mot de passe n'y figure pas."""
    app, _ = app_fixture
    arbre = app.get_active_window().arbre
    lignes = []
    sink = logger.add(lambda m: lignes.append(str(m)), level="TRACE")
    logger.enable("gcm4")
    try:
        d = _ouvrir(arbre, "add", "LAB")
        d.nom.set_text("secret")
        d.adresse.set_text("h")
        d.mot_de_passe.set_text("Mdp-Tres-Secret-42")
        assert d.enregistrer()
        pomper()
        d = _ouvrir(arbre, "edit", "LAB/secret")
        d.description.set_text("x")
        assert d.enregistrer()
        pomper()
    finally:
        logger.disable("gcm4")
        logger.remove(sink)
    assert lignes and not any("Mdp-Tres-Secret-42" in ligne for ligne in lignes)


# --- zone d'options du plugin et port par défaut (#146) -----------------------


class _FauxPlugin:
    """Plugin de test : port par défaut et option « mode » modifiable."""

    def __init__(self, port):
        self._port = port
        self.protocols = ("test",)  # parcouru par all_default_ports (contrat v1)
        self.appels = []
        self.ligne = None

    def default_port(self, protocol):
        return self._port

    def build_host_options_widget(self, host, options, changed):
        self.appels.append((host.name, dict(options)))
        ligne = Gtk.Entry()
        ligne.set_text(options.get("mode", ""))

        def saisie(*_args):
            options["mode"] = ligne.get_text()
            changed()

        ligne.connect("changed", saisie)
        self.ligne = ligne
        return ligne

    def validate_options(self, protocol, options):
        if options.get("mode") == "interdit":
            return [FieldError(champ="mode", message="mode interdit")]
        return []


class _FauxRegistre:
    """Registre minimal : get/protocols/all."""

    def __init__(self, plugins):
        self._plugins = plugins

    def get(self, protocole):
        return self._plugins.get(protocole)

    def protocols(self):
        return sorted(self._plugins)

    def all(self):
        return list(self._plugins.values())


def test_zone_sans_plugin_message_de_conservation(app_fixture, pomper):
    """Sans plugin : la zone affiche le message de conservation, options intactes (#146)."""
    app, _ = app_fixture
    arbre = app.get_active_window().arbre
    d = _ouvrir(arbre, "edit", "LAB/web")  # proto-b, aucun plugin installé
    zone = d._ligne_options.get_child()
    assert isinstance(zone, Gtk.Label)
    assert zone.get_label() == (
        "No extension is installed for this protocol: its options are kept and cannot be modified."
    )
    assert arbre.store.find("LAB/web").protocol_options["proto-b"] == {"resolution": "1280x800"}
    d.close()


def test_options_rdp_vnc_ssh_sans_plugin(app_fixture, pomper):
    """Hôtes RDP/VNC/SSH modifiés et enregistrés sans plugin : options conservées (#146)."""
    app, dossier = app_fixture
    arbre = app.get_active_window().arbre
    attendu = {
        "rdp": {"domain": "CORP", "width": "1920"},
        "vnc": {"depth": "24"},
        "ssh": {"tunnel": "L8080:localhost:80", "x11": "True", "keep_alive": "30"},
    }
    for proto, options in attendu.items():
        hote = Host(name=proto, group="LAB", host=f"{proto}.example.org", protocol=proto)
        hote.protocol_options[proto] = dict(options)
        arbre.store.add(hote)
    arbre.recharger()
    for proto, options in attendu.items():
        d = _ouvrir(arbre, "edit", f"LAB/{proto}")
        assert isinstance(d._ligne_options.get_child(), Gtk.Label)
        d.description.set_text(f"modifié {proto}")
        assert d.enregistrer()
        pomper()
        h = _relire(dossier).store.find(f"LAB/{proto}")
        assert h.protocol_options[proto] == options


def test_zone_options_et_port_par_defaut(app_fixture, pomper):
    """Zone du plugin : options éditées écrites à l'enregistrement, port qui suit le protocole (#146)."""
    app, _ = app_fixture
    fenetre = app.get_active_window()
    ssh, rdp, vnc = _FauxPlugin(22), _FauxPlugin(3389), _FauxPlugin(4242)
    registre = _FauxRegistre({"ssh": ssh, "rdp": rdp, "vnc": vnc})
    hote = Host(name="srv", group="LAB", host="srv.example.org", protocol="ssh", port=22)
    hote.protocol_options["ssh"] = {"mode": "rapide"}
    enregistres = []
    d = HostDialog(
        fenetre,
        fenetre.arbre.store,
        MODIFICATION,
        lambda h, _o: enregistres.append(h) or True,
        hote=hote,
        registre=registre,
    )
    pomper()
    # Zone remplie par le plugin du protocole, avec les options existantes.
    assert d._ligne_options.get_child() is ssh.ligne
    assert ssh.appels == [("srv", {"mode": "rapide"})]
    assert d.port.get_text() == "22"
    # Bascule de protocole : le port par défaut suit, la zone est remplacée.
    d.protocole.set_selected(d._protocoles.index("rdp"))
    pomper()
    assert d.port.get_text() == "3389"
    assert d._ligne_options.get_child() is rdp.ligne
    assert rdp.appels == [("srv", {})]
    # Port libre, même égal au défaut d'un tiers protocole : jamais écrasé
    # au changement de protocole (seul le défaut de l'ancien est suivi).
    d.port.set_text("4242")
    d.protocole.set_selected(d._protocoles.index("ssh"))
    pomper()
    assert d.port.get_text() == "4242"
    # Option saisie dans la zone : écrite seulement à l'enregistrement,
    # options de l'autre protocole conservées.
    d.protocole.set_selected(d._protocoles.index("rdp"))
    pomper()
    rdp.ligne.set_text("avance")
    pomper()
    assert d.enregistrer()
    pomper()
    h = enregistres[0]
    assert h.protocol == "rdp"
    assert h.protocol_options["rdp"] == {"mode": "avance"}
    assert h.protocol_options["ssh"] == {"mode": "rapide"}


def test_options_invalides_bloquent_l_enregistrement(app_fixture, pomper):
    """validate_options du plugin : erreur affichée, « Enregistrer » inactif (#146)."""
    app, _ = app_fixture
    fenetre = app.get_active_window()
    plugin = _FauxPlugin(4242)
    hote = Host(name="srv", group="LAB", host="srv.example.org", protocol="demo")
    hote.protocol_options["demo"] = {"mode": ""}
    d = HostDialog(
        fenetre,
        fenetre.arbre.store,
        MODIFICATION,
        lambda h, _o: True,
        hote=hote,
        registre=_FauxRegistre({"demo": plugin}),
    )
    pomper()
    plugin.ligne.set_text("interdit")
    pomper()
    assert any(e.champ == "mode" for e in d.erreurs)
    assert not d.bouton_enregistrer.get_sensitive()
    assert not d.enregistrer()
    plugin.ligne.set_text("avance")
    pomper()
    assert d.bouton_enregistrer.get_sensitive() and d.enregistrer()


def test_zone_options_plugin_demo(app_demo, pomper):
    """Zone du plugin démo : option « mode » saisie, écrite à l'enregistrement (#146)."""
    app, dossier = app_demo
    arbre = app.get_active_window().arbre
    hote = arbre.store.find("LAB/web")
    hote.protocol = "demo"
    hote.protocol_options = {"demo": {"mode": "rapide"}}
    d = _ouvrir(arbre, "edit", "LAB/web")
    zone = d._ligne_options.get_child()
    assert isinstance(zone, Gtk.Entry) and zone.get_text() == "rapide"
    zone.set_text("avance")
    pomper()
    # Annulation : l'hôte d'origine est intact, l'option n'est pas écrite.
    d.close()
    pomper()
    d.confirmation.emit("response", "abandonner")
    pomper()
    assert arbre.store.find("LAB/web").protocol_options["demo"] == {"mode": "rapide"}
    # Enregistrement : l'option est écrite (à la relecture, le format INI
    # fusionne les options dans le protocole courant, #146 ne change rien
    # à l'écriture).
    d = _ouvrir(arbre, "edit", "LAB/web")
    zone = d._ligne_options.get_child()
    zone.set_text("avance")
    pomper()
    assert d.enregistrer()
    pomper()
    h = _relire(dossier).store.find("LAB/web")
    assert h.protocol == "demo" and h.protocol_options["demo"] == {"mode": "avance"}


def test_sous_titre_protocole_inconnu_synchronise(app_fixture, pomper):
    """Le sous-titre « aucune extension » suit la sélection, comme la zone (#146)."""
    app, _ = app_fixture
    fenetre = app.get_active_window()
    ssh = _FauxPlugin(22)
    hote = Host(name="srv", group="LAB", host="srv.example.org", protocol="proto-a")
    d = HostDialog(
        fenetre,
        fenetre.arbre.store,
        MODIFICATION,
        lambda h, _o: True,
        hote=hote,
        registre=_FauxRegistre({"ssh": ssh}),
    )
    pomper()
    # Protocole inconnu conservé : sous-titre et message de la zone alignés.
    assert (
        d.protocole.get_subtitle() == "No extension handles this protocol: its options are kept."
    )
    assert isinstance(d._ligne_options.get_child(), Gtk.Label)
    # Choix d'un protocole installé : sous-titre effacé, zone du plugin.
    d.protocole.set_selected(d._protocoles.index("ssh"))
    pomper()
    assert d.protocole.get_subtitle() == ""
    assert d._ligne_options.get_child() is ssh.ligne
    # Retour au protocole inconnu : conservé, options intouchables.
    d.protocole.set_selected(d._protocoles.index("proto-a"))
    pomper()
    assert (
        d.protocole.get_subtitle() == "No extension handles this protocol: its options are kept."
    )
    assert isinstance(d._ligne_options.get_child(), Gtk.Label)


def test_port_par_defaut_a_l_ouverture(app_demo, pomper):
    """Ouverture en création : champ vide → port par défaut du plugin, sans modification."""
    app, _ = app_demo
    arbre = app.get_active_window().arbre
    d = _ouvrir(arbre, "add", None)
    assert d.port.get_text() == "4242"
    assert not d.modifie()


def test_hote_local_sans_adresse(app_local, pomper):
    """#297 : protocole local — champ Adresse masqué, enregistrement sans adresse."""
    app, dossier = app_local
    arbre = app.get_active_window().arbre
    d = _ouvrir(arbre, "add", "LAB/bases")
    pomper()
    assert "local" in d._protocoles
    d.protocole.set_selected(d._protocoles.index("local"))
    pomper()
    assert not d.adresse.get_visible()
    d.nom.set_text("poste")
    assert d.bouton_enregistrer.get_sensitive() and not d.message.get_visible()
    assert d.enregistrer()
    pomper()
    h = _relire(dossier).store.find("LAB/bases/poste")
    assert h is not None and h.host == "" and h.protocol == "local"


def test_hote_local_edite_sans_adresse(app_local, pomper):
    """#297 : un hôte local sans adresse (importé de GTK3) reste modifiable."""
    app, dossier = app_local
    arbre = app.get_active_window().arbre
    d = _ouvrir(arbre, "add", "LAB/bases")
    pomper()
    d.protocole.set_selected(d._protocoles.index("local"))
    pomper()
    d.nom.set_text("poste")
    assert d.enregistrer()
    pomper()
    d2 = _ouvrir(arbre, "edit", "LAB/bases/poste")
    pomper()
    assert d2.mode == MODIFICATION and not d2.adresse.get_visible()
    d2.description.set_text("modifié")
    assert d2.enregistrer()
    pomper()
    h = _relire(dossier).store.find("LAB/bases/poste")
    assert h.description == "modifié"


def test_sans_plugin_adresse_visible_et_exigee(app_fixture, pomper):
    """#297 : sans plugin, le champ Adresse reste visible et l'adresse exigée."""
    app, _ = app_fixture
    arbre = app.get_active_window().arbre
    d = _ouvrir(arbre, "add", "LAB/bases")
    pomper()
    assert d.adresse.get_visible()
    d.nom.set_text("neuf")
    assert d.adresse.has_css_class("error")
    d.close()
