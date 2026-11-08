# SPDX-License-Identifier: GPL-3.0-or-later
"""Vérification empirique des gestes GTK4 (#72) : de vrais événements X.

Les portages des sessions 39-44 (déclenchements ``Gtk.GestureClick``,
sélections VTE) étaient vérifiés en logique pure, GTK3 étant absent de
l'environnement de développement. Ces tests injectent de vrais clics et
frappes dans le serveur X (XTEST) et observent le comportement réel de
la fenêtre :

- clic droit sur l'arbre : menu contextuel correct pour un hôte, un
  groupe et la zone vide, la ligne sous le pointeur étant sélectionnée ;
- clic milieu sur l'étiquette d'un onglet : fermeture confirmée par
  défaut, directe quand l'option est désactivée ;
- F2 sur un groupe sélectionné : dialogue de renommage, frappe réelle.

Les gestes dans le terminal (sélection mot/ligne, focus du clic
simple) ne sont pas testés ici : le terminal est passé à xterm.js dans
WebKitGTK (#76), dont la sélection vit dans le DOM — à vérifier
empiriquement séparément (voir l'issue #72).

Les menus contextuels du terminal et de l'étiquette d'onglet portés en
sessions 39-41 vivaient dans le cœur GTK3 (``widgets.py``), retiré du
dépôt depuis (#182) : sans équivalent GTK4 à ce jour, ils ne sont pas
testés ici — voir l'issue #72 pour le suivi.
"""

from __future__ import annotations

import ctypes
import shutil
import subprocess
import time
from collections.abc import Callable

import pytest

import gcm4.ui  # noqa: F401  (require_version avant gi.repository)
from gcm4.core.i18n import _

XDOTOOL = shutil.which("xdotool")

pytestmark = pytest.mark.skipif(XDOTOOL is None, reason="xdotool absent : clics XTEST impossibles")


@pytest.fixture(autouse=True)
def _refermer_dialogues_oublies(pomper):
    """Referme les fenêtres laissées ouvertes par les tests précédents.

    Les dialogues et fenêtres annexes des tests précédents (couleur de
    groupe, messages, « à propos »…) restent mappés sur le serveur X avec
    leur grab modal : toute l'entrée XTEST — clics comme clavier — leur
    serait déviée silencieusement. Certains n'apparaissent pas dans
    ``app.get_windows()``, que la fixture ``app_fixture`` détruit pourtant
    correctement.
    """
    from gi.repository import GObject, Gtk

    # Cette fixture s'exécute avant ``app_fixture`` : toute fenêtre de
    # premier niveau encore vivante à ce stade est un vestige d'un test
    # précédent (dialogue, « à propos », fenêtre principale non détruite…).
    # Sur les anciennes libadwaita, ces fenêtres sont de vraies fenêtres X
    # mappées avec leur grab modal : toute l'entrée XTEST — clics comme
    # clavier — leur serait déviée silencieusement. On referme tout, du
    # plus récemment ouvert au plus ancien.
    for fenetre in reversed(list(Gtk.Window.list_toplevels())):
        print(f"VESTIGE: {GObject.type_name(fenetre)}")
        fenetre.destroy()
        pomper(2)
    try:
        visibles = _xdotool("search", "--onlyvisible", ".").split()
        for identifiant in visibles:
            coquille = _xdotool("getwindowgeometry", "--shell", identifiant)
            nom = _xdotool("getwindowname", identifiant).strip()
            print(f"X RESTANT: {identifiant} {nom!r} {coquille.replace(chr(10), ' ')}")
    except Exception as erreur:  # diagnostic purement informatif
        print(f"X RESTANT: échec ({erreur!r})")
    yield


def _xdotool(*arguments: str) -> str:
    """Commande xdotool exécutée sur le serveur X de la session de test."""
    resultat = subprocess.run(
        [XDOTOOL, *arguments],
        check=True,
        capture_output=True,
        text=True,  # type: ignore[list-item]
        timeout=10,
    )
    return resultat.stdout


def _position_fenetre(widget) -> tuple[int, int]:
    """Origine écran de la zone cliente de la fenêtre de ``widget``.

    La surface X d'une fenêtre libadwaita inclut son ombre CSD : le widget
    fenêtre y est décalé d'une marge uniforme, déduite des tailles surface /
    fenêtre. Retourne la position du coin haut-gauche du widget fenêtre.
    """
    from gi.repository import GdkX11

    fenetre = widget.get_root()
    surface = fenetre.get_surface()
    assert isinstance(surface, GdkX11.X11Surface), "test X11 uniquement"
    marge_x = (surface.get_width() - fenetre.get_width()) // 2
    marge_y = (surface.get_height() - fenetre.get_height()) // 2
    coquille = _xdotool("getwindowgeometry", "--shell", str(surface.get_xid()))
    valeurs = dict(ligne.split("=", 1) for ligne in coquille.splitlines() if "=" in ligne)
    return int(valeurs["X"]) + marge_x, int(valeurs["Y"]) + marge_y


def _position_dans_fenetre(widget, dx: float, dy: float) -> tuple[int, int]:
    """Position de ``dx, dy`` (coordonnées du widget) dans la fenêtre.

    Le contenu d'un ``Adw.Dialog`` (renommage, confirmations) a pour
    racine GTK le dialogue, pas la fenêtre : la traduction classique
    échoue alors, et la chaîne des ``get_parent()`` — qui relie le
    dialogue à sa fenêtre hôte — permet de replier les allocations,
    relatives au parent, en une position dans la fenêtre.
    """
    fenetre = widget
    while fenetre.get_parent() is not None:
        fenetre = fenetre.get_parent()
    traduit = widget.translate_coordinates(fenetre, dx, dy)
    if traduit is not None and traduit[0] is not False:
        if isinstance(traduit[0], bool):
            _ok, x, y = traduit  # PyGObject : (ok, x, y)
        else:
            x, y = traduit  # PyGObject : (x, y)
        return int(round(x)), int(round(y))
    x, y = float(dx), float(dy)
    courant: object = widget
    while courant is not None:
        if hasattr(courant, "get_allocation"):
            allocation = courant.get_allocation()
            x += allocation.x
            y += allocation.y
        courant = courant.get_parent()
    return int(round(x)), int(round(y))


def _activer(widget) -> None:
    """Met la fenêtre de ``widget`` au-dessus de la pile et lui rend le focus.

    Les fenêtres des tests précédents du lot restent mappées sur le
    serveur X : sans WM sous Xvfb, elles se superposent toutes à la même
    position — les clics XTEST iraient à la plus haute, pas à celle du
    test en cours.
    """
    fenetre = widget
    while fenetre.get_parent() is not None:
        fenetre = fenetre.get_parent()
    surface = fenetre.get_surface()
    assert surface is not None, "fenêtre sans surface"
    xid = str(surface.get_xid())
    _xdotool("windowraise", xid)
    _xdotool("windowfocus", xid)


def _attendre_allocation(widget, pomper: Callable) -> None:
    """Pompe la boucle principale jusqu'à la première allocation du widget.

    Le contenu d'un ``Adw.Dialog`` qui vient de s'ouvrir n'est alloué
    qu'au premier tour d'horloge de frames : une itération bloquante
    (bornée par une source de délai de 200 ms) le déclenche là où les
    seules itérations non bloquantes de ``pomper`` n'y suffisent pas.
    """
    from gi.repository import GLib

    if widget.get_width() > 0 and widget.get_height() > 0:
        return
    contexte = GLib.MainContext.default()
    for _essai in range(50):
        if widget.get_width() > 0 and widget.get_height() > 0:
            break
        GLib.timeout_add(200, lambda: False)  # borne l'attente bloquante
        contexte.iteration(True)
    pomper(5)
    if widget.get_width() <= 0 or widget.get_height() <= 0:
        pytest.fail(f"widget {type(widget).__name__} jamais alloué")


def _est_dans(widget, ancetre) -> bool:
    """Le widget a-t-il ``ancetre`` dans sa chaîne des ``get_parent()`` ?"""
    courant: object = widget
    while courant is not None:
        if courant is ancetre:
            return True
        courant = courant.get_parent()
    return False


def _attendre_presentation(dialogue, pomper: Callable) -> None:
    """Attend la fin de la présentation du dialogue (fondu d'ouverture).

    Pendant la présentation, le dialogue ignore toute entrée — clics comme
    clavier. Le focus par défaut (bouton de réponse par défaut) n'est posé
    qu'au terme de l'animation : c'est le signal de fin de présentation.
    Avec le repli ``Adw.MessageDialog`` des vieilles libadwaita (< 1.5),
    fenêtre séparée, aucun focus n'est garanti : un délai fixe borne alors
    l'attente, la présentation durant ~250 ms.
    """
    fenetre = dialogue
    while fenetre.get_parent() is not None:
        fenetre = fenetre.get_parent()
    for _essai in range(20):  # ~1 s
        focus = fenetre.get_focus()
        if focus is not None and _est_dans(focus, dialogue):
            pomper(5)
            return
        pomper(1)
        time.sleep(0.05)
    time.sleep(0.4)
    pomper(10)


def _attendre_disparition(dialogue, pomper: Callable) -> None:
    """Attend la fermeture du dialogue (déparenté ou caché, selon la version).

    ``Adw.AlertDialog`` (libadwaita ≥ 1.5) vit sans parent dans la fenêtre ;
    le repli ``AdwMessageDialog`` est une fenêtre X à part — ``get_parent``
    y est toujours ``None``, seule l'invisibilité prouve la fermeture.
    """
    from gi.repository import Gtk

    for _essai in range(40):  # ~2 s au pire
        ferme = not dialogue.get_visible() or (
            not isinstance(dialogue, Gtk.Window) and dialogue.get_parent() is None
        )
        if ferme:
            pomper(5)
            return
        pomper(1)
        time.sleep(0.05)
    pytest.fail("dialogue jamais refermé")


def _clic_sur(
    widget,
    dx: float,
    dy: float,
    bouton: int,
    pomper: Callable,
    repetitions: int = 1,
) -> None:
    """Déplace le pointeur sur ``dx, dy`` (coordonnées du widget) et clique.

    Le clic part de XTEST : il traverse toute la chaîne d'entrée GTK
    (gestes compris), contrairement à un appel direct des gestionnaires.
    Un double/triple-clic espace ses pressions de 250 ms : plus près, le
    serveur X du test coalesce parfois les pressions et VTE ne voit qu'un
    clic simple. Un widget d'un dialogue qui vient de s'ouvrir attend
    sa première allocation (nulle le temps de l'animation d'ouverture).

    Les clics visant l'enfant droit du ``Gtk.Paned`` restent à distance
    du séparateur : jusqu'à GTK 4.12, le geste de glissement du panneau
    revendique les pressions bouton-1 tombant dans la marge de sa
    poignée ; la pression est alors consommée et n'atteint jamais
    l'enfant (constaté #72 : un clic à 10 px du bord gauche du terminal
    ne lui donne jamais le focus sous Debian 12 / GTK 4.8).
    """
    _attendre_allocation(widget, pomper)
    _activer(widget)
    from gi.repository import Gtk

    racine = widget
    while racine.get_parent() is not None:
        racine = racine.get_parent()
    for _essai in range(10):
        x, y = _position_dans_fenetre(widget, dx, dy)
        pique = racine.pick(x, y, Gtk.PickFlags.DEFAULT)
        if pique is None or _est_dans(pique, widget):
            break
        pomper(2)  # la mise en page bouge encore : on la laisse se calmer
    ox, oy = _position_fenetre(widget)
    _xdotool("mousemove", str(int(ox + x)), str(int(oy + y)))
    if repetitions == 1:
        _xdotool("click", str(bouton))
    else:
        _xdotool("click", "--repeat", str(repetitions), "--delay", "250", str(bouton))
    pomper()


#: Keycodes du serveur X, lus une seule fois (valeur keysym → keycode).
_KEYCODES: dict[int, int] | None = None

#: Bibliothèques X (libX11, libXtst) chargées une seule fois, signatures posées.
_X_XTST: tuple[ctypes.CDLL, ctypes.CDLL] | None = None


def _x11_xtst() -> tuple[ctypes.CDLL, ctypes.CDLL]:
    """libX11 et libXtst avec leurs signatures ctypes, une seule fois.

    Sans ``restype``/``argtypes``, ctypes traite ``XOpenDisplay`` comme
    renvoyant un ``int`` : le pointeur ``Display *`` 64 bits est tronqué
    et l'appel suivant segfaulte (constaté #72 sur Rocky 9, où le
    segment du tas dépasse 4 Go — Debian 12 y échappait par chance
    d'adresse). Tout handle passe donc par ``c_void_p``.
    """
    global _X_XTST
    if _X_XTST is not None:
        return _X_XTST
    x11 = ctypes.CDLL("libX11.so.6")
    xtst = ctypes.CDLL("libXtst.so.6")
    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
    x11.XSync.argtypes = [ctypes.c_void_p, ctypes.c_bool]
    x11.XFree.argtypes = [ctypes.c_void_p]
    x11.XStringToKeysym.restype = ctypes.c_ulong
    x11.XStringToKeysym.argtypes = [ctypes.c_char_p]
    x11.XDisplayKeycodes.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_int),
        ctypes.POINTER(ctypes.c_int),
    ]
    x11.XGetKeyboardMapping.restype = ctypes.POINTER(ctypes.c_ulong)
    x11.XGetKeyboardMapping.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint8,
        ctypes.c_int,
        ctypes.POINTER(ctypes.c_int),
    ]
    xtst.XTestFakeKeyEvent.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint,
        ctypes.c_bool,
        ctypes.c_ulong,
    ]
    _X_XTST = (x11, xtst)
    return _X_XTST


def _keycodes() -> dict[int, int]:
    """Keycodes X (valeur keysym → keycode) lus dans la table du serveur.

    ``XGetKeyboardMapping`` plutôt que ``xmodmap`` : absent des images
    minimales (conteneurs CI), le binaire ne se trouve pas partout. Le
    premier keycode portant le keysym gagne (colonne de base d'abord,
    comme le ``xmodmap -pke`` jadis parsé).
    """
    global _KEYCODES
    if _KEYCODES is not None:
        return _KEYCODES
    x11, _ = _x11_xtst()
    affichage = x11.XOpenDisplay(None)
    assert affichage, "serveur X du test inaccessible"
    try:
        code_min, code_max = ctypes.c_int(), ctypes.c_int()
        x11.XDisplayKeycodes(affichage, ctypes.byref(code_min), ctypes.byref(code_max))
        par_code = ctypes.c_int()
        table = x11.XGetKeyboardMapping(
            affichage,
            code_min.value,
            code_max.value - code_min.value + 1,
            ctypes.byref(par_code),
        )
        assert table, "table des keycodes illisible"
        codes: dict[int, int] = {}
        for keycode in range(code_min.value, code_max.value + 1):
            for colonne in range(par_code.value):
                keysym = table[(keycode - code_min.value) * par_code.value + colonne]
                if keysym and keysym not in codes:
                    codes[keysym] = keycode
        x11.XFree(table)
    finally:
        x11.XCloseDisplay(affichage)
    _KEYCODES = codes
    return codes


def _touche(symbole: str, avec_ctrl: bool = False) -> None:
    """Presse puis relâche une touche par nom keysym, XTEST direct.

    ``xdotool key`` envoie un ``Alt_L`` pressé avant la touche (constaté
    contre le serveur X du test) : un raccourci strict comme ``F2`` ne
    l'entendrait pas, Alt étant un modificateur actif.
    """
    x11, xtst = _x11_xtst()
    affichage = x11.XOpenDisplay(None)
    assert affichage, "serveur X du test inaccessible"
    try:
        codes = _keycodes()
        keysym = x11.XStringToKeysym(symbole.encode())
        assert keysym, f"keysym {symbole} inconnu du serveur X"
        assert keysym in codes, f"keysym {symbole} absent du serveur X"
        ctrl = x11.XStringToKeysym(b"Control_L")
        assert ctrl in codes, "Control_L absent du serveur X"
        if avec_ctrl:
            xtst.XTestFakeKeyEvent(affichage, codes[ctrl], True, 0)
        xtst.XTestFakeKeyEvent(affichage, codes[keysym], True, 0)
        xtst.XTestFakeKeyEvent(affichage, codes[keysym], False, 0)
        if avec_ctrl:
            xtst.XTestFakeKeyEvent(affichage, codes[ctrl], False, 0)
        x11.XSync(affichage, False)
    finally:
        x11.XCloseDisplay(affichage)


def _lignes_de_l_arbre(arbre) -> dict[str, int]:
    """Chemin visible → ordonnée du milieu de sa ligne, retrouvée par ``pick()``.

    Même cheminement que le gestionnaire du clic droit : le widget sous
    le pointeur remonte jusqu'au ``Gtk.TreeExpander`` de la ligne. Le
    milieu de la plage détectée est retourné : le premier pixel d'une
    ligne peut appartenir à la marge de la liste, où ``pick()`` ne
    renvoie aucun enfant.
    """
    from gi.repository import Gtk

    plages: dict[str, list[int]] = {}
    liste = arbre.liste
    largeur = liste.get_width()
    for y in range(2, max(liste.get_height() - 2, 0), 4):
        widget = liste.pick(largeur // 2, y, Gtk.PickFlags.DEFAULT)
        while widget is not None and not isinstance(widget, Gtk.TreeExpander):
            widget = widget.get_parent() if widget is not liste else None
        if widget is None:
            continue
        ligne = widget.get_list_row()
        if ligne is None:
            continue
        plages.setdefault(ligne.get_item().noeud.chemin, []).append(y)
    return {chemin: (ys[0] + ys[-1]) // 2 for chemin, ys in plages.items()}


def _labels(modele) -> list[str]:
    """Libellés plats d'un ``Gio.Menu`` (sections parcourues récursivement)."""
    from gi.repository import GLib

    labels: list[str] = []
    for i in range(modele.get_n_items()):
        section = modele.get_item_link(i, "section")
        if section is not None:
            labels.extend(_labels(section))
            continue
        valeur = modele.get_item_attribute_value(i, "label", GLib.VariantType("s"))
        if valeur is not None:
            labels.append(valeur.get_string())
    return labels


def _presse_papiers_primaire() -> str:
    """Texte du presse-papiers primaire, lu de façon asynchrone (GTK4)."""
    from gi.repository import Gdk, GLib

    clip = Gdk.Display.get_default().get_primary_clipboard()
    resultat: dict[str, object] = {}
    boucle = GLib.MainLoop()

    def _fin(clip_, res) -> None:
        try:
            resultat["texte"] = clip_.read_text_finish(res)
        except Exception as exc:  # noqa: BLE001
            resultat["erreur"] = exc
        boucle.quit()

    clip.read_text_async(None, _fin)
    boucle.run()
    if "erreur" in resultat:
        pytest.fail(f"lecture du presse-papiers primaire : {resultat['erreur']}")
    return str(resultat.get("texte"))


def test_clic_droit_hote(app_fixture, pomper):
    """Clic droit sur un hôte : ligne sélectionnée, menu d'hôte (zéro plugin)."""
    app, _dossier = app_fixture
    fenetre = app.get_active_window()
    arbre = fenetre.arbre
    lignes = _lignes_de_l_arbre(arbre)
    assert "LAB/web" in lignes, lignes
    _clic_sur(arbre.liste, arbre.liste.get_width() // 2, lignes["LAB/web"], 3, pomper)

    menu = arbre.menu
    assert menu.popover.get_visible()
    assert menu.cible is not None and menu.cible.chemin == "LAB/web"
    assert arbre.chemin_selectionne() == "LAB/web"
    labels = _labels(menu.popover.get_menu_model())
    for attendu in (_("Copy Address"), _("Edit"), _("Duplicate Host"), _("Remove")):
        assert attendu in labels, labels
    assert _("Rename Group…") not in labels
    # zéro plugin chargé : aucun protocole connu, pas de « Connect »
    assert _("Connect") not in labels
    menu.popover.popdown()
    pomper()


def test_clic_droit_groupe_et_zone_vide(app_fixture, pomper):
    """Clic droit sur un groupe puis la zone vide : menus distincts et corrects."""
    app, _dossier = app_fixture
    fenetre = app.get_active_window()
    arbre = fenetre.arbre
    lignes = _lignes_de_l_arbre(arbre)
    _clic_sur(arbre.liste, arbre.liste.get_width() // 2, lignes["LAB"], 3, pomper)

    menu = arbre.menu
    assert menu.popover.get_visible()
    assert menu.cible is not None and menu.cible.chemin == "LAB"
    labels = _labels(menu.popover.get_menu_model())
    for attendu in (_("Add Host"), _("New Group…"), _("Rename Group…"), _("Remove")):
        assert attendu in labels, labels
    assert _("Copy Address") not in labels
    assert _("Edit") not in labels
    menu.popover.popdown()
    pomper()

    y_vide = arbre.liste.get_height() - 15
    _clic_sur(arbre.liste, arbre.liste.get_width() // 2, y_vide, 3, pomper)
    assert menu.popover.get_visible()
    assert menu.cible is None
    labels = _labels(menu.popover.get_menu_model())
    for attendu in (_("Add Host"), _("New Group…")):
        assert attendu in labels, labels
    assert _("Remove") not in labels
    assert _("Rename Group…") not in labels
    menu.popover.popdown()
    pomper()


def _bouton(racine, libelle: str):
    """Premier ``Gtk.Button`` alloué de l'arbre de ``racine`` portant ``libelle``.

    ``Adw.AlertDialog`` n'expose pas ses boutons de réponse : ils sont
    retrouvés en parcourant l'arbre des enfants. Le repli vertical du
    ``AdwMessageDialog`` (vieilles libadwaita) garde aussi une copie de
    taille nulle : on préfère un bouton réellement alloué.
    """
    from gi.repository import Gtk

    pile = [racine]
    de_repli: Gtk.Button | None = None
    while pile:
        widget = pile.pop()
        if isinstance(widget, Gtk.Button) and widget.get_label() == libelle:
            if widget.get_width() > 0 and widget.get_height() > 0:
                return widget
            de_repli = de_repli or widget
        enfant = widget.get_first_child()
        while enfant is not None:
            pile.append(enfant)
            enfant = enfant.get_next_sibling()
    return de_repli


def test_clic_milieu_ferme_l_onglet(app_fixture, pomper):
    """Clic milieu : confirmation par défaut (Échap annule), directe sans l'option."""
    from gi.repository import Gtk

    app, _dossier = app_fixture
    fenetre = app.get_active_window()
    contenu = Gtk.Label(label=_("Manual"))
    fenetre.ajouter_onglet(_("Manual"), contenu)
    pomper(5)
    etiquettes = fenetre.onglets.get_n_pages()
    assert etiquettes >= 2  # « Home » + l'onglet
    etiquette = fenetre.onglets.get_tab_label(contenu)
    assert etiquette is not None
    _attendre_allocation(etiquette, pomper)

    # Clic sur la zone de texte de l'étiquette : le bouton « fermer » est à
    # l'extrémité droite, et un clic-milieu dessus déclencherait la
    # confirmation « fermer l'onglet » (bouton ordinaire), pas celle du
    # clic-milieu.
    _clic_sur(etiquette, etiquette.get_width() // 4, etiquette.get_height() // 2, 2, pomper)
    # comportement par défaut : confirmation demandée, onglet encore là
    assert contenu in fenetre.confirmations_onglets
    dialogue = fenetre.confirmations_onglets[contenu]
    annuler = _bouton(dialogue, _("Cancel"))
    assert annuler is not None
    _attendre_presentation(dialogue, pomper)
    _clic_sur(annuler, annuler.get_width() // 2, annuler.get_height() // 2, 1, pomper)
    assert fenetre.onglets.get_n_pages() == etiquettes
    # le rappel « annuler » a tourné, l'animation de fermeture est finie
    assert contenu not in fenetre.confirmations_onglets
    _attendre_disparition(dialogue, pomper)

    # option désactivée : fermeture directe
    fenetre.config.options.confirm_close_tab_middle = False
    _clic_sur(etiquette, etiquette.get_width() // 4, etiquette.get_height() // 2, 2, pomper)
    assert fenetre.onglets.get_n_pages() == etiquettes - 1


def test_f2_renomme_un_groupe(app_fixture, pomper):
    """F2 sur un groupe sélectionné : dialogue de renommage, frappe clavier réelle."""
    app, _dossier = app_fixture
    fenetre = app.get_active_window()
    arbre = fenetre.arbre
    lignes = _lignes_de_l_arbre(arbre)
    _clic_sur(arbre.liste, arbre.liste.get_width() // 2, lignes["LAB"], 1, pomper)
    assert arbre.chemin_selectionne() == "LAB"

    _touche("F2")
    pomper()
    menu = arbre.menu
    assert menu.dialogue is not None
    _attendre_presentation(menu.dialogue, pomper)

    # l'entrée ne prend pas le focus toute seule : un clic l'obtient,
    # puis la frappe réelle remplace tout par « LABO » et valide
    entree = menu.dialogue.get_extra_child()
    assert entree is not None
    assert entree.get_text() == "LAB"
    _clic_sur(entree, entree.get_width() // 2, entree.get_height() // 2, 1, pomper)
    # en GTK4, le focus tient dans le ``Gtk.Text`` interne de l'entrée ;
    # il peut se trouver dans la fenêtre principale (dialogue interne aux
    # libadwaita récentes) ou dans la fenêtre du repli ``MessageDialog``
    racine = entree
    while racine.get_parent() is not None:
        racine = racine.get_parent()
    assert _est_dans(racine.get_focus(), entree)
    _activer(entree)  # vieux Adw : fenêtre X séparée, le clavier X y va
    _touche("a", avec_ctrl=True)
    _xdotool("type", "--delay", "30", "LABO")
    # la frappe doit être inscrite avant la validation : sans cette pompe,
    # les lettres et le Return se jouent dans le même lot d'événements et
    # la réponse du dialogue peut être lue avant les insertions (constaté
    # #72 sous GTK 4.8 : renommage en « LAB », donc sans effet)
    pomper()
    assert entree.get_text() == "LABO"
    _touche("Return")
    pomper()
    _attendre_disparition(menu.dialogue, pomper)
    visibles = {chemin for _, chemin in arbre.lignes_visibles()}
    assert "LABO" in visibles
    assert "LAB" not in visibles
    assert "LABO/web" in visibles  # le contenu suit le groupe renommé
