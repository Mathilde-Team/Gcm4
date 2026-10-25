"""Verrou textuel sur l'état GTK4 du plugin RDP (audit session-46).

Contexte : dernier des trois chantiers dont l'ordre reste à confirmer avec
l'auteure (`CLAUDE.md`, `docs/gtk4-migration.md` §3.6-terdecies/§3.7) —
le couple `focus-out-event`/`populate-popup`, `plugins/ssh/gtk4.py` (audité
en session-45), et RDP (§3.4, « le point le plus incertain »). Cette
session complète l'audit des trois en traitant RDP, sur le même principe
que la session-45 pour `plugins/ssh/gtk4.py` : inventaire avant tout
portage de code, conformément à `CONSIGNES-AGENTS-IA.md`.

**Correction importante apportée par cet audit** : §3.4 (rédigé le
2026-08-29) décrit `GtkFrdp.Display` et un repli `Gtk.Socket`/XEmbed comme
deux mécanismes *coexistants*, tous deux dépendants de X11. Ce n'est plus
le cas du code actuel :

- `plugins/plugin_rdp.py` (le plugin RDP réellement chargé par
  `PluginRegistry.autoload()`) n'utilise que `GtkFrdp.Display` — sa propre
  docstring de classe le dit explicitement (« Seule solution RDP de GCM
  (pas de fallback) »). Aucune trace de `Gtk.Socket`/`GtkSocket` ni de
  sous-processus `xfreerdp` avec `/parent-window:<XID>` dans ce fichier ni
  dans `gnome_connection_manager.py`.
- L'ancien mécanisme XEmbed (classe `RdpEmbeddedTab`, sous-processus
  `xfreerdp` embarqué via `Gtk.Socket`, détection X11 via
  `_rdp_socket_available()`) a bien existé — `tests/test_gcm.py` le
  prouve, avec ses classes `TestRdpEmbeddedTabBuildCmd`
  (`_build_cmd()`/`/parent-window:`) — mais **a été retiré de
  `gnome_connection_manager.py`**. C'est très exactement l'orphelin déjà
  identifié côté `_vm_name_split`/`_rdp_socket_available`
  (`docs/architecture.md` §5 point 5, `CLAUDE.md` session-36) : cet audit
  confirme que `_rdp_socket_available` était le détecteur X11 de ce chemin
  XEmbed précisément, et que sa disparition n'est donc pas un accident
  isolé mais la trace du passage à l'architecture à plugins (`plugin_rdp.py`,
  « quatrième plugin implémenté » selon sa propre docstring). Le sort des
  tests orphelins (`tests/test_gcm.py`) a été tranché depuis : supprimés
  (décision de l'autrice du 2026-10-12, #87).
- Conséquence : le point de rupture réel n'est plus « quel mécanisme
  remplace `Gtk.Socket`/XEmbed » (question désormais sans objet), mais
  « `gtk-frdp` (bibliothèque C vendorisée dans `gtk-frdp/`, consommée via
  GObject Introspection) a-t-elle un chemin GTK4 ? ». Inspection du code
  source vendorisé : `FrdpDisplay` est une sous-classe directe de
  `GtkDrawingArea` (`G_DEFINE_TYPE_WITH_PRIVATE (FrdpDisplay, frdp_display,
  GTK_TYPE_DRAWING_AREA)`, `src/frdp-display.c`), sans **aucune** trace
  d'API X11/Xlib dans tout `gtk-frdp/src/` (recherche exhaustive :
  `X11`/`Xlib`/`gdk_x11`/`GDK_WINDOWING_X11`/`xcb_` — zéro occurrence) —
  §3.4 avait donc tort d'imputer une dépendance X11 à `GtkFrdp.Display`
  lui-même, seul l'ancien repli XEmbed (aujourd'hui disparu) en avait une.
  En revanche `gtk-frdp/src/meson.build` épingle explicitement
  `dependency('gtk+-3.0')` — la bibliothèque vendorisée ne se construit pas
  contre GTK4 telle quelle aujourd'hui — et `frdp-session.c` utilise
  `GdkWindow` (type supprimé en GTK4, remplacé par `GdkSurface`), une
  rupture d'API réelle mais d'une classe déjà bien maîtrisée par ce dépôt
  (même famille que les points déjà portés ailleurs), pas un blocage
  architectural.
- Recherche web (2026-09-19, à reconfirmer périodiquement comme le
  recommandait déjà §3.5) : `gtk-frdp` est un petit projet GNOME
  (Felipe Borges/Marek Kašík, ~25 issues/3 MR sur gitlab.gnome.org),
  développé à l'origine pour GNOME Boxes. Boxes vient justement de
  terminer sa propre migration GTK4/libadwaita (bêta annoncée le
  2026-08-03 par F. Borges lui-même) — mais l'annonce et sa couverture
  presse (Phoronix, UbuntuHandbook, itsfoss) ne décrivent que le
  remplacement du widget SPICE (GTK3 → **Libmks**, nouvelle bibliothèque
  dédiée) ; aucune ne mentionne RDP/gtk-frdp. Aucune branche/MR GTK4 pour
  `gtk-frdp` lui-même trouvée. Signal réel mais non concluant : le statut
  GTK4 de `gtk-frdp` reste **non confirmé**, comme le disait déjà §3.5,
  et le reste apparemment même du côté de son propre mainteneur trois
  semaines plus tard.
- Détail distinct trouvé au passage, propre à RDP/VNC/SPICE : le
  « constructeur générique RDP/VNC/SPICE de `widgets.py` » cité par
  l'audit initial des menus contextuels (session-29,
  `build_remote_desktop_context_menu()`, partagé par `plugin_rdp.py`,
  `plugin_vnc.py` et `plugin_spice.py`) est resté en `Gtk.Menu` classique
  — `tests/test_gtk4_context_menus_baseline.py` le documentait déjà
  correctement (« Restent hors périmètre de ce fichier », mise à jour
  session-33), mais le résumé de `CLAUDE.md` pour la session-33 affirme
  que « les quatre menus contextuels applicatifs... sont désormais tous
  portés », ce qui est inexact pour celui-ci (3 des 4 seulement :
  `popupMenu`/`popupMenuFolder`/`popupMenuTab`). Ce fichier verrouille
  cet état pour le périmètre RDP/VNC/SPICE en complément de
  `tests/test_gtk4_context_menus_baseline.py` (qui garde son propre
  compteur `WIDGETS_FILE` inchangé).

Ce fichier ne teste pas de comportement runtime (mêmes contraintes que
`tests/test_gtk4_core_rupture_points.py`/`tests/test_ssh_gtk4_baseline.py` :
GTK3/VTE indisponibles dans cet environnement de travail, et le fichier
`gtk-frdp/src/meson.build` inspecté ici est un fichier Meson/texte, pas du
Python). Trois classes :

1. ``TestRdpXEmbedRuptureAlreadyResolved`` — verrouille, sur le principe de
   ``test_gtk4_core_rupture_points.py`` (verrouiller l'*absence* d'un point
   de rupture déjà résolu), la disparition confirmée de l'ancien mécanisme
   XEmbed du code réellement chargé.
2. ``TestRdpPluginUsesGtkFrdpNatively`` — verrouille la dépendance actuelle
   à ``GtkFrdp``/GTK3 telle qu'elle existe aujourd'hui.
3. ``TestRemoteDesktopContextMenuStillLegacy`` — verrouille l'état non porté
   de ``build_remote_desktop_context_menu()`` pour les trois plugins qui le
   consomment.

Si un test échoue ici après un changement délibéré (portage effectif d'un
de ces points, apparition d'un chemin GTK4 pour `gtk-frdp`), c'est le signe
attendu qu'il faut mettre à jour la baseline et `docs/gtk4-migration.md`
§3.8 — pas une régression à l'aveugle.
"""

import os
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CORE_FILE = os.path.join(REPO_ROOT, "gnome_connection_manager.py")
RDP_PLUGIN_FILE = os.path.join(REPO_ROOT, "plugins", "plugin_rdp.py")
VNC_PLUGIN_FILE = os.path.join(REPO_ROOT, "plugins", "plugin_vnc.py")
SPICE_PLUGIN_FILE = os.path.join(REPO_ROOT, "plugins", "plugin_spice.py")
WIDGETS_FILE = os.path.join(REPO_ROOT, "widgets.py")
GTK_FRDP_MESON = os.path.join(REPO_ROOT, "gtk-frdp", "src", "meson.build")


def _read(path: str) -> str:
    """Lit un fichier texte du dépôt en UTF-8.

    Args:
        path (str): Chemin absolu du fichier à lire.

    Returns:
        str: Contenu intégral du fichier.
    """
    with open(path, encoding="utf-8") as fh:
        return fh.read()


# Baseline établie à l'audit session-46 (2026-09-19). Les occurrences
# textuelles de "Gtk.Socket"/"GtkSocket"/"XEmbed"/"/parent-window:" restantes
# sont toutes des commentaires explicatifs (comparaison à l'ancien mécanisme,
# ou justification de l'absence de dépendance) — jamais du code actif. Toute
# variation doit être accompagnée d'une relecture consciente, pas d'un ajustement
# muet : une hausse pourrait signaler la réintroduction accidentelle du chemin
# XEmbed, une baisse la disparition d'un commentaire utile.
EXPECTED_XEMBED_MARKER_COUNTS = {
    CORE_FILE: {
        "Gtk.Socket": 0,
        "GtkSocket": 0,
        "XEmbed": 0,
        "/parent-window:": 0,
    },
    RDP_PLUGIN_FILE: {
        "Gtk.Socket": 0,
        "GtkSocket": 0,
        # 1 commentaire comparant le traitement TLS actuel à celui de
        # « l'ancien mode XEmbed » (/cert:ignore) — pas du code actif.
        "XEmbed": 1,
        "/parent-window:": 0,
    },
    VNC_PLUGIN_FILE: {
        # 1 commentaire : « Widget GtkVnc.Display : ... pas de Gtk.Socket/XID
        # requis » — explique une absence, n'introduit pas de dépendance.
        "Gtk.Socket": 1,
        "GtkSocket": 0,
        # 2 commentaires expliquant que le widget VNC natif remplace
        # XEmbed/subprocess, sans fenêtre externe ni XEmbed.
        "XEmbed": 2,
        "/parent-window:": 0,
    },
    SPICE_PLUGIN_FILE: {
        "Gtk.Socket": 0,
        "GtkSocket": 0,
        "XEmbed": 0,
        "/parent-window:": 0,
    },
}


class TestRdpXEmbedRuptureAlreadyResolved(unittest.TestCase):
    """L'ancien mécanisme XEmbed (RdpEmbeddedTab) a disparu du code réellement chargé."""

    def test_xembed_marker_counts_match_baseline(self):
        """Chaque marqueur XEmbed/Socket doit apparaître exactement le nombre attendu.

        Toute variation doit être accompagnée d'une mise à jour consciente de
        `EXPECTED_XEMBED_MARKER_COUNTS` et de `docs/gtk4-migration.md` §3.8 —
        jamais d'un ajustement muet.
        """
        for path, markers in EXPECTED_XEMBED_MARKER_COUNTS.items():
            source = _read(path)
            relpath = os.path.relpath(path, REPO_ROOT)
            for needle, expected in markers.items():
                with self.subTest(file=relpath, needle=needle):
                    actual = source.count(needle)
                    self.assertEqual(
                        actual,
                        expected,
                        f"{relpath} : {actual} occurrence(s) de {needle!r} "
                        f"trouvée(s), {expected} attendue(s) (baseline "
                        "session-46). Voir docs/gtk4-migration.md §3.8 avant "
                        "d'ajuster ce test.",
                    )

    def test_rdp_embedded_tab_and_socket_helper_absent_from_core(self):
        """`RdpEmbeddedTab` et `_rdp_socket_available` restent absents du cœur.

        Confirme, pour le périmètre précis de cet audit, l'orphelin déjà
        documenté (`docs/architecture.md` §5 point 5, `CLAUDE.md`
        session-36) : `tests/test_gcm.py` référence encore ces deux noms
        (`TestRdpEmbeddedTabBuildCmd`, `TestVmNameSplit`) mais aucun des
        deux n'existe plus dans `gnome_connection_manager.py`. Ce test ne
        touche pas au sort de ces tests orphelins (suppression vs.
        réintroduction), laissé à confirmer avec l'auteure comme documenté
        — il verrouille seulement l'état actuel du cœur.
        """
        source = _read(CORE_FILE)
        for needle in ("class RdpEmbeddedTab", "_rdp_socket_available"):
            with self.subTest(needle=needle):
                self.assertNotIn(
                    needle,
                    source,
                    f"{needle!r} trouvé dans gnome_connection_manager.py — "
                    "si réintroduit délibérément, voir docs/architecture.md "
                    "§5 point 5 et docs/gtk4-migration.md §3.8 avant "
                    "d'ajuster ce test.",
                )


class TestRdpPluginUsesGtkFrdpNatively(unittest.TestCase):
    """Le plugin RDP actuel ne dépend que de `GtkFrdp.Display` (GTK3 pour l'instant)."""

    def test_plugin_rdp_requires_gtkfrdp_and_builds_native_widget(self):
        """`plugins/plugin_rdp.py` verrouille `GtkFrdp` 0.2 et construit le widget natif.

        Chacun des deux marqueurs doit apparaître exactement une fois —
        toute variation (site dupliqué, renommage) mérite une relecture
        consciente avant d'ajuster ce test.
        """
        source = _read(RDP_PLUGIN_FILE)
        for needle in (
            'gi.require_version("GtkFrdp", "0.2")',
            "GtkFrdp.Display.new()",
        ):
            with self.subTest(needle=needle):
                actual = source.count(needle)
                self.assertEqual(
                    actual,
                    1,
                    f"plugins/plugin_rdp.py : {actual} occurrence(s) de "
                    f"{needle!r} trouvée(s), 1 attendue (baseline "
                    "session-46).",
                )

    def test_vendored_gtk_frdp_still_pinned_to_gtk3(self):
        """`gtk-frdp/src/meson.build` (vendorisé) dépend encore de `gtk+-3.0`, pas GTK4.

        Documente l'état constaté lors de l'audit session-46 : la
        bibliothèque C vendorisée ne se construit pas contre GTK4 telle
        quelle aujourd'hui — voir `docs/gtk4-migration.md` §3.8 pour le
        détail (recherche upstream sur `gitlab.gnome.org/GNOME/gtk-frdp` et
        sur la migration GTK4 de GNOME Boxes, son principal consommateur
        réel). Si ce test échoue après une mise à jour délibérée du
        sous-projet vendorisé (upgrade vers une version GTK4), c'est le
        signal attendu qu'un chemin de portage vient de s'ouvrir pour
        `plugins/plugin_rdp.py` — pas une régression.
        """
        source = _read(GTK_FRDP_MESON)
        self.assertIn(
            "dependency('gtk+-3.0')",
            source,
            "gtk-frdp/src/meson.build ne dépend plus de gtk+-3.0 — si "
            "gtk-frdp a été mis à jour vers une version compatible GTK4, "
            "voir docs/gtk4-migration.md §3.8 avant d'ajuster ce test.",
        )
        self.assertNotIn(
            "gtk+-4.0",
            source,
            "gtk-frdp/src/meson.build mentionne désormais gtk+-4.0 — "
            "possible chemin de portage GTK4 apparu en amont, voir "
            "docs/gtk4-migration.md §3.8 avant d'ajuster ce test.",
        )


class TestRemoteDesktopContextMenuStillLegacy(unittest.TestCase):
    """`build_remote_desktop_context_menu()` (RDP/VNC/SPICE) reste en `Gtk.Menu` classique.

    Complète, pour le périmètre spécifique RDP/VNC/SPICE,
    `tests/test_gtk4_context_menus_baseline.py` (qui documente déjà ce
    point comme « hors périmètre » de son propre inventaire depuis la
    session-33, sans le verrouiller en tant que check-list dédiée) —
    corrige au passage l'affirmation de `CLAUDE.md` (session-33) selon
    laquelle « les quatre menus contextuels applicatifs... sont désormais
    tous portés » : celui-ci, le quatrième, ne l'est pas.
    """

    def test_build_remote_desktop_context_menu_still_uses_two_gtk_menus(self):
        """`widgets.py` construit toujours ce menu avec deux `Gtk.Menu()` classiques.

        Un menu principal (Connect/Disconnect) et un sous-menu « Special
        keys » — voir `widgets.build_remote_desktop_context_menu()`.
        """
        source = _read(WIDGETS_FILE)
        actual = source.count("Gtk.Menu()")
        self.assertEqual(
            actual,
            2,
            f"widgets.py : {actual} occurrence(s) de 'Gtk.Menu()' trouvée(s), "
            "2 attendues (baseline session-46 : menu principal + "
            "keys_submenu de build_remote_desktop_context_menu()). Voir "
            "docs/gtk4-migration.md §3.8 avant d'ajuster ce test.",
        )

    def test_three_plugins_still_call_the_legacy_builder(self):
        """RDP, VNC et SPICE appellent chacun `build_remote_desktop_context_menu()` une fois."""
        for path in (RDP_PLUGIN_FILE, VNC_PLUGIN_FILE, SPICE_PLUGIN_FILE):
            source = _read(path)
            relpath = os.path.relpath(path, REPO_ROOT)
            with self.subTest(file=relpath):
                actual = source.count("build_remote_desktop_context_menu(")
                self.assertEqual(
                    actual,
                    1,
                    f"{relpath} : {actual} appel(s) à "
                    "build_remote_desktop_context_menu() trouvé(s), 1 "
                    "attendu (baseline session-46).",
                )


if __name__ == "__main__":
    unittest.main()
