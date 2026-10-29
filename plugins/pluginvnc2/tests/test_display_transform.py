# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests pour get_display_transform() / widget_to_remote() (VncDisplay).

Contrairement aux autres fichiers de tests "logique pure" du paquet, ceux-ci
instancient un VRAI VncDisplay (via `conftest.make_test_display`) plutôt que
de n'appeler que des méthodes statiques -- c'était l'un des points non
couverts par le stub initial (cf. `docs/sessions/session-01.md`). Voir la
docstring de `make_test_display` dans `conftest.py` pour le
détail de ce qui est substitué (taille allouée, taille distante, client).

Ce que ça NE teste PAS : le rendu réel (`Gdk.MemoryTexture`, letterboxing
visuel) ni les événements GTK -- juste la conversion de coordonnées, qui est
la partie où une régression serait la plus facile à introduire sans s'en
apercevoir (cf. les pièges listés dans CLAUDE.md à ce sujet).
"""

from __future__ import annotations

from collections import namedtuple

from conftest import GTK4_AVAILABLE, make_test_display, requires_gtk4

if GTK4_AVAILABLE:
    from gi.repository import Gtk

# Screen minimal : juste les champs lus par _find_active_screen()/
# widget_to_remote() (id, x, y, width, height) -- pas besoin du vrai
# asyncvnc2.Screen (qui porte aussi .slices, non utilisé ici).
Screen = namedtuple("Screen", "id x y width height")


@requires_gtk4
def test_returns_none_without_client():
    display = make_test_display(remote_w=1024, remote_h=768, alloc_w=512, alloc_h=384)
    display.client = None

    assert display.get_display_transform() is None


@requires_gtk4
def test_returns_none_before_first_allocation():
    # alloc_w/alloc_h <= 0 : widget pas encore posé/dimensionné par GTK.
    display = make_test_display(remote_w=1024, remote_h=768, alloc_w=0, alloc_h=0)

    assert display.get_display_transform() is None


@requires_gtk4
def test_contain_fit_uniform_scale_with_letterboxing():
    # Conteneur carré (512x512) plus petit que le distant (1024x768) dans
    # les deux dimensions -- CONTAIN doit choisir la plus petite échelle
    # (ici la largeur) et centrer verticalement (letterboxing haut/bas).
    display = make_test_display(
        remote_w=1024,
        remote_h=768,
        alloc_w=512,
        alloc_h=512,
        content_fit=Gtk.ContentFit.CONTAIN,
    )

    scale_x, scale_y, offset_x, offset_y = display.get_display_transform()

    assert scale_x == scale_y == 0.5
    assert offset_x == 0.0
    assert offset_y == 64.0  # (512 - 768*0.5) / 2


@requires_gtk4
def test_fill_fit_uses_independent_scales_and_no_offset():
    display = make_test_display(
        remote_w=1024,
        remote_h=768,
        alloc_w=800,
        alloc_h=400,
        content_fit=Gtk.ContentFit.FILL,
    )

    scale_x, scale_y, offset_x, offset_y = display.get_display_transform()

    assert scale_x == 800 / 1024
    assert scale_y == 400 / 768
    assert offset_x == 0.0
    assert offset_y == 0.0


@requires_gtk4
def test_widget_to_remote_round_trips_through_contain_letterboxing():
    # Même configuration que test_contain_fit_uniform_scale_with_letterboxing
    # (scale=0.5, offset=(0, 64)) : un point widget doit retomber sur les
    # coordonnées distantes attendues, décalage de letterboxing déduit.
    display = make_test_display(
        remote_w=1024,
        remote_h=768,
        alloc_w=512,
        alloc_h=512,
        content_fit=Gtk.ContentFit.CONTAIN,
    )

    assert display.widget_to_remote(256, 160) == (512, 192)


@requires_gtk4
def test_widget_to_remote_falls_back_to_identity_without_transform():
    # Pas de client -> get_display_transform() renvoie None -> repli sur
    # les coordonnées widget telles quelles (cf. widget_to_remote), pas de
    # crash.
    display = make_test_display(remote_w=1024, remote_h=768, alloc_w=512, alloc_h=384)
    display.client = None

    assert display.widget_to_remote(123.7, 45.2) == (123, 45)


@requires_gtk4
def test_widget_to_remote_adds_active_screen_offset():
    # Deuxième écran (ExtendedDesktopSize) placé à droite du premier dans
    # le framebuffer distant complet. Le crop affiché est local à cet
    # écran (1024x768), mais mouse.move() attend des coordonnées absolues
    # -- widget_to_remote() doit donc rajouter le décalage (screen.x, screen.y).
    screen = Screen(id=2, x=1024, y=0, width=1024, height=768)
    display = make_test_display(
        remote_w=2048,  # sans effet ici : un écran actif court-circuite client.video.*
        remote_h=768,
        alloc_w=512,
        alloc_h=384,  # exactement la moitié de 1024x768 dans les deux dimensions
        content_fit=Gtk.ContentFit.CONTAIN,
        screens=[screen],
        active_screen_id=2,
    )

    # Local à l'écran (avant décalage) : (100, 50) / 0.5 = (200, 100).
    # Absolu attendu : + (screen.x=1024, screen.y=0).
    assert display.widget_to_remote(100, 50) == (1224, 100)


@requires_gtk4
def test_get_display_transform_falls_back_to_full_desktop_when_active_screen_vanished():
    # L'écran choisi a disparu (moniteur déconnecté côté serveur) --
    # _apply_active_screen_crop() retombe déjà sur le framebuffer COMPLET
    # (pas de crop) dans ce cas ; get_display_transform() doit calculer sa
    # transformation sur ce même framebuffer complet plutôt que de
    # renvoyer None, sous peine de continuer à convertir les coordonnées
    # comme si un crop était encore actif alors que l'image affichée ne
    # l'est plus (bug réel corrigé le 2026-09-04 -- avant ce correctif,
    # cette fonction renvoyait None ici, ce que ce test vérifiait
    # explicitement comme "attendu").
    display = make_test_display(
        remote_w=1024,
        remote_h=768,
        alloc_w=512,
        alloc_h=384,  # exactement la moitié de 1024x768 dans les deux dimensions
        content_fit=Gtk.ContentFit.CONTAIN,
        screens=[],  # écran actif référencé mais absent de la liste courante
        active_screen_id=99,
    )

    assert display.get_display_transform() == (0.5, 0.5, 0.0, 0.0)


@requires_gtk4
def test_widget_to_remote_uses_full_desktop_scale_without_offset_when_active_screen_vanished():
    # Même configuration que le test précédent : les coordonnées doivent
    # être mises à l'échelle du bureau complet, SANS décalage d'écran
    # (puisqu'aucun crop n'est réellement appliqué une fois l'écran
    # disparu -- cf. _apply_active_screen_crop).
    display = make_test_display(
        remote_w=1024,
        remote_h=768,
        alloc_w=512,
        alloc_h=384,
        content_fit=Gtk.ContentFit.CONTAIN,
        screens=[],
        active_screen_id=99,
    )

    assert display.widget_to_remote(100, 50) == (200, 100)  # (100, 50) / 0.5
