# SPDX-License-Identifier: GPL-3.0-or-later
"""Mode zéro plugin et plugin démo dans l'application (#151)."""

from __future__ import annotations

from gi.repository import GLib, Gtk


def _libelles(menu):
    modele = menu.popover.get_menu_model()
    return [
        modele.get_item_link(i, "section").get_item_attribute_value(j, "action", None).get_string()
        for i in range(modele.get_n_items())
        for j in range(modele.get_item_link(i, "section").get_n_items())
    ]


def _connecter(fenetre, chemin, pomper):
    fenetre.activate_action("win.connect-host", GLib.Variant.new_string(chemin))
    pomper()


def _demo(fenetre):
    hote = fenetre.config.store.find("LAB/web")
    hote.protocol = "demo"
    return hote, fenetre.registre.get("demo")


def test_zero_plugin(app_fixture, pomper):
    """Sans plugin : registre vide, message, aucun onglet, hôte intact, ni Connecter ni protocole proposé."""
    app, _ = app_fixture
    fenetre = app.get_active_window()
    messages = []
    fenetre.notifier = messages.append
    assert app.registre.plugins() == [] and app.registre.erreurs() == []
    avant = repr(fenetre.config.store.find("LAB/web"))
    _connecter(fenetre, "LAB/web", pomper)
    assert messages == [
        "No extension handles the protocol «proto-b». Install it to open this connection."
    ]
    assert fenetre.onglets.get_n_pages() == 1 and fenetre.sessions == {}
    assert repr(fenetre.config.store.find("LAB/web")) == avant
    _connecter(fenetre, "LAB/inexistant", pomper)
    assert len(messages) == 1
    arbre = fenetre.arbre
    arbre.selectionner("LAB/web")
    arbre.menu._menu_clavier()
    pomper()
    assert "arbre.connect" not in _libelles(arbre.menu)
    arbre.menu.popover.popdown()
    dialogue = arbre.menu.ouvrir_dialogue_hote(
        "modification", fenetre.config.store.find("LAB/web")
    )
    assert dialogue._protocoles == ["proto-b"]  # conservé, sans autre choix
    dialogue.destroy()


def test_plugin_demo_ouvre_ferme_rouvre(app_demo, pomper):
    """Plugin démo : onglet ouvert, fermé (close() une fois), rouvert ; Connecter dans le menu."""
    app, _ = app_demo
    fenetre = app.get_active_window()
    assert [p.name for p in app.registre.plugins()] == ["demo"]
    hote, plugin = _demo(fenetre)
    _connecter(fenetre, "LAB/web", pomper)
    assert fenetre.onglets.get_n_pages() == 2 and fenetre.onglets.get_current_page() == 1
    contenu = fenetre.onglets.get_nth_page(1)
    assert isinstance(contenu, Gtk.Label) and contenu.get_label() == f"demo:{hote.host}"
    assert (plugin.ouvertures, plugin.fermetures) == (1, 0) and contenu in fenetre.sessions
    entete = fenetre.onglets.get_tab_label(contenu)
    titre, bouton = entete.get_first_child(), entete.get_last_child()
    assert titre.get_label() == "web" and isinstance(bouton, Gtk.Button)
    bouton.emit("clicked")  # « Confirmer la fermeture d'une console » : actif par défaut (#247)
    pomper()
    dialogue = fenetre.confirmations_onglets[contenu]
    assert fenetre.onglets.get_n_pages() == 2 and plugin.fermetures == 0
    dialogue.emit("response", "annuler")
    dialogue.close()
    pomper()
    assert fenetre.onglets.get_n_pages() == 2 and fenetre.confirmations_onglets == {}
    bouton.emit("clicked")
    pomper()
    dialogue = fenetre.confirmations_onglets[contenu]
    dialogue.emit("response", "fermer")
    dialogue.close()
    pomper()
    assert fenetre.onglets.get_n_pages() == 1 and (plugin.ouvertures, plugin.fermetures) == (1, 1)
    fenetre.fermer_onglet(contenu)  # déjà fermé : rien de plus
    assert plugin.fermetures == 1

    arbre = fenetre.arbre
    arbre.selectionner("LAB/web")
    arbre.menu._menu_clavier()
    pomper()
    assert "arbre.connect" in _libelles(arbre.menu)
    arbre.menu.popover.popdown()
    arbre.menu.executer("connect")
    pomper()
    assert fenetre.onglets.get_n_pages() == 2 and plugin.ouvertures == 2
    fenetre.close()  # connexion ouverte : « Quitter quand même ? » (#147)
    pomper()
    assert fenetre.confirmation_fermeture is not None and plugin.fermetures == 1
    dialogue = fenetre.confirmation_fermeture
    dialogue.emit("response", "quitter")
    dialogue.close()  # un vrai clic ferme le dialogue ; emit() seul ne le fait pas
    pomper()
    assert plugin.fermetures == 2 and fenetre.sessions == {}


def test_plugin_demo_protocole_absent_et_echec(app_demo, pomper):
    """Autre protocole : message ; échec du plugin : message, warning, aucun onglet."""
    app, _ = app_demo
    fenetre = app.get_active_window()
    messages = []
    fenetre.notifier = messages.append
    _connecter(fenetre, "PROD/eu/api", pomper)
    assert "«proto-a»" in messages[-1]
    hote, plugin = _demo(fenetre)
    hote.host = "echec"
    _connecter(fenetre, "LAB/web", pomper)
    assert messages[-1] == "Unable to open «web»: refusée"
    assert fenetre.onglets.get_n_pages() == 1 and plugin.ouvertures == 0
    dialogue = fenetre.arbre.menu.ouvrir_dialogue_hote("modification", hote)
    assert dialogue._protocoles == ["demo"]
    dialogue.destroy()


def test_contexte_du_plugin(app_demo, pomper):
    """config() en lecture seule, notify, open_tab sans session, ask_password asynchrone."""
    app, _ = app_demo
    fenetre = app.get_active_window()
    hote, plugin = _demo(fenetre)
    ctx = plugin.context
    assert dict(ctx.config()) == {}
    fenetre.config.brut.add_section("plugin demo")
    fenetre.config.brut.set("plugin demo", "cle", "valeur")
    config = ctx.config()
    assert dict(config) == {"cle": "valeur"}
    try:
        config["cle"] = "x"
    except TypeError:
        pass
    else:
        raise AssertionError("config() modifiable")
    messages = []
    fenetre.notifier = messages.append
    ctx.notify("bonjour")
    assert messages == ["bonjour"]
    ctx.open_tab("Journal", Gtk.Label(label="journal"))
    assert fenetre.onglets.get_n_pages() == 2 and fenetre.sessions == {}
    fenetre.fermer_onglet(fenetre.onglets.get_nth_page(1))
    assert fenetre.onglets.get_n_pages() == 1

    recus = []
    ctx.ask_password(hote, recus.append)
    pomper()
    ctx.dialogue.get_extra_child().set_text("secret")
    ctx.dialogue.emit("response", "ok")
    ctx.ask_password(hote, recus.append)
    pomper()
    ctx.dialogue.emit("response", "cancel")
    assert recus == ["secret", None]


def test_contexte_sans_fenetre(app_demo, pomper):
    """Fenêtre fermée : ask_password rend None, notify et open_tab ne font rien, config() vide."""
    app, _ = app_demo
    fenetre = app.get_active_window()
    _hote, plugin = _demo(fenetre)
    fenetre.destroy()
    pomper()
    recus = []
    plugin.context.ask_password(_hote, recus.append)
    plugin.context.notify("x")
    plugin.context.open_tab("x", Gtk.Label())
    assert recus == [None] and dict(plugin.context.config()) == {}
