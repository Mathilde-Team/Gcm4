# SPDX-License-Identifier: GPL-3.0-or-later
"""Dialogue d'hôte et protocole local (#297) : champ Adresse masqué, sans adresse.

Comme le reste de ``test_plugin_local.py``, ces tests chargent le plugin
local (mode source) : ils ont besoin de VTE GTK4, absent de Rocky 9 où ce
fichier est écarté (job « gcm4 sur Rocky Linux 9 »).
"""

from __future__ import annotations

from pathlib import Path

import pytest

import gcm4.ui  # noqa: F401  (require_version avant gi.repository)
from gcm4.core import registre
from gcm4.core.chiffrement import ChiffrementGcm
from gcm4.core.config import load_config
from gcm4.ui.host_dialog import MODIFICATION
from tests.gtk4.conftest import _ouvrir as _ouvrir_application  # noqa: E402


@pytest.fixture
def app_local(tmp_path, monkeypatch, pomper):
    """Application sur la fixture aes avec les plugins en mode source (local inclus)."""
    racine = Path(__file__).resolve().parents[2]
    app, dossier = _ouvrir_application(
        tmp_path, monkeypatch, pomper, registre.candidats_dossier(racine / "plugins")
    )
    yield app, dossier
    for fenetre in list(app.get_windows()):
        fenetre.destroy()
    pomper()


def _relire(dossier):
    return load_config(str(dossier / "gcm4.conf"), ChiffrementGcm(str(dossier)))


def _ouvrir(arbre, action, chemin):
    noeud = arbre.ligne(chemin).get_item().noeud if chemin else None
    arbre.menu.cible = noeud
    arbre.menu.executer(action)
    return arbre.menu.dialogue_hote


def test_hote_local_sans_adresse(app_local, pomper):
    """#297 : protocole local — champ Adresse masqué, enregistrement sans adresse."""
    app, dossier = app_local
    arbre = app.get_active_window().arbre
    d = _ouvrir(arbre, "add", "LAB/bases")
    pomper()
    assert "local" in d._protocoles
    d.protocole.set_selected(d._protocoles.index("local"))
    pomper()
    assert not d.adresse.get_visible()
    d.nom.set_text("poste")
    assert d.bouton_enregistrer.get_sensitive() and not d.message.get_visible()
    assert d.enregistrer()
    pomper()
    h = _relire(dossier).store.find("LAB/bases/poste")
    assert h is not None and h.host == "" and h.protocol == "local"


def test_hote_local_edite_sans_adresse(app_local, pomper):
    """#297 : un hôte local sans adresse (importé de GTK3) reste modifiable."""
    app, dossier = app_local
    arbre = app.get_active_window().arbre
    d = _ouvrir(arbre, "add", "LAB/bases")
    pomper()
    d.protocole.set_selected(d._protocoles.index("local"))
    pomper()
    d.nom.set_text("poste")
    assert d.enregistrer()
    pomper()
    d2 = _ouvrir(arbre, "edit", "LAB/bases/poste")
    pomper()
    assert d2.mode == MODIFICATION and not d2.adresse.get_visible()
    d2.description.set_text("modifié")
    assert d2.enregistrer()
    pomper()
    h = _relire(dossier).store.find("LAB/bases/poste")
    assert h.description == "modifié"
