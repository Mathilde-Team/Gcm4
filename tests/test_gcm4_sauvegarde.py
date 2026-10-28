# SPDX-License-Identifier: GPL-3.0-or-later
"""Aperçu d'import, bilan et copie de la configuration, sans GTK (#153)."""

from __future__ import annotations

import os
import stat

import pytest

from gcm4.core import sauvegarde
from gcm4.core.chiffrement import ChiffrementGcm
from gcm4.core.config import Config, load_config, save_config
from gcm4.core.exchange import Conflit, ExchangeError, Rapport
from gcm4.core.model import Host, HostStore


def _store(*hotes: Host) -> HostStore:
    store = HostStore()
    for h in hotes:
        store.add(h)
    return store


def test_apercu_ne_modifie_pas_le_magasin():
    """Aperçu : bilan de chaque stratégie, magasin identique avant et après."""
    store = _store(Host(name="web", group="LAB", host="a"))
    lus = [Host(name="web", group="LAB", host="b"), Host(name="db", group="NEUF", host="c")]
    ignorer = sauvegarde.apercu(store, lus, Conflit.IGNORER)
    assert (ignorer.ajoutes, ignorer.ignores, ignorer.groupes_crees) == (
        ["NEUF/db"],
        ["LAB/web"],
        ["NEUF"],
    )
    assert sauvegarde.apercu(store, lus, Conflit.RENOMMER).renommes == [("LAB/web", "LAB/web (2)")]
    assert sauvegarde.apercu(store, lus, Conflit.REMPLACER).remplaces == ["LAB/web"]
    assert [h.path for h in store.hosts] == ["LAB/web"]
    assert store.find("LAB/web").host == "a"
    assert "NEUF" not in store.groups


def test_bilan():
    """Phrase de bilan (msgid anglais sans traduction chargée)."""
    rapport = Rapport(
        ajoutes=["a/x", "a/y"],
        remplaces=["b"],
        renommes=[("c", "c (2)")],
        ignores=[],
        groupes_crees=["a"],
    )
    assert sauvegarde.bilan("h.json", rapport) == (
        "Import of h.json: added 2, replaced 1, renamed 1, ignored 0, new groups 1"
    )


def test_avec_mots_de_passe():
    """Un seul mot de passe suffit ; aucun hôte : faux."""
    assert sauvegarde.avec_mots_de_passe([Host(name="a"), Host(name="b", password="p")])
    assert not sauvegarde.avec_mots_de_passe([Host(name="a")])
    assert not sauvegarde.avec_mots_de_passe([])


def _config(dossier, *hotes):
    dossier.mkdir(parents=True, exist_ok=True)
    cfg = Config()
    for h in hotes:
        cfg.store.add(h)
    save_config(str(dossier / "gcm4.conf"), cfg, ChiffrementGcm(str(dossier)))
    return str(dossier)


def test_enregistrer_sous_copie_conf_et_cle(tmp_path):
    """gcm4.conf et .gcm4.key copiés en 0600 ; la copie se relit avec ses mots de passe."""
    source = _config(tmp_path / "conf", Host(name="h", group="G", password="s3cret"))
    cible = tmp_path / "copie"
    cible.mkdir()
    crees = sauvegarde.enregistrer_sous(source, cible)
    assert sorted(p.name for p in crees) == [".gcm4.key", "gcm4.conf"]
    for fichier in crees:
        assert stat.S_IMODE(fichier.stat().st_mode) == 0o600
    relu = load_config(str(cible / "gcm4.conf"), ChiffrementGcm(str(cible)))
    assert relu.store.find("G/h").password == "s3cret"


def test_enregistrer_sous_sans_cle(tmp_path):
    """Pas de .gcm4.key (aucun mot de passe enregistré) : seul gcm4.conf est copié."""
    source = tmp_path / "conf"
    source.mkdir()
    (source / "gcm4.conf").write_text("[options]\n", encoding="utf-8")
    cible = tmp_path / "copie"
    cible.mkdir()
    assert [p.name for p in sauvegarde.enregistrer_sous(str(source), cible)] == ["gcm4.conf"]


def test_enregistrer_sous_refus(tmp_path):
    """Aucune configuration, même dossier, fichier déjà présent : erreur, rien d'écrasé."""
    vide = tmp_path / "vide"
    vide.mkdir()
    with pytest.raises(ExchangeError, match="No configuration to save yet"):
        sauvegarde.enregistrer_sous(str(vide), tmp_path)
    source = _config(tmp_path / "conf", Host(name="h", password="p"))
    with pytest.raises(ExchangeError, match="other than the configuration folder"):
        sauvegarde.enregistrer_sous(source, source)
    cible = tmp_path / "copie"
    cible.mkdir()
    (cible / ".gcm4.key").write_text("autre", encoding="utf-8")
    with pytest.raises(ExchangeError, match=r"already contains \.gcm4\.key"):
        sauvegarde.enregistrer_sous(source, cible)
    assert (cible / ".gcm4.key").read_text(encoding="utf-8") == "autre"
    assert not (cible / "gcm4.conf").exists()


def test_enregistrer_sous_echec_ne_laisse_rien(tmp_path, monkeypatch):
    """Écriture interrompue après le premier fichier : le fichier déjà copié est retiré."""
    source = _config(tmp_path / "conf", Host(name="h", password="p"))
    cible = tmp_path / "copie"
    cible.mkdir()
    vrai_open = os.open
    appels = []

    def open_fragile(chemin, *args, **kwargs):
        appels.append(chemin)
        if len(appels) == 2:
            raise OSError(28, "disque plein")
        return vrai_open(chemin, *args, **kwargs)

    monkeypatch.setattr(sauvegarde.os, "open", open_fragile)
    with pytest.raises(ExchangeError, match="disque plein"):
        sauvegarde.enregistrer_sous(source, cible)
    assert list(cible.iterdir()) == []
