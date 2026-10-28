# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests pour VncDisplay._on_button_pressed / _on_button_released (maintien
de bouton souris pour le drag, cf. _gdk_button_to_vnc).

Bug corrigé le 2026-09-06 : contrairement à `_on_key_pressed` (qui a un
garde anti-répétition explicite -- `if keycode in self._active_key_holds:
return True`), `_on_button_pressed` n'avait AUCUN garde équivalent. Or
`_gdk_button_to_vnc` traduit délibérément tout bouton GDK non reconnu
(boutons latéraux 8/9, etc.) vers 0 (clic gauche) -- deux boutons
PHYSIQUES différents peuvent donc se traduire vers le MÊME bouton VNC. Si
le second était pressé avant que le premier soit relâché,
`_active_mouse_holds[0]` était silencieusement écrasé par le second hold,
perdant toute référence au premier -- celui-ci n'était alors JAMAIS
relâché côté serveur (bouton "collé" au premier relâchement physique
reçu, quel qu'il soit, jusqu'à déconnexion/reconnexion).

Second bug corrigé le 2026-09-09, zone différente (relâchement, pas
appui) : contrairement à `_on_key_released` (qui avale déjà toute
exception de `hold_cm.__exit__()`), `_on_button_released` n'avait AUCUN
garde équivalent. Scénario réel : la connexion tombe pendant qu'un bouton
est physiquement maintenu (coupure réseau en plein drag) -- `_read_loop`
ne relâche pas les holds actifs sur exception, et `self.client` n'est
jamais remis à `None` après une erreur de lecture, donc rien n'empêche le
relâchement physique, ultérieur et normal, d'atteindre `__exit__()` sur un
hold dont le socket est déjà mort. Voir les tests dédiés plus bas.

Troisième bug corrigé le 2026-09-08, zone symétrique de la précédente mais
côté appui (pas relâchement) : `_on_button_pressed` n'avait aucun garde
contre une exception de `mouse.move()`/`hold_cm.__enter__()`, alors que le
même `self.client` non remis à `None` après une erreur de lecture rend ce
chemin tout aussi atteignable qu'`_on_button_released`. Voir les tests
dédiés plus bas.

Ces tests appellent les VRAIES méthodes de `VncDisplay` (pas une
réimplémentation de la logique) via `conftest.make_test_display`, en
substituant juste `client.mouse` par un faux mouse qui trace les holds
créés -- assez pour détecter un hold "perdu" (créé mais jamais sorti).
"""

from __future__ import annotations

from conftest import make_test_display, requires_gtk4


class FakeHold:
    """Faux gestionnaire de contexte renvoyé par mouse.hold(button) --
    trace juste s'il a été entré/sorti, pour vérifier qu'un hold "perdu"
    (créé puis jamais explicitement sorti) est détectable dans les tests."""

    def __init__(self, button):
        self.button = button
        self.entered = False
        self.exited = False

    def __enter__(self):
        self.entered = True
        return self

    def __exit__(self, *exc_info):
        self.exited = True
        return False


class FakeMouse:
    def __init__(self):
        self.holds_created = []
        self.moved_to = None

    def move(self, x, y):
        self.moved_to = (x, y)

    def hold(self, button):
        hold = FakeHold(button)
        self.holds_created.append(hold)
        return hold


class FakeGesture:
    """Remplace Gtk.GestureClick : seul get_current_button() est utilisé
    par _on_button_pressed/_on_button_released."""

    def __init__(self, button):
        self._button = button

    def get_current_button(self):
        return self._button


def _make_display_with_fake_mouse():
    display = make_test_display(remote_w=1024, remote_h=768, alloc_w=1024, alloc_h=768)
    display.client.mouse = FakeMouse()
    return display


@requires_gtk4
def test_single_button_press_and_release_holds_and_releases_once():
    display = _make_display_with_fake_mouse()
    gesture = FakeGesture(1)  # bouton gauche GDK -> bouton VNC 0

    display._on_button_pressed(gesture, 1, 10, 20)

    assert 0 in display._active_mouse_holds
    hold = display._active_mouse_holds[0]
    assert hold.entered and not hold.exited

    display._on_button_released(gesture, 1, 10, 20)

    assert 0 not in display._active_mouse_holds
    assert hold.exited


@requires_gtk4
def test_two_physical_buttons_mapping_to_same_vnc_button_do_not_leak_a_hold():
    # Bouton latéral (8, non mappé -> 0 par défaut, cf. _gdk_button_to_vnc)
    # tenu en premier, PUIS le vrai clic gauche (1 -> 0 aussi) pressé sans
    # relâcher le premier.
    display = _make_display_with_fake_mouse()
    side_button = FakeGesture(8)
    left_button = FakeGesture(1)

    display._on_button_pressed(side_button, 1, 10, 20)
    first_hold = display._active_mouse_holds[0]

    display._on_button_pressed(left_button, 1, 10, 20)

    # Le second appui, traduit vers le MÊME bouton VNC, ne doit PAS avoir
    # ouvert un second hold qui écraserait la référence au premier.
    assert display.client.mouse.holds_created == [first_hold]
    assert display._active_mouse_holds[0] is first_hold
    assert first_hold.entered and not first_hold.exited


@requires_gtk4
def test_releasing_after_ignored_duplicate_press_still_releases_original_hold():
    display = _make_display_with_fake_mouse()
    side_button = FakeGesture(8)
    left_button = FakeGesture(1)

    display._on_button_pressed(side_button, 1, 10, 20)
    first_hold = display._active_mouse_holds[0]
    display._on_button_pressed(left_button, 1, 10, 20)  # ignoré (collision)

    display._on_button_released(left_button, 1, 10, 20)

    assert first_hold.exited
    assert 0 not in display._active_mouse_holds


@requires_gtk4
def test_different_vnc_buttons_each_get_their_own_hold():
    # Cas normal (pas de collision) : bouton gauche et bouton droit tenus
    # simultanément -- deux holds distincts, indépendamment relâchables.
    display = _make_display_with_fake_mouse()
    left = FakeGesture(1)
    right = FakeGesture(3)

    display._on_button_pressed(left, 1, 10, 20)
    display._on_button_pressed(right, 1, 10, 20)

    assert set(display._active_mouse_holds) == {0, 2}
    assert len(display.client.mouse.holds_created) == 2

    display._on_button_released(left, 1, 10, 20)

    assert set(display._active_mouse_holds) == {2}


# ---------------------------------------------------------------------------
# _on_button_released doit survivre à une exception de hold_cm.__exit__() --
# symétrique du garde déjà présent dans _on_key_released (cf. le commentaire
# dans vnc_tab.py). Scénario réel : la connexion tombe PENDANT qu'un bouton
# est physiquement maintenu (ex. coupure réseau en plein drag). _read_loop
# ne relâche pas les holds actifs sur exception (seul stop() le fait), et
# self.client n'est jamais remis à None après une erreur de lecture -- donc
# ni l'un ni l'autre garde n'empêche le relâchement physique, ultérieur et
# normal, d'atteindre __exit__() sur un hold dont le socket est déjà mort.
# Bug réel trouvé et corrigé le 2026-09-09.
# ---------------------------------------------------------------------------


@requires_gtk4
def test_release_swallows_exception_from_dead_connection_hold_exit():
    display = _make_display_with_fake_mouse()
    gesture = FakeGesture(1)
    display._on_button_pressed(gesture, 1, 10, 20)
    hold = display._active_mouse_holds[0]

    def _boom(*_exc_info):
        raise OSError("connexion déjà fermée (simulé)")

    hold.__exit__ = _boom  # simule un socket déjà mort à l'appui de __exit__

    display._on_button_released(gesture, 1, 10, 20)  # ne doit pas lever


@requires_gtk4
def test_release_clears_the_hold_entry_even_when_exit_raises():
    display = _make_display_with_fake_mouse()
    gesture = FakeGesture(1)
    display._on_button_pressed(gesture, 1, 10, 20)
    hold = display._active_mouse_holds[0]
    hold.__exit__ = lambda *_exc_info: (_ for _ in ()).throw(OSError("simulé"))

    display._on_button_released(gesture, 1, 10, 20)

    # pop() a lieu avant l'appel à __exit__() dans _on_button_released :
    # l'entrée doit disparaître de _active_mouse_holds même si __exit__ lève,
    # sinon un futur appui sur ce même bouton VNC serait bloqué en
    # permanence par le garde anti-collision de _on_button_pressed.
    assert 0 not in display._active_mouse_holds


# ---------------------------------------------------------------------------
# _on_button_pressed doit survivre à une exception de mouse.move()/
# hold_cm.__enter__() -- symétrique du garde ci-dessus côté relâchement.
# Scénario réel : la connexion tombe juste avant un appui (self.client n'est
# jamais remis à None après une erreur de lecture -- seule une nouvelle
# connexion réussie le réaffecte), donc le garde `if not self.client:` en
# tête de la méthode ne protège pas ce chemin. Bug de la même famille que
# celui corrigé le 2026-09-09 dans _on_button_released.
# ---------------------------------------------------------------------------


@requires_gtk4
def test_press_swallows_exception_from_dead_connection_move():
    display = _make_display_with_fake_mouse()
    gesture = FakeGesture(1)

    def _boom(*_args):
        raise OSError("connexion déjà fermée (simulé)")

    display.client.mouse.move = _boom

    display._on_button_pressed(gesture, 1, 10, 20)  # ne doit pas lever

    assert 0 not in display._active_mouse_holds


@requires_gtk4
def test_press_swallows_exception_from_dead_connection_hold_enter():
    display = _make_display_with_fake_mouse()
    gesture = FakeGesture(1)

    def _boom(button):
        hold = FakeHold(button)
        hold.__enter__ = lambda: (_ for _ in ()).throw(OSError("simulé"))
        return hold

    display.client.mouse.hold = _boom

    display._on_button_pressed(gesture, 1, 10, 20)  # ne doit pas lever

    # Un hold dont __enter__() a levé ne doit pas rester référencé --
    # sinon un futur relâchement physique de ce bouton tenterait de sortir
    # un hold jamais réellement entré côté serveur.
    assert 0 not in display._active_mouse_holds


@requires_gtk4
def test_button_pressable_normally_after_a_failed_press():
    # Contre-épreuve fonctionnelle : après un appui raté (move() a levé et
    # a été avalé), un nouvel appui sur le même bouton doit se comporter
    # normalement -- pas d'état corrompu durable côté client.
    display = _make_display_with_fake_mouse()
    gesture = FakeGesture(1)
    real_move = display.client.mouse.move
    calls = {"n": 0}

    def _move_once_then_boom(x, y):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("simulé")
        real_move(x, y)

    display.client.mouse.move = _move_once_then_boom

    display._on_button_pressed(gesture, 1, 10, 20)  # échoue, avalé
    assert 0 not in display._active_mouse_holds

    display._on_button_pressed(gesture, 1, 10, 20)  # doit réussir normalement

    assert 0 in display._active_mouse_holds
    hold = display._active_mouse_holds[0]
    assert hold.entered and not hold.exited


@requires_gtk4
def test_button_is_pressable_again_normally_after_a_failed_release():
    # Contre-épreuve fonctionnelle : après le relâchement raté (exit levé
    # et avalé), un nouvel appui/relâchement sur le même bouton VNC doit se
    # comporter normalement -- pas d'état corrompu durable côté client.
    display = _make_display_with_fake_mouse()
    gesture = FakeGesture(1)
    display._on_button_pressed(gesture, 1, 10, 20)
    display._active_mouse_holds[0].__exit__ = lambda *_exc_info: (_ for _ in ()).throw(OSError("simulé"))
    display._on_button_released(gesture, 1, 10, 20)

    display._on_button_pressed(gesture, 1, 10, 20)

    assert 0 in display._active_mouse_holds
    new_hold = display._active_mouse_holds[0]
    assert new_hold.entered and not new_hold.exited

    display._on_button_released(gesture, 1, 10, 20)

    assert 0 not in display._active_mouse_holds
    assert new_hold.exited
