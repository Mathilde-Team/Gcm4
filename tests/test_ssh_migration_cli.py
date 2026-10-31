# SPDX-License-Identifier: GPL-3.0-or-later
"""Palier 6 de couverture (issue #293) — migration et import ssh_config.

Couvre les chemins non testés de ``plugins/ssh/core.py`` : écriture
priviliégiée (pkexec), backups horodatés, rendu/parsing de stanzas,
réécriture de gcm.conf, ``migrate_ssh_hosts`` et ``import_ssh_config``
de bout en bout (dry-run, erreurs, filtres), ainsi que l'entrée CLI
``main()``.
"""

from __future__ import annotations

import configparser
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from plugins.ssh.core import (
    _SSH_MARKER_BEGIN,
    _SSH_MARKER_END,
    GCM_TYPE_SSH,
    _build_parser,
    _configure_logging,
    _extract_ssh_config_aliases,
    _load_existing_stanzas,
    _parse_tunnel_to_proxyjump,
    _rewrite_gcm_conf,
    _write_ssh_config,
    _write_text_privileged,
    _write_text_via_pkexec,
    backup_file,
    import_ssh_config,
    is_shared_target,
    main,
    migrate_ssh_hosts,
)

# ---------------------------------------------------------------------------
# Gcm.conf de départ : deux hôtes SSH migrables, un non-SSH, un déjà managé.
# ---------------------------------------------------------------------------


def _gcm_conf_de_depart(chemin: Path) -> Path:
    """Vérifie :  gcm conf de depart."""
    contenu = """[host bastion-prod]
name = bastion-prod
host = bastion.example.fr
type = 0
port = 22
user = alice
description = Bastion de production

[host bastion-secours]
name = bastion-secours
host = secours.example.fr
type = 0
user = bob

[host vieux-poste]
name = vieux-poste
host = vieux.example.fr
type = 3

[host deja-gegre]
name = deja-gegre
host = gre.example.fr
type = 0
ssh_config_managed = true

[option]
cle = valeur
"""
    chemin.write_text(contenu, encoding="utf-8")
    return chemin


def _config_ssh_existant(chemin: Path) -> Path:
    """Vérifie :  config ssh existant."""
    chemin.write_text(
        "Host manuel\n    HostName manuel.example.fr\n",
        encoding="utf-8",
    )
    return chemin


# ---------------------------------------------------------------------------
# _write_text_via_pkexec / _write_text_privileged
# ---------------------------------------------------------------------------


class TestEcriturePrivilegiee:
    """Vérifie : TestEcriturePrivilegiee."""

    def test_pkexec_succes_ecrit_et_nettoie(self, tmp_path: Path):
        """Vérifie : pkexec succes ecrit et nettoie."""
        cible = tmp_path / "etc" / "config_gcm.conf"
        supprimes: list[Path] = []
        ecrits: list[tuple] = []

        def faux_unlink(self, missing_ok=False):  # noqa: ANN001, ANN202
            supprimes.append(self)

        def faux_run(args, **kwargs):
            """Vérifie : faux unlink."""
            ecrits.append((args, kwargs))
            script = args[3]
            assert "install -m 644" in script
            return subprocess.CompletedProcess(args, 0, "", "")

        with (
            patch("plugins.ssh.core.subprocess.run", side_effect=faux_run),
            patch.object(Path, "unlink", faux_unlink),
        ):
            _write_text_via_pkexec(cible, "contenu", 0o644)

        assert len(ecrits) == 1
        assert supprimes, "le fichier temporaire doit être supprimé"

    def test_pkexec_echec_leve_oserror(self, tmp_path: Path):
        """Vérifie : pkexec echec leve oserror."""

        def faux_run(args, **kwargs):
            """Vérifie : faux run."""
            return subprocess.CompletedProcess(args, 127, "", "refusé")

        with (
            patch("plugins.ssh.core.subprocess.run", side_effect=faux_run),
            patch.object(Path, "unlink", lambda self, missing_ok=False: None),
        ):
            with pytest.raises(OSError, match="pkexec"):
                _write_text_via_pkexec(tmp_path / "x.conf", "c", 0o644)

    def test_privilege_partage_appelle_pkexec(self, tmp_path: Path):
        """Vérifie : privilege partage appelle pkexec."""
        appels: list[tuple] = []

        def faux_pkexec(path, content, mode):
            """Vérifie : faux pkexec."""
            appels.append((path, content, mode))

        with patch("plugins.ssh.core._write_text_via_pkexec", side_effect=faux_pkexec):
            _write_text_privileged(tmp_path / "p.conf", "c", 0o644, shared=True)
        assert appels == [(tmp_path / "p.conf", "c", 0o644)]

    def test_privilege_personnel_ecrit_directement(self, tmp_path: Path):
        """Vérifie : privilege personnel ecrit directement."""
        cible = tmp_path / "sous" / "config"
        _write_text_privileged(cible, "contenu", 0o600, shared=False)
        assert cible.read_text(encoding="utf-8") == "contenu"
        assert cible.stat().st_mode & 0o777 == 0o600


# ---------------------------------------------------------------------------
# backup_file
# ---------------------------------------------------------------------------


class TestBackupFile:
    """Vérifie : TestBackupFile."""

    def test_fichier_absent_aucun_backup(self, tmp_path: Path):
        """Vérifie : fichier absent aucun backup."""
        assert backup_file(tmp_path / "inexistant") is None

    def test_backup_cree_une_copie_horodatee(self, tmp_path: Path):
        """Vérifie : backup cree une copie horodatee."""
        source = tmp_path / "config"
        source.write_text("contenu", encoding="utf-8")
        dest = backup_file(source)
        assert dest is not None
        assert dest.name.startswith("config.bak.")
        assert dest.read_text(encoding="utf-8") == "contenu"

    def test_backup_partage_via_pkexec(self, tmp_path: Path):
        """Vérifie : backup partage via pkexec."""
        source = tmp_path / "partage.conf"
        source.write_text("contenu partagé", encoding="utf-8")
        appels: list[tuple] = []

        def faux_pkexec(path, content, mode):
            """Vérifie : faux pkexec."""
            appels.append((path, content, mode))

        with patch("plugins.ssh.core._write_text_via_pkexec", side_effect=faux_pkexec):
            dest = backup_file(source, shared=True)
        assert dest is not None
        assert appels and appels[0][0] == dest
        assert appels[0][1] == "contenu partagé"


# ---------------------------------------------------------------------------
# _parse_tunnel_to_proxyjump
# ---------------------------------------------------------------------------


class TestTunnelVersProxyJump:
    """Vérifie : TestTunnelVersProxyJump."""

    def test_hote_seul(self):
        """Vérifie : hote seul."""
        assert _parse_tunnel_to_proxyjump("bastion.example.fr") == "bastion.example.fr"

    def test_utilisateur_et_port_de_rebond(self):
        """Vérifie : utilisateur et port de rebond."""
        assert _parse_tunnel_to_proxyjump("ops@bastion:2222:interne:22") == "ops@bastion:2222"

    def test_port_22_par_defaut_sans_suffixe(self):
        """Vérifie : port 22 par defaut sans suffixe."""
        assert _parse_tunnel_to_proxyjump("ops@bastion:22:interne:22") == "ops@bastion"

    def test_port_non_entier_retombe_sur_22(self):
        """Vérifie : port non entier retombe sur 22."""
        assert _parse_tunnel_to_proxyjump("bastion:abc:interne:22") == "bastion"

    def test_hote_vide_apres_arobase(self):
        """Vérifie : hote vide apres arobase."""
        assert _parse_tunnel_to_proxyjump("ops@:2222:interne:22") == ""

    def test_brut_vide(self):
        """Vérifie : brut vide."""
        assert _parse_tunnel_to_proxyjump("") == ""

    def test_arobase_initial_sans_utilisateur(self):
        """Vérifie : arobase initial sans utilisateur."""
        assert _parse_tunnel_to_proxyjump("@bastion") == "bastion"


# ---------------------------------------------------------------------------
# _write_ssh_config / _load_existing_stanzas
# ---------------------------------------------------------------------------


class TestWriteSshConfig:
    """Vérifie : TestWriteSshConfig."""

    def test_ecrit_bloc_gere_en_mode_600(self, tmp_path: Path):
        """Vérifie : ecrit bloc gere en mode 600."""
        cible = tmp_path / ".ssh" / "config"
        _write_ssh_config(cible, {"bastion": "Host bastion\n    HostName b.example.fr\n"})
        texte = cible.read_text(encoding="utf-8")
        assert _SSH_MARKER_BEGIN in texte
        assert _SSH_MARKER_END in texte
        assert "Host bastion" in texte
        assert cible.stat().st_mode & 0o777 == 0o600

    def test_conserve_le_contenu_hors_bloc(self, tmp_path: Path):
        """Vérifie : conserve le contenu hors bloc."""
        cible = tmp_path / "config"
        _config_ssh_existant(cible)
        _write_ssh_config(cible, {"bastion": "Host bastion\n"})
        texte = cible.read_text(encoding="utf-8")
        assert texte.startswith("Host manuel")
        assert _SSH_MARKER_BEGIN in texte

    def test_remplace_un_bloc_gere_existant(self, tmp_path: Path):
        """Vérifie : remplace un bloc gere existant."""
        cible = tmp_path / "config"
        _write_ssh_config(cible, {"ancien": "Host ancien\n    HostName a.example.fr\n"})
        _write_ssh_config(cible, {"neuf": "Host neuf\n    HostName n.example.fr\n"})
        texte = cible.read_text(encoding="utf-8")
        assert texte.count(_SSH_MARKER_BEGIN) == 1
        assert "Host neuf" in texte
        assert "HostName n.example.fr" in texte
        # le bloc précédent est remplacé, pas cumulé
        assert "HostName a.example.fr" not in texte


class TestLoadExistingStanzas:
    """Vérifie : TestLoadExistingStanzas."""

    def test_fichier_absent_vide(self, tmp_path: Path):
        """Vérifie : fichier absent vide."""
        assert _load_existing_stanzas(tmp_path / "rien") == {}

    def test_sans_marqueurs_vide(self, tmp_path: Path):
        """Vérifie : sans marqueurs vide."""
        cible = _config_ssh_existant(tmp_path / "config")
        assert _load_existing_stanzas(cible) == {}

    def test_extrait_les_stanzas_du_bloc_gere(self, tmp_path: Path):
        """Vérifie : extrait les stanzas du bloc gere."""
        cible = tmp_path / "config"
        _write_ssh_config(
            cible,
            {
                "bastion": "Host bastion\n    HostName b.example.fr\n",
                "secours": "Host secours\n    HostName s.example.fr\n    User bob\n",
            },
        )
        stanzas = _load_existing_stanzas(cible)
        assert set(stanzas) == {"bastion", "secours"}
        assert "HostName s.example.fr" in stanzas["secours"]
        # HostName n'est pas confondu avec un nouvel alias Host
        assert "HostName b.example.fr" in stanzas["bastion"]


# ---------------------------------------------------------------------------
# _extract_ssh_config_aliases / _rewrite_gcm_conf
# ---------------------------------------------------------------------------


class TestExtractAliases:
    """Vérifie : TestExtractAliases."""

    def test_fichier_absent_vide(self, tmp_path: Path):
        """Vérifie : fichier absent vide."""
        assert _extract_ssh_config_aliases(tmp_path / "rien") == []

    def test_lit_deduplique_et_ignore_jokers(self, tmp_path: Path):
        """Vérifie : lit deduplique et ignore jokers."""
        cible = tmp_path / "config"
        cible.write_text(
            "# commentaire\n"
            "Host bastion secours\n"
            "Host bastion\n"  # doublon conservé une seule fois
            "Host *\n"
            "Host ??\n"
            "Hostname ignoble\n",
            encoding="utf-8",
        )
        assert _extract_ssh_config_aliases(cible) == ["bastion", "secours"]


class TestRewriteGcmConf:
    """Vérifie : TestRewriteGcmConf."""

    def test_patche_les_alias_migres_et_epargne_les_autres(self, tmp_path: Path):
        """Vérifie : patche les alias migres et epargne les autres."""
        gcm = _gcm_conf_de_depart(tmp_path / "gcm.conf")
        _rewrite_gcm_conf(gcm, {"bastion-prod"})

        cp = configparser.RawConfigParser()
        cp.optionxform = str
        cp.read(str(gcm), encoding="utf-8")
        assert cp.get("host bastion-prod", "ssh_config_managed") == "true"
        assert cp.get("host bastion-prod", "name") == "bastion-prod"
        assert cp.get("host bastion-prod", "host") == "bastion-prod"
        assert cp.get("host bastion-prod", "description").startswith("⚙ SSH config")
        # clés SSH migrées vers ssh_config : supprimées de gcm.conf
        assert "private-key" not in cp["host bastion-prod"]
        assert "tunnel" not in cp["host bastion-prod"]
        # Non migré : inchangé
        assert cp.get("host bastion-secours", "user") == "bob"
        # Section sans rapport : inchangée
        assert cp.get("option", "cle") == "valeur"


# ---------------------------------------------------------------------------
# migrate_ssh_hosts
# ---------------------------------------------------------------------------


class TestMigrateSshHosts:
    """Vérifie : TestMigrateSshHosts."""

    def test_gcm_conf_absent_leve_filenotfound(self, tmp_path: Path):
        """Vérifie : gcm conf absent leve filenotfound."""
        with pytest.raises(FileNotFoundError):
            migrate_ssh_hosts(gcm_conf=tmp_path / "absent.conf", ssh_config=tmp_path / "c")

    def test_dry_run_ne_write_rien(self, tmp_path: Path):
        """Vérifie : dry run ne write rien."""
        gcm = _gcm_conf_de_depart(tmp_path / "gcm.conf")
        ssh = tmp_path / "config"
        avant = gcm.read_text(encoding="utf-8")
        resultat = migrate_ssh_hosts(gcm_conf=gcm, ssh_config=ssh, dry_run=True)

        assert resultat["migrated"] == [
            "bastion-prod/bastion-prod",
            "bastion-secours/bastion-secours",
        ]
        assert resultat["skipped"] == ["vieux-poste/vieux-poste"]
        assert resultat["already"] == ["deja-gegre/deja-gegre"]
        assert resultat["errors"] == []
        assert not ssh.exists()
        assert gcm.read_text(encoding="utf-8") == avant  # gcm.conf intact

    def test_migration_complete(self, tmp_path: Path):
        """Vérifie : migration complete."""
        gcm = _gcm_conf_de_depart(tmp_path / "gcm.conf")
        ssh = _config_ssh_existant(tmp_path / "config")
        _write_ssh_config(ssh, {"ancien": "Host ancien\n    HostName a.example.fr\n"})

        resultat = migrate_ssh_hosts(gcm_conf=gcm, ssh_config=ssh)

        assert resultat["migrated"] == [
            "bastion-prod/bastion-prod",
            "bastion-secours/bastion-secours",
        ]
        assert len(resultat["backups"]) == 2
        texte_ssh = ssh.read_text(encoding="utf-8")
        assert "Host manuel" in texte_ssh  # contenu personnel préservé
        assert "Host ancien" in texte_ssh  # stanza gérée précédente préservée
        assert "Host bastion-prod" in texte_ssh
        cp = configparser.RawConfigParser()
        cp.optionxform = str
        cp.read(str(gcm), encoding="utf-8")
        assert cp.get("host bastion-prod", "ssh_config_managed") == "true"

    def test_only_names_filtre(self, tmp_path: Path):
        """Vérifie : only names filtre."""
        gcm = _gcm_conf_de_depart(tmp_path / "gcm.conf")
        ssh = tmp_path / "config"
        resultat = migrate_ssh_hosts(
            gcm_conf=gcm, ssh_config=ssh, dry_run=True, only_names={"bastion-secours"}
        )
        assert resultat["migrated"] == ["bastion-secours/bastion-secours"]

    def test_hote_sans_hostname_ou_sans_name_en_erreur(self, tmp_path: Path):
        """Vérifie : hote sans hostname ou sans name en erreur."""
        gcm = tmp_path / "gcm.conf"
        gcm.write_text(
            "[host sans-host]\nname = sans-host\ntype = 0\n"
            "[host sans-name]\nhost = sn.example.fr\ntype = 0\n",
            encoding="utf-8",
        )
        resultat = migrate_ssh_hosts(gcm_conf=gcm, ssh_config=tmp_path / "c", dry_run=True)
        assert len(resultat["errors"]) == 2
        assert resultat["migrated"] == []

    def test_rien_a_migrer_ne_touche_rien(self, tmp_path: Path):
        """Vérifie : rien a migrer ne touche rien."""
        gcm = tmp_path / "gcm.conf"
        gcm.write_text(
            "[host deja]\nname = deja\nhost = d.example.fr\ntype = 0\nssh_config_managed = true\n",
            encoding="utf-8",
        )
        ssh = tmp_path / "config"
        resultat = migrate_ssh_hosts(gcm_conf=gcm, ssh_config=ssh)
        assert resultat["already"] == ["deja/deja"]
        assert not ssh.exists()

    def test_echec_ecriture_ssh_config_signale(self, tmp_path: Path):
        """Vérifie : echec ecriture ssh config signale."""
        gcm = _gcm_conf_de_depart(tmp_path / "gcm.conf")
        ssh = tmp_path / "config"

        def echec(*args, **kwargs):
            """Vérifie : echec."""
            raise OSError("disque plein")

        with patch("plugins.ssh.core._write_ssh_config", side_effect=echec):
            resultat = migrate_ssh_hosts(gcm_conf=gcm, ssh_config=ssh)
        assert any("Failed to write" in e for e in resultat["errors"])

    def test_echec_reecriture_gcm_signale(self, tmp_path: Path):
        """Vérifie : echec reecriture gcm signale."""
        gcm = _gcm_conf_de_depart(tmp_path / "gcm.conf")
        ssh = tmp_path / "config"

        def echec(*args, **kwargs):
            """Vérifie : echec."""
            raise OSError("lecture seule")

        with patch("plugins.ssh.core._rewrite_gcm_conf", side_effect=echec):
            resultat = migrate_ssh_hosts(gcm_conf=gcm, ssh_config=ssh)
        assert any("Failed to rewrite" in e for e in resultat["errors"])

    def test_cible_partagee_passe_par_pkexec(self, tmp_path: Path):
        """Vérifie : cible partagee passe par pkexec."""
        gcm = _gcm_conf_de_depart(tmp_path / "gcm.conf")
        # On déclare le répertoire partagé DANS tmp_path pour rendre la cible
        # « partagée » sans toucher au vrai /etc.
        partage_dir = tmp_path / "etc" / "ssh" / "ssh_config.d"
        partage_dir.mkdir(parents=True)
        ssh = partage_dir / "config_gcm.conf"
        ssh.write_text("Host manuel\n", encoding="utf-8")
        appels: list[tuple] = []

        def faux_pkexec(path, content, mode):
            """Vérifie : faux pkexec."""
            appels.append((path, content, mode))

        with (
            patch("plugins.ssh.core.SHARED_SSH_CONFIG_PATH", ssh),
            patch("plugins.ssh.core._write_text_via_pkexec", side_effect=faux_pkexec),
        ):
            resultat = migrate_ssh_hosts(gcm_conf=gcm, ssh_config=ssh)
        assert appels  # backup + écriture passés par pkexec
        assert appels[-1][2] == 0o644
        assert resultat["migrated"]
        # l'écriture est simulée : on vérifie le contenu transmis à pkexec
        assert "Fichier PARTAGE" in appels[-1][1]
        assert "Host bastion-prod" in appels[-1][1]


# ---------------------------------------------------------------------------
# import_ssh_config
# ---------------------------------------------------------------------------


class TestImportSshConfig:
    """Vérifie : TestImportSshConfig."""

    def test_source_vide_ne_fait_rien(self, tmp_path: Path):
        """Vérifie : source vide ne fait rien."""
        source = tmp_path / "vide"
        source.write_text("# rien\n", encoding="utf-8")
        gcm = tmp_path / "gcm.conf"
        resultat = import_ssh_config(source=source, gcm_conf=gcm)
        assert resultat == {"imported": [], "skipped_existing": [], "errors": []}
        assert not gcm.exists()

    def test_importe_les_alias_en_groupe_ssh_config(self, tmp_path: Path):
        """Vérifie : importe les alias en groupe ssh config."""
        source = tmp_path / "config"
        source.write_text(
            "Host bastion\n    HostName b.example.fr\nHost neuf\n    HostName n.example.fr\n",
            encoding="utf-8",
        )
        gcm = tmp_path / "gcm.conf"
        gcm.write_text(
            "[host bastion]\nname = bastion\nhost = bastion\ntype = 0\n",
            encoding="utf-8",
        )
        resultat = import_ssh_config(source=source, gcm_conf=gcm)

        assert resultat["imported"] == ["neuf"]
        assert resultat["skipped_existing"] == ["bastion"]
        cp = configparser.RawConfigParser()
        cp.optionxform = str
        cp.read(str(gcm), encoding="utf-8")
        assert cp.get("host neuf", "name") == "neuf"
        assert cp.get("host neuf", "host") == "neuf"
        assert cp.get("host neuf", "group") == "ssh_config"
        assert cp.get("host neuf", "ssh_config_managed") == "true"
        assert cp.get("host neuf", "type") == str(GCM_TYPE_SSH)
        assert "Import depuis" in cp.get("host neuf", "description")

    def test_dry_run_n_ecrit_pas(self, tmp_path: Path):
        """Vérifie : dry run n ecrit pas."""
        source = tmp_path / "config"
        source.write_text("Host neuf\n", encoding="utf-8")
        gcm = tmp_path / "gcm.conf"
        resultat = import_ssh_config(source=source, gcm_conf=gcm, dry_run=True)
        assert resultat["imported"] == ["neuf"]
        assert not gcm.exists()

    def test_gcm_absent_cree_le_fichier(self, tmp_path: Path):
        """Vérifie : gcm absent cree le fichier."""
        source = tmp_path / "config"
        source.write_text("Host neuf\n", encoding="utf-8")
        gcm = tmp_path / "dossier" / "gcm.conf"
        resultat = import_ssh_config(source=source, gcm_conf=gcm)
        assert resultat["imported"] == ["neuf"]
        assert gcm.exists()

    def test_tout_deja_present_aucune_ecriture(self, tmp_path: Path):
        """Vérifie : tout deja present aucune ecriture."""
        source = tmp_path / "config"
        source.write_text("Host bastion\n", encoding="utf-8")
        gcm = tmp_path / "gcm.conf"
        gcm.write_text(
            "[host bastion]\nname = bastion\nhost = bastion\ntype = 0\n",
            encoding="utf-8",
        )
        resultat = import_ssh_config(source=source, gcm_conf=gcm, dry_run=False)
        assert resultat["skipped_existing"] == ["bastion"]
        # pas de backup créé : rien n'a été importé
        assert not list(tmp_path.glob("gcm.conf.bak.*"))


# ---------------------------------------------------------------------------
# is_shared_target / CLI (main, _build_parser, _configure_logging)
# ---------------------------------------------------------------------------


class TestIsSharedTarget:
    """Vérifie : TestIsSharedTarget."""

    def test_chemins_partages(self):
        """Vérifie : chemins partages."""
        assert is_shared_target(Path("/etc/ssh/ssh_config.d/config_gcm.conf"))
        assert is_shared_target(Path("/etc/ssh/ssh_config.d/autre.conf"))

    def test_chemins_personnels(self):
        """Vérifie : chemins personnels."""
        assert not is_shared_target(Path("/home/alice/.ssh/config"))
        assert not is_shared_target(Path("/etc/ssh/sshd_config"))


class TestCli:
    """Vérifie : TestCli."""

    def test_build_parser_valeurs_par_defaut(self):
        """Vérifie : build parser valeurs par defaut."""
        args = _build_parser().parse_args([])
        assert args.dry_run is False
        assert args.log_level == "INFO"
        assert args.import_from is None

    def test_configure_logging_n_echoue_pas(self):
        """Vérifie : configure logging n echoue pas."""
        _configure_logging("DEBUG")

    def test_main_migre_et_renvoie_zero(self, tmp_path: Path):
        """Vérifie : main migre et renvoie zero."""
        gcm = _gcm_conf_de_depart(tmp_path / "gcm.conf")
        ssh = tmp_path / "config"
        code = main(["--gcm-conf", str(gcm), "--ssh-config", str(ssh)])
        assert code == 0
        assert ssh.exists()

    def test_main_dry_run_n_ecrit_pas(self, tmp_path: Path):
        """Vérifie : main dry run n ecrit pas."""
        gcm = _gcm_conf_de_depart(tmp_path / "gcm.conf")
        ssh = tmp_path / "config"
        code = main(["--gcm-conf", str(gcm), "--ssh-config", str(ssh), "--dry-run"])
        assert code == 0
        assert not ssh.exists()

    def test_main_gcm_absent_renvoie_un(self, tmp_path: Path):
        """Vérifie : main gcm absent renvoie un."""
        code = main(["--gcm-conf", str(tmp_path / "absent.conf")])
        assert code == 1

    def test_main_import_renvoie_zero(self, tmp_path: Path):
        """Vérifie : main import renvoie zero."""
        source = tmp_path / "source"
        source.write_text("Host neuf\n", encoding="utf-8")
        gcm = tmp_path / "gcm.conf"
        code = main(["--import-from", str(source), "--gcm-conf", str(gcm)])
        assert code == 0
        assert gcm.exists()

    def test_main_import_en_dry_run_n_ecrit_pas(self, tmp_path: Path):
        """Vérifie : main import en dry run n ecrit pas."""
        source = tmp_path / "source"
        source.write_text("Host neuf\n", encoding="utf-8")
        gcm = tmp_path / "gcm.conf"
        code = main(["--import-from", str(source), "--gcm-conf", str(gcm), "--dry-run"])
        assert code == 0
        assert not gcm.exists()

    def test_main_import_oserror_renvoie_un(self, tmp_path: Path):
        """Vérifie : main import oserror renvoie un."""

        def echec(*args, **kwargs):
            """Vérifie : echec."""
            raise OSError("source illisible")

        with patch("plugins.ssh.core.import_ssh_config", side_effect=echec):
            code = main(["--import-from", "/nulle/part", "--gcm-conf", "/nulle/part"])
        assert code == 1

    def test_main_erreurs_de_migration_renvoie_un(self, tmp_path: Path):
        """Vérifie : main erreurs de migration renvoie un."""
        gcm = tmp_path / "gcm.conf"
        gcm.write_text(
            "[host sans-host]\nname = sans-host\ntype = 0\n",
            encoding="utf-8",
        )
        code = main(["--gcm-conf", str(gcm), "--ssh-config", str(tmp_path / "c")])
        assert code == 1
