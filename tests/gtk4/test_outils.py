# SPDX-License-Identifier: GPL-3.0-or-later
"""Plugins outils dans l'interface : menu, action, échec notifié (#73)."""

from __future__ import annotations

from gi.repository import Gtk

FIXTURES_CHEMIN = "tests/fixtures"


def _libelles_menu(fenetre):
    """[(titre du sous-menu, [libellés des entrées])] du menu principal.

    Le modèle imbrique les sections : racine -> sous-menu -> section ->
    entrées ; seules les entrées portent un libellé.
    """
    modele = fenetre.bouton_menu.get_menu_model()
    resultat = []
    for i in range(modele.get_n_items()):
        titre = modele.get_item_attribute_value(i, "label", None).get_string()
        sous_menu = modele.get_item_link(i, "submenu")
        lignes = []
        if sous_menu is not None:
            for j in range(sous_menu.get_n_items()):
                section = sous_menu.get_item_link(j, "section")
                if section is None:
                    continue
                for k in range(section.get_n_items()):
                    ligne = section.get_item_attribute_value(k, "label", None)
                    if ligne is not None:
                        lignes.append(ligne.get_string())
        resultat.append((titre, lignes))
    return resultat


def test_outil_dans_le_menu_et_l_action(app_outil, pomper):
    """L'outil démo apparaît dans le sous-menu Tools et son action l'active."""
    app, _ = app_outil
    fenetre = app.get_active_window()
    libelles = _libelles_menu(fenetre)
    sous_menus = [titre for titre, _ in libelles]
    assert "Tools" in sous_menus  # msgid, non traduit dans le modèle
    outils = dict(libelles)["Tools"]
    assert "Demo Tool…" in outils
    fenetre.activate_action("win.outil-outil-demo", None)
    pomper()
    plugin = fenetre.registre.outil("outil-demo")
    assert plugin.activations == 1
    assert fenetre.onglets.get_n_pages() == 2  # accueil + outil
    contenu = fenetre.onglets.get_nth_page(1)
    assert isinstance(contenu, Gtk.Label) and contenu.get_label() == "outil:demo"
    assert fenetre.titres_onglets[contenu] == "outil:demo"


def test_outil_fragile_notifie_sans_fermer(app_outil, pomper):
    """L'échec d'activate() est notifié ; la fenêtre reste utilisable."""
    app, _ = app_outil
    fenetre = app.get_active_window()
    messages = []
    fenetre.notifier = messages.append
    fenetre.activate_action("win.outil-outil-fragile", None)
    pomper()
    assert len(messages) == 1 and "outil-fragile" in messages[0]
    # La fenêtre et l'autre outil restent utilisables.
    fenetre.activate_action("win.outil-outil-demo", None)
    pomper()
    assert fenetre.registre.outil("outil-demo").activations == 1
    assert fenetre.onglets.get_n_pages() == 2


def test_sans_outil_pas_de_sous_menu_tools(app_fixture):
    """Sans outil, le menu principal n'a pas de sous-menu Tools."""
    app, _ = app_fixture
    fenetre = app.get_active_window()
    assert "Tools" not in [titre for titre, _ in _libelles_menu(fenetre)]
