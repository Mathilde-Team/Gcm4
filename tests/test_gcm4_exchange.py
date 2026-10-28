# SPDX-License-Identifier: GPL-3.0-or-later
"""Import et export des hôtes en CSV et JSON, sans GTK (#152)."""

from __future__ import annotations

import ast
import configparser
import csv
import io
import json
import stat
from pathlib import Path

import pytest

from gcm4.core import exchange
from gcm4.core.config import Config, load_config, save_config
from gcm4.core.exchange import (
    CHAMP_AUTRES_PROTOCOLES,
    CHAMP_MOT_DE_PASSE,
    Conflit,
    ExchangeError,
    dict_vers_hote,
    ecrire_csv,
    ecrire_json,
    exporter_fichier,
    hote_vers_dict,
    importer,
    importer_fichier,
    lire_csv,
    lire_fichier,
    lire_json,
)
from gcm4.core.model import Host, HostStore

RACINE = Path(__file__).resolve().parents[1]


def _colonnes_gtk3() -> list[str]:
    """``Wmain._CSV_CORE_FIELDS`` lu dans le code GTK3 (sans l'importer : il charge GTK)."""
    arbre = ast.parse((RACINE / "gnome_connection_manager.py").read_text(encoding="utf-8"))
    for noeud in ast.walk(arbre):
        if isinstance(noeud, (ast.AnnAssign, ast.Assign)):  # noqa: UP038 (Python 3.9, #223)
            cibles = [noeud.target] if isinstance(noeud, ast.AnnAssign) else noeud.targets
            if any(getattr(c, "id", "") == "_CSV_CORE_FIELDS" for c in cibles):
                return list(ast.literal_eval(noeud.value))
    raise AssertionError("_CSV_CORE_FIELDS introuvable")


#: Champs déclarés par des plugins GTK3 (host_fields), exportés après les champs core.
CHAMPS_PLUGINS_GTK3 = ["rdp_cert_ignore", "vnc_view_only", "serial_baudrate"]


def _ligne_gtk3(**valeurs: str) -> dict[str, str]:
    """Ligne d'export GTK3 : ``str(getattr(h, f, "") or "")`` pour chaque colonne."""
    ligne = dict.fromkeys(_colonnes_gtk3() + CHAMPS_PLUGINS_GTK3, "")
    ligne.update(valeurs)
    return ligne


LIGNE_SSH = _ligne_gtk3(
    group="PROD/eu",
    name="api",
    description="Réseau européen",
    host="api.example.org",
    user="admin",
    port="2222",
    type="ssh",
    protocol="ssh",
    commands="uptime\nwho",
    keep_alive="30",
    x11="False",
    agent="True",
    compression="True",
    compressionLevel="6",
    log="False",
    backspace_key="0",
    delete_key="0",
    term="xterm-256color",
    rdp_cert_ignore="False",
)
LIGNE_RDP = _ligne_gtk3(
    group="LAB", name="win", host="10.0.0.5", user="adm", port="3389", type="rdp",
    protocol="rdp", rdp_cert_ignore="True",
)  # fmt: skip


def _csv(lignes: list[dict[str, str]]) -> str:
    sortie = io.StringIO()
    w = csv.DictWriter(sortie, fieldnames=list(lignes[0]))
    w.writeheader()
    w.writerows(lignes)
    return sortie.getvalue()


def _hote_complet() -> Host:
    h = Host(name="api", group="PROD/eu", host="api.example.org", port=2222, user="admin",
             password="s3cret", description="d", protocol="proto-a", workspace=True,
             post_connect_command="ls")  # fmt: skip
    h.protocol_options = {
        "proto-a": {"keepalive": "30", "commands": "a\\nb", "x11": "False", "vide": ""},
        "proto-b": {"opt": "1"},
    }
    return h


# --- export GTK3 → gcm4 -----------------------------------------------------


def test_colonnes_gtk3_lues_dans_le_code():
    """Garde-fou : la fixture suit les colonnes réellement exportées par GTK3."""
    assert _colonnes_gtk3()[:2] == ["group", "name"]
    assert {"keep_alive", "compressionLevel", "backspace_key", "commands"} <= set(_colonnes_gtk3())


def test_export_csv_gtk3_importe_sans_perte():
    """Chaque cellule non vide d'un export GTK3 se retrouve dans l'hôte importé."""
    ssh, rdp = lire_csv(_csv([LIGNE_SSH, LIGNE_RDP]))
    assert (ssh.path, ssh.host, ssh.port, ssh.user, ssh.protocol) == (
        "PROD/eu/api", "api.example.org", 2222, "admin", "ssh",
    )  # fmt: skip
    assert ssh.description == "Réseau européen"
    opts = ssh.protocol_options["ssh"]
    # Noms de colonnes GTK3 → clés de gcm.conf (models.save_host_to_ini).
    assert opts["keepalive"] == "30" and "keep_alive" not in opts
    assert opts["compression-level"] == "6"
    assert opts["backspace-key"] == "0" and opts["delete-key"] == "0"
    assert opts["commands"] == "uptime\\nwho"  # échappé comme dans gcm.conf
    assert opts["x11"] == "False" and opts["agent"] == "True" and opts["type"] == "ssh"
    assert opts["rdp_cert_ignore"] == "False"
    assert "vnc_view_only" not in opts  # cellule vide : option absente
    assert rdp.protocol_options["rdp"]["rdp_cert_ignore"] == "True"
    # Réexport : on retrouve les cellules GTK3 non vides.
    for colonne, valeur in LIGNE_SSH.items():
        if valeur:
            assert hote_vers_dict(ssh)[colonne] == valeur, colonne


def test_export_gtk3_reecrit_dans_gcm_conf_avec_les_cles_gtk3(tmp_path):
    """Importé puis enregistré : les clés INI sont celles que GTK3 relit."""
    cfg = Config()
    importer(cfg.store, lire_csv(_csv([LIGNE_SSH])))
    chemin = tmp_path / "gcm4.conf"
    save_config(str(chemin), cfg)
    cp = configparser.RawConfigParser(interpolation=None)
    cp.optionxform = str  # type: ignore[assignment,method-assign]
    cp.read(chemin, encoding="utf-8")
    section = next(s for s in cp.sections() if s.startswith("host "))
    assert cp.get(section, "keepalive") == "30"
    assert cp.get(section, "compression-level") == "6"
    assert cp.get(section, "commands") == "uptime\\nwho"
    assert cp.get(section, "pass") == ""
    relu = load_config(str(chemin)).store.find("PROD/eu/api")
    assert relu is not None and relu.protocol_options == cfg.store.hosts[0].protocol_options


def test_export_json_gtk3_liste_a_plat():
    """L'export JSON GTK3 (liste de dicts) s'importe ; cellules vides conservées."""
    (h,) = lire_json(json.dumps([LIGNE_SSH]))
    assert h.path == "PROD/eu/api"
    assert h.protocol_options["ssh"]["vnc_view_only"] == ""
    assert h.protocol_options["ssh"]["keepalive"] == "30"


# --- aller-retour gcm4 -------------------------------------------------------


def test_json_aller_retour_sans_perte():
    """JSON : tous les champs, options de tous les protocoles, mot de passe si demandé."""
    h = _hote_complet()
    (relu,) = lire_json(ecrire_json([h], mots_de_passe=True))
    assert relu == h
    doc = json.loads(ecrire_json([h]))
    assert (doc["format"], doc["version"]) == ("gcm4-hosts", 1)
    assert doc["hosts"][0][CHAMP_AUTRES_PROTOCOLES] == {"proto-b": {"opt": "1"}}
    assert doc["hosts"][0]["commands"] == "a\nb"


def test_csv_aller_retour_sans_perte_hors_options_vides():
    """CSV : aller-retour exact, sauf les options vides (cellule vide = absente)."""
    h = _hote_complet()
    autre = Host(name="b", protocol="proto-b", port=None)
    autre.protocol_options = {"proto-b": {"opt": "2"}}
    relus = lire_csv(ecrire_csv([h, autre], mots_de_passe=True))
    attendu = h.clone()
    del attendu.protocol_options["proto-a"]["vide"]
    assert relus == [attendu, autre]


def test_mots_de_passe_jamais_exportes_par_defaut():
    """Sans option explicite, aucune colonne password, ni valeur."""
    h = _hote_complet()
    for texte in (ecrire_csv([h]), ecrire_json([h])):
        assert "s3cret" not in texte and CHAMP_MOT_DE_PASSE not in texte
    assert lire_json(ecrire_json([h]))[0].password == ""
    assert CHAMP_MOT_DE_PASSE in ecrire_csv([h], mots_de_passe=True).splitlines()[0]


def test_csv_sans_autres_protocoles_ni_options():
    """Hôte minimal : seules les colonnes communes."""
    texte = ecrire_csv([Host(name="x")])
    assert texte.splitlines()[0].split(",") == list(exchange.CHAMPS_COMMUNS)
    assert lire_csv(texte) == [Host(name="x")]


def test_port_et_workspace_invalides_conserves_bruts():
    """Comme config.py : valeur invalide gardée dans les options, puis réexportée."""
    h = dict_vers_hote(
        {"name": "n", "protocol": "p", "port": "abc", "workspace": "peut-être"}, "l"
    )
    assert h.port is None and h.workspace is False
    assert h.protocol_options["p"] == {"port": "abc", "workspace": "peut-être"}
    ligne = hote_vers_dict(h)
    assert (ligne["port"], ligne["workspace"]) == ("abc", "peut-être")
    v = dict_vers_hote({"name": "n", "port": "", "workspace": "yes"}, "l")
    assert v.port is None and v.workspace is True and v.protocol_options == {}


def test_valeurs_json_scalaires_converties():
    """Nombres, booléens et null JSON deviennent du texte (port entier accepté)."""
    h = dict_vers_hote({"name": "n", "type": "t", "port": 22, "x": True, "y": None, "z": 1.5}, "e")
    assert h.protocol == "t" and h.port == 22
    assert h.protocol_options["t"] == {"type": "t", "x": "True", "y": "", "z": "1.5"}


def test_autres_protocoles_ne_remplacent_pas_le_protocole_courant():
    """Une entrée du protocole courant dans other_protocol_options est ignorée."""
    h = dict_vers_hote(
        {"name": "n", "protocol": "a", "k": "1", CHAMP_AUTRES_PROTOCOLES: {"a": {"k": "2"}}}, "e"
    )
    assert h.protocol_options == {"a": {"k": "1"}}


# --- fichiers mal formés ------------------------------------------------------


@pytest.mark.parametrize(
    ("texte", "attendu"),
    [
        ("{pas du json", "Expecting"),
        ('{"format": "autre", "version": 1, "hosts": []}', "unsupported format or version"),
        ('{"format": "gcm4-hosts", "version": 2, "hosts": []}', "unsupported format or version"),
        ('{"format": "gcm4-hosts", "version": 1}', "unsupported format or version"),
        ("42", "unsupported format or version"),
        ('[{"name": "ok"}, "texte"]', "entry 2: unexpected value for hosts"),
        ('[{"name": "  "}]', "entry 1: the host name is missing"),
        ('[{"name": "a", "port": [1]}]', "entry 1: unexpected value for port"),
        ('[{"name": "a", "other_protocol_options": "{"}]', "unexpected value for other_proto"),
        ('[{"name": "a", "other_protocol_options": [1]}]', "unexpected value for other_proto"),
        ('[{"name": "a", "other_protocol_options": {"p": 1}}]', "unexpected value for other_"),
        ('[{"name": "a", "other_protocol_options": {"p": {"k": {}}}}]', "unexpected value"),
    ],
)
def test_json_mal_forme(texte, attendu):
    """Chaque défaut est signalé, avec sa position."""
    with pytest.raises(ExchangeError, match=attendu):
        lire_json(texte)


@pytest.mark.parametrize(
    ("texte", "attendu"),
    [
        ("", "the column name is missing"),
        ("group,host\nA,h\n", "the column name is missing"),
        ("name,host\nok,h\n,h2\n", "line 3: the host name is missing"),
        ("name,host\na,h,en trop\n", "line 2: too many cells"),
        ('name,host\n"a,h\n', "unexpected end of data"),
        ('name,host\n"a"x,h\n', "',' expected after"),
    ],
)
def test_csv_mal_forme(texte, attendu):
    """CSV sans colonne name, nom vide, cellules en trop, guillemet non fermé."""
    with pytest.raises(ExchangeError, match=attendu):
        lire_csv(texte)


def test_fichier_mal_forme_ne_modifie_rien(tmp_path):
    """Une erreur en fin de fichier : aucun hôte importé, magasin intact."""
    store = HostStore()
    store.add(Host(name="garde", group="G"))
    avant = (list(store.hosts), dict(store.groups))
    chemin = tmp_path / "hotes.csv"
    chemin.write_text("group,name\nA,un\nB,\n", encoding="utf-8")
    with pytest.raises(ExchangeError, match="is not a valid host export: line 3"):
        importer_fichier(store, chemin)
    assert (store.hosts, store.groups) == avant


def test_fichier_illisible(tmp_path):
    """Fichier absent ou non UTF-8 : message traduit, rien d'importé."""
    with pytest.raises(ExchangeError, match="Unable to read the file"):
        lire_fichier(tmp_path / "absent.json")
    binaire = tmp_path / "x.csv"
    binaire.write_bytes(b"name\n\xff\xfe\x00")
    with pytest.raises(ExchangeError, match="Unable to read the file"):
        lire_fichier(binaire)


def test_bom_utf8_accepte(tmp_path):
    """Un CSV enregistré par un tableur (BOM UTF-8) s'importe."""
    chemin = tmp_path / "tableur.CSV"
    chemin.write_text("name,host\nx,h\n", encoding="utf-8-sig")
    assert [h.name for h in lire_fichier(chemin)] == ["x"]


# --- conflits et rapport ------------------------------------------------------


def _magasin() -> HostStore:
    store = HostStore()
    store.add(Host(name="api", group="PROD", host="ancien"))
    return store


def _arrivants() -> list[Host]:
    return [
        Host(name="api", group="PROD", host="nouveau"),
        Host(name="neuf", group="LAB/x"),
        Host(name="neuf", group="LAB/x", host="doublon du fichier"),
    ]


def test_conflit_ignorer_par_defaut():
    """Défaut : l'existant est gardé, le doublon du fichier aussi ignoré."""
    store = _magasin()
    r = importer(store, _arrivants())
    assert store.find("PROD/api").host == "ancien"
    assert (r.ajoutes, r.ignores) == (["LAB/x/neuf"], ["PROD/api", "LAB/x/neuf"])
    assert r.groupes_crees == ["LAB", "LAB/x"]
    assert (r.remplaces, r.renommes) == ([], [])


def test_conflit_renommer():
    """Renommer : « nom (2) » dans le même groupe."""
    store = _magasin()
    r = importer(store, _arrivants(), Conflit.RENOMMER)
    assert store.find("PROD/api (2)").host == "nouveau"
    assert r.renommes == [("PROD/api", "PROD/api (2)"), ("LAB/x/neuf", "LAB/x/neuf (2)")]
    assert r.ajoutes == ["LAB/x/neuf"]


def test_conflit_remplacer():
    """Remplacer : même rang, nouvelles valeurs ; les hôtes importés ne sont pas partagés."""
    store = _magasin()
    arrivants = _arrivants()
    r = importer(store, arrivants, Conflit.REMPLACER)
    assert store.hosts[0].host == "nouveau" and store.hosts[0] is not arrivants[0]
    assert store.find("LAB/x/neuf").host == "doublon du fichier"
    assert r.remplaces == ["PROD/api", "LAB/x/neuf"]


def test_importer_fichier_json_avec_rapport(tmp_path):
    """Export puis import d'un fichier : bilan et groupes créés."""
    chemin = tmp_path / "hotes.json"
    assert exporter_fichier(chemin, [_hote_complet()]) == 1
    store = HostStore()
    r = importer_fichier(store, chemin, Conflit.IGNORER)
    assert r.ajoutes == ["PROD/eu/api"] and r.groupes_crees == ["PROD", "PROD/eu"]
    assert store.hosts[0].password == ""


def test_export_fichier_permissions_et_format(tmp_path):
    """0600 (mots de passe possibles), format choisi par l'extension, fichier remplacé."""
    csv_ = tmp_path / "hotes.csv"
    csv_.write_text("ancien contenu", encoding="utf-8")
    csv_.chmod(0o644)
    exporter_fichier(csv_, [_hote_complet()], mots_de_passe=True)
    assert stat.S_IMODE(csv_.stat().st_mode) == 0o600
    assert csv_.read_text(encoding="utf-8").startswith("group,name,")
    assert "s3cret" in csv_.read_text(encoding="utf-8")
    js = tmp_path / "hotes.JSON"
    exporter_fichier(js, [])
    assert json.loads(js.read_text(encoding="utf-8"))["hosts"] == []


def test_export_fichier_impossible(tmp_path):
    """Dossier absent : message traduit."""
    with pytest.raises(ExchangeError, match="Unable to write the file"):
        exporter_fichier(tmp_path / "absent" / "x.csv", [])
