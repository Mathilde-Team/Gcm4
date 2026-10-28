# SPDX-License-Identifier: GPL-3.0-or-later
"""Arbre des hôtes sous Xvfb : fixture du lot 2, couleurs, repliés, erreurs (#139)."""

from __future__ import annotations

import argparse
import configparser
import shutil
from pathlib import Path

from gi.repository import Adw, Gio

import gcm4.ui  # noqa: F401  (require_version avant gi.repository)
from gcm4.ui.application import Gcm4Application
from gcm4.ui.host_tree import HostTree

FIXTURE = Path(__file__).resolve().parents[1] / "data" / "config" / "aes"


def test_fixture_affichee_groupe_replie(app_fixture):
    """ARCHIVE, LAB (déplié, sous-groupe bases), PROD replié (collapsed-folders = 2)."""
    app, _ = app_fixture
    arbre = app.get_active_window().arbre
    assert isinstance(arbre, HostTree)
    assert arbre.lignes_visibles() == [
        (0, "ARCHIVE"),
        (0, "LAB"),
        (1, "LAB/bases"),
        (2, "LAB/bases/db"),
        (1, "LAB/web"),
        (0, "PROD"),
    ]
    assert not arbre.ligne("ARCHIVE").is_expandable()  # groupe vide : pas de flèche


def test_deplier_puis_fermer_enregistre_au_format_gtk3(app_fixture, pomper):
    """Déplier PROD, replier LAB : collapsed-folders = « 1 » (index GTK3 de LAB), sous-groupe restauré."""
    app, dossier = app_fixture
    fenetre = app.get_active_window()
    arbre = fenetre.arbre
    arbre.ligne("PROD").set_expanded(True)
    pomper()
    assert (1, "PROD/eu") in arbre.lignes_visibles() and (
        2,
        "PROD/eu/api",
    ) in arbre.lignes_visibles()
    arbre.ligne("LAB").set_expanded(False)
    pomper()
    app.activate_action("quit", None)
    pomper()
    cp = configparser.RawConfigParser()
    cp.read(dossier / "gcm4.conf", encoding="utf-8")
    assert cp.get("window", "collapsed-folders") == "1"
    assert cp.get("plugin inconnu", "cle") == "valeur"


def test_sous_groupe_conserve_quand_le_parent_se_replie(app_fixture, pomper):
    """Replier LAB/bases puis LAB, redéplier LAB : LAB/bases est toujours replié, et l'inverse."""
    app, _ = app_fixture
    arbre = app.get_active_window().arbre
    arbre.ligne("LAB/bases").set_expanded(False)
    arbre.ligne("LAB").set_expanded(False)
    pomper()
    arbre.ligne("LAB").set_expanded(True)
    pomper()
    assert (2, "LAB/bases/db") not in arbre.lignes_visibles()
    arbre.ligne("LAB/bases").set_expanded(True)
    arbre.ligne("LAB").set_expanded(False)
    pomper()
    arbre.ligne("LAB").set_expanded(True)
    pomper()
    assert (2, "LAB/bases/db") in arbre.lignes_visibles()


def test_couleurs_et_infobulle(app_fixture, pomper):
    """Classes de couleur chargées ; libellé de LAB coloré ; info-bulle de l'hôte."""
    app, _ = app_fixture
    css = app.css_couleurs.to_string()
    assert ".gcm-couleur-ff8800" in css and ".gcm-couleur-00aaff" in css
    arbre = app.get_active_window().arbre
    libelles = _libelles(arbre.liste)
    assert "gcm-couleur-ff8800" in libelles["LAB"].get_css_classes()
    assert "gcm-couleur-ff8800" in libelles["web"].get_css_classes()
    infobulle = libelles["web"].get_parent().get_tooltip_markup()
    assert infobulle.startswith("<b>web</b>\nproto-b:u@10.0.0.1")
    assert "gcm-rayures" in arbre.liste.get_css_classes()


def _libelles(widget, resultat=None):
    """Libellés des lignes construites, par texte."""
    from gi.repository import Gtk

    resultat = {} if resultat is None else resultat
    enfant = widget.get_first_child()
    while enfant is not None:
        if isinstance(enfant, Gtk.Label) and enfant.get_text():
            resultat[enfant.get_text()] = enfant
        _libelles(enfant, resultat)
        enfant = enfant.get_next_sibling()
    return resultat


def test_sans_rayures(tmp_path, pomper):
    """disable-hosts-stripes = True : pas de classe gcm-rayures."""
    (tmp_path / "gcm4.conf").write_text(
        "[options]\ndisable-hosts-stripes = True\n", encoding="utf-8"
    )
    app = Gcm4Application(
        argparse.Namespace(config_dir=str(tmp_path), debug=False, hotes=[]),
        flags=Gio.ApplicationFlags.NON_UNIQUE,
    )
    app.register(None)
    app.activate()
    pomper()
    try:
        assert "gcm-rayures" not in app.get_active_window().arbre.liste.get_css_classes()
    finally:
        for fenetre in list(app.get_windows()):
            fenetre.destroy()
        pomper()


def test_configuration_illisible_jamais_ecrasee(tmp_path, pomper):
    """Clé .gcm4.key absente : message dans le panneau, aucun arbre, gcm4.conf intact après Ctrl+Q."""
    shutil.copy(FIXTURE / "gcm.conf", tmp_path / "gcm4.conf")
    avant = (tmp_path / "gcm4.conf").read_bytes()
    app = Gcm4Application(
        argparse.Namespace(config_dir=str(tmp_path), debug=False, hotes=[]),
        flags=Gio.ApplicationFlags.NON_UNIQUE,
    )
    app.register(None)
    app.activate()
    pomper()
    fenetre = app.get_active_window()
    assert fenetre.arbre is None and isinstance(fenetre.erreur_config, Adw.StatusPage)
    assert ".gcm4.key" in fenetre.erreur_config.get_description()
    app.activate_action("quit", None)
    pomper()
    # L'état de fenêtre est écrit (lecture INI correcte) ; les mots de passe et hôtes restent.
    apres = (tmp_path / "gcm4.conf").read_text(encoding="utf-8")
    for ligne in avant.decode("utf-8").splitlines():
        if ligne.startswith("pass = ") or ligne.startswith("name = "):
            assert ligne in apres


def test_icones_presentes_dans_le_theme(application):
    """Chaque icône de l'arbre et de la page d'accueil existe (sinon : icône cassée)."""
    from gi.repository import Gtk

    from gcm4.ui import host_tree

    theme = Gtk.IconTheme.get_for_display(application.get_active_window().get_display())
    accueil = application.get_active_window().page_accueil.get_icon_name()
    for nom in (host_tree.ICONE_GROUPE, host_tree.ICONE_HOTE, accueil):
        assert theme.has_icon(nom), nom
