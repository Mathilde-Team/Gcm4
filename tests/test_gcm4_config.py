# SPDX-License-Identifier: GPL-3.0-or-later
"""Lecture et écriture de gcm.conf sans GTK (#137)."""

from __future__ import annotations

import codecs
import configparser
import errno
import os
import stat

import pytest

from gcm4.core import config as config_mod
from gcm4.core.config import AppConfig, ConfigError, load_config, save_config, update_keys
from gcm4.core.model import Host

# gcm.conf telle qu'écrite par Wmain.writeConfig (anonymisée) : options
# globales, [window], groupes vides, raccourcis, hôtes de deux protocoles,
# groupes imbriqués, section inconnue d'un plugin, valeurs atypiques.
GCM_CONF = """\
[options]
word-separators = -A-Za-z0-9,./?%&#:_=+@~
buffer-lines = 2000
confirm-exit = 1
confirm-close-tab = False
cycle-tabs = peut-etre
font = Monospace 11
transparency = 10
version = 1.3.3
app-title = Gnome Connection Manager
libvirt-default-user = root

[window]
collapsed-folders = 2
group-colors = LAB=#ff8800,PROD/eu=#00aaff
left-panel-width = 260
window-width = 1200
show-panel = True
open-tabs = LAB/web

[host 2]
group = PROD/eu
name = api
description = Réseau européen
host = api.example.org
user = admin
pass = Q2hpZmZyw6k=
private_key = /home/u/.ssh/id_ed25519
port = 2222
tunnel = L8080:localhost:80
type = ssh
commands = uptime\\nwho
keepalive = 30
font-color =
back-color =
x11 = False
agent = True
protocol = proto-a
workspace = False

[host 1]
group = LAB
name = web
host = 10.0.0.1
user = u
pass =
port = abc
protocol = proto-b
workspace = oui
resolution = 1280x800
pre-connect-command = echo avant

[empty_groups]
names = LAB/vide,ARCHIVE

[shortcuts]
shortcut1 = <control>F1
command1 = ls -la\\n

[plugin inconnu]
cle = valeur
"""


@pytest.fixture
def conf(tmp_path, monkeypatch):
    """Fichier gcm.conf de référence ; la mémoire des .bak est remise à zéro."""
    monkeypatch.setattr(config_mod, "_sauvegardes", set())
    chemin = tmp_path / "gcm.conf"
    chemin.write_text(GCM_CONF, encoding="utf-8")
    return chemin


def _ini(chemin):
    cp = configparser.RawConfigParser(interpolation=None)
    cp.optionxform = str
    cp.read(chemin, encoding="utf-8")
    return cp


def _hotes(cp):
    return sorted(
        (dict(cp.items(s)) for s in cp.sections() if s.startswith("host ")),
        key=lambda d: (d.get("group", ""), d.get("name", "")),
    )


def test_fichier_absent(tmp_path):
    """Fichier absent : configuration par défaut, aucune erreur."""
    cfg = load_config(str(tmp_path / "absent.conf"))
    assert cfg.options == AppConfig() and cfg.store.hosts == [] and cfg.brut.sections() == []


def test_aller_retour_sans_perte(conf, tmp_path):
    """save_config(load_config(f)) : chaque section, clé et valeur d'origine est conservée."""
    sortie = tmp_path / "sortie.conf"
    save_config(str(sortie), load_config(str(conf)))
    avant, apres = _ini(conf), _ini(sortie)
    for section in avant.sections():
        if section.startswith("host "):
            continue
        for cle, valeur in avant.items(section):
            if (section, cle) in {("window", "group-colors"), ("empty_groups", "names")}:
                continue  # réécrits depuis le modèle, comparés ci-dessous
            assert apres.get(section, cle) == valeur, (section, cle)
    hotes_avant, hotes_apres = _hotes(avant), _hotes(apres)
    # Longueurs comparées à la main : zip(strict=True) n'existe pas en Python 3.9 (#223).
    assert len(hotes_apres) == len(hotes_avant)
    for h_avant, h_apres in zip(hotes_avant, hotes_apres):  # noqa: B905
        assert {k: v for k, v in h_apres.items() if k in h_avant} == h_avant
    assert sorted(apres.get("window", "group-colors").split(",")) == [
        "LAB=#ff8800",
        "PROD/eu=#00aaff",
    ]
    assert apres.get("empty_groups", "names") == "ARCHIVE,LAB/vide"


def test_ecriture_idempotente(conf, tmp_path):
    """Deux allers-retours donnent le même fichier, octet pour octet."""
    un, deux = tmp_path / "1.conf", tmp_path / "2.conf"
    save_config(str(un), load_config(str(conf)))
    save_config(str(deux), load_config(str(un)))
    assert un.read_bytes() == deux.read_bytes()


def test_options_typees(conf):
    """Options connues typées ; texte d'origine gardé (« 1 », valeur invalide)."""
    cfg = load_config(str(conf))
    o = cfg.options
    assert (o.confirm_exit, o.confirm_close_tab, o.cycle_tabs) == (True, False, True)
    assert (o.font, o.transparency, o.app_title) == (
        "Monospace 11",
        10,
        "Gnome Connection Manager",
    )
    assert cfg.brut.get("options", "libvirt-default-user") == "root"  # option de plugin, brute


def test_option_modifiee_ecrite(conf, tmp_path):
    """Une option modifiée est écrite au format de Wmain (str())."""
    cfg = load_config(str(conf))
    cfg.options.cycle_tabs = False
    cfg.options.transparency = 25
    save_config(str(conf), cfg)
    cp = _ini(conf)
    assert (cp.get("options", "cycle-tabs"), cp.get("options", "transparency")) == ("False", "25")
    assert cp.get("options", "confirm-exit") == "1"


def test_hotes_champs_communs_et_options_de_protocole(conf):
    """Champs communs dans Host ; le reste dans protocol_options[protocole], tel quel."""
    store = load_config(str(conf)).store
    api, web = store.find("PROD/eu/api"), store.find("LAB/web")
    assert (api.host, api.user, api.port, api.description) == (
        "api.example.org",
        "admin",
        2222,
        "Réseau européen",
    )
    assert api.password == "Q2hpZmZyw6k="  # SansChiffrement : valeur stockée conservée
    assert api.protocol_options["proto-a"]["tunnel"] == "L8080:localhost:80"
    assert api.protocol_options["proto-a"]["commands"] == "uptime\\nwho"
    assert web.port is None and web.protocol_options["proto-b"]["port"] == "abc"
    assert web.workspace is False and web.protocol_options["proto-b"]["workspace"] == "oui"
    assert web.protocol_options["proto-b"]["resolution"] == "1280x800"
    assert web.pre_connect_command == "echo avant"


def test_groupes(conf):
    """Groupes vides, couleurs et repliés relus."""
    store = load_config(str(conf)).store
    assert {"LAB", "LAB/vide", "ARCHIVE", "PROD", "PROD/eu"} <= set(store.groups)
    assert store.groups["PROD/eu"].color == "#00aaff"
    assert store.groups["PROD"].collapsed and not store.groups["LAB"].collapsed


def test_modifications_du_modele_ecrites(conf):
    """Ajout, renommage, couleur et repli sont écrits ; hôtes renumérotés host 1..N."""
    cfg = load_config(str(conf))
    cfg.store.rename(cfg.store.find("LAB/web"), "www")
    cfg.store.add(Host("db", group="LAB", host="10.0.0.2", port=5432))
    cfg.store.groups["LAB"].collapsed = True
    save_config(str(conf), cfg)
    cp = _ini(conf)
    assert sorted(s for s in cp.sections() if s.startswith("host ")) == [
        "host 1",
        "host 2",
        "host 3",
    ]
    assert [h["name"] for h in _hotes(cp)] == ["db", "www", "api"]
    # Format GTK3 (Gtk.TreePath) : ARCHIVE = 0, LAB = 1, PROD = 2.
    assert cp.get("window", "collapsed-folders") == "1,2"


def test_doublons_conserves_a_la_lecture(tmp_path):
    """Deux sections du même groupe/nom : les deux hôtes sont gardés (rien n'est écarté)."""
    chemin = tmp_path / "gcm.conf"
    chemin.write_text(
        "[host 1]\nname = a\nhost = x\n[host 2]\nname = a\nhost = y\n", encoding="utf-8"
    )
    assert sorted(h.host for h in load_config(str(chemin)).store.hosts) == ["x", "y"]


def test_chiffrement_injecte(conf, tmp_path):
    """Le champ pass passe par l'objet Chiffrement dans les deux sens."""

    class Rot13:
        def dechiffrer(self, stocke):
            return codecs.decode(stocke, "rot13")

        def chiffrer(self, clair):
            return codecs.encode(clair, "rot13")

    cfg = load_config(str(conf), Rot13())
    api = cfg.store.find("PROD/eu/api")
    assert api.password == codecs.decode("Q2hpZmZyw6k=", "rot13")
    api.password = "secret"
    save_config(str(conf), cfg, Rot13())
    assert any(h.get("pass") == "frperg" for h in _hotes(_ini(conf)))


@pytest.mark.parametrize(
    ("contenu", "message"),
    [
        (b"[options\nx = 1\n", "corrupted"),
        (b"cle sans section\n", "corrupted"),
        (b"[options]\nfont = \xff\xfe\n", "Unable to read"),
    ],
)
def test_fichier_corrompu_ou_illisible(tmp_path, contenu, message):
    """INI corrompu ou encodage invalide → ConfigError ; le fichier n'est pas touché."""
    chemin = tmp_path / "gcm.conf"
    chemin.write_bytes(contenu)
    with pytest.raises(ConfigError, match=message):
        load_config(str(chemin))
    assert chemin.read_bytes() == contenu


def test_chemin_illisible(tmp_path):
    """Un dossier à la place du fichier → ConfigError (pas une configuration vide)."""
    with pytest.raises(ConfigError, match="Unable to read"):
        load_config(str(tmp_path))


def test_ecriture_atomique_permissions_et_bak(conf):
    """0600, .bak créé à la première écriture seulement, aucun temporaire restant."""
    cfg = load_config(str(conf))
    save_config(str(conf), cfg)
    bak = conf.with_name("gcm.conf.bak")
    assert bak.read_text(encoding="utf-8") == GCM_CONF
    assert stat.S_IMODE(os.stat(conf).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(bak).st_mode) == 0o600
    cfg.options.font = "Autre 12"
    save_config(str(conf), cfg)
    assert bak.read_text(encoding="utf-8") == GCM_CONF  # pas écrasé par la 2e écriture
    assert [p.name for p in conf.parent.iterdir() if p.suffix == ".tmp"] == []


def test_disque_plein_ancien_fichier_intact(conf, monkeypatch):
    """ENOSPC pendant l'écriture : ConfigError, gcm.conf intact, temporaire supprimé."""
    cfg = load_config(str(conf))
    cfg.options.font = "Autre 12"

    def plein(_fd):
        raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))

    monkeypatch.setattr(config_mod.os, "fsync", plein)
    with pytest.raises(ConfigError, match="Unable to write"):
        save_config(str(conf), cfg)
    assert conf.read_text(encoding="utf-8") == GCM_CONF
    assert [p.name for p in conf.parent.iterdir() if p.suffix == ".tmp"] == []


def test_dossier_cree(tmp_path, monkeypatch):
    """Le dossier de configuration est créé s'il manque ; pas de .bak sans fichier."""
    monkeypatch.setattr(config_mod, "_sauvegardes", set())
    chemin = tmp_path / "nouveau" / "gcm.conf"
    save_config(str(chemin), load_config(str(chemin)))
    assert chemin.is_file() and not chemin.with_name("gcm.conf.bak").exists()


def test_save_config_garde_les_cles_window_ecrites_en_session(tmp_path):
    """Thème / géométrie écrits par update_keys après le chargement : save_config ne les remet pas (#141)."""
    conf = tmp_path / "gcm4.conf"
    conf.write_text(
        "[window]\ntheme-mode = system\nwindow-width = 800\ngroup-colors = A=#111111\n",
        encoding="utf-8",
    )
    cfg = load_config(str(conf))
    update_keys(str(conf), "window", {"theme-mode": "dark", "window-width": "1200"})
    cfg.store.ensure_group("A").color = "#222222"
    save_config(str(conf), cfg)
    relu = configparser.RawConfigParser()
    relu.read(conf, encoding="utf-8")
    assert (
        relu.get("window", "theme-mode") == "dark" and relu.get("window", "window-width") == "1200"
    )
    assert relu.get("window", "group-colors") == "A=#222222"  # le modèle fait foi pour ses clés


def test_save_config_refuse_un_fichier_devenu_corrompu(tmp_path):
    """Fichier corrompu entre chargement et enregistrement : ConfigError, fichier intact."""
    conf = tmp_path / "gcm4.conf"
    conf.write_text("[window]\ntheme-mode = dark\n", encoding="utf-8")
    cfg = load_config(str(conf))
    conf.write_text("pas un INI", encoding="utf-8")
    with pytest.raises(ConfigError):
        save_config(str(conf), cfg)
    assert conf.read_text(encoding="utf-8") == "pas un INI"
