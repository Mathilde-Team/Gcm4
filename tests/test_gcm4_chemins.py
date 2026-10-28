# SPDX-License-Identifier: GPL-3.0-or-later
"""Dossier ~/.config/gcm4 et migration depuis ~/.gcm (#99)."""

from __future__ import annotations

import os
import stat

import pytest

from gcm4 import app
from gcm4.core import chemins

CONF = "[options]\nversion = 1\n[host 1]\nname = web\n"


def _ancien(home, cle="CLE", droits_cle=0o600):
    """Crée ~/.gcm (GTK3) : gcm.conf, .gcm.key, gcm.conf.bak, log/."""
    d = home / ".gcm"
    (d / "log").mkdir(parents=True)
    (d / "gcm.conf").write_text(CONF, encoding="utf-8")
    (d / "gcm.conf.bak").write_text("ancienne", encoding="utf-8")
    (d / ".gcm.key").write_text(cle, encoding="utf-8")
    os.chmod(d / ".gcm.key", droits_cle)
    (d / "log" / "gcm-app.log").write_text("x", encoding="utf-8")
    return d


def _contenu(d):
    return {str(p.relative_to(d)): p.read_bytes() for p in sorted(d.rglob("*")) if p.is_file()}


def test_dossier_par_defaut_xdg(tmp_path):
    """XDG_CONFIG_HOME absolu, sinon ~/.config (absente, vide ou relative)."""
    assert chemins.dossier_par_defaut("/h", {}) == "/h/.config/gcm4"
    assert chemins.dossier_par_defaut("/h", {"XDG_CONFIG_HOME": ""}) == "/h/.config/gcm4"
    assert chemins.dossier_par_defaut("/h", {"XDG_CONFIG_HOME": "rel"}) == "/h/.config/gcm4"
    assert chemins.dossier_par_defaut("/h", {"XDG_CONFIG_HOME": "/x"}) == "/x/gcm4"
    assert chemins.ancien_dossier("/h") == "/h/.gcm"
    assert chemins.fichier_conf("/c") == "/c/gcm4.conf"


def test_ancien_seul_copie_et_renomme(tmp_path):
    """~/.gcm seul : tout copié, fichiers renommés gcm4, ancien dossier intact."""
    ancien = _ancien(tmp_path)
    avant = _contenu(ancien)
    nouveau = tmp_path / ".config" / "gcm4"
    m = chemins.preparer(str(nouveau), str(ancien))
    assert m.effectuee and m.dossier_copie_depuis == str(ancien)
    assert sorted(m.fichiers_copies) == [".gcm4.key", "gcm4.conf", "gcm4.conf.bak"]
    assert _contenu(nouveau) == {
        ".gcm4.key": b"CLE",
        "gcm4.conf": CONF.encode(),
        "gcm4.conf.bak": b"ancienne",
        "log/gcm-app.log": b"x",
    }
    assert _contenu(ancien) == avant
    assert stat.S_IMODE(os.stat(nouveau / ".gcm4.key").st_mode) == 0o600
    assert stat.S_IMODE(os.stat(nouveau / "gcm4.conf").st_mode) == 0o600


def test_cle_lisible_par_tous_reprise_en_0600(tmp_path):
    """Une .gcm.key 0644 devient une .gcm4.key 0600."""
    ancien = _ancien(tmp_path, droits_cle=0o644)
    nouveau = tmp_path / "n"
    chemins.preparer(str(nouveau), str(ancien))
    assert stat.S_IMODE(os.stat(nouveau / ".gcm4.key").st_mode) == 0o600


def test_idempotent_et_jamais_d_ecrasement(tmp_path):
    """Second appel : rien ; nouveau dossier modifié puis ancien modifié : nouveau intact."""
    ancien = _ancien(tmp_path)
    nouveau = tmp_path / "n"
    chemins.preparer(str(nouveau), str(ancien))
    (nouveau / "gcm4.conf").write_text("modifié", encoding="utf-8")
    (ancien / "gcm.conf").write_text("GTK3 a écrit", encoding="utf-8")
    m = chemins.preparer(str(nouveau), str(ancien))
    assert not m.effectuee
    assert (nouveau / "gcm4.conf").read_text(encoding="utf-8") == "modifié"


def test_nouveau_seul_et_aucun(tmp_path):
    """Nouveau seul : rien ; aucun dossier : rien, et rien n'est créé."""
    nouveau = tmp_path / "n"
    nouveau.mkdir()
    (nouveau / "gcm4.conf").write_text("x", encoding="utf-8")
    assert not chemins.preparer(str(nouveau), str(tmp_path / ".gcm")).effectuee
    vide = tmp_path / "vide"
    assert not chemins.preparer(str(vide), str(tmp_path / "absent")).effectuee
    assert not vide.exists()


def test_dossier_explicite_ancien_format(tmp_path):
    """``-c ~/.gcm`` : gcm4.conf/.gcm4.key copiés à côté, anciens fichiers conservés pour GTK3."""
    ancien = _ancien(tmp_path)
    m = chemins.preparer(str(ancien))
    assert m.dossier_copie_depuis is None and ".gcm4.key" in m.fichiers_copies
    assert (ancien / "gcm.conf").read_text(encoding="utf-8") == CONF
    assert (ancien / "gcm4.conf").read_text(encoding="utf-8") == CONF
    assert (ancien / ".gcm.key").exists() and (ancien / ".gcm4.key").read_text() == "CLE"


def test_destination_presente_jamais_ecrasee(tmp_path):
    """gcm4.conf déjà là à côté d'un gcm.conf : gcm4.conf intact, la clé seule est copiée."""
    d = tmp_path / "d"
    d.mkdir()
    (d / "gcm.conf").write_text("ancien", encoding="utf-8")
    (d / ".gcm.key").write_text("CLE", encoding="utf-8")
    (d / "gcm4.conf").write_text("actuel", encoding="utf-8")
    assert chemins.preparer(str(d)).fichiers_copies == [".gcm4.key"]
    assert (d / "gcm4.conf").read_text(encoding="utf-8") == "actuel"


def test_copie_concurrente_jamais_ecrasee(tmp_path):
    """Destination créée entre la vérification et la copie : False, rien d'écrasé, aucun temporaire."""
    d = tmp_path / "d"
    d.mkdir()
    (d / "a").write_text("source", encoding="utf-8")
    (d / "b").write_text("concurrent", encoding="utf-8")
    assert chemins._copier_sans_ecraser(str(d / "a"), str(d / "b")) is False
    assert (d / "b").read_text(encoding="utf-8") == "concurrent"
    assert sorted(p.name for p in d.iterdir()) == ["a", "b"]


def test_echec_ancien_intact(tmp_path, monkeypatch):
    """Copie interrompue (disque plein) : MigrationError, ancien intact, dossier partiel supprimé."""
    import shutil

    import gcm4_core

    ancien = _ancien(tmp_path)
    avant = _contenu(ancien)
    vraie_copie = shutil.copytree

    def copie_interrompue(source, destination, *a, **k):
        os.makedirs(destination)
        shutil.copy2(os.path.join(source, "gcm.conf"), destination)
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(gcm4_core.shutil, "copytree", copie_interrompue)
    with pytest.raises(chemins.MigrationError, match="No space"):
        chemins.preparer(str(tmp_path / "n"), str(ancien))
    monkeypatch.setattr(gcm4_core.shutil, "copytree", vraie_copie)
    assert not (tmp_path / "n").exists()
    assert _contenu(ancien) == avant


def test_main_echec_migration_code_4(tmp_path, monkeypatch, capsys):
    """Migration impossible : code 4, message sur stderr, GTK jamais chargé."""

    def echoue(*_a):
        raise chemins.MigrationError("disque plein")

    monkeypatch.setattr(chemins, "preparer", echoue)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert app.main([]) == app.CODE_MIGRATION_IMPOSSIBLE
    erreur = capsys.readouterr().err
    assert "disque plein" in erreur and str(tmp_path / ".gcm") in erreur
