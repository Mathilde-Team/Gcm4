#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""Logique métier de GNOME Connection Manager — zéro dépendance GTK.

Extrait de ``gnome_connection_manager.py`` le 2026-08-30 (première étape de
la séparation cœur métier / cœur GTK4, avant le découpage des plugins en
dossiers — voir ``proposition-architecture-plugins-gtk4.md``). Toute
fonction ajoutée ici doit rester importable sans ``gi``/``Gtk``/``Vte`` : ce
fichier est le socle testable sans stub GTK, et servira de référence à la
future couche GTK4 (``gnome_connection_manager.py`` après portage) comme aux
plugins réorganisés en ``plugins/<nom>/core.py``.

Conventions strictes de ce fichier (voir ``CONSIGNES-AGENTS-IA.md`` à la
racine du dépôt, qui les impose à tout code futur) :

- Loguru (``app_logger``), au moins un ``debug()`` en entrée de fonction et un
  à chaque sortie (chaque ``return``, y compris les sorties anticipées).
- Docstrings style Google, avec ``Args``/``Returns``/``Raises``.
- Zéro état externe implicite : toute dépendance (registre de plugins,
  version de config...) est reçue en paramètre, jamais lue depuis un
  singleton GTK global (cf. ``proto_default_port``/``all_default_ports``
  ci-dessous, réécrites pour ne plus lire ``wMain.plugin_registry``).
- Une erreur ne doit jamais déclencher elle-même une boîte de dialogue
  (aucun ``Gtk`` possible ici) : elle est levée en exception, à charge de la
  couche GTK4 appelante de l'afficher (cf. ``load_encryption_key``).
- **Neutralité de protocole stricte** : ce fichier ne doit contenir aucune
  donnée ni logique spécifique à un protocole (SSH, série, telnet, RDP...).
  Erreur commise puis corrigée en écrivant ce fichier (2026-08-30) : les
  templates série (``_SERIAL_TEMPLATES_DEFAULT``, presets Cisco/Huawei/
  Arduino...) y avaient été déplacés depuis ``gnome_connection_manager.py``
  — remis à leur place dans ce dernier après relecture, le série étant déjà
  un plugin (``plugins/plugin_serial.py``). Toute donnée qui nomme un
  protocole ou un constructeur appartient au plugin concerné, jamais à ce
  fichier — voir ``proto_default_port``/``all_default_ports`` ci-dessous
  pour le contre-exemple correct : un *mécanisme* générique paramétré par
  ``plugin_registry``, sans aucun nom de protocole en dur.

⚠️ Point non traité dans cette extraction, à confirmer avant d'y toucher :
``widgets.py`` recalcule indépendamment son propre ``CONFIG_DIR``/
``CONFIG_FILE``/``KEY_FILE`` (ligne ~49) et son propre logger Loguru
(``_setup_app_logger``, ligne ~62), au lieu d'importer ceux de ce fichier —
ce qui signifie que l'option ``--config`` (#80) est actuellement invisible
pour tout code passant par ``widgets.CONFIG_FILE`` (ex. ``plugin_spice.py``,
pairage d'hôtes SPICE) et que deux configurations Loguru distinctes
coexistent selon l'ordre d'import. Trouvé en écrivant ce fichier, non
corrigé ici (risque de double init du logger non vérifiable sans GTK/VTE
réel) — voir ``features.md`` §2.8.

⚠️ Autres vestiges de protocole repérés dans ``gnome_connection_manager.py``
(2026-08-30), non traités ici (portée plus large que cette extraction,
touchent le contrat ``ConnectionPlugin`` de ``plugin_base.py``) : ``TEL_BIN``
(constante nommant le binaire ``telnet``) et les ensembles ``vte_protocols``/
``is_vte`` qui énumèrent en dur ``{"ssh", "telnet", "local", "serial",
"ipmi"}`` pour décider si un protocole utilise un terminal VTE. Le cœur ne
devrait pas avoir à connaître cette liste — elle devrait venir d'un attribut
déclaratif du plugin lui-même. Voir ``features.md`` §2.8.
"""

from __future__ import annotations

import argparse
import base64
import operator
import os
import random
import re
import shutil
from typing import TYPE_CHECKING, Any, cast

from loguru import logger as app_logger

# Convention bibliothèque (#130) : muet à l'import, réactivé par
# logging_config.configure_logging() (CORE_MODULES).
app_logger.disable(__name__)

import pyAES

if TYPE_CHECKING:
    from plugins.plugin_base import PluginRegistry

# État jamais relu ailleurs dans le dépôt (vérifié), conservé tel quel par
# fidélité de portage — voir bindtextdomain() ci-dessous. Nettoyage éventuel
# à traiter séparément, pas dans cette extraction.
_gcm_app_name = ""


# ─────────────────────────────────────────────────────────────────────────
# Résolution du dossier de configuration (option CLI --config, issue #80)
# ─────────────────────────────────────────────────────────────────────────


def _resolve_config_dir(argv: list[str], default_home: str) -> tuple[str, list[str]]:
    """Détermine le dossier de configuration à partir de la ligne de commande.

    Cherche une option ``--config PATH`` / ``--config=PATH`` (alias court
    ``-c PATH``) dans ``argv``. Le reste de ``argv`` — notamment les
    spécificateurs d'hôte ``groupe/nom`` ouverts au démarrage par la boucle
    historique de ``Wmain.__init__`` — est préservé tel quel et dans son
    ordre d'origine, pour ne pas changer le comportement CLI existant.

    Fonction pure (aucun accès disque : c'est l'appelant qui crée le dossier
    si besoin).

    Args:
        argv (list[str]): Arguments de la ligne de commande, sans ``argv[0]``
            (typiquement ``sys.argv[1:]``).
        default_home (str): Répertoire utilisateur par défaut (``$HOME``),
            utilisé pour construire ``~/.gcm`` en l'absence de ``--config``.

    Returns:
        tuple[str, list[str]]: Chemin absolu du dossier de configuration à
        utiliser, et liste des arguments restants (non consommés par
        ``--config``/``-c``).
    """
    app_logger.debug(f"_resolve_config_dir() called | argv={argv!r} default_home={default_home!r}")
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--config", "-c", dest="config", default=None)
    args, remaining = parser.parse_known_args(argv)
    config_dir = args.config or os.path.join(default_home, ".gcm")
    resolved = os.path.abspath(os.path.expanduser(config_dir))
    app_logger.debug(
        f"_resolve_config_dir() returning | config_dir={resolved!r} remaining={remaining!r}"
    )
    return resolved, remaining


def migrate_legacy_config_dir(old_dir: str, new_dir: str) -> bool:
    """Copie un ancien dossier de configuration vers son nouvel emplacement.

    Mécanisme générique préparé pour le renommage du projet (GCM → gcm4,
    décision actée le 2026-08-29 — voir ``docs/gcm4-rename.md`` pour
    l'inventaire complet et le plan), mais volontairement sans aucun nom de
    dossier en dur : ni ``old_dir`` ni ``new_dir`` ne sont supposés par cette
    fonction (voir ``CONSIGNES-AGENTS-IA.md`` §2, aucune dépendance externe
    implicite) — c'est à l'appelant de les fournir, une fois les noms
    définitifs (dossier de config, fichier de conf, fichier de clé) tranchés
    avec l'auteure. **Pas encore câblée** dans ``gnome_connection_manager.py``
    ni dans ``widgets.py`` : cette session (session-30) livre le mécanisme
    testé en isolation, pas son branchement — voir
    ``docs/sessions/session-30.md``.

    Copie plutôt que déplace, et ne supprime jamais ``old_dir`` : un
    utilisateur qui reviendrait à une version antérieure du logiciel doit
    retrouver sa configuration intacte à l'ancien emplacement. Les
    permissions des fichiers copiés sont préservées (``shutil.copytree``
    utilise ``copy2`` par défaut), important pour le fichier de clé de
    chiffrement (``0600``, voir ``initialise_encyption_key``). N'écrase
    jamais un ``new_dir`` déjà présent — idempotent : un second appel après
    une migration réussie, ou après que l'utilisateur a commencé à modifier
    le nouveau dossier, ne fait rien. En cas d'échec en cours de copie, le
    ``new_dir`` partiellement écrit est supprimé pour ne pas être pris pour
    une migration terminée au prochain démarrage.

    Args:
        old_dir (str): Ancien dossier de configuration (ex. l'actuel
            ``~/.gcm``).
        new_dir (str): Nouveau dossier de configuration, nom définitif pas
            encore choisi (voir ``docs/gcm4-rename.md``).

    Returns:
        bool: True si une migration a été effectuée, False si ``new_dir``
        existait déjà ou si ``old_dir`` est absent/n'est pas un dossier
        (rien à migrer, ex. premier lancement).

    Raises:
        RuntimeError: Si la copie échoue en cours de route (permissions,
        disque plein...) — ``old_dir`` n'est jamais touché, y compris dans
        ce cas.
    """
    app_logger.debug(
        f"migrate_legacy_config_dir() called | old_dir={old_dir!r} new_dir={new_dir!r}"
    )
    if os.path.exists(new_dir):
        app_logger.debug(
            "migrate_legacy_config_dir() returning | new_dir déjà présent, rien à faire"
        )
        return False
    if not os.path.isdir(old_dir):
        app_logger.debug(
            "migrate_legacy_config_dir() returning | old_dir absent ou pas un dossier"
        )
        return False
    try:
        shutil.copytree(old_dir, new_dir, symlinks=True)
    except Exception as exc:
        shutil.rmtree(new_dir, ignore_errors=True)
        app_logger.debug(f"migrate_legacy_config_dir() raising | old_dir={old_dir!r} erreur={exc}")
        raise RuntimeError(f"Error migrating config dir from {old_dir} to {new_dir}") from exc
    app_logger.debug(
        f"migrate_legacy_config_dir() returning | migration effectuée vers {new_dir!r}"
    )
    return True


# ─────────────────────────────────────────────────────────────────────────
# Dépendances système / gettext
# ─────────────────────────────────────────────────────────────────────────


def dep_install_hint(pkg_debian: str, pkg_fedora: str, pkg_arch: str | None = None) -> str:
    """Retourne la commande d'installation adaptée à l'OS détecté (``/etc/os-release``).

    Args:
        pkg_debian (str): Nom du paquet pour les distributions Debian/Ubuntu
            (et dérivées : Mint, Pop!_OS, Kali, Raspbian) et pour openSUSE
            (``zypper``).
        pkg_fedora (str): Nom du paquet pour Fedora/RHEL/CentOS/Rocky/Alma/
            Oracle, et repli pour Arch si ``pkg_arch`` est absent.
        pkg_arch (str, optional): Nom du paquet pour Arch/Manjaro/
            EndeavourOS/Garuda. Si absent, ``pkg_fedora`` est utilisé comme
            repli pour ces distributions.

    Returns:
        str: Commande d'installation suggérée pour la distribution détectée,
        ou un résumé Debian+Fedora si la distribution n'est pas reconnue
        (ou ``/etc/os-release`` illisible).
    """
    app_logger.debug(
        f"dep_install_hint() called | pkg_debian={pkg_debian!r} pkg_fedora={pkg_fedora!r}"
    )
    try:
        with open("/etc/os-release") as _f:
            _rel = _f.read().lower()
    except OSError as exc:
        app_logger.debug("dep_install_hint: {} : {}", type(exc).__name__, exc)
        _rel = ""
    if any(
        x in _rel for x in ("ubuntu", "debian", "mint", "pop", "kali", "raspbian", "linuxmint")
    ):
        result = f"sudo apt install {pkg_debian}"
    elif any(x in _rel for x in ("fedora", "rhel", "centos", "rocky", "alma", "oracle")):
        result = f"sudo dnf install {pkg_fedora}"
    elif any(x in _rel for x in ("arch", "manjaro", "endeavouros", "garuda")):
        result = f"sudo pacman -S {pkg_arch or pkg_fedora}"
    elif any(x in _rel for x in ("opensuse", "suse")):
        result = f"sudo zypper install {pkg_debian}"
    else:
        result = f"Debian/Ubuntu : sudo apt install {pkg_debian}\n  Fedora/RHEL   : sudo dnf install {pkg_fedora}"
    app_logger.debug(f"dep_install_hint() returning | result={result!r}")
    return result


def bindtextdomain(app_name: str, locale_dir: str | None = None) -> None:
    """Configure le domaine gettext pour les traductions.

    Injecte ``_()`` dans les builtins (``builtins.__dict__["_"]``) — c'est
    ainsi que la fonction de traduction devient disponible sans import dans
    tout le reste du code.

    Args:
        app_name (str): Nom du domaine de traduction.
        locale_dir (str, optional): Dossier contenant les fichiers ``.mo``.

    Returns:
        None
    """
    app_logger.debug(f"bindtextdomain() called | app_name={app_name!r} locale_dir={locale_dir!r}")
    global _gcm_app_name
    _gcm_app_name = app_name
    import builtins
    import gettext
    import locale

    def _lang_candidates() -> list[str]:
        """Retourne les langues candidates, avec repli anglais en dernier.

        Returns:
            list[str]: Codes de langue par ordre de préférence.
        """

        app_logger.debug("bindtextdomain._lang_candidates: entrée")

        def _expand_locale(loc: str) -> list[str]:
            """Décompose une locale en variantes de repli successives.

            Args:
                loc (str): Locale brute (ex. ``fr_FR.UTF-8@euro``).

            Returns:
                list[str]: Variantes ordonnées (précise -> générique).
            """
            app_logger.debug("bindtextdomain._lang_candidates._expand_locale: entrée")
            if not loc:
                app_logger.debug("bindtextdomain._lang_candidates._expand_locale: sortie")
                return []
            out = [loc]
            base = loc.split("@", 1)[0]
            base = base.split(".", 1)[0]
            if base and base not in out:
                out.append(base)
            if "_" in base:
                short = base.split("_", 1)[0]
                if short and short not in out:
                    out.append(short)
            app_logger.debug("bindtextdomain._lang_candidates._expand_locale: sortie")
            return out

        langs = []
        seen = set()

        language_env = os.environ.get("LANGUAGE", "")
        if language_env:
            for item in [x for x in language_env.split(":") if x]:
                for cand in _expand_locale(item):
                    if cand not in seen:
                        seen.add(cand)
                        langs.append(cand)

        for key in ("LC_ALL", "LC_MESSAGES", "LANG"):
            val = os.environ.get(key, "")
            if not val:
                continue
            for cand in _expand_locale(val):
                if cand not in seen:
                    seen.add(cand)
                    langs.append(cand)

        for fallback in ("en_US", "en"):
            if fallback not in seen:
                seen.add(fallback)
                langs.append(fallback)
        app_logger.debug("bindtextdomain._lang_candidates: sortie")
        return langs

    try:
        locale.setlocale(locale.LC_ALL, "")
    except (OSError, locale.Error):
        try:
            os.environ["LANG"] = "en_US.UTF-8"
            os.environ["LANGUAGE"] = "en"
            locale.setlocale(locale.LC_ALL, "")
        except Exception:
            builtins.__dict__["_"] = lambda x: x
            app_logger.debug("bindtextdomain() returning | locale indisponible, _() = identité")
            return

    try:
        locale.bindtextdomain(app_name, locale_dir)
        gettext.bindtextdomain(app_name, locale_dir)
        gettext.textdomain(app_name)
        translation = gettext.translation(
            app_name,
            localedir=locale_dir,
            languages=_lang_candidates(),
            fallback=True,
        )
        builtins.__dict__["_"] = translation.gettext
        app_logger.debug("bindtextdomain() returning | traduction installée")
    except Exception:
        builtins.__dict__["_"] = lambda x: x
        app_logger.debug("bindtextdomain() returning | échec traduction, _() = identité")
    app_logger.debug("bindtextdomain: fin")


# ─────────────────────────────────────────────────────────────────────────
# Logger applicatif
# ─────────────────────────────────────────────────────────────────────────


def setup_app_logger(config_dir: str, debug: bool = False):
    """Initialise le logger applicatif (Loguru) sur le dossier de config donné.

    Délègue à ``logging_config.configure_logging`` (issue #130) : niveau
    ``INFO`` par défaut, ``DEBUG`` avec ``--debug`` ou ``GCM_DEBUG=1``,
    ``GCM_LOG_LEVEL`` prioritaire. Le journal permanent
    ``config_dir/log/gcm-app.log`` suit le même niveau.

    Args:
        config_dir (str): Dossier de configuration (voir ``_resolve_config_dir``)
            sous lequel écrire ``log/gcm-app.log``.
        debug (bool): Option ``--debug`` de la ligne de commande.

    Returns:
        loguru.Logger: Le logger configuré (``app_logger`` de ce module).
    """
    app_logger.debug("setup_app_logger: entrée")
    import logging_config

    logger_ = logging_config.configure_logging(
        "DEBUG" if debug else None, config_dir=config_dir, force=True
    )
    app_logger.debug(
        "setup_app_logger() returning | niveau={} fichiers={}",
        logging_config.current_level(),
        logging_config.log_files(),
    )
    return logger_


# ─────────────────────────────────────────────────────────────────────────
# Identité utilisateur / chiffrement des mots de passe
# ─────────────────────────────────────────────────────────────────────────

_enc_passwd = ""


def get_username() -> str | None:
    """Retourne le nom de l'utilisateur système courant.

    Returns:
        str | None: Nom d'utilisateur (``$USER``/``$LOGNAME``/``$USERNAME``),
        ou ``None`` si aucune de ces variables d'environnement n'est définie.
    """
    app_logger.debug("get_username() called")
    result = os.getenv("USER") or os.getenv("LOGNAME") or os.getenv("USERNAME")
    app_logger.debug(f"get_username() returning | result={'<vide>' if not result else '<masqué>'}")
    return result


def get_password() -> str:
    """Retourne le mot de passe de trousseau (dérivé de la clé de chiffrement locale).

    ⚠️ Divergence délibérée du 2026-08-30 par rapport à l'original : l'original
    faisait ``get_username() + enc_passwd`` sans garde, ce qui levait un
    ``TypeError`` si aucune des variables d'environnement ``USER``/
    ``LOGNAME``/``USERNAME`` n'était définie (``get_username()`` retournant
    alors ``None``) — cas limite réel mais non testé jusqu'ici. Ajout d'un
    ``or ""`` défensif pour l'éviter ; à confirmer que ce n'est pas un
    comportement attendu (échec volontaire) avant de considérer ceci comme
    acquis.

    Returns:
        str: Nom d'utilisateur concaténé à la clé de chiffrement locale.
    """
    app_logger.debug("get_password() called")
    pwd = (get_username() or "") + _enc_passwd
    app_logger.debug(f"get_password() returning | length={len(pwd)} (masqué)")
    return pwd


def load_encryption_key(key_file: str) -> None:
    """Charge la clé de chiffrement locale depuis ``key_file``.

    Contrairement à la version d'origine (``gnome_connection_manager.py``
    avant le 2026-08-30), cette fonction ne montre plus de boîte de dialogue
    en cas d'erreur — impossible depuis un module sans GTK. Elle lève une
    exception à la place ; c'est à l'appelant GTK4 de l'attraper et
    d'afficher son propre message.

    Args:
        key_file (str): Chemin du fichier de clé (``KEY_FILE``).

    Returns:
        None

    Raises:
        RuntimeError: Si ``key_file`` existe mais ne peut pas être lu.
    """
    app_logger.debug(f"load_encryption_key() called | key_file={key_file!r}")
    global _enc_passwd
    try:
        if os.path.exists(key_file):
            with open(key_file) as f:
                _enc_passwd = f.read()
        else:
            _enc_passwd = ""
    except Exception as exc:
        _enc_passwd = ""
        app_logger.debug(f"load_encryption_key() raising | key_file={key_file!r} erreur={exc}")
        raise RuntimeError(f"Error trying to open key_file: {key_file}") from exc
    app_logger.debug("load_encryption_key() returning | clé chargée")


def initialise_encyption_key(key_file: str) -> None:
    """Génère et persiste une nouvelle clé de chiffrement locale.

    Voir ``load_encryption_key`` pour la différence de contrat avec l'ancien
    comportement (exception au lieu de boîte de dialogue).

    Args:
        key_file (str): Chemin du fichier de clé à créer (``KEY_FILE``).

    Returns:
        None

    Raises:
        RuntimeError: Si ``key_file`` ne peut pas être créé/écrit.
    """
    app_logger.debug(f"initialise_encyption_key() called | key_file={key_file!r}")
    global _enc_passwd
    x = int(str(random.random())[2:])
    y = int(str(random.random())[2:])
    _enc_passwd = "%x" % (x * y)
    try:
        with os.fdopen(os.open(key_file, os.O_WRONLY | os.O_CREAT, 0o600), "w") as f:
            f.write(_enc_passwd)
    except Exception as exc:
        app_logger.debug(
            f"initialise_encyption_key() raising | key_file={key_file!r} erreur={exc}"
        )
        raise RuntimeError(f"Error initialising key_file: {key_file}") from exc
    app_logger.debug("initialise_encyption_key() returning | clé générée et écrite")


# Fonctions de chiffrement — pas très sûres, mais évitent que les mots de
# passe soient enregistrés en texte clair.
def xor(pw: str, str1: str) -> list[str]:
    """Applique un XOR octet-à-octet entre deux séquences.

    Args:
        pw (str): Clé XOR (chaîne de caractères).
        str1 (str): Données à chiffrer (chaîne de caractères).

    Returns:
        list[str]: Liste des caractères résultant du XOR.
    """
    app_logger.debug(f"xor() called | len(str1)={len(str1)}")
    c = 0
    liste = []
    for k in range(len(str1)):
        if c > len(pw) - 1:
            c = 0
        fi = ord(pw[c])
        c += 1
        se = ord(str1[k])
        fin = operator.xor(fi, se)
        liste += [chr(fin)]
    app_logger.debug(f"xor() returning | len(result)={len(liste)}")
    return liste


def encrypt_old(passw: str, string: str) -> str:
    """Chiffre une chaîne avec l'ancien algorithme XOR (compatibilité).

    Args:
        passw (str): Clé de chiffrement XOR.
        string (str): Texte en clair à chiffrer.

    Returns:
        str: Texte chiffré encodé en base64, ou chaîne vide en cas d'erreur.
    """
    app_logger.debug(f"encrypt_old() called | len(string)={len(string) if string else 0}")
    try:
        ret = xor(passw, string)
        s = base64.b64encode("".join(ret).encode()).decode()
    except Exception:
        app_logger.debug("encrypt_old() returning | échec, chaîne vide")
        return ""
    app_logger.debug(f"encrypt_old() returning | len(result)={len(s)}")
    return s


def decrypt_old(passw: str, string: str) -> str:
    """Déchiffre une chaîne chiffrée avec l'ancien algorithme XOR.

    Args:
        passw (str): Clé de chiffrement XOR.
        string (str): Texte chiffré encodé en base64.

    Returns:
        str: Texte en clair, ou chaîne vide en cas d'erreur.
    """
    app_logger.debug(f"decrypt_old() called | len(string)={len(string) if string else 0}")
    try:
        decoded = base64.b64decode(string if isinstance(string, bytes) else string.encode())
        ret = xor(passw, decoded.decode())
        s = "".join(ret)
    except Exception:
        app_logger.debug("decrypt_old() returning | échec, chaîne vide")
        return ""
    app_logger.debug(f"decrypt_old() returning | len(result)={len(s)}")
    return s


def encrypt(passw: str, string: str) -> str:
    """Chiffre une chaîne avec AES.

    Args:
        passw (str): Clé de chiffrement AES.
        string (str): Texte en clair à chiffrer.

    Returns:
        str: Texte chiffré encodé en base64, ou chaîne vide en cas d'erreur.
    """
    app_logger.debug(f"encrypt() called | len(string)={len(string) if string else 0}")
    try:
        s = pyAES.encrypt(string, passw)
    except Exception:
        app_logger.exception("Erreur de chiffrement AES")
        app_logger.debug("encrypt() returning | échec, chaîne vide")
        return ""
    app_logger.debug(f"encrypt() returning | len(result)={len(s) if s else 0}")
    return s


def decrypt(passw: str, string: str, version: int = 0) -> str:
    """Déchiffre une chaîne chiffrée avec AES (ou l'ancien XOR selon ``version``).

    Réécriture du 2026-08-30 : reçoit désormais ``version`` en paramètre
    explicite au lieu de lire le singleton global ``utils.conf.VERSION``
    (``utils.py`` importe GTK, ce module ne doit rien lui devoir). L'appelant
    passe ``conf.VERSION`` — le défaut ``0`` reproduit le comportement
    d'origine pour tout appel qui ne le précise pas.

    Args:
        passw (str): Clé de chiffrement AES.
        string (str): Texte chiffré base64.
        version (int): Version du format de stockage — ``0`` = ancien XOR,
            toute autre valeur = AES. Defaults to 0.

    Returns:
        str: Texte en clair, ou chaîne vide en cas d'erreur.
    """
    app_logger.debug(
        f"decrypt() called | len(string)={len(string) if string else 0} version={version}"
    )
    try:
        s = decrypt_old(passw, string) if version == 0 else pyAES.decrypt(string, passw)
    except Exception:
        app_logger.exception("Erreur de déchiffrement AES")
        app_logger.debug("decrypt() returning | échec, chaîne vide")
        return ""
    app_logger.debug(f"decrypt() returning | len(result)={len(s) if s else 0}")
    return s


# ─────────────────────────────────────────────────────────────────────────
# Couleurs (formatage pur — le parsing Gdk.RGBA reste côté GTK)
# ─────────────────────────────────────────────────────────────────────────


def color_to_hex(rgba, diff: int = 0) -> str:
    """Convertit des composantes RGBA en chaîne hexadécimale CSS.

    Fonction pure par duck-typing : n'importe aucun module GTK/Gdk, mais
    accepte en pratique un ``Gdk.RGBA`` (ou tout objet exposant les mêmes
    attributs, ex. dans les tests).

    Args:
        rgba: Objet couleur exposant ``.red``, ``.green``, ``.blue`` en
            float 0..1 (ex. ``Gdk.RGBA``).
        diff (int): Différence additive à appliquer aux composantes (avant
            conversion en 0..255). Defaults to 0.

    Returns:
        str: Chaîne ``'#rrggbb'`` (non zero-paddée, comportement d'origine
        conservé à l'identique).
    """
    app_logger.debug(f"color_to_hex() called | diff={diff}")
    result = "#%x%x%x" % (
        int(rgba.red * 255 + diff),
        int(rgba.green * 255 + diff),
        int(rgba.blue * 255 + diff),
    )
    app_logger.debug(f"color_to_hex() returning | result={result}")
    return result


# ─────────────────────────────────────────────────────────────────────────
# Ports par défaut par protocole (dérivés des plugins chargés)
# ─────────────────────────────────────────────────────────────────────────


def proto_default_port(proto: str, plugin_registry: PluginRegistry) -> str:
    """Retourne le port par défaut (str) déclaré par le plugin de ``proto``.

    Réécriture du 2026-08-30 : reçoit désormais ``plugin_registry`` en
    paramètre explicite au lieu de lire le singleton GTK global ``wMain``
    (couplage repéré en écrivant ce fichier — un module métier ne doit
    jamais reconstruire une dépendance en allant chercher un objet GTK par
    son nom global).

    Complément du 2026-10-18 (#146) : ``plugin.default_port`` est accepté
    comme attribut (plugins GTK3) **ou** comme méthode appelée avec
    ``proto`` (contrat v1 de ``gcm4.core.plugins.ConnectionPlugin``) —
    auparavant, une méthode était convertie en chaîne "<bound method ...>".

    Args:
        proto (str): Identifiant de protocole (ex. ``"ssh"``, ``"rdp"``...).
        plugin_registry (PluginRegistry): Registre de plugins chargé
            (``wMain.plugin_registry`` côté appelant GTK4).

    Returns:
        str: Port par défaut, ou chaîne vide si non pertinent/plugin absent.
    """
    app_logger.debug(f"proto_default_port() called | proto={proto!r}")
    plugin = plugin_registry.get(proto)
    if plugin is None:
        app_logger.debug(
            "proto_default_port() returning | vide (plugin absent ou sans port par défaut)"
        )
        return ""
    port = plugin.default_port
    if callable(port):  # contrat v1 : default_port(protocol) est une méthode
        port = port(proto)
    if port is None:
        app_logger.debug(
            "proto_default_port() returning | vide (plugin absent ou sans port par défaut)"
        )
        return ""
    result = str(port)
    app_logger.debug(f"proto_default_port() returning | result={result!r}")
    return result


def all_default_ports(plugin_registry: PluginRegistry) -> set[str]:
    """Retourne l'ensemble des ports par défaut connus, tous protocoles confondus.

    Utilisé pour détecter si le champ Port affiche encore une valeur par
    défaut générique (donc remplaçable sans écraser une saisie utilisateur).
    Voir ``proto_default_port`` pour le contexte de la réécriture du
    2026-08-30 (paramètre explicite au lieu du global ``wMain``) et le
    complément du 2026-10-18 (#146) : les plugins du contrat v1 y déclarent
    leur port par une méthode, parcourue via ``plugin.protocols``.

    Args:
        plugin_registry (PluginRegistry): Registre de plugins chargé.

    Returns:
        set[str]: Ports par défaut (+ chaîne vide).
    """
    app_logger.debug("all_default_ports() called")
    result = {""}
    # Registre GTK4 (``plugins()``) ou ancien registre GTK3 (``all()``).
    tous: list[Any] = (
        plugin_registry.all()
        if hasattr(plugin_registry, "all")
        else cast(Any, plugin_registry).plugins()
    )
    for plugin in tous:
        port = plugin.default_port
        if callable(port):  # contrat v1 : default_port(protocol) est une méthode
            for proto in getattr(plugin, "protocols", ()):  # protocoles gérés par le plugin
                valeur = port(proto)
                if valeur is not None:
                    result.add(str(valeur))
        elif port is not None:
            result.add(str(port))
    app_logger.debug(f"all_default_ports() returning | count={len(result)}")
    return result


# ─────────────────────────────────────────────────────────────────────────
# Zoom terminal
# ─────────────────────────────────────────────────────────────────────────

_TERMINAL_ZOOM_MIN_SIZE = 4
_TERMINAL_ZOOM_MAX_SIZE = 72


def compute_zoom_size(
    current_size: float,
    delta: float,
    minimum: float = _TERMINAL_ZOOM_MIN_SIZE,
    maximum: float = _TERMINAL_ZOOM_MAX_SIZE,
) -> float:
    """Calcule la nouvelle taille de police après un pas de zoom, bornée.

    Fonction pure volontairement isolée de ``Wmain.terminal_zoom()`` pour
    rester testable sans stub GTK/VTE.

    Args:
        current_size (float): Taille actuelle en points.
        delta (float): Variation à appliquer (positive = zoom avant,
            négative = zoom arrière, 0 = inchangé).
        minimum (float): Taille minimale autorisée. Defaults to
            ``_TERMINAL_ZOOM_MIN_SIZE``.
        maximum (float): Taille maximale autorisée. Defaults to
            ``_TERMINAL_ZOOM_MAX_SIZE``.

    Returns:
        float: Nouvelle taille, bornée entre ``minimum`` et ``maximum``.
    """
    app_logger.debug(f"compute_zoom_size() called | current_size={current_size} delta={delta}")
    result = max(minimum, min(maximum, current_size + delta))
    app_logger.debug(f"compute_zoom_size() returning | result={result}")
    return result


# Alias rétro-compatible (nom historique dans gnome_connection_manager.py
# et dans tests/test_gcm.py, conservé pour ne rien casser côté appelants
# existants pendant la transition — cf. claude.md).
_compute_zoom_size = compute_zoom_size


# ─────────────────────────────────────────────────────────────────────────
# Mode cluster — masquage des mots de passe (#P=..., features.md §4.2)
# ─────────────────────────────────────────────────────────────────────────

# Convention introduite ici (aucune trace de ce marqueur ailleurs dans le
# dépôt ni dans une doc historique) : dans la zone de commandes du dialogue
# Cluster (Wcluster), un utilisateur peut saisir ``#P=<valeur>`` à la place
# d'un mot de passe en clair. ``<valeur>`` s'arrête au premier espace ou en
# fin de ligne (pas de guillemets pris en charge). Ce mécanisme est neutre
# de protocole : le marqueur est traité au niveau du dialogue cluster
# lui-même (mode SSH/telnet/local/série...), pas d'un plugin en particulier.
_CLUSTER_PASSWORD_TOKEN_RE = re.compile(r"#P=(\S*)")


def resolve_cluster_command(text: str) -> str:
    """Remplace chaque marqueur ``#P=<valeur>`` par la valeur en clair.

    C'est le texte à effectivement envoyer aux terminaux (``vte_feed``) :
    contrairement à ``mask_cluster_command()`` (utilisé pour l'historique
    affiché), cette fonction ne masque rien — la commande envoyée à l'hôte
    distant doit contenir le vrai mot de passe.

    Fonction pure (aucun accès GTK/VTE), volontairement séparée de
    ``Wcluster.send_cluster_commands()`` pour rester testable sans stub.

    Args:
        text (str): Texte brut saisi dans la zone de commandes cluster,
            pouvant contenir zéro, un ou plusieurs marqueurs ``#P=<valeur>``.

    Returns:
        str: ``text`` avec chaque marqueur ``#P=<valeur>`` remplacé par
        ``<valeur>`` seule (marqueur ``#P=`` retiré).
    """
    app_logger.debug(f"resolve_cluster_command() called | length={len(text)}")
    result = _CLUSTER_PASSWORD_TOKEN_RE.sub(lambda m: m.group(1), text)
    app_logger.debug(f"resolve_cluster_command() returning | length={len(result)}")
    return result


def mask_cluster_command(text: str) -> str:
    """Masque la valeur de chaque marqueur ``#P=<valeur>`` par des astérisques.

    Utilisé pour ce qui est conservé dans l'historique cluster
    (``widget.history``, rappel CTRL+UP/CTRL+DOWN) : le mot de passe en
    clair ne doit jamais persister dans cet historique en mémoire, même
    temporairement — voir ``send_cluster_commands()`` dans
    ``gnome_connection_manager.py``. Conséquence assumée : un marqueur
    rappelé depuis l'historique n'est plus réutilisable tel quel (l'astérisque
    n'est pas le mot de passe), l'utilisateur doit resaisir la valeur —
    compromis volontaire, la sécurité d'affichage prime sur le confort de
    rappel pour ce cas précis.

    Fonction pure (aucun accès GTK/VTE).

    Args:
        text (str): Texte brut saisi dans la zone de commandes cluster,
            pouvant contenir zéro, un ou plusieurs marqueurs ``#P=<valeur>``.

    Returns:
        str: ``text`` avec chaque valeur de marqueur remplacée par autant
        d'astérisques (``#P=`` conservé tel quel, pour que la commande reste
        lisible comme « un mot de passe était ici »).
    """
    app_logger.debug(f"mask_cluster_command() called | length={len(text)}")
    result = _CLUSTER_PASSWORD_TOKEN_RE.sub(lambda m: "#P=" + "*" * len(m.group(1)), text)
    app_logger.debug(f"mask_cluster_command() returning | length={len(result)}")
    return result


# ─────────────────────────────────────────────────────────────────────────
# Mode cluster — commandes favorites (#84)
# ─────────────────────────────────────────────────────────────────────────

# Décision de l'auteure (#84, 2026-10-12) : la « liste déroulante » du
# dialogue Cluster est une liste de commandes favorites éditable, distincte
# de l'historique CTRL+haut/CTRL+bas, préremplie avec ces trois commandes.
CLUSTER_FAVORITES_DEFAULT: tuple[str, ...] = ("df", "apt update", "updatedb")

# Section INI dédiée (clés ``command1``..``commandN`` dans l'ordre). Section
# absente : favoris par défaut ; section présente mais vide : l'utilisateur
# a tout retiré, la liste reste vide.
CLUSTER_FAVORITES_SECTION = "cluster_favorites"


def _cluster_favorite_key_index(option: str) -> int:
    """Rang numérique d'une option ``commandN`` (``-1`` si elle est invalide).

    Args:
        option (str): Nom d'option lu dans la section ``[cluster_favorites]``.

    Returns:
        int: ``N`` pour ``commandN``, ``-1`` pour toute autre option.
    """
    app_logger.debug(f"_cluster_favorite_key_index() called | option={option!r}")
    suffix = option[len("command") :] if option.startswith("command") else ""
    result = int(suffix) if suffix.isdigit() else -1
    app_logger.debug(f"_cluster_favorite_key_index() returning | result={result}")
    return result


def load_cluster_favorites(cp: Any) -> list[str]:
    """Lit les commandes favorites du cluster depuis un ConfigParser.

    Les retours à la ligne sont stockés échappés (barre oblique inverse
    suivie de ``n``), comme les raccourcis de la section ``[shortcuts]``.
    Les clés non reconnues et les valeurs vides sont ignorées, les doublons supprimés (ordre conservé).

    Args:
        cp (configparser.RawConfigParser): Configuration déjà lue.

    Returns:
        list[str]: Favoris dans l'ordre d'affichage.
    """
    app_logger.debug("load_cluster_favorites() called")
    if not cp.has_section(CLUSTER_FAVORITES_SECTION):
        result = list(CLUSTER_FAVORITES_DEFAULT)
        app_logger.debug(f"load_cluster_favorites() returning | defaults count={len(result)}")
        return result
    rows = []
    for option, value in cp.items(CLUSTER_FAVORITES_SECTION):
        index = _cluster_favorite_key_index(option)
        if index >= 0:
            rows.append((index, value.replace("\\n", "\n")))
    result = []
    for _index, value in sorted(rows):
        if value.strip() and value not in result:
            result.append(value)
    app_logger.debug(f"load_cluster_favorites() returning | count={len(result)}")
    return result


def save_cluster_favorites(cp: Any, favorites: list[str]) -> None:
    """Écrit (en remplaçant) la section ``[cluster_favorites]``.

    Args:
        cp (configparser.RawConfigParser): Configuration à mettre à jour.
        favorites (list[str]): Favoris dans l'ordre d'affichage.
    """
    app_logger.debug(f"save_cluster_favorites() called | count={len(favorites)}")
    if cp.has_section(CLUSTER_FAVORITES_SECTION):
        cp.remove_section(CLUSTER_FAVORITES_SECTION)
    cp.add_section(CLUSTER_FAVORITES_SECTION)
    for position, command in enumerate(favorites, start=1):
        cp.set(CLUSTER_FAVORITES_SECTION, f"command{position}", command.replace("\n", "\\n"))
    app_logger.debug("save_cluster_favorites() returning")


def add_cluster_favorite(favorites: list[str], text: str) -> list[str]:
    """Retourne la liste des favoris complétée par ``text``.

    Le texte est débarrassé des blancs de fin (le retour chariot est ajouté à
    l'envoi). Une commande contenant un marqueur ``#P=`` est refusée : un
    favori est écrit en clair dans ``gcm.conf``, le mot de passe y fuirait.

    Args:
        favorites (list[str]): Favoris actuels (non modifiés).
        text (str): Commande à ajouter.

    Returns:
        list[str]: Nouvelle liste (inchangée si la commande y figure déjà).

    Raises:
        ValueError: Commande vide ou contenant un marqueur ``#P=``.
    """
    app_logger.debug(f"add_cluster_favorite() called | count={len(favorites)} length={len(text)}")
    command = text.rstrip()
    if not command.strip():
        app_logger.debug("add_cluster_favorite() raising | empty command")
        raise ValueError("empty command")
    if _CLUSTER_PASSWORD_TOKEN_RE.search(command):
        app_logger.debug("add_cluster_favorite() raising | password token")
        raise ValueError("password token")
    result = list(favorites)
    if command not in result:
        result.append(command)
    app_logger.debug(f"add_cluster_favorite() returning | count={len(result)}")
    return result


def remove_cluster_favorite(favorites: list[str], text: str) -> list[str]:
    """Retourne la liste des favoris privée de ``text`` (absent : inchangée).

    Args:
        favorites (list[str]): Favoris actuels (non modifiés).
        text (str): Commande à retirer.

    Returns:
        list[str]: Nouvelle liste.
    """
    app_logger.debug(f"remove_cluster_favorite() called | count={len(favorites)}")
    result = [command for command in favorites if command != text]
    app_logger.debug(f"remove_cluster_favorite() returning | count={len(result)}")
    return result


def resolve_auto_close_tab(host_override: str, global_default: int) -> int:
    """Résout le mode de fermeture automatique effectif pour un onglet.

    Combine la surcharge par hôte (``Host.auto_close_tab``, voir
    ``models.py`` — backlog features.md §4.2, "fermeture automatique
    d'onglet #77, volet par host") avec le réglage global
    ``conf.AUTO_CLOSE_TAB`` quand la surcharge est vide ou invalide.

    Fonction pure (aucun accès GTK/VTE), extraite de
    ``NotebookTabLabel.mark_tab_as_closed()``/``effective_auto_close_tab()``
    (``widgets.py``) pour rester testable sans stub GTK — même logique que
    ``resolve_cluster_command()``/``compute_zoom_size()`` ci-dessus.

    Args:
        host_override (str): Valeur de ``Host.auto_close_tab`` : ``""``
            (hérite du réglage global), ``"0"``/``"1"``/``"2"``
            (Jamais/Toujours/Seulement en sortie propre). Toute autre
            valeur (y compris ``None`` ou une chaîne inattendue provenant
            d'un ancien fichier de configuration) est traitée comme ``""``
            — repli silencieux sur le réglage global plutôt qu'une
            exception, cohérent avec ``HostUtils.get_val()`` qui retourne
            déjà des chaînes non validées depuis l'INI.
        global_default (int): Valeur courante de ``conf.AUTO_CLOSE_TAB``
            (0/1/2), utilisée quand ``host_override`` n'est pas une
            surcharge valide.

    Returns:
        int: 0 (jamais), 1 (toujours) ou 2 (seulement en sortie propre).
    """
    app_logger.debug(
        f"resolve_auto_close_tab() called | host_override={host_override!r} "
        f"global_default={global_default!r}"
    )
    if host_override in ("0", "1", "2"):
        result = int(host_override)
        app_logger.debug(
            f"resolve_auto_close_tab() returning | surcharge hôte appliquée: {result}"
        )
        return result
    app_logger.debug(
        f"resolve_auto_close_tab() returning | repli sur global_default={global_default}"
    )
    return global_default


def workspace_hosts(groups: dict) -> list:
    """Liste les hôtes marqués comme faisant partie de l'espace de travail.

    Backlog features.md §4.2, "Espace de travail (workspace)" : une coche
    dans "Éditer hôte" (``Host.workspace``, voir ``models.py``) ajoute un
    hôte à l'espace de travail ; un bouton/action ouvre en une fois tous les
    hôtes ainsi marqués, quel que soit leur groupe ou protocole.

    Fonction pure (aucun accès GTK/VTE) : parcourt ``groups`` sans importer
    ``models.Host`` — duck-typing via ``getattr(host, "workspace", False)``
    plutôt qu'un ``isinstance`` — pour rester testable avec de simples
    objets factices, sans dépendance croisée avec ``models.py``.

    Args:
        groups (dict): Dictionnaire nom de groupe -> liste de ``Host``, tel
            que ``gnome_connection_manager.py::groups`` (reconstruit à
            chaque ``loadConfig()`` depuis les hôtes chargés).

    Returns:
        list: Les objets hôte pour lesquels ``workspace`` est vrai, dans
        l'ordre de parcours de ``groups`` (l'ordre des groupes puis des
        hôtes à l'intérieur de chaque groupe, sans tri supplémentaire).
    """
    app_logger.debug(f"workspace_hosts() called | groups={len(groups)} groupe(s)")
    result = [
        host for hosts in groups.values() for host in hosts if getattr(host, "workspace", False)
    ]
    app_logger.debug(
        f"workspace_hosts() returning | {len(result)} hôte(s) dans l'espace de travail"
    )
    return result


def serialize_open_tabs(open_hosts) -> list:
    """Construit la liste des identifiants "groupe/nom" à persister pour la
    restauration des onglets au prochain démarrage.

    Backlog features.md §4.2, "Sauvegarde/restauration des onglets ouverts".
    Fonction pure (aucun accès GTK/VTE) : reçoit la liste des hôtes déjà
    extraits des onglets ouverts (même parcours de widgets que
    ``Wmain.on_btnCluster_clicked``, ``terminal.host`` pour chaque page de
    ``nbConsole``), sans jamais toucher directement à ``Gtk.Notebook`` — ce
    fichier reste neutre de GTK, la traversée des widgets reste du ressort
    de ``gnome_connection_manager.py::writeConfig()``.

    Args:
        open_hosts: Itérable d'objets hôte (duck-typing sur ``.group``/
            ``.name``, pas d'``isinstance`` sur ``models.Host`` pour rester
            testable sans dépendance croisée). Un onglet sans groupe/nom
            résolus dans ``groups`` (ex. l'onglet "local", créé avec
            ``Host(group="", name="local")`` dans ``addTab``) est ignoré :
            il n'a pas d'entrée stable dans l'arbre des hôtes à restaurer.

    Returns:
        list: Chaînes "groupe/nom", dans l'ordre de première apparition,
        dédoublonnées (un même hôte ouvert dans plusieurs onglets ne doit
        pas être restauré plusieurs fois).
    """
    app_logger.debug(f"serialize_open_tabs() called | {len(open_hosts)} onglet(s) ouvert(s)")
    seen = set()
    specs = []
    for host in open_hosts:
        group = getattr(host, "group", "") or ""
        name = getattr(host, "name", "") or ""
        if not group or not name:
            continue
        spec = f"{group}/{name}"
        if spec in seen:
            continue
        seen.add(spec)
        specs.append(spec)
    app_logger.debug(f"serialize_open_tabs() returning | {len(specs)} identifiant(s)")
    return specs


def parse_open_tabs(raw: str) -> list:
    """Découpe la chaîne persistée (``conf.OPEN_TABS``, section ``window``
    du fichier INI) en liste d'identifiants "groupe/nom".

    Fonction pure symétrique de ``serialize_open_tabs`` côté lecture :
    sépare sur la virgule, ignore les entrées vides (chaîne vide au
    premier démarrage, virgules superflues) sans lever d'exception.

    Args:
        raw (str): Valeur brute lue dans le fichier INI (``conf.OPEN_TABS``),
            ex. ``"prod/web1,dev/test1"``. Chaîne vide ou ``None`` accepté.

    Returns:
        list: Identifiants "groupe/nom" non vides, dans l'ordre d'origine.
    """
    app_logger.debug(f"parse_open_tabs() called | raw={raw!r}")
    if not raw:
        app_logger.debug("parse_open_tabs() returning | liste vide (raw vide)")
        return []
    specs = [spec.strip() for spec in raw.split(",") if spec.strip()]
    app_logger.debug(f"parse_open_tabs() returning | {len(specs)} identifiant(s)")
    return specs


def resolve_host_specs(specs, groups: dict) -> list:
    """Résout une liste d'identifiants "groupe/nom" en objets hôte réels.

    Logique de résolution extraite de la boucle historique d'ouverture
    d'hôtes par spécificateur en ligne de commande (``for arg in
    sys.argv[1:]: ...`` dans ``Wmain.__init__``, cf. ``claude.md``) —
    réutilisée ici pour la restauration des onglets au démarrage
    (``serialize_open_tabs``/``parse_open_tabs`` ci-dessus) plutôt que
    dupliquée. Duck-typing sur les objets de ``groups`` (``.name``), pas
    d'``isinstance`` sur ``models.Host``, pour rester testable sans
    dépendance croisée.

    Args:
        specs: Itérable de chaînes "groupe/nom" (ex. sortie de
            ``parse_open_tabs``).
        groups (dict): Dictionnaire nom de groupe -> liste d'objets hôte,
            tel que ``gnome_connection_manager.py::groups``.

    Returns:
        list: Objets hôte résolus, dans l'ordre de ``specs``. Un
        spécificateur malformé (pas de "/"), un groupe absent de
        ``groups``, ou un nom d'hôte introuvable dans ce groupe est
        silencieusement ignoré (l'hôte a pu être renommé/supprimé depuis
        la dernière sauvegarde) plutôt que de lever une exception.
    """
    app_logger.debug("resolve_host_specs() called")
    resolved = []
    for spec in specs:
        if not spec:
            continue
        i = spec.rfind("/")
        if i == -1:
            continue
        group, name = spec[:i], spec[i + 1 :]
        if not group or not name or group not in groups:
            continue
        for host in groups[group]:
            if getattr(host, "name", None) == name:
                resolved.append(host)
                break
    app_logger.debug(f"resolve_host_specs() returning | {len(resolved)} hôte(s) résolu(s)")
    return resolved


def serialize_group_colors(color_map: dict) -> str:
    """Sérialise les couleurs de groupe en une chaîne persistable (INI).

    Backlog features.md §4.2, "Couleurs par groupe" (comme PuTTY). Fonction
    pure (aucun accès GTK/VTE), même convention de sérialisation que
    ``serialize_open_tabs``/``parse_open_tabs`` ci-dessus (chaîne unique
    séparée par des virgules dans la section ``window`` du fichier INI,
    plutôt qu'une section dédiée — ``configparser`` normalise la casse des
    clés d'option par défaut, ce qui casserait un nom de groupe contenant
    des majuscules si on l'utilisait comme clé d'option).

    Args:
        color_map (dict): Dictionnaire chemin de groupe (ex. ``"Prod/Web"``)
            -> couleur hexadécimale (ex. ``"#ff8800"``), tel que
            ``Wmain.group_colors``. Une entrée sans groupe ou sans couleur
            est ignorée.

    Returns:
        str: Entrées ``"groupe=couleur"`` séparées par des virgules, dans
        l'ordre d'itération de ``color_map``. Chaîne vide si ``color_map``
        est vide ou ``None``.
    """
    app_logger.debug(f"serialize_group_colors() called | {len(color_map or {})} entrée(s)")
    if not color_map:
        app_logger.debug("serialize_group_colors() returning | chaîne vide (color_map vide)")
        return ""
    parts = [f"{group}={color}" for group, color in color_map.items() if group and color]
    result = ",".join(parts)
    app_logger.debug(f"serialize_group_colors() returning | {len(parts)} entrée(s) sérialisée(s)")
    return result


def parse_group_colors(raw: str) -> dict:
    """Découpe la chaîne persistée (``conf.GROUP_COLORS``) en dictionnaire.

    Fonction pure symétrique de ``serialize_group_colors`` côté lecture :
    ignore silencieusement les entrées malformées (pas de ``"="``, groupe
    ou couleur vide) plutôt que de lever une exception — même tolérance que
    ``parse_open_tabs`` pour un ancien fichier de configuration.

    Args:
        raw (str): Valeur brute lue dans le fichier INI (``conf.GROUP_COLORS``),
            ex. ``"Prod/Web=#ff8800,Dev=#00aaff"``. Chaîne vide ou ``None``
            accepté.

    Returns:
        dict: Dictionnaire chemin de groupe -> couleur hexadécimale. Dict
        vide si ``raw`` est vide ou ``None``.
    """
    app_logger.debug(f"parse_group_colors() called | raw={raw!r}")
    result: dict[str, str] = {}
    if not raw:
        app_logger.debug("parse_group_colors() returning | dict vide (raw vide)")
        return result
    for part in raw.split(","):
        part = part.strip()
        if not part or "=" not in part:
            continue
        group, _, color = part.partition("=")
        group = group.strip()
        color = color.strip()
        if group and color:
            result[group] = color
    app_logger.debug(f"parse_group_colors() returning | {len(result)} entrée(s)")
    return result


def resolve_group_color(group: str, color_map: dict, default: str = "") -> str:
    """Résout la couleur effective d'un groupe (ou d'un hôte via son groupe).

    Un sous-dossier ou un hôte sans couleur propre hérite de la couleur du
    premier dossier ancêtre qui en a une (ex. une couleur posée sur
    ``"Prod"`` s'applique aussi à ``"Prod/Web"`` et aux hôtes qu'il
    contient, sauf si ``"Prod/Web"`` a sa propre couleur qui prime) —
    comportement attendu d'une coloration "par dossier" façon PuTTY.

    Fonction pure (aucun accès GTK/VTE), extraite pour rester testable sans
    stub GTK — même principe que ``resolve_auto_close_tab``.

    Args:
        group (str): Chemin de groupe complet (ex. ``"Prod/Web"``), ou
            chaîne vide/``None`` pour un hôte sans groupe (ex. l'onglet
            "local").
        color_map (dict): Dictionnaire chemin de groupe -> couleur
            hexadécimale, tel que retourné par ``parse_group_colors``.
        default (str): Valeur retournée si aucun ancêtre (ni ``group``
            lui-même) n'a de couleur. Par défaut chaîne vide (aucune
            surcharge : le rendu doit alors utiliser la couleur du thème).

    Returns:
        str: La couleur hexadécimale effective, ou ``default``.
    """
    app_logger.debug(f"resolve_group_color() called | group={group!r}")
    if not group or not color_map:
        app_logger.debug(f"resolve_group_color() returning | default={default!r}")
        return default
    parts = group.split("/")
    for i in range(len(parts), 0, -1):
        candidate = "/".join(parts[:i])
        if candidate in color_map:
            result = color_map[candidate]
            app_logger.debug(
                f"resolve_group_color() returning | trouvé sur ancêtre {candidate!r}: {result!r}"
            )
            return result
    app_logger.debug(
        f"resolve_group_color() returning | default={default!r} (aucun ancêtre coloré)"
    )
    return default


def resolve_connection_hook_command(
    template: str,
    host_name: str = "",
    host_address: str = "",
    host_group: str = "",
    protocol: str = "",
) -> str | None:
    """Résout les marqueurs d'un modèle de commande "hook" avant/après connexion.

    Utilisée par ``run_pre_connect_hook()`` et par la résolution du hook
    "après connexion" câblée dans ``Wmain._open_connection_tab()``/
    ``Wmain.addTab()`` (backlog features.md §4.2 #116, "Scripting avant/après
    connexion", les deux volets sont faits) : ``Host.pre_connect_command``/
    ``Host.post_connect_command`` sont des modèles de commande shell
    arbitraires saisis par l'utilisateur dans "Éditer hôte", pouvant
    contenir zéro ou plusieurs marqueurs ``{name}``, ``{address}``,
    ``{group}``, ``{protocol}``. Fonction pure (aucun accès
    GTK/VTE/subprocess), volontairement séparée de l'exécution effective de
    la commande pour rester testable sans stub — même principe que
    ``resolve_cluster_command()``/``resolve_auto_close_tab()`` ci-dessus.

    Un marqueur inconnu (faute de frappe, ex. ``{nam}``) ne doit jamais faire
    planter la résolution : ``template`` est alors retourné inchangé plutôt
    que de lever une exception, le shell affichera l'accolade littérale et
    l'utilisateur pourra corriger sa saisie — préférable à une connexion qui
    échoue silencieusement à cause d'un hook mal écrit.

    Args:
        template (str): Modèle de commande brut, ou chaîne vide/``None`` si
            aucun hook n'est configuré pour cet hôte.
        host_name (str): Valeur substituée à ``{name}``. Defaults to "".
        host_address (str): Valeur substituée à ``{address}``. Defaults to "".
        host_group (str): Valeur substituée à ``{group}``. Defaults to "".
        protocol (str): Valeur substituée à ``{protocol}``. Defaults to "".

    Returns:
        str | None: ``None`` si ``template`` est vide/blanc (aucune commande
        à exécuter) ; sinon la commande avec chaque marqueur connu remplacé,
        ou ``template`` inchangé si un marqueur inconnu y figure.
    """
    app_logger.debug(f"resolve_connection_hook_command() called | template={template!r}")
    if not template or not template.strip():
        app_logger.debug("resolve_connection_hook_command() returning | None (modèle vide)")
        return None
    values = {
        "name": host_name or "",
        "address": host_address or "",
        "group": host_group or "",
        "protocol": protocol or "",
    }
    try:
        result = template.format(**values)
    except (KeyError, IndexError) as exc:
        app_logger.debug(
            f"resolve_connection_hook_command() returning | modèle inchangé, "
            f"marqueur inconnu ignoré : {exc}"
        )
        return template
    app_logger.debug(f"resolve_connection_hook_command() returning | result={result!r}")
    return result


def resolve_theme_mode(raw_mode: str) -> str:
    """Valide/normalise le mode de thème GTK persisté (``conf.THEME_MODE``).

    Backlog features.md §4.2 #113 ("Mode sombre dédié") : la fonctionnalité
    elle-même (détection automatique du thème du bureau, préférence
    Système/Clair/Sombre persistée, application au démarrage) existait déjà
    avant cette session — voir features.md §4.1 pour l'audit complet. Cette
    fonction centralise la seule partie qui manquait : une validation
    unique de la valeur persistée, jusqu'ici dupliquée avec deux replis
    différents à deux endroits (chargement de ``gcm.ini`` sans validation
    du tout côté ``loadConfig()`` ; repli déduit de ``conf.DARK_MODE`` côté
    présélection du combo Système/Clair/Sombre de ``Wconfig``).

    Fonction pure (aucun accès GTK), extraite pour rester testable sans
    stub — même principe que ``resolve_auto_close_tab()``/
    ``resolve_group_color()`` ci-dessus.

    Args:
        raw_mode (str): Valeur à valider — typiquement lue depuis
            ``gcm.ini`` (clé ``theme-mode``, fichier modifiable à la main)
            ou l'attribut ``conf.THEME_MODE`` courant. Toute valeur hors de
            ``{"system", "light", "dark"}`` (fichier corrompu, ancienne/
            future version, ``None``, casse différente...) est considérée
            invalide.

    Returns:
        str: ``raw_mode`` inchangé s'il vaut "system", "light" ou "dark" ;
        sinon "system" (repli silencieux, cohérent avec la valeur par
        défaut du module ``utils.py``).
    """
    app_logger.debug(f"resolve_theme_mode() called | raw_mode={raw_mode!r}")
    if raw_mode in ("system", "light", "dark"):
        app_logger.debug(f"resolve_theme_mode() returning | mode valide: {raw_mode!r}")
        return raw_mode
    app_logger.debug('resolve_theme_mode() returning | repli sur "system" (valeur invalide)')
    return "system"
