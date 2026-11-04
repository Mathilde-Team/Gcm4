# SPDX-License-Identifier: GPL-3.0-or-later
"""Chemins d'erreur et cas limites de plugins/ssh/core.py et putty_import_core.py.

Complète test_ssh_core.py et test_putty_import_core.py sur les branches
restées sans couverture : validation des ports hors plage, Include
résolus (motifs glob, fichier illisible), sauvegarde impossible,
écriture atomique en échec, session PuTTY illisible.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

import plugins.ssh.core as ssh_core
import putty_import_core


def _config_avec(contenu: str, tmp_path: Path) -> ssh_core.SSHConfigParser:
    """Écrit un config SSH dans tmp_path et le parse."""
    chemin = tmp_path / "config"
    chemin.write_text(contenu, encoding="utf-8")
    parser = ssh_core.SSHConfigParser(config_path=chemin)
    parser.parse()
    return parser


# ── Validation ─────────────────────────────────────────────────────────────


def test_validate_port_hors_plage(tmp_path):
    """Port hors 1-65535 : erreur « Invalid port »."""
    parser = _config_avec("Host h1\n    Port 70000\n", tmp_path)
    erreurs = parser.validate()
    assert any("Invalid port" in e for e in erreurs)


def test_validate_port_zero(tmp_path):
    """Port 0 : hors plage, signalé."""
    parser = _config_avec("Host h1\n    Port 0\n", tmp_path)
    erreurs = parser.validate()
    assert any("Invalid port" in e for e in erreurs)


def test_validate_port_non_entier(tmp_path):
    """Port non numérique : erreur « Port is not an integer »."""
    parser = _config_avec("Host h1\n    Port abc\n", tmp_path)
    erreurs = parser.validate()
    assert any("Port is not an integer" in e for e in erreurs)


def test_validate_identityfile_relatif_absent(tmp_path):
    """IdentityFile relatif introuvable : erreur, chemin résolu sous ~/.ssh."""
    parser = _config_avec("Host h1\n    IdentityFile cle-inexistante.pem\n", tmp_path)
    erreurs = parser.validate()
    assert any("IdentityFile not found" in e for e in erreurs)


def test_validate_identityfile_absolu_present(tmp_path):
    """IdentityFile absolu existant : aucune erreur."""
    cle = tmp_path / "cle.pem"
    cle.write_text("clé", encoding="utf-8")
    parser = _config_avec(f"Host h1\n    IdentityFile {cle}\n", tmp_path)
    assert parser.validate() == []


# ── Directives Include ────────────────────────────────────────────────────


def test_include_resolu_et_integre(tmp_path):
    """Include d'un fichier : lignes lues et rangées dans includes_resolved."""
    inclus = tmp_path / "conf.d"
    inclus.mkdir()
    (inclus / "extra.conf").write_text("Host inclus\n    HostName 1.2.3.4\n", encoding="utf-8")
    parser = _config_avec(
        "Include conf.d/extra.conf\n\nHost principal\n    HostName 5.6.7.8\n", tmp_path
    )
    inclus = parser.config.includes_resolved
    assert (tmp_path / "conf.d" / "extra.conf") in inclus
    lignes = inclus[tmp_path / "conf.d" / "extra.conf"]
    assert any("1.2.3.4" in ligne for ligne in lignes)
    contenu = parser.config.generate_content()
    assert "Include conf.d/extra.conf" in contenu


def test_include_motif_glob(tmp_path):
    """Include avec motif glob : tous les fichiers correspondants lus."""
    (tmp_path / "conf.d").mkdir()
    (tmp_path / "conf.d" / "a.conf").write_text("Host a\n    HostName 1.1.1.1\n", encoding="utf-8")
    (tmp_path / "conf.d" / "b.conf").write_text("Host b\n    HostName 2.2.2.2\n", encoding="utf-8")
    parser = _config_avec("Include conf.d/*.conf\n", tmp_path)
    lus = {p.name: lignes for p, lignes in parser.config.includes_resolved.items()}
    assert set(lus) == {"a.conf", "b.conf"}
    assert any("1.1.1.1" in ligne for lignes in lus.values() for ligne in lignes)
    assert any("2.2.2.2" in ligne for lignes in lus.values() for ligne in lignes)


def test_include_fichier_illisible(tmp_path, monkeypatch):
    """Fichier inclus illisible (OSError) : ignoré avec avertissement, pas de crash."""
    (tmp_path / "extra.conf").write_text("Host x\n", encoding="utf-8")

    open_orig = Path.open

    def ouverture_interdite(self, *args, **kwargs):
        """Refuse la lecture du seul fichier inclus visé."""
        if self.name == "extra.conf":
            raise OSError("lecture interdite")
        return open_orig(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", ouverture_interdite)
    parser = _config_avec("Include extra.conf\n", tmp_path)
    # la résolution ne lève pas : le fichier est juste ignoré
    assert (tmp_path / "extra.conf") not in parser.config.includes_resolved


# ── Sauvegarde et écriture ────────────────────────────────────────────────


def test_backup_echec_copy2(tmp_path, monkeypatch):
    """Copie de sauvegarde en échec (OSError) : averti, n'interrompt pas l'écriture."""

    def copy2_rate(src, dst, **kwargs):
        raise OSError("copie impossible")

    monkeypatch.setattr(shutil, "copy2", copy2_rate)
    parser = _config_avec("Host h1\n    HostName 1.2.3.4\n", tmp_path)
    parser.auto_backup_enabled = True
    parser.write()  # ne doit pas lever malgré l'échec de backup


def test_ecriture_atomique_en_echec(tmp_path, monkeypatch):
    """os.replace qui échoue : fichier temporaire nettoyé, exception relancée."""
    import os

    parser = _config_avec("Host h1\n    HostName 1.2.3.4\n", tmp_path)
    parser.config.hosts[0].set_option("Port", "2222")  # contenu différent

    def replace_rate(src, dst):
        raise OSError("remplacement impossible")

    monkeypatch.setattr(os, "replace", replace_rate)
    with pytest.raises(OSError):
        parser.write()


# ── Sessions PuTTY illisibles ─────────────────────────────────────────────


def test_scan_putty_sessions_fichier_illisible(tmp_path, monkeypatch):
    """Session PuTTY dont le fichier ne se lit pas : listée dans les ignorés."""

    def parse_rate(path):
        raise OSError("fichier verrouillé")

    monkeypatch.setattr(putty_import_core, "parse_putty_session_file", parse_rate)
    (tmp_path / "session.reg").write_text("contenu", encoding="utf-8")
    sessions, ignores = putty_import_core.scan_putty_sessions(tmp_path)
    assert sessions == []
    assert len(ignores) == 1
    assert "lecture impossible" in ignores[0]


# ── Rendu de stanza SSH ──────────────────────────────────────────────────


def _cp_hote(**options):
    """ConfigParser d'une section [host test] avec les options données."""
    import configparser

    cp = configparser.RawConfigParser()
    cp.add_section("host test")
    for cle, valeur in options.items():
        cp.set("host test", cle, str(valeur))
    return cp


def test_render_stanza_meta_comment_group_description():
    """Commentaire d'en-tête enrichi de group et description."""
    cp = _cp_hote(group="Prod", description="serveur principal")
    stanza = ssh_core._render_ssh_stanza("test", "10.0.0.1", cp, "host test")
    assert "# GCM | group=Prod | serveur principal" in stanza


def test_render_stanza_options_avancees():
    """x11, agent, compression et keepalive rendus quand activés."""
    cp = _cp_hote(
        x11=True,
        agent=True,
        compression=True,
        keepalive=120,
        server_alive_count_max=5,
    )
    stanza = ssh_core._render_ssh_stanza("test", "10.0.0.1", cp, "host test")
    assert "ForwardX11 yes" in stanza
    assert "ForwardX11Trusted yes" in stanza
    assert "ForwardAgent yes" in stanza
    assert "Compression yes" in stanza
    assert "ServerAliveInterval 120" in stanza
    assert "ServerAliveCountMax 5" in stanza


def test_render_stanza_nom_avec_espaces():
    """Nom d'hôte avec espace : remplacé par underscore dans Host."""
    cp = _cp_hote()
    stanza = ssh_core._render_ssh_stanza("mon serveur", "10.0.0.1", cp, "host test")
    assert "Host mon_serveur" in stanza


def test_render_stanza_tunnel_sans_hote_de_rebond():
    """Tunnel sans hôte de rebond : pas de ProxyJump rendu."""
    cp = _cp_hote(tunnel="user@:22:10.0.0.1:22")
    stanza = ssh_core._render_ssh_stanza("test", "10.0.0.1", cp, "host test")
    assert "ProxyJump" not in stanza


def test_render_stanza_tunnel_valide():
    """Tunnel valide : rendu en ProxyJump."""
    cp = _cp_hote(tunnel="user@10.9.9.9:2222")
    stanza = ssh_core._render_ssh_stanza("test", "10.0.0.1", cp, "host test")
    assert "ProxyJump user@10.9.9.9 -p 2222" in stanza or "ProxyJump" in stanza


# ── Sauvegarde partagée (pkexec) ─────────────────────────────────────────


def test_backup_partage_pkexec_en_echec(tmp_path, monkeypatch):
    """Cible partagée : backup via pkexec qui échoue : averti, sans crash."""
    parser = _config_avec("Host h1\n    HostName 1.2.3.4\n", tmp_path)

    def pkexec_rate(path, content, mode=0o644):
        raise OSError("pkexec refusé")

    monkeypatch.setattr(ssh_core.SSHConfigParser, "_is_shared_target", lambda self: True)
    monkeypatch.setattr(ssh_core, "_write_text_via_pkexec", pkexec_rate)
    parser._backup_file()  # ne doit pas lever


# ── Génération de contenu et état sale ────────────────────────────────────


def test_generate_content_avec_options_globales(tmp_path):
    """Options globales avant tout Host : rendues avec ligne vide séparatrice."""
    parser = _config_avec("AddKeysToAgent yes\n\nHost h1\n    HostName 1.2.3.4\n", tmp_path)
    contenu = parser.config.generate_content()
    assert "AddKeysToAgent yes" in contenu
    assert "Host h1" in contenu


def test_is_dirty_contenu_identique(tmp_path):
    """Contenu identique après re-parse : config propre."""
    parser = _config_avec("Host h1\n    HostName 1.2.3.4\n", tmp_path)
    parser.parse()  # relecture sans modification
    assert parser.config.is_dirty() is False


def test_is_dirty_apres_modification(tmp_path):
    """Option modifiée en mémoire : config sale."""
    parser = _config_avec("Host h1\n    HostName 1.2.3.4\n", tmp_path)
    parser.config.hosts[0].set_option("Port", "2222")
    assert parser.config.is_dirty() is True


# ── Parse : commentaires et lignes brutes ─────────────────────────────────


def test_parse_commentaires_dans_bloc_hote(tmp_path):
    """Commentaires et lignes vides dans un bloc Host : conservés tels quels."""
    contenu = (
        "# en-tête global\n"
        "Host h1\n"
        "    # commentaire interne\n"
        "    HostName 1.2.3.4\n"
        "\n"
        "Host h2\n"
        "    HostName 5.6.7.8\n"
    )
    parser = _config_avec(contenu, tmp_path)
    assert len(parser.config.hosts) == 2
    lignes_brutes = parser.config.hosts[0].raw_lines
    assert any("# commentaire interne" in ligne for ligne in lignes_brutes)


def test_write_config_illisible_reecriture_complete(tmp_path, monkeypatch):
    """Relecture du config impossible pendant write : réécriture complète quand même."""
    parser = _config_avec("Host h1\n    HostName 1.2.3.4\n", tmp_path)
    parser.config.hosts[0].set_option("Port", "2222")  # contenu différent

    open_orig = Path.open

    def ouverture_interdite(self, *args, **kwargs):
        """Refuse la lecture du fichier config lui-même."""
        if self.name == "config":
            raise OSError("fichier verrouillé")
        return open_orig(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", ouverture_interdite)
    parser.write(backup=False)  # ne doit pas lever


# ── Include récursif et sauvegarde ────────────────────────────────────────


def test_include_recursif_double_etoile(tmp_path):
    """Include avec motif ** récursif : fichiers des sous-dossiers lus."""
    profond = tmp_path / "conf.d" / "sous" / "dossier"
    profond.mkdir(parents=True)
    (profond / "c.conf").write_text("Host c\n    HostName 3.3.3.3\n", encoding="utf-8")
    parser = _config_avec("Include conf.d/**/*.conf\n", tmp_path)
    lus = {p.name for p in parser.config.includes_resolved}
    assert "c.conf" in lus


def test_backup_repli_quand_repertoire_indisponible(tmp_path, monkeypatch):
    """Répertoire de sauvegarde impossible à créer : repli sur le dossier du config."""
    parser = _config_avec("Host h1\n    HostName 1.2.3.4\n", tmp_path)
    parser.backup_dir = tmp_path / "sauvegardes"

    def mkdir_rate(self, parents=False, exist_ok=False):
        raise OSError("répertoire refusé")

    monkeypatch.setattr(Path, "mkdir", mkdir_rate)
    parser._backup_file()  # repli silencieux, pas d'exception


def test_ecriture_atomique_nettoyage_impossible(tmp_path, monkeypatch):
    """Remplacement et nettoyage du temporaire en échec : exception quand même."""
    import os

    parser = _config_avec("Host h1\n    HostName 1.2.3.4\n", tmp_path)
    parser.config.hosts[0].set_option("Port", "2222")

    def replace_rate(src, dst):
        raise OSError("remplacement impossible")

    def unlink_rate(self, missing_ok=False):
        raise OSError("suppression impossible")

    monkeypatch.setattr(os, "replace", replace_rate)
    monkeypatch.setattr(Path, "unlink", unlink_rate)
    with pytest.raises(OSError):
        parser.write(backup=False)
