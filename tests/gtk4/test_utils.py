# SPDX-License-Identifier: GPL-3.0-or-later
"""``utils.py`` (racine) : helpers GTK et ``GCMBase`` (issue #293, palier 5).

Ces helpers exigent un vrai GTK sous écran (``Gtk.Dialog``,
``Gtk.FileChooserDialog``, ``Gtk.MessageDialog``), d'où leur place dans
``tests/gtk4/`` : le job Qualité, sans PyGObject, ne peut pas les importer.
``run_dialog_sync`` est monkeypatché là où l'on teste l'appelant
(``show_open_dialog``, ``msgbox``) et exercé pour de vrai par ailleurs.
"""

from __future__ import annotations

import base64
import builtins
import gc
import importlib
import sys
import types
import weakref
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[2]
if str(RACINE) not in sys.path:
    sys.path.insert(0, str(RACINE))

from gi.repository import Gio, GLib, Gtk  # noqa: E402

import utils  # noqa: E402

# PNG 1×1 transparent minimal : icône valide pour set_icon_from_file.
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
    "AAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


class Ecran(utils.GCMBase):
    """Écran minimal : GCMBase sans autre responsabilité."""


def _apres_boucle(fonction):
    """Planifie l'exécution de fonction dans la boucle GTK principale."""
    GLib.idle_add(fonction)


# ---- run_dialog_sync ----


def test_run_dialog_sync_reponse():
    """Un dialogue répondu renvoie la réponse obtenue."""
    dialogue = Gtk.Dialog()
    dialogue.set_default_size(200, 100)

    def repondre():
        dialogue.emit("response", Gtk.ResponseType.OK)
        return GLib.SOURCE_REMOVE

    _apres_boucle(repondre)
    assert utils.run_dialog_sync(dialogue) == Gtk.ResponseType.OK
    dialogue.destroy()


def test_run_dialog_sync_fermeture_sans_reponse():
    """Un dialogue détruit sans réponse renvoie NONE (filet de sécurité).

    En GTK4, le signal ``destroy`` n'est émis qu'à la finalisation : on
    l'émet directement, comme le ferait la dernière libération de référence
    pendant que la boucle tourne.
    """
    dialogue = Gtk.Dialog()
    dialogue.set_default_size(200, 100)

    def fermer():
        dialogue.emit("destroy")
        return GLib.SOURCE_REMOVE

    _apres_boucle(fermer)
    assert utils.run_dialog_sync(dialogue) == Gtk.ResponseType.NONE


def test_run_dialog_sync_destruction_apres_reponse():
    """Réponse puis destruction : la déconnexion des handlers est sautée."""
    dialogue = Gtk.Dialog()
    dialogue.set_default_size(200, 100)

    def repondre_puis_detruire():
        dialogue.emit("response", Gtk.ResponseType.CANCEL)
        dialogue.emit("destroy")
        return GLib.SOURCE_REMOVE

    _apres_boucle(repondre_puis_detruire)
    assert utils.run_dialog_sync(dialogue) == Gtk.ResponseType.CANCEL


# ---- show_open_dialog ----


def _faux_run_avec_fichier(chemin):
    """Stub de ``run_dialog_sync`` : sélectionne ``chemin`` puis renvoie OK.

    La sélection d'un ``Gtk.FileChooser`` s'applique en asynchrone (requête
    GIO) : on laisse la boucle GTK tourner un instant, comme le fait le vrai
    ``run_dialog_sync`` avec sa ``MainLoop``.
    """

    def faux_run(dialogue):
        dialogue.show()
        # Le chargement du dossier courant est asynchrone : le laisser
        # terminer avant de sélectionner, sinon la sélection serait
        # écrasée ; puis laisser la sélection s'appliquer pareil.
        boucle = GLib.MainLoop()
        GLib.timeout_add(150, boucle.quit)
        boucle.run()
        dialogue.set_file(Gio.File.new_for_path(str(chemin)))
        boucle = GLib.MainLoop()
        GLib.timeout_add(150, boucle.quit)
        boucle.run()
        return Gtk.ResponseType.OK

    return faux_run


def test_show_open_dialog_choix(tmp_path, monkeypatch):
    """Réponse OK : chemin sélectionné renvoyé, lastPath retenu sur le porteur."""
    fichier = tmp_path / "choix.txt"
    fichier.write_text("contenu", encoding="utf-8")

    monkeypatch.setattr(utils, "run_dialog_sync", _faux_run_avec_fichier(fichier))
    porteur = types.SimpleNamespace()

    resultat = utils.show_open_dialog(None, "Ouvrir", Gtk.FileChooserAction.OPEN, porteur)

    assert resultat == str(fichier)
    assert porteur.lastPath == str(tmp_path)


def test_show_open_dialog_annule(monkeypatch):
    """Réponse annulée : None renvoyé, aucun chemin retenu."""
    monkeypatch.setattr(utils, "run_dialog_sync", lambda _dialogue: Gtk.ResponseType.CANCEL)

    resultat = utils.show_open_dialog(None, "Ouvrir", Gtk.FileChooserAction.OPEN)

    assert resultat is None


def test_show_open_dialog_porteur_deja_persiste(tmp_path, monkeypatch):
    """Un porteur qui a déjà lastPath le conserve puis le met à jour."""
    sous = tmp_path / "sous"
    sous.mkdir()
    fichier = sous / "choix.txt"
    fichier.write_text("contenu", encoding="utf-8")

    monkeypatch.setattr(utils, "run_dialog_sync", _faux_run_avec_fichier(fichier))
    porteur = types.SimpleNamespace()
    porteur.lastPath = str(tmp_path)

    resultat = utils.show_open_dialog(None, "Ouvrir", Gtk.FileChooserAction.OPEN, porteur)

    assert resultat == str(fichier)
    assert porteur.lastPath == str(sous)


def test_show_open_dialog_sauvegarde(tmp_path, monkeypatch):
    """Action SAVE : le bouton proposé est « Save », le choix fonctionne pareil."""
    fichier = tmp_path / "sauve.txt"
    fichier.write_text("contenu", encoding="utf-8")

    monkeypatch.setattr(utils, "run_dialog_sync", _faux_run_avec_fichier(fichier))

    resultat = utils.show_open_dialog(None, "Enregistrer", Gtk.FileChooserAction.SAVE)

    assert resultat == str(fichier)


# ---- msgbox ----


def test_msgbox_sans_icone(monkeypatch):
    """Sans icône : un MessageDialog modal, détruit après fermeture."""
    dialogues = []

    def faux_run(dialogue):
        dialogues.append(dialogue)
        return Gtk.ResponseType.OK

    monkeypatch.setattr(utils, "run_dialog_sync", faux_run)

    utils.msgbox("message de test")

    assert len(dialogues) == 1
    assert isinstance(dialogues[0], Gtk.MessageDialog)


def test_msgbox_icone_introuvable(tmp_path, monkeypatch):
    """Icône absente : l'erreur GLib est journalisée, le dialogue s'affiche quand même."""
    monkeypatch.setattr(utils, "run_dialog_sync", lambda _dialogue: Gtk.ResponseType.OK)

    utils.msgbox("message de test", icon_path=str(tmp_path / "icone-absente.png"))


def test_msgbox_icone_valide(tmp_path, monkeypatch):
    """Icône présente : elle est posée sur la fenêtre sans erreur."""
    icone = tmp_path / "icone.png"
    icone.write_bytes(PNG_1X1)
    monkeypatch.setattr(utils, "run_dialog_sync", lambda _dialogue: Gtk.ResponseType.OK)

    utils.msgbox("message de test", icon_path=str(icone))


# ---- GCMBase ----


def test_gcm_base_attributs():
    """Kwargs weakref-ables en proxy, les autres en attribut direct."""

    class Boite:
        """Objet simple, weakref-able."""

    objet = Boite()
    ecran = Ecran(fenetre=objet, nombre=3)

    assert ecran.is_tab is False
    assert ecran.tab_key is None
    assert ecran.builder is None
    assert ecran.nombre == 3
    assert ecran.fenetre is not objet  # proxy weakref
    assert weakref.ref(objet)() is objet


def test_gcm_base_point_d_extension():
    """Les points d'extension par défaut ne font rien."""
    ecran = Ecran()

    assert ecran.on_tab_will_close() is None
    assert ecran.new() is None


def test_gcm_base_run_interrompu(monkeypatch):
    """Gtk.main interrompu au clavier : l'interruption est attrapée."""

    def main_interrompu():
        raise KeyboardInterrupt

    monkeypatch.setattr(utils, "Gtk", types.SimpleNamespace(main=main_interrompu))

    Ecran().run()


def test_request_close_onglet_epingle(monkeypatch):
    """Écran ouvert en onglet : la fermeture délègue à wMain.close_management_tab."""
    appels = []
    faux_module = types.ModuleType("gnome_connection_manager")

    class WMain:
        """Fenêtre principale factice."""

        @staticmethod
        def close_management_tab(cle):
            appels.append(cle)

    faux_module.wMain = WMain
    monkeypatch.setitem(sys.modules, "gnome_connection_manager", faux_module)
    ecran = Ecran()
    ecran.is_tab = True
    ecran.tab_key = "ma-cle"

    ecran.request_close()

    assert appels == ["ma-cle"]


def test_request_close_fenetre_autonome():
    """Écran autonome : la fermeture détruit son main_widget.

    L'assertion passe par la finalisation GTK4 : après ``destroy()``, le
    signal ``destroy`` n'est émis qu'une fois la dernière référence libérée.
    """
    fenetre = Gtk.Window()
    detruits = []
    fenetre.connect("destroy", lambda *_a: detruits.append(1))
    ecran = Ecran()
    ecran.main_widget = fenetre

    ecran.request_close()
    ecran.main_widget = None
    del fenetre
    gc.collect()

    assert detruits == [1]


def test_request_close_sans_widget():
    """Aucun main_widget : la fermeture ne fait rien, sans erreur."""
    ecran = Ecran()
    ecran.main_widget = None

    ecran.request_close()


# ---- embed_dialog_content ----


def test_embed_dialog_contenu_de_dialogue():
    """Racine Gtk.Dialog : la zone de contenu est détachée, la coquille détruite."""
    dialogue = Gtk.Dialog()
    dialogue.set_default_size(200, 100)
    ecran = Ecran()
    ecran.main_widget = dialogue
    detruit = []
    dialogue.connect("destroy", lambda *_a: detruit.append(1))

    contenu = utils.embed_dialog_content(ecran)

    assert contenu is not None
    assert contenu.get_parent() is None  # détaché
    assert ecran.main_widget is contenu
    del dialogue
    gc.collect()
    assert detruit == [1]


def test_embed_dialog_contenu_de_fenetre():
    """Racine Gtk.Window : l'unique enfant est détaché, la coquille détruite."""
    fenetre = Gtk.Window()
    enfant = Gtk.Box()
    fenetre.set_child(enfant)
    ecran = Ecran()
    ecran.main_widget = fenetre
    detruit = []
    fenetre.connect("destroy", lambda *_a: detruit.append(1))

    contenu = utils.embed_dialog_content(ecran)

    assert contenu is enfant
    assert ecran.main_widget is enfant
    del fenetre
    gc.collect()
    assert detruit == [1]


def test_embed_dialog_contenu_type_invalide():
    """Racine ni Dialog ni Window : TypeError explicite."""
    ecran = Ecran()
    ecran.main_widget = Gtk.Box()

    with pytest.raises(TypeError, match="Gtk.Dialog ou Gtk.Window"):
        utils.embed_dialog_content(ecran)


def test_embed_dialog_contenu_instance_est_la_racine():
    """L'instance EST la racine GTK : la coquille n'est pas détruite."""

    class EcranFenetre(utils.GCMBase, Gtk.Window):
        """Écran dont l'instance est elle-même la fenêtre racine."""

        def __init__(self):
            Gtk.Window.__init__(self)
            utils.GCMBase.__init__(self)
            self.main_widget = self
            self.set_child(Gtk.Box())

    ecran = EcranFenetre()

    contenu = utils.embed_dialog_content(ecran)

    assert ecran.main_widget is contenu
    assert ecran.get_child() is None  # contenu retiré, fenêtre toujours vivante


# ---- repli de traduction ----


def test_traduction_repli(monkeypatch):
    """Sans _ global : le repli no-op de utils renvoie la chaîne telle quelle."""
    monkeypatch.delattr(builtins, "_", raising=False)
    module = importlib.reload(utils)

    assert module._("chaîne inchangée") == "chaîne inchangée"
