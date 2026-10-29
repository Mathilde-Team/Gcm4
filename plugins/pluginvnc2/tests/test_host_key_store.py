# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests pour HostKeyStore (pinning des clés hôte Apple ARD).

Logique vérifiée de façon équivalente hors GTK pendant le développement
(round-trip DER/base64, forget, JSON corrompu) -- ces tests-ci passent
par le vrai vnc_tab.HostKeyStore et ont besoin de GTK4 pour s'importer
(cf. conftest.py).
"""

from __future__ import annotations

import json

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from conftest import GTK4_AVAILABLE, requires_gtk4

if GTK4_AVAILABLE:
    from vnc_tab import HostKeyStore


@pytest.fixture
def rsa_keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


@requires_gtk4
def test_get_on_empty_store_returns_none(tmp_path):
    store = HostKeyStore(path=tmp_path / "keys.json")
    assert store.get("host1", 5900) is None


@requires_gtk4
def test_set_then_get_roundtrips_the_exact_key(tmp_path, rsa_keypair):
    _private_key, public_key = rsa_keypair
    store = HostKeyStore(path=tmp_path / "keys.json")

    store.set("host1", 5900, public_key)
    retrieved = store.get("host1", 5900)

    assert retrieved.public_numbers() == public_key.public_numbers()


@requires_gtk4
def test_set_persists_to_disk_across_instances(tmp_path, rsa_keypair):
    _private_key, public_key = rsa_keypair
    path = tmp_path / "keys.json"

    HostKeyStore(path=path).set("host1", 5900, public_key)
    reloaded = HostKeyStore(path=path)

    assert reloaded.get("host1", 5900).public_numbers() == public_key.public_numbers()


@requires_gtk4
def test_different_host_or_port_is_a_different_entry(tmp_path, rsa_keypair):
    _private_key, public_key = rsa_keypair
    store = HostKeyStore(path=tmp_path / "keys.json")
    store.set("host1", 5900, public_key)

    assert store.get("host2", 5900) is None
    assert store.get("host1", 5901) is None


@requires_gtk4
def test_forget_removes_the_entry(tmp_path, rsa_keypair):
    _private_key, public_key = rsa_keypair
    store = HostKeyStore(path=tmp_path / "keys.json")
    store.set("host1", 5900, public_key)

    store.forget("host1", 5900)

    assert store.get("host1", 5900) is None


@requires_gtk4
def test_forget_persists_across_instances(tmp_path, rsa_keypair):
    _private_key, public_key = rsa_keypair
    path = tmp_path / "keys.json"
    HostKeyStore(path=path).set("host1", 5900, public_key)

    HostKeyStore(path=path).forget("host1", 5900)

    assert HostKeyStore(path=path).get("host1", 5900) is None


@requires_gtk4
def test_forget_on_unknown_entry_is_a_no_op(tmp_path):
    store = HostKeyStore(path=tmp_path / "keys.json")

    store.forget("never-seen-host", 5900)  # ne doit pas lever d'exception


@requires_gtk4
def test_corrupted_json_file_resets_to_empty_instead_of_crashing(tmp_path):
    path = tmp_path / "keys.json"
    path.write_text("{ceci nest pas du json valide")

    store = HostKeyStore(path=path)

    assert store.get("anything", 5900) is None


@requires_gtk4
def test_default_path_is_under_dot_gcm(monkeypatch, tmp_path):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    store = HostKeyStore()

    assert store.path == tmp_path / ".gcm" / "vnc_host_keys.json"


@requires_gtk4
def test_get_on_corrupted_single_entry_returns_none_without_raising(tmp_path):
    """Fichier JSON par ailleurs valide, mais avec UNE entrée corrompue
    (édition manuelle, écriture tronquée en plein milieu d'un `_save()`...).

    Avant ce correctif, `get()` laissait remonter telle quelle l'exception
    de `base64.b64decode()`/`load_der_public_key()` -- et
    `VncDisplay._connect_and_run()` appelle `get()` AVANT son bloc
    try/except (qui ne protège que `asyncvnc2.connect()` et la suite) :
    cette exception n'était donc jamais interceptée, la tâche asyncio
    plantait silencieusement sans jamais atteindre
    `_set_status(VncStatus.ERROR, ...)`, et l'onglet restait bloqué
    indéfiniment sur le spinner "Connexion à ...", sans bouton "Réessayer"
    ni "Oublier la clé enregistrée" (masqués tant que le statut reste
    CONNECTING). Ce test vérifie `HostKeyStore.get()` isolément : une
    entrée illisible doit être traitée comme absente (`None`), pas lever.
    """
    path = tmp_path / "keys.json"
    path.write_text(json.dumps({"host1:5900": "ceci-nest-pas-du-base64-valide-!!!"}))

    store = HostKeyStore(path=path)

    assert store.get("host1", 5900) is None


@requires_gtk4
def test_get_on_corrupted_entry_does_not_affect_other_entries(tmp_path, rsa_keypair):
    """Une entrée corrompue ne doit pas faire perdre les autres clés déjà
    pinnées dans le même fichier -- contrairement au repli de `_load()`
    (fichier JSON entier illisible), qui lui réinitialise tout le store
    faute de pouvoir isoler l'entrée fautive."""
    _private_key, public_key = rsa_keypair
    path = tmp_path / "keys.json"
    store = HostKeyStore(path=path)
    store.set("good-host", 5900, public_key)

    # Injection directe d'une entrée corrompue à côté d'une entrée valide,
    # sans repasser par `set()` -- simule une corruption externe au
    # fonctionnement normal de la classe.
    store._data["bad-host:5900"] = "###invalide###"
    store._save()
    reloaded = HostKeyStore(path=path)

    assert reloaded.get("bad-host", 5900) is None
    assert reloaded.get("good-host", 5900).public_numbers() == public_key.public_numbers()


@requires_gtk4
def test_save_does_not_leave_a_temp_file_behind(tmp_path, rsa_keypair):
    """`_save()` écrit d'abord dans un fichier temporaire puis le renomme
    (`os.replace`) vers la cible -- ce fichier temporaire ne doit jamais
    subsister une fois `_save()` revenu normalement (sinon le dossier
    `~/.gcm/` accumulerait des `.vnc_host_keys.json.<rand>.tmp` orphelins
    à chaque écriture)."""
    _private_key, public_key = rsa_keypair
    path = tmp_path / "keys.json"

    HostKeyStore(path=path).set("host1", 5900, public_key)

    leftovers = [p for p in tmp_path.iterdir() if p != path]
    assert leftovers == []


@requires_gtk4
def test_save_replaces_target_only_once_fully_written(tmp_path, rsa_keypair):
    """La cible ne doit jamais être visible dans un état tronqué : soit
    elle contient encore l'ancien contenu complet, soit le nouveau -- pas
    un mélange. On vérifie ça indirectement en s'assurant qu'après deux
    `set()` successifs, le fichier final est intégralement celui du
    second, et pas un artefact du premier `write_text()`-style tronqué."""
    _private_key, public_key = rsa_keypair
    path = tmp_path / "keys.json"
    store = HostKeyStore(path=path)

    store.set("host1", 5900, public_key)
    first_size = path.stat().st_size
    store.set("host2", 5901, public_key)
    second_size = path.stat().st_size

    # Deux entrées valides désormais présentes, taille cohérente (pas une
    # troncature à mi-écriture qui laisserait un fichier plus PETIT que le
    # premier alors qu'il contient strictement plus de données).
    assert second_size > first_size
    reloaded = HostKeyStore(path=path)
    assert reloaded.get("host1", 5900) is not None
    assert reloaded.get("host2", 5901) is not None


@requires_gtk4
def test_save_cleans_up_temp_file_on_write_failure(tmp_path, rsa_keypair, monkeypatch):
    """Si l'écriture échoue en cours de route (disque plein, erreur
    d'E/S...), le fichier temporaire ne doit pas rester traîner -- `_save()`
    le nettoie avant de laisser remonter l'exception."""
    _private_key, _public_key = rsa_keypair
    path = tmp_path / "keys.json"
    store = HostKeyStore(path=path)
    store._data["host1:5900"] = "peu-importe"

    import vnc_tab as vnc_tab_module

    def _boom(*_args, **_kwargs):
        raise OSError("disque plein (simulé)")

    monkeypatch.setattr(vnc_tab_module.json, "dumps", _boom)

    with pytest.raises(OSError):
        store._save()

    leftovers = [p for p in tmp_path.iterdir()]
    assert leftovers == []
