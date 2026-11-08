# SPDX-License-Identifier: GPL-3.0-or-later
"""Environnements de test RDP et VNC regroupés (``environnements-test/``, #427 lot 5).

Vérifie la forme des deux fichiers (compose standard et pile swarm) sans
lancer Docker : mêmes services et ports des deux côtés, image VNC construite
par la version standard et seulement référencée par la version swarm, aucune
clé que ``docker stack deploy`` ignore, et plus d'anciens dossiers à la racine.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

RACINE = Path(__file__).resolve().parent.parent
DOSSIER = RACINE / "environnements-test"
STANDARD = DOSSIER / "docker-compose.yml"
SWARM = DOSSIER / "docker-stack.yml"

# Clés de service non prises en charge par `docker stack deploy`.
IGNOREES_PAR_SWARM = {"build", "container_name", "restart", "shm_size", "security_opt", "devices"}


def _charger(chemin: Path) -> dict:
    """Contenu YAML d'un fichier compose."""
    with chemin.open(encoding="utf-8") as flux:
        return yaml.safe_load(flux)


def _ports(service: dict) -> set[int]:
    """Ports publiés d'un service, syntaxe courte ``"h:c"`` ou longue."""
    publies = set()
    for port in service.get("ports", []):
        if isinstance(port, dict):
            publies.add(int(port["published"]))
        else:
            publies.add(int(str(port).split(":")[0]))
    return publies


def test_anciens_dossiers_retires_de_la_racine():
    """``rdp/`` et ``vnc/`` ne sont plus à la racine du dépôt."""
    assert not (RACINE / "rdp").exists()
    assert not (RACINE / "vnc").exists()


def test_fichiers_presents():
    """Les deux versions, le contexte VNC et le mode d'emploi sont là."""
    for nom in (
        "docker-compose.yml",
        "docker-stack.yml",
        "README.md",
        "vnc/Dockerfile",
        "vnc/entrypoint.sh",
    ):
        assert (DOSSIER / nom).is_file(), nom


@pytest.mark.parametrize("chemin", [STANDARD, SWARM], ids=["standard", "swarm"])
def test_services_et_ports(chemin):
    """Chaque version propose ``rdp`` sur 3389 et ``vnc`` sur 5901."""
    services = _charger(chemin)["services"]
    assert set(services) == {"rdp", "vnc"}
    assert _ports(services["rdp"]) == {3389}
    assert _ports(services["vnc"]) == {5901}


def test_meme_image_vnc_des_deux_cotes():
    """La version standard construit l'image que la pile swarm déploie."""
    standard = _charger(STANDARD)["services"]["vnc"]
    swarm = _charger(SWARM)["services"]["vnc"]
    assert standard["build"] == "./vnc"
    assert standard["image"] == swarm["image"] == "gcm4-test-vnc:latest"
    assert (
        _charger(STANDARD)["services"]["rdp"]["image"]
        == _charger(SWARM)["services"]["rdp"]["image"]
    )


def test_swarm_sans_cle_ignoree():
    """Aucune clé ignorée par swarm ; ``deploy`` et ``/dev/shm`` en tmpfs la remplacent."""
    for nom, service in _charger(SWARM)["services"].items():
        assert not IGNOREES_PAR_SWARM & set(service), nom
        assert "restart_policy" in service["deploy"], nom
        tmpfs = [v for v in service["volumes"] if isinstance(v, dict) and v.get("type") == "tmpfs"]
        assert [v["target"] for v in tmpfs] == ["/dev/shm"], nom


@pytest.mark.parametrize("chemin", [STANDARD, SWARM], ids=["standard", "swarm"])
def test_etat_rdp_hors_du_depot(chemin):
    """L'état du bureau RDP va dans un volume nommé, pas dans l'arbre du dépôt."""
    contenu = _charger(chemin)
    montages = contenu["services"]["rdp"]["volumes"]
    assert "rdp-config:/config" in montages or any(
        isinstance(m, dict) and m.get("source") == "rdp-config" for m in montages
    )
    assert "rdp-config" in contenu["volumes"]
    assert "/var/run/docker.sock" not in chemin.read_text(encoding="utf-8")
