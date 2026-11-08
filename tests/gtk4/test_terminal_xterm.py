# SPDX-License-Identifier: GPL-3.0-or-later
"""Vrai WebKit/xterm local : ANSI, PTY, presse-papiers, recherche et refus (#76).

Le sandbox WebKit reste actif en production. Comme test_plugin_web_webkit,
ces tests seuls le désactivent dans les conteneurs sans user namespaces.
"""

from __future__ import annotations

import json
import os
import signal
import sys
import time
from types import SimpleNamespace

import pytest
from gi.repository import GLib, Gtk

os.environ.setdefault("WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS", "1")

from gcm4.core.terminal import Lancement  # noqa: E402
from gcm4.ui import xterm  # noqa: E402
from gcm4.ui.terminal import PALETTE, rgba  # noqa: E402


def attendre(condition, delai=15):
    """Pompe GTK jusqu'à une condition, sans masquer une erreur par un saut."""
    fin = time.monotonic() + delai
    contexte = GLib.MainContext.default()
    while not condition() and time.monotonic() < fin:
        contexte.iteration(False)
        time.sleep(0.002)
    assert condition()


def js(terminal, expression):
    """Lit une valeur du vrai moteur JavaScript, sans boucle imbriquée en production."""
    resultat = []
    terminal._eval(
        f"JSON.stringify({expression})",
        lambda valeur: resultat.append(json.loads(valeur.to_string())),
    )
    attendre(lambda: resultat)
    return resultat[0]


def texte(terminal):
    """Buffer réel xterm, pas la sortie brute capturée du processus."""
    resultat = []
    terminal.texte(resultat.append)
    attendre(lambda: resultat)
    return resultat[0]


def message(terminal, genre, valeur=None):
    """Injecte un message au même validateur que le signal WebKit."""
    terminal._message(
        None,
        SimpleNamespace(to_string=lambda: json.dumps({"type": genre, "value": valeur})),
    )


@pytest.fixture
def terminal():
    """Page locale réelle et fenêtre native, enfants toujours arrêtés et récoltés."""
    t = xterm.Terminal()
    fenetre = Gtk.Window(child=t, default_width=800, default_height=500)
    fenetre.present()
    attendre(lambda: t._ready)
    yield t, fenetre
    if isinstance(t._processus, xterm.ProcessusPty):
        t.stop()
        attendre(lambda: t._processus.fd == -1)
    else:
        t._processus = None
    fenetre.destroy()


def test_ansi_unicode_et_sortie_non_interpretee(terminal):
    """Le renderer analyse ANSI, rend Unicode et ne peut exécuter la sortie distante."""
    t, _ = terminal
    t.feed(b"\x1b[31mrouge\x1b[0m caf\xc3")
    t.feed(b"\xa9 <script>window.intrus=1</script>\r\n")
    attendre(lambda: "café" in texte(t))
    rendu = texte(t)
    assert "rouge café <script>window.intrus=1</script>" in rendu
    assert "\x1b" not in rendu
    assert js(t, "typeof window.intrus") == "undefined"
    assert t.view.get_network_session().is_ephemeral()
    assert not t.view.get_settings().get_allow_file_access_from_file_urls()
    assert not t.view.get_settings().get_allow_universal_access_from_file_urls()


def test_focus_natif_transmis_a_la_saisie(terminal):
    """Le focus GTK atteint le textarea xterm, puis la recherche peut le reprendre."""
    t, fenetre = terminal
    t.view.grab_focus()
    attendre(lambda: js(t, "document.activeElement.classList.contains('xterm-helper-textarea')"))
    message(t, "search")
    assert fenetre.get_focus() is not t.view
    assert t.recherche.get_search_mode()


def test_vrai_pty_entree_et_taille(terminal):
    """Saisie par le pont xterm et redimensionnement transmis au vrai programme."""
    t, fenetre = terminal
    lance = []
    fini = []
    t.connect("child-exited", lambda _t, statut: fini.append(statut))
    t.spawn(
        Lancement(
            [
                sys.executable,
                "-u",
                "-c",
                "import os; print('pret'); s=input(); print('recu='+s); "
                "print('taille='+str(os.get_terminal_size(0).columns))",
            ]
        ),
        ["TERM=xterm"],
        lambda _t, pid, erreur: lance.append((pid, erreur)),
    )
    attendre(lambda: lance)
    assert lance[0][0] > 0 and lance[0][1] is None
    attendre(lambda: "pret" in texte(t))
    avant = js(t, "gcmTerminal.size()")[0]
    fenetre.set_default_size(1100, 700)
    attendre(lambda: js(t, "gcmTerminal.size()")[0] > avant)
    colonnes = js(t, "gcmTerminal.size()")[0]
    t._eval('gcmTerminal.paste("été\\r")')
    attendre(lambda: fini)
    attendre(lambda: "recu=été" in texte(t))
    assert f"taille={colonnes}" in texte(t)
    assert fini == [0] and t._processus.fd == -1


def test_recherche_et_presse_papiers_reels(terminal):
    """Les contrôles GTK et les callbacks du presse-papiers utilisent le vrai buffer."""
    t, _ = terminal
    t.feed(b"alpha aiguille beta aiguille\r\n")
    attendre(lambda: "aiguille" in texte(t))
    message(t, "search")
    assert t.recherche.get_search_mode()
    t.champ.set_text("aiguille")
    t.champ.emit("activate")
    attendre(lambda: js(t, "gcmTerminal.selection()") == "aiguille")
    ligne = t.recherche.get_child()
    ligne.get_last_child().get_prev_sibling().emit("clicked")
    ligne.get_last_child().emit("clicked")
    message(t, "copy")
    resultat = []

    def lu(clipboard, reponse):
        resultat.append(clipboard.read_text_finish(reponse))

    attendre(lambda: not t._en_vol)
    # La copie asynchrone du buffer doit finir avant de relire le clipboard.
    attendre(lambda: js(t, "gcmTerminal.selection()") == "aiguille")
    t.get_clipboard().read_text_async(None, lu)
    attendre(lambda: resultat)
    assert resultat == ["aiguille"]
    message(t, "paste")


def test_palette_et_encodage_incremental(terminal):
    """Les couleurs de l'hôte sont appliquées, le décodage conserve les caractères fragmentés."""
    t, _ = terminal
    t.set_colors(rgba("#123456"), rgba("#234567"), [rgba(c) for c in PALETTE])
    t.set_backspace_binding(3)
    t.set_delete_binding(1)
    attendre(lambda: js(t, "document.body.style.backgroundColor") == "rgb(35, 69, 103)")
    t.set_encoding("SHIFT_JIS")
    donnees = "日本".encode("shift_jis")
    t.feed(donnees[:1])
    t.feed(donnees[1:])
    attendre(lambda: "日本" in texte(t))
    assert t.get_encoding() == "SHIFT_JIS"


@pytest.mark.parametrize(
    "brut",
    [
        "pas json",
        "[]",
        "null",
        "{}",
        '{"type":"input","value":17}',
        '{"type":"resize","value":[true,24]}',
        '{"type":"resize","value":[1,24]}',
        '{"type":"resize","value":[80,1001]}',
        '{"type":"resize","value":[80]}',
        '{"type":"inconnu"}',
        "x" * 131073,
    ],
)
def test_messages_invalides_sans_effet(terminal, brut):
    """Un message mal formé ne lance ni processus ni commande et n'arrête pas la page."""
    t, _ = terminal
    t._message(None, SimpleNamespace(to_string=lambda: brut))
    t._message(None, object())
    assert t._processus is None and not t._entree and not t._erreur


def test_messages_valides_et_refus_clipboard(terminal, monkeypatch):
    """Validation des dimensions, du binaire et des erreurs du clipboard."""
    t, _ = terminal
    tailles, entrees, scripts = [], [], []
    t._processus = SimpleNamespace(
        fd=9, resize=lambda *a: tailles.append(a), erase=lambda: b"\x08"
    )
    monkeypatch.setattr(t, "_ajouter_entree", entrees.append)
    message(t, "resize", [100, 40])
    message(t, "binary", "\x80")
    message(t, "input", "été")
    message(t, "erase")
    message(t, "ready")
    message(t, "ack")
    assert tailles == [(100, 40)] and entrees == [b"\x80", "été".encode(), b"\x08"]
    t._processus.fd = -1
    message(t, "resize", [100, 40])
    message(t, "erase")
    assert len(tailles) == 1 and len(entrees) == 3
    monkeypatch.setattr(t, "_eval", lambda script: scripts.append(script))
    t._coller(SimpleNamespace(read_text_finish=lambda _r: ""), None)
    t._coller(SimpleNamespace(read_text_finish=lambda _r: "collé"), None)
    assert len(scripts) == 1 and "gcmTerminal.paste" in scripts[0]

    def refuser(_r):
        raise GLib.Error("lecture refusée")

    t._coller(SimpleNamespace(read_text_finish=refuser), None)
    assert len(scripts) == 1


@pytest.mark.parametrize(
    "adresse",
    ["file:///etc/passwd", "https://example.invalid/", "gcm4-terminal://local/../versions.json"],
)
def test_navigation_etrangere_refusee(terminal, adresse):
    """Un vrai changement d'URL ne remplace jamais la page locale par du contenu étranger."""
    t, _ = terminal
    vus = []
    t.view.connect("decide-policy", lambda *_a: vus.append(True))
    # Notre handler peut arrêter l'émission ; notify::uri atteste aussi la requête.
    t.view.connect("notify::uri", lambda *_a: vus.append(True))
    t.view.load_uri(adresse)
    attendre(lambda: vus)
    attendre(lambda: t.view.get_uri() == xterm.URI)
    assert js(t, "typeof gcmTerminal") == "object"
    assert not t._erreur


def test_politiques_et_permissions(terminal):
    """Refus explicite des fenêtres et des permissions, ressource locale autorisée."""
    t, _ = terminal
    appels = []
    demande = SimpleNamespace(deny=lambda: appels.append("deny"))
    assert t._permission(None, demande)
    decision = SimpleNamespace(ignore=lambda: appels.append("ignore"))
    assert t._politique(None, decision, xterm.WebKit.PolicyDecisionType.NEW_WINDOW_ACTION)
    assert appels == ["deny", "ignore"]


def test_ressources_limitees_et_fichier_absent(tmp_path, monkeypatch):
    """Le handler de schéma ne lit jamais un fichier libre ou un manifeste privé."""
    finies, erreurs = [], []
    demande = SimpleNamespace(
        get_path=lambda: "/versions.json",
        get_uri=lambda: "gcm4-terminal://local/versions.json",
        finish_error=erreurs.append,
        finish=lambda *_a: finies.append(True),
    )
    xterm._ressource(demande)
    assert erreurs and not finies
    demande.get_path = lambda: "/terminal.js"
    demande.get_uri = lambda: "gcm4-terminal://autre/terminal.js"
    xterm._ressource(demande)
    demande.get_uri = lambda: "gcm4-terminal://local/terminal.js"
    monkeypatch.setattr(xterm, "ASSETS", tmp_path)
    xterm._ressource(demande)
    assert len(erreurs) == 3 and not finies


def test_erreur_renderer_visible_et_idempotente(terminal):
    """Une erreur de rendu n'est jamais une page blanche ni une exception avalée."""
    t, _ = terminal
    annulee = GLib.Error.new_literal(
        xterm.WebKit.network_error_quark(), "annulé", xterm.WebKit.NetworkError.CANCELLED
    )
    assert t._echec(t.view, None, xterm.URI, annulee)
    assert not t._erreur
    t._echec()
    t._echec()
    assert t._erreur
    assert "xterm.js" in t.get_last_child().get_label()
    t._evalue(SimpleNamespace(evaluate_javascript_finish=lambda _r: None), None, None)

    def refuser(_r):
        raise GLib.Error("JavaScript refusé")

    t._evalue(SimpleNamespace(evaluate_javascript_finish=refuser), None, None)


def test_fermeture_recalcitrante_recoltee(terminal):
    """Un enfant qui ignore SIGHUP et SIGTERM est tué, sans figer GTK ni rester zombie."""
    t, _ = terminal
    fini = []
    t.connect("child-exited", lambda _t, statut: fini.append(statut))
    t.spawn(
        Lancement(
            [
                sys.executable,
                "-u",
                "-c",
                "import signal,time; signal.signal(signal.SIGHUP,signal.SIG_IGN); "
                "signal.signal(signal.SIGTERM,signal.SIG_IGN); print('pret'); time.sleep(60)",
            ]
        ),
        ["TERM=xterm"],
        lambda *_a: None,
    )
    attendre(lambda: "pret" in texte(t))
    t.stop()
    t.stop()
    attendre(lambda: fini, delai=8)
    assert fini == [signal.SIGKILL] and t._escalade == 2
    assert t._processus.process.poll() == -signal.SIGKILL


def test_ecritures_partielles_et_erreur_pty(terminal, monkeypatch):
    """Les écritures partielles sont conservées, une erreur arrête la surveillance."""
    t, _ = terminal
    veilles = []
    tailles = iter((1, 2))
    t._processus = SimpleNamespace(fd=9, write=lambda _d: next(tailles))
    monkeypatch.setattr(GLib, "io_add_watch", lambda *a: veilles.append(a) or 123)
    t._ajouter_entree(b"ab")
    t._ajouter_entree(b"c")
    assert len(veilles) == 1
    assert t._ecrire(None, None) == GLib.SOURCE_CONTINUE
    assert t._entree == b"bc"
    assert t._ecrire(None, None) == GLib.SOURCE_REMOVE
    assert not t._entree and t._ecriture == 0

    def refuser(_d):
        raise OSError(5, "PTY indisponible")

    t._processus.write = refuser
    t._entree.extend(b"reste")
    assert t._ecrire(None, None) == GLib.SOURCE_REMOVE
    assert not t._entree


def test_confirmation_exec_et_secret_progressifs(terminal):
    """L'amorce non prête et le tube secret partiel ne sont pas déclarés terminés."""
    t, _ = terminal
    reponses = []
    prets = iter((False, True))
    secrets = iter((False, True))
    t._processus = SimpleNamespace(
        pret=lambda: next(prets),
        envoyer_secret=lambda: next(secrets),
        process=SimpleNamespace(pid=42),
    )

    def callback(*a):
        reponses.append(a)

    assert t._lancement_fini(None, None, callback) == GLib.SOURCE_CONTINUE
    assert not t._spawn_complete and not reponses
    assert t._lancement_fini(None, None, callback) == GLib.SOURCE_REMOVE
    assert t._spawn_complete and reponses == [(t, 42, None)]
    t._secret_watch = 123
    assert t._envoyer_secret() and t._secret_watch == 123
    assert not t._envoyer_secret() and t._secret_watch == 0


def test_erreur_exec_et_lecture_recoltees_sans_fausse_fin(terminal, monkeypatch):
    """Une erreur d'exec ou lecture ne fuit ni callback ni surveillance GLib."""
    t, _ = terminal
    reponses, signaux, fermees, retirees, fins = [], [], [], [], []

    def refuser():
        raise OSError(5, "PTY indisponible")

    t._processus = SimpleNamespace(
        pret=refuser,
        read=refuser,
        eof=False,
        poll=lambda: None,
        signaler=lambda: signaux.append(True),
        close=lambda: fermees.append(True),
    )
    assert t._lancement_fini(None, None, lambda *a: reponses.append(a)) == GLib.SOURCE_REMOVE
    assert t._spawn_failed and reponses[0][1] == -1
    assert isinstance(reponses[0][2], GLib.Error)
    assert t._lire() == GLib.SOURCE_REMOVE
    assert t._processus.eof and signaux == [True]
    t._processus.poll = lambda: 256
    t._lecture = t._ecriture = t._secret_watch = t._spawn_watch = 123
    monkeypatch.setattr(GLib, "source_remove", retirees.append)
    t.connect("child-exited", lambda *a: fins.append(a))
    assert t._poller() == GLib.SOURCE_REMOVE
    assert retirees == [123] * 4 and fermees == [True] and not fins
