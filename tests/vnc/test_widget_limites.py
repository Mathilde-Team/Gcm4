# SPDX-License-Identifier: GPL-3.0-or-later
"""Interactions natives du widget VNC et branches de transport mesurées dans le job GTK4."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from types import SimpleNamespace

import numpy as np
import pytest
from gi.repository import Gtk

from plugins.pluginvnc2.gtk4 import charger_widget


@pytest.fixture
def module(monkeypatch):
    """Garde les vrais widgets, mais ne démarre pas de connexion implicite."""
    resultat = charger_widget()
    monkeypatch.setattr(resultat.VncDisplay, "start", lambda _s: None)
    return resultat


@pytest.fixture
def onglet(module):
    """Onglet non rattaché, fermé même en cas d'échec d'une assertion."""
    resultat = module.VncTab(module.VncConnectionInfo(name="bureau", host="localhost"))
    yield resultat
    resultat.close()


def test_relacher_holds_valides_et_oublier_cle(onglet):
    """Relâche effectivement les contextes d'entrée ; le pinning reste facultatif."""
    affichage = onglet.display
    sorties = []
    affichage._active_mouse_holds[1] = SimpleNamespace(
        __exit__=lambda *_a: sorties.append("souris")
    )
    affichage._active_key_holds[42] = SimpleNamespace(
        __exit__=lambda *_a: sorties.append("clavier")
    )
    affichage._release_all_mouse_holds()
    affichage._release_all_key_holds()
    assert sorties == ["souris", "clavier"]
    assert affichage._active_mouse_holds == affichage._active_key_holds == {}
    oublis = []
    affichage.host_key_store = SimpleNamespace(forget=lambda *a: oublis.append(a))
    affichage.forget_host_key()
    affichage.info.pin_host_key = False
    affichage.forget_host_key()
    assert oublis == [("localhost", 5900)]


def test_annonces_et_recadrage_multi_ecran(onglet):
    """Les changements sont émis une fois ; un écran retiré rend le framebuffer entier."""
    affichage = onglet.display
    pixels = np.arange(4 * 8 * 4, dtype=np.uint8).reshape(4, 8, 4)
    ecran = SimpleNamespace(id=9, width=4, height=4, slices=(slice(0, 4), slice(4, 8)))
    curseur = SimpleNamespace(width=0, height=0)
    video = SimpleNamespace(
        width=8, height=4, screens=[ecran], cursor=curseur, as_rgba=lambda: pixels
    )
    affichage.client = SimpleNamespace(video=video)
    evenements = []
    for signal in ("vnc-size-changed", "vnc-screens-changed", "vnc-cursor-changed"):
        affichage.connect(signal, lambda *_a, nom=signal: evenements.append(nom))
    for _ in range(2):
        affichage._check_cursor_changed()
        affichage._check_size_changed()
        affichage._check_screens_changed()
    assert sorted(evenements) == sorted(
        ["vnc-size-changed", "vnc-screens-changed", "vnc-cursor-changed"]
    )
    assert affichage._apply_active_screen_crop(pixels) is pixels
    affichage.set_active_screen(9)
    assert np.array_equal(affichage._apply_active_screen_crop(pixels), pixels[:, 4:])
    affichage.set_active_screen(99)
    assert affichage._apply_active_screen_crop(pixels) is pixels
    affichage.client = None
    affichage.set_active_screen(None)
    assert affichage._active_screen_id is None


@pytest.mark.parametrize("continu", [False, True])
def test_boucle_lecture_tous_types_et_erreur(onglet, module, monkeypatch, continu):
    """Vidéo/clipboard/bell sont distribués ; un type interne n'est pas traité deux fois."""
    affichage = onglet.display
    evenements = []
    rafraichissements = []
    annonces = iter(
        [
            module.asyncvnc2.UpdateType.VIDEO,
            module.asyncvnc2.UpdateType.CLIPBOARD,
            module.asyncvnc2.UpdateType.BELL,
            object(),
        ]
    )

    async def lire():
        """Fin contrôlée après les quatre catégories de mise à jour."""
        try:
            return next(annonces)
        except StopIteration as exc:
            raise EOFError("fin distante") from exc

    affichage.client = SimpleNamespace(
        continuous_updates_active=continu,
        read=lire,
        video=SimpleNamespace(refresh=lambda: rafraichissements.append(True)),
    )
    for methode in ("_handle_video_update", "_handle_clipboard_update", "_handle_bell_update"):
        monkeypatch.setattr(affichage, methode, lambda nom=methode: evenements.append(nom))
    statuts = []
    monkeypatch.setattr(affichage, "_set_status", lambda *a: statuts.append(a))
    asyncio.run(affichage._read_loop())
    assert evenements == [
        "_handle_video_update",
        "_handle_clipboard_update",
        "_handle_bell_update",
    ]
    assert rafraichissements == ([] if continu else [True])
    assert statuts[0][0] == module.VncStatus.ERROR
    assert "fin distante" in statuts[0][1]


@pytest.mark.parametrize("continu", [False, True])
def test_mises_a_jour_video_clipboard_et_bell(onglet, monkeypatch, continu):
    """Une image est réellement peinte ; ContinuousUpdates ne redemande pas de refresh."""
    affichage = onglet.display
    rafraichissements = []
    affichage.client = SimpleNamespace(
        video=SimpleNamespace(
            width=2,
            height=2,
            screens=[],
            cursor=None,
            as_rgba=lambda: np.zeros((2, 2, 4), dtype=np.uint8),
            refresh=lambda: rafraichissements.append(True),
        ),
        continuous_updates_active=continu,
        clipboard=SimpleNamespace(text="texte distant"),
    )
    affichage._handle_video_update()
    assert affichage._last_frame_texture.get_width() == 2
    assert rafraichissements == ([] if continu else [True])
    ecritures = []
    monkeypatch.setattr(affichage, "_set_system_clipboard", ecritures.append)
    affichage._handle_clipboard_update()
    affichage.info.sync_clipboard = False
    affichage._handle_clipboard_update()
    assert ecritures == ["texte distant"]
    bips = []
    monkeypatch.setattr(
        affichage, "get_display", lambda: SimpleNamespace(beep=lambda: bips.append(True))
    )
    affichage._handle_bell_update()
    monkeypatch.setattr(affichage, "get_display", lambda: None)
    affichage._handle_bell_update()
    assert bips == [True]
    affichage.client = None


def test_clipboard_realisation_unique_et_absence_ecran(onglet, monkeypatch):
    """Pas de double abonnement à realize, ni d'accès au clipboard sans display."""
    affichage = onglet.display
    abonnements = []
    contenus = []
    presse_papier = SimpleNamespace(
        connect=lambda *a: abonnements.append(a), set_content=contenus.append
    )
    monkeypatch.setattr(affichage, "get_display", lambda: None)
    affichage._watch_local_clipboard()
    affichage._set_system_clipboard("texte")
    monkeypatch.setattr(
        affichage, "get_display", lambda: SimpleNamespace(get_clipboard=lambda: presse_papier)
    )
    affichage._watch_local_clipboard()
    affichage._watch_local_clipboard()
    assert len(abonnements) == 1 and abonnements[0][0] == "changed"
    affichage._set_system_clipboard("")
    affichage._set_system_clipboard("texte")
    assert len(contenus) == 1


def test_selection_ecran_et_retrait(onglet):
    """Le menu suit l'écran actif ; sa disparition restaure le bureau complet."""
    ecrans = [
        SimpleNamespace(id=1, width=100, height=50),
        SimpleNamespace(id=2, width=120, height=60),
    ]
    onglet.display._active_screen_id = 2
    onglet._on_screens_changed(onglet.display, ecrans)
    assert onglet.screen_dropdown.get_visible()
    assert onglet.screen_dropdown.get_selected() == 2
    onglet.screen_dropdown.set_selected(1)
    assert onglet.display._active_screen_id == 1
    onglet._on_screen_selected(SimpleNamespace(get_selected=lambda: 999))
    assert onglet.display._active_screen_id is None
    onglet.display._active_screen_id = 1
    onglet._on_screens_changed(onglet.display, [])
    assert not onglet.screen_dropdown.get_visible()
    assert onglet.display._active_screen_id is None


def test_curseur_visible_mouvement_et_retrait(onglet):
    """Le curseur reçu a une texture et suit le pointeur ; le serveur peut le masquer."""
    curseur = SimpleNamespace(
        width=3, height=2, x=1, y=1, data=np.zeros((2, 3, 4), dtype=np.uint8)
    )
    onglet._on_display_motion(None, 8, 9)
    onglet._on_cursor_changed(onglet.display, curseur)
    assert onglet.cursor_picture.get_visible()
    assert onglet.cursor_picture.get_paintable().get_width() == 3
    onglet._on_display_motion(None, 20, 30)
    assert onglet.cursor_picture.get_margin_start() == 19
    assert onglet.cursor_picture.get_margin_top() == 29
    onglet._on_cursor_changed(onglet.display, None)
    assert not onglet.cursor_picture.get_visible()


def test_taille_fenetre_et_plein_ecran(onglet):
    """Sans racine, les actions sont sûres ; avec fenêtre, les dimensions sont appliquées."""
    onglet._resize_window_to_remote()
    onglet._toggle_fullscreen()
    onglet._last_remote_size = (640, 480)
    onglet._resize_window_to_remote()
    fenetre = Gtk.Window()
    fenetre.set_child(onglet)
    try:
        onglet._resize_window_to_remote()
        assert fenetre.get_default_size() == (640, 480)
        onglet._toggle_fullscreen()
        assert onglet._is_fullscreen
        onglet._toggle_fullscreen()
        assert not onglet._is_fullscreen
    finally:
        fenetre.set_child(None)
        fenetre.destroy()


def test_statuts_erreur_deconnexion_et_continu_reconnexion(onglet, module, monkeypatch):
    """Les boutons suivent les statuts et le mode continu est réappliqué à reconnexion."""
    for statut in (module.VncStatus.ERROR, module.VncStatus.DISCONNECTED):
        onglet._on_status_changed(onglet.display, statut.value, "information")
        assert not onglet.btn_screenshot.get_sensitive()
        assert onglet.placeholder.get_visible()
    continus = []
    monkeypatch.setattr(onglet.display, "set_continuous_updates", continus.append)
    onglet._on_continuous_toggled(SimpleNamespace(get_active=lambda: True))
    onglet._on_connected(onglet.display)
    assert continus == [True, True]
    assert onglet.btn_screenshot.get_sensitive()


def test_entrees_absentes_repetition_relachement_et_scroll_nul(onglet, monkeypatch):
    """Les touches tenues ne se répètent pas ; relâcher une entrée inconnue est sans effet."""
    affichage = onglet.display
    assert affichage._on_key_pressed(None, 0, 1, 0)
    affichage._on_motion(None, 0, 0)
    affichage._on_button_pressed(None, 1, 0, 0)
    affichage._on_button_released(None, 1, 0, 0)
    affichage.set_continuous_updates(True)
    actions = []

    @contextmanager
    def tenir(*touches):
        """Observe chaque véritable appui et chaque relâchement de contexte."""
        actions.append(("appui", touches))
        yield
        actions.append(("relâchement", touches))

    affichage.client = SimpleNamespace(
        keyboard=SimpleNamespace(hold=tenir),
        mouse=SimpleNamespace(move=lambda *_a: None, hold=tenir),
        video=SimpleNamespace(width=0, height=0),
    )
    assert affichage.get_display_transform() is None
    monkeypatch.setattr(affichage, "_resolve_vnc_key_name", lambda _v: None)
    assert affichage._on_key_pressed(None, 0, 1, 0)
    assert actions == []
    monkeypatch.setattr(affichage, "_resolve_vnc_key_name", lambda _v: "a")
    affichage._on_key_pressed(None, 0, 1, 0)
    affichage._on_key_pressed(None, 0, 1, 0)
    affichage._on_key_released(None, 0, 1, 0)
    affichage._on_key_released(None, 0, 1, 0)
    assert actions == [("appui", ("a",)), ("relâchement", ("a",))]
    gesture = SimpleNamespace(get_current_button=lambda: 1)
    monkeypatch.setattr(affichage, "widget_to_remote", lambda *_a: (0, 0))
    affichage._on_button_pressed(gesture, 1, 0, 0)
    affichage._on_button_released(gesture, 1, 0, 0)
    affichage._on_button_released(gesture, 1, 0, 0)
    assert actions[-2:] == [("appui", (0,)), ("relâchement", (0,))]
    assert affichage._on_scroll(None, 1, 0)
    assert affichage._active_key_holds == affichage._active_mouse_holds == {}
    affichage.client = None


def test_cad_planifie_une_seule_coroutine(onglet, module, monkeypatch):
    """Le mode contrôle conserve Ctrl+Alt+Suppr ; le transport n'est pas lancé ici."""
    affichage = onglet.display
    affichage.client = SimpleNamespace()
    taches = []
    monkeypatch.setattr(module.asyncio, "ensure_future", taches.append)
    affichage.send_ctrl_alt_del()
    assert len(taches) == 1
    taches[0].close()
    affichage.client = None


@pytest.mark.parametrize("partage", [False, True])
def test_ancien_client_ne_contourne_pas_partage(onglet, module, monkeypatch, partage):
    """Sans shared=, seul le défaut partagé historique autorise un repli."""
    affichage = onglet.display
    affichage.info.shared = partage
    appels = []

    def ouvrir(*_a, **options):
        """Simule l'ancienne signature, puis un refus de transport sans connexion effective."""
        appels.append(options)
        if "shared" in options:
            raise TypeError("shared non reconnu")
        raise OSError("transport indisponible")

    monkeypatch.setattr(module.asyncvnc2, "connect", ouvrir)
    asyncio.run(affichage._connect_and_run())
    assert len(appels) == (2 if partage else 1)
    assert affichage.status is module.VncStatus.ERROR
    assert appels[0]["shared"] is partage
    if partage:
        assert "shared" not in appels[1]


@pytest.mark.parametrize("cle", [None, b"cle-test"])
def test_authentification_refusee_avec_et_sans_pinning(onglet, module, monkeypatch, cle):
    """Le message de refus distingue une clé déjà enregistrée ; aucun secret n'est demandé."""
    affichage = onglet.display
    affichage.host_key_store = SimpleNamespace(get=lambda *_a: cle)
    etats = []
    affichage.connect("vnc-status-changed", lambda _s, *a: etats.append(a))

    def refuser(*_a, **_options):
        """Refus d'authentification avant toute création de contexte réseau."""
        raise PermissionError("refus de test")

    monkeypatch.setattr(module.asyncvnc2, "connect", refuser)
    asyncio.run(affichage._connect_and_run())
    assert affichage.status is module.VncStatus.ERROR
    assert ("clé enregistrée" in etats[-1][1]) is (cle is not None)


def test_statut_inconnu_ne_desactive_pas_les_actions(onglet):
    """Une extension de statut conserve les contrôles sans inventer de transition."""
    onglet.btn_screenshot.set_sensitive(True)
    onglet._on_status_changed(onglet.display, "futur", "information")
    assert onglet.btn_screenshot.get_sensitive()
    assert "information" in onglet.status_label.get_label()


def descendants(widget):
    """Parcourt de vrais contrôles GTK sans dépendre de leur position dans les boîtes."""
    yield widget
    enfant = widget.get_first_child()
    while enfant is not None:
        yield from descendants(enfant)
        enfant = enfant.get_next_sibling()


@pytest.mark.parametrize("avec_parent", [False, True])
def test_dialogues_confirmation_et_envoi_reels(onglet, monkeypatch, avec_parent):
    """Le nom exact autorise l'oubli ; l'envoi vide, répété et confirmé garde ses règles."""
    racine = Gtk.Window() if avec_parent else None
    if racine is not None:
        racine.set_child(onglet)
    oublis = []
    redemarrages = []
    textes = []
    alimentations = []
    monkeypatch.setattr(onglet.display, "forget_host_key", lambda: oublis.append(True))
    monkeypatch.setattr(onglet.display, "reconnect", lambda: redemarrages.append(True))
    monkeypatch.setattr(onglet.display, "send_text", textes.append)
    monkeypatch.setattr(onglet.display, "send_power_action", alimentations.append)
    dialogues = []

    def ouvrir(methode, *args):
        """Retrouve uniquement la fenêtre créée par l'action courante."""
        avant = set(Gtk.Window.list_toplevels())
        methode(*args)
        nouvelles = set(Gtk.Window.list_toplevels()) - avant
        assert len(nouvelles) == 1
        dialogue = nouvelles.pop()
        dialogues.append(dialogue)
        assert dialogue.get_transient_for() is racine
        return dialogue

    try:
        dialogue = ouvrir(onglet._show_forget_key_confirmation_dialog)
        entree = next(w for w in descendants(dialogue) if isinstance(w, Gtk.Entry))
        confirmer = next(
            w
            for w in descendants(dialogue)
            if isinstance(w, Gtk.Button) and w.has_css_class("destructive-action")
        )
        assert not confirmer.get_sensitive()
        entree.set_text("autre-hote")
        entree.emit("activate")
        assert oublis == [] and redemarrages == [] and dialogue.get_visible()
        entree.set_text("localhost")
        assert confirmer.get_sensitive()
        entree.emit("activate")
        assert oublis == redemarrages == [True]

        dialogue = ouvrir(onglet._show_send_text_dialog)
        entree = next(w for w in descendants(dialogue) if isinstance(w, Gtk.Entry))
        garder = next(w for w in descendants(dialogue) if isinstance(w, Gtk.CheckButton))
        entree.emit("activate")
        assert textes == [] and dialogue.get_visible()
        garder.set_active(True)
        entree.set_text("premier")
        entree.emit("activate")
        assert textes == ["premier"] and dialogue.get_visible()
        garder.set_active(False)
        entree.set_text("second")
        entree.emit("activate")
        assert textes == ["premier", "second"]

        dialogue = ouvrir(onglet._show_power_confirmation_dialog, "reboot")
        confirmer = next(
            w
            for w in descendants(dialogue)
            if isinstance(w, Gtk.Button) and w.has_css_class("destructive-action")
        )
        assert alimentations == []
        confirmer.emit("clicked")
        assert alimentations == ["reboot"]
    finally:
        for dialogue in dialogues:
            dialogue.destroy()
        if racine is not None:
            racine.set_child(None)
            racine.destroy()
