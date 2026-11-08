# SPDX-License-Identifier: GPL-3.0-or-later
"""Adaptateur VNC réel avec ses dépendances : registre, chargement et transport (#179)."""

from __future__ import annotations

import asyncio
import importlib.util
import struct
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from gi.repository import Gtk

from gcm4.core import registre
from gcm4.core.model import Host

RACINE = Path(__file__).resolve().parents[2]


@pytest.fixture
def adaptateur():
    """Charge le même candidat que le registre utilisateur, puis retire son paquet."""
    candidat = next(
        c for c in registre.candidats_dossier(RACINE / "plugins") if c.nom == "pluginvnc2"
    )
    classe = candidat.charger()
    module = sys.modules[classe.__module__]
    yield module
    registre._oublier_paquet("gcm4_plugin_pluginvnc2")


def hote(options=None, **champs):
    """Hôte VNC sans connexion effective avant open()."""
    return Host(
        name="bureau",
        host="127.0.0.1",
        protocol="vnc",
        protocol_options={"vnc": options or {}},
        **champs,
    )


def test_decouverte_sans_dependance_et_sans_asyncio(adaptateur, monkeypatch):
    """Le protocole est proposé même sans asyncvnc2 ; seul son lancement est refusé."""
    monkeypatch.setitem(sys.modules, "asyncvnc2", None)
    monkeypatch.setattr(adaptateur.boucle, "asyncio_disponible", lambda: False)
    plugin = adaptateur.PLUGIN(SimpleNamespace())
    assert plugin.name == "vnc" and plugin.protocols == ("vnc",)
    assert plugin.default_port("vnc") == 5900
    assert plugin.default_options("vnc")["vnc_shared"] == "True"
    assert plugin.validate_options("vnc", {}) == []
    assert plugin.validate_options("vnc", {"vnc_display": "-1"})[0].champ == "vnc_display"
    with pytest.raises(RuntimeError, match="PyGObject 3.50"):
        plugin.open(hote())


def test_formulaire_options_et_copie(adaptateur):
    """Chaque contrôle conserve sa clé ; aucune dépendance VNC n'est chargée."""
    options = adaptateur.core.options_defaut()
    options["vnc_viewer"] = "ancien"
    changements = []
    zone = adaptateur.PLUGIN(SimpleNamespace()).build_host_options_widget(
        hote(), options, lambda: changements.append(True)
    )
    for cle in ("vnc_shared", "vnc_view_only", "vnc_fullscreen", "vnc_share_clipboard"):
        bouton = zone.controles[cle]
        avant = bouton.get_active()
        bouton.set_active(not avant)
        assert options[cle] == str(not avant)
        bouton.set_active(avant)
    zone.controles["vnc_display"].set_text("12")
    assert options["vnc_display"] == "12"
    assert options["vnc_viewer"] == "ancien"
    assert len(changements) >= 9


@pytest.mark.parametrize("manque", ["spec", "loader", "import", "autre"])
def test_chargement_echec_et_nettoyage(adaptateur, monkeypatch, manque):
    """Le cache partiel est retiré ; les erreurs non liées à l'import sont conservées."""
    monkeypatch.delitem(sys.modules, adaptateur.MODULE_WIDGET, raising=False)
    if manque == "spec":
        monkeypatch.setattr(importlib.util, "spec_from_file_location", lambda *_a: None)
    elif manque == "loader":
        monkeypatch.setattr(
            importlib.util, "spec_from_file_location", lambda *_a: SimpleNamespace(loader=None)
        )
    else:
        erreur = ImportError("absent") if manque == "import" else RuntimeError("inattendu")

        def echouer(_module):
            """Simule l'échec de l'exécution après insertion du module dans le cache."""
            raise erreur

        spec = importlib.util.spec_from_loader(adaptateur.MODULE_WIDGET, loader=None)
        spec.loader = SimpleNamespace(create_module=lambda _spec: None, exec_module=echouer)
        monkeypatch.setattr(importlib.util, "spec_from_file_location", lambda *_a: spec)
    with pytest.raises(RuntimeError if manque == "autre" else FileNotFoundError):
        adaptateur.charger_widget()
    assert adaptateur.MODULE_WIDGET not in sys.modules


def test_module_stable_apres_redecouverte(adaptateur):
    """Les classes GObject ne sont pas réenregistrées en rechargeant l'adaptateur."""
    module = adaptateur.charger_widget()
    classe = registre._importer_fichier("pluginvnc2", RACINE / "plugins/pluginvnc2/gtk4.py")
    reimporte = sys.modules[classe.__module__]
    assert reimporte.charger_widget() is module


@pytest.mark.parametrize("lecture_seule", [False, True])
def test_session_vrai_widget_options_et_fermeture(adaptateur, monkeypatch, lecture_seule):
    """Le vrai VncTab reçoit port/secret/partage ; fermer deux fois n'émet qu'une fois."""
    module = adaptateur.charger_widget()
    monkeypatch.setattr(adaptateur.boucle, "asyncio_disponible", lambda: True)
    # Le transport sera testé séparément ; ici la construction et les
    # contrôleurs sont réels, mais aucune tâche réseau n'est lancée.
    demarrages = []
    monkeypatch.setattr(module.VncDisplay, "start", lambda _s: demarrages.append(True))
    session = adaptateur.PLUGIN(SimpleNamespace()).open(
        hote(
            {
                "vnc_shared": "False",
                "vnc_view_only": str(lecture_seule),
                "vnc_display": "2",
                "vnc_share_clipboard": "True",
            },
            user="utilisateur",
            password="secret-test",
            port=5909,
        )
    )
    assert isinstance(session.widget, Gtk.Box)
    info = session.widget.info
    assert (info.host, info.port, info.username, info.password) == (
        "127.0.0.1",
        5902,
        "utilisateur",
        "secret-test",
    )
    assert info.shared is False and info.view_only is lecture_seule
    assert info.sync_clipboard is not lecture_seule
    assert session.widget.toolbar.get_sensitive() is not lecture_seule
    # Uniquement le suivi visuel du curseur en lecture seule ; aucun
    # contrôleur clavier, clic ou molette n'est attaché par le display.
    controles = session.widget.display.observe_controllers()
    if lecture_seule:
        assert controles.get_n_items() == 1
    else:
        assert controles.get_n_items() > 1
    fermetures = []
    session.widget.connect("close-requested", lambda _s: fermetures.append(True))
    session.close()
    session.close()
    assert fermetures == [True] and demarrages == [True]
    assert session.widget.display._connect_task is None


@pytest.mark.parametrize("partage", [True, False])
@pytest.mark.parametrize("lecture_seule", [True, False])
def test_vrai_rfb_clientinit_et_image(adaptateur, monkeypatch, partage, lecture_seule):
    """Le vrai asyncvnc2 négocie RFB 3.8, transmet shared et décode une image brute."""
    monkeypatch.setattr(adaptateur.boucle, "asyncio_disponible", lambda: True)

    async def scenario():
        """Serveur RFB de boucle locale : pas de réseau externe ni de temporisation fixe."""
        recus = []
        touches = []
        connecte = asyncio.Event()
        touche = asyncio.Event()
        ferme = asyncio.Event()
        erreurs = []

        async def servir(reader, writer):
            """Négocie sécurité None, publie un bureau 8×6 et observe les messages client."""
            try:
                writer.write(b"RFB 003.008\n")
                assert await reader.readexactly(12) == b"RFB 003.008\n"
                writer.write(b"\x01\x01")
                assert await reader.readexactly(1) == b"\x01"
                writer.write(b"\x00\x00\x00\x00")
                recus.append(await reader.readexactly(1))
                nom = b"bureau-local-vnc"
                pixel = bytes([32, 24, 0, 1, 0, 255, 0, 255, 0, 255, 16, 8, 0, 0, 0, 0])
                writer.write(struct.pack(">HH", 8, 6) + pixel + struct.pack(">I", len(nom)) + nom)
                await writer.drain()
                image_envoyee = False
                while True:
                    type_message = (await reader.readexactly(1))[0]
                    if type_message == 0:
                        await reader.readexactly(19)
                    elif type_message == 2:
                        entete = await reader.readexactly(3)
                        await reader.readexactly(int.from_bytes(entete[1:], "big") * 4)
                    elif type_message == 3:
                        await reader.readexactly(9)
                        if not image_envoyee:
                            writer.write(
                                b"\x00\x00\x00\x01"
                                + struct.pack(">HHHHi", 0, 0, 8, 6, 0)
                                + bytes([0, 0, 255, 0]) * 48
                            )
                            await writer.drain()
                            image_envoyee = True
                    elif type_message == 4:
                        message = await reader.readexactly(7)
                        touches.append((message[0], int.from_bytes(message[3:], "big")))
                        touche.set()
                    elif type_message == 5:
                        await reader.readexactly(5)
                    else:
                        raise AssertionError(f"Message inattendu : {type_message}")
            except asyncio.IncompleteReadError:
                pass
            except Exception as exc:
                erreurs.append(exc)
                raise
            finally:
                writer.close()
                await writer.wait_closed()
                ferme.set()

        serveur = await asyncio.start_server(servir, "127.0.0.1", 0)
        port = serveur.sockets[0].getsockname()[1]
        session = adaptateur.PLUGIN(SimpleNamespace()).open(
            hote(
                {
                    "vnc_shared": str(partage),
                    "vnc_view_only": str(lecture_seule),
                    "vnc_share_clipboard": "False",
                },
                port=port,
            )
        )
        session.widget.display.connect("vnc-connected", lambda _s: connecte.set())
        try:
            await asyncio.wait_for(connecte.wait(), 5)
            affichage = session.widget.display
            assert recus == [b"\x01" if partage else b"\x00"]
            assert affichage.client.video.name == "bureau-local-vnc"
            texture = affichage._last_frame_texture
            assert (texture.get_width(), texture.get_height()) == (8, 6)
            affichage.send_text("a")
            if not lecture_seule:
                await asyncio.wait_for(touche.wait(), 2)
                assert touches[0] == (1, ord("a"))
            else:
                assert touches == []
        finally:
            session.close()
            await asyncio.wait_for(ferme.wait(), 3)
            serveur.close()
            await serveur.wait_closed()
        assert erreurs == []

    asyncio.run(asyncio.wait_for(scenario(), 12))


def test_plein_ecran_attend_racine_et_pas_apres_fermeture(adaptateur, monkeypatch):
    """Map avant rattachement ne consomme pas l'option ; fullscreen ne part qu'une fois."""
    module = adaptateur.charger_widget()
    monkeypatch.setattr(module.VncDisplay, "start", lambda _s: None)
    session = adaptateur.VncSession(
        hote(), adaptateur.core.Reglages(5900, False, True, True, True), module
    )
    session._afficher(session.widget)
    assert session.plein_ecran is True
    fenetre = Gtk.Window()
    fenetre.set_child(session.widget)
    demandes = []
    fenetre.fullscreen = lambda: demandes.append(True)
    session._afficher(session.widget)
    session._afficher(session.widget)
    assert demandes == [True] and session.plein_ecran is False
    session.close()
    session.plein_ecran = True
    session._afficher(session.widget)
    assert demandes == [True]
    fenetre.destroy()


def test_lecture_seule_ne_planifie_aucune_ecriture(adaptateur, monkeypatch):
    """Même les raccourcis globaux et une fin de lecture clipboard restent sans effet."""
    module = adaptateur.charger_widget()
    affichage = module.VncDisplay(
        module.VncConnectionInfo(name="test", host="localhost", view_only=True)
    )
    affichage.client = SimpleNamespace()
    planifications = []
    monkeypatch.setattr(module.asyncio, "ensure_future", planifications.append)
    affichage.send_text("ne pas écrire")
    affichage.send_ctrl_alt_del()
    assert affichage.send_power_action("reboot") is False
    affichage._watch_local_clipboard()
    affichage._on_local_clipboard_read(
        SimpleNamespace(read_text_finish=lambda _r: "ne pas transmettre"), None
    )
    assert planifications == []
    assert affichage._clipboard_watched is None
    assert affichage._last_clipboard_text == ""


def test_identifiants_absents_restent_none(adaptateur):
    """Sans utilisateur/mot de passe, pas de chaîne vide transmise au client."""

    class Onglet(Gtk.Box):
        """Widget de contrat isolant le transfert des arguments."""

        def __init__(self, info):
            """Garde les informations sans lancer le transport."""
            super().__init__()
            self.info = info
            self.toolbar = Gtk.Box()

        def close(self):
            """Pas de transport à arrêter."""

    module = ModuleType("client_de_test")
    module.VncConnectionInfo = SimpleNamespace
    module.VncTab = Onglet
    session = adaptateur.VncSession(hote(), adaptateur.core.reglages(hote()), module)
    assert session.widget.info.username is None and session.widget.info.password is None
    session._afficher(session.widget)
    session.close()
