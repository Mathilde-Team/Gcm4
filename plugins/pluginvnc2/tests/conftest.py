# SPDX-License-Identifier: GPL-3.0-or-later
"""Fixtures partagées et détection GTK4.

vnc_tab.py est un fichier unique qui importe GTK4 au niveau module (choix
assumé pour simplifier l'installation, cf. CLAUDE.md). Consequence pour
les tests : TOUT le module échoue à l'import si PyGObject/GTK4 n'est pas
disponible -- y compris les tests qui ne touchent en réalité qu'à de la
logique pure (HostKeyStore, VncConnectionInfo). Ce conftest détecte GTK4
une fois ici et expose `requires_gtk4` pour sauter proprement les tests
concernés plutôt que de faire échouer toute la collecte pytest.

Dans un environnement sans display (CI headless sans Xvfb, sandbox de dev
sans PyGObject), attends-toi à voir ces tests marqués "skipped" -- ce
n'est pas un échec, c'est la détection qui fonctionne. Exécute la suite
dans un environnement GTK4 réel (poste de dev, ou CI avec `xvfb-run`)
pour un résultat pass/fail réel.
"""

import sys
import types
from pathlib import Path

import pytest

# Permet `from vnc_tab import ...` depuis les fichiers de tests, sans
# installer le paquet -- vnc_tab.py est à la racine du dépôt.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _gtk4_available() -> bool:
    try:
        import gi

        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk  # noqa: F401
    except (ImportError, ValueError):
        return False
    return True


GTK4_AVAILABLE = _gtk4_available()

requires_gtk4 = pytest.mark.skipif(
    not GTK4_AVAILABLE,
    reason="PyGObject/GTK4 non disponible dans cet environnement (besoin d'un display ou de Xvfb)",
)


def _real_asyncvnc2() -> bool:
    """Faux sous le stub ``tests/gtk_stub/asyncvnc2.py`` (qui n'a pas de
    ``Client``) : seuls les tests de bout en bout contre le vrai protocole
    en dépendent. La CI (Xvfb) installe le vrai asyncvnc2, ils y tournent."""
    try:
        import asyncvnc2
    except ImportError:
        return False
    return hasattr(asyncvnc2, "Client")


requires_real_asyncvnc2 = pytest.mark.skipif(
    not _real_asyncvnc2(),
    reason="stub asyncvnc2 (tests/gtk_stub) : test de bout en bout contre le vrai protocole",
)


def _real_gtk() -> bool:
    """Faux sous le stub ``tests/gtk_stub/gi`` (boucle GLib et signaux
    GObject non fonctionnels) : seuls les tests qui font tourner une vraie
    boucle GLib en dépendent. La CI (Xvfb) a le vrai GTK4, ils y tournent."""
    try:
        import gi
    except ImportError:
        return False
    return "gtk_stub" not in (getattr(gi, "__file__", "") or "")


requires_real_gtk = pytest.mark.skipif(
    not _real_gtk(),
    reason="stub gi (tests/gtk_stub) : test qui fait tourner une vraie boucle GLib",
)


def make_test_display(
    remote_w,
    remote_h,
    alloc_w,
    alloc_h,
    content_fit=None,
    active_screen_id=None,
    screens=None,
):
    """Construit un VRAI `VncDisplay` (pas juste sa classe) avec une taille
    allouée et une taille distante figées, pour tester la logique de
    `get_display_transform()`/`widget_to_remote()` sans display GTK réel ni
    connexion serveur.

    Fonctionne aussi bien avec le stub `tests/gtk_stub/` (`get_width()` /
    `get_height()` / `get_content_fit()` n'y renvoient normalement que des
    `_NoOp()` inexploitables pour du calcul) qu'avec un vrai GTK4 -- dans
    les deux cas on écrase ces accesseurs par des valeurs fixes en
    attribut d'instance, ce qui prime sur la méthode de classe. `client`
    est remplacé par un `types.SimpleNamespace` minimal exposant juste
    `video.width`/`video.height`, suffisant pour `get_display_transform()`.

    N'appelle PAS `VncDisplay.__init__` en mode dégradé : l'instanciation
    reste réelle (constructeur complet, `_setup_input_controllers()`
    compris), seuls les accesseurs de taille/contenu et `client` sont
    substitués après coup. Si un futur test a besoin d'un multi-écran,
    passer `screens` (liste d'objets exposant `.id`/`.x`/`.y`/`.width`/
    `.height`, ex. `collections.namedtuple`) et `active_screen_id`.
    """
    from vnc_tab import VncConnectionInfo, VncDisplay
    from gi.repository import Gtk

    display = VncDisplay(VncConnectionInfo(name="test", host="192.0.2.1"))
    display.get_width = lambda: alloc_w
    display.get_height = lambda: alloc_h
    display.get_content_fit = lambda: content_fit if content_fit is not None else Gtk.ContentFit.CONTAIN
    display.client = types.SimpleNamespace(video=types.SimpleNamespace(width=remote_w, height=remote_h))
    if screens is not None:
        display._last_screens = screens
    display._active_screen_id = active_screen_id
    return display
