#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""Modèles de données pour les hôtes et utilitaires de configuration."""

import getpass

from gi.repository import Vte
from loguru import logger


def _protocol_field_defaults():
    """Retourne les champs ``Host`` spécifiques à un protocole, appris des plugins.

    Chaque ``(nom_attribut, valeur_par_defaut)`` provient de
    ``ConnectionPlugin.host_fields()`` (cf. plugin_base.py) via le registre
    global de l'application — aucun nom de protocole ni de champ n'est
    connu de ce module. Import différé (lazy) pour éviter tout import
    circulaire avec ``gnome_connection_manager`` (qui importe ``models``) ;
    en pratique toujours résolu, ``Host`` n'étant jamais instancié avant la
    fin du chargement des plugins par ``Wmain``.

    Returns:
        list[tuple[str, object]]: Liste vide si l'application n'est pas
        encore initialisée (garde-fou théorique, cf. plugin_base.py).
    """
    logger.debug("_protocol_field_defaults: entrée")
    try:
        from gnome_connection_manager import wMain
    except Exception as exc:
        logger.debug("_protocol_field_defaults: {} : {}", type(exc).__name__, exc)
        logger.debug("_protocol_field_defaults: sortie")
        return []
    if wMain is None:
        logger.debug("_protocol_field_defaults: sortie")
        return []
    logger.debug("_protocol_field_defaults: sortie")
    return wMain.plugin_registry.all_host_fields()


class Host:
    """Classe représentant une connexion d'hôte avec tous ses paramètres.

    Les champs "core" (communs à tous les protocoles : nom, connexion,
    apparence...) sont déclarés ci-dessous. Les champs spécifiques à un
    protocole (ex. ``rdp_domain``, ``vnc_viewer``...) ne sont PAS connus de
    cette classe : ils sont appris dynamiquement à la construction depuis
    les plugins enregistrés (cf. :func:`_protocol_field_defaults`) — ajouter
    un nouveau protocole avec ses propres champs ne nécessite aucune
    modification ici.
    """

    #: (nom_attribut, valeur_par_defaut) communs à tous les protocoles.
    _CORE_FIELDS = [
        ("group", None),
        ("name", None),
        ("description", None),
        ("host", None),
        ("user", None),
        ("password", None),
        ("private_key", None),
        ("port", 22),
        ("tunnel", ""),
        ("type", "ssh"),
        ("commands", None),
        ("keep_alive", 0),
        ("font_color", ""),
        ("back_color", ""),
        ("x11", False),
        ("agent", False),
        ("compression", False),
        ("compressionLevel", ""),
        ("extra_params", ""),
        ("log", False),
        ("backspace_key", int(Vte.EraseBinding.AUTO)),
        ("delete_key", int(Vte.EraseBinding.AUTO)),
        ("term", ""),
        ("protocol", "ssh"),
        # Surcharge par hôte de conf.AUTO_CLOSE_TAB (préférence globale, voir
        # widgets.py::NotebookTabLabel.mark_tab_as_closed()) : "" = hérite du
        # réglage global, "0"/"1"/"2" = Jamais/Toujours/Seulement en sortie
        # propre, mêmes valeurs que la préférence globale (features.md §4.2,
        # backlog #77 "volet restant : option par host").
        ("auto_close_tab", ""),
        # Appartenance a l'espace de travail (backlog features.md §4.2,
        # "Espace de travail (workspace)") : coche dans "Editer hote" ; le
        # bouton/action "open-workspace" (gnome_connection_manager.py,
        # gcm4_core.workspace_hosts()) ouvre en une fois tous les hotes ayant
        # ce champ a True, quel que soit leur groupe ou protocole.
        ("workspace", False),
        # Commande shell exécutée localement avant l'ouverture de la connexion
        # (backlog features.md §4.2, #116 "Scripting avant/après connexion",
        # volet "avant" fait le 2026-09-07 ; le volet "après" reste à faire,
        # voir features.md). Chaîne vide = aucun hook. Marqueurs disponibles
        # ({name}/{address}/{group}/{protocol}) résolus par
        # gcm4_core.resolve_connection_hook_command(), lancée en tâche de
        # fond par gnome_connection_manager.run_pre_connect_hook() —
        # applicable à tous les protocoles (câblée dans
        # Wmain._open_connection_tab(), point d'entrée générique unique),
        # pas seulement VTE.
        ("pre_connect_command", ""),
        # Commande shell exécutée localement quand la connexion se termine
        # (backlog features.md §4.2, #116 "Scripting avant/après connexion",
        # volet "après" — symétrique de pre_connect_command ci-dessus).
        # Chaîne vide = aucun hook. Résolution des marqueurs déléguée à la
        # même fonction pure gcm4_core.resolve_connection_hook_command().
        # Contrairement à pre_connect_command (câblé une seule fois, point
        # d'entrée générique unique), il n'existe aucun point d'accroche
        # commun à tous les protocoles pour "la connexion vient de se
        # terminer" : seul le signal VTE "child-exited" (protocoles SSH/
        # telnet/local/serial/ipmi, via
        # widgets.NotebookTabLabel.mark_tab_as_closed()) en tient lieu
        # aujourd'hui. Le champ est donc propagé aux 5 sites de création de
        # NotebookTabLabel dans gnome_connection_manager.py (même chantier de
        # propagation que auto_close_tab), mais n'est effectivement exécuté
        # que pour les onglets terminal VTE — limitation connue, voir
        # features.md §4.2.
        ("post_connect_command", ""),
    ]

    def __init__(self, **kwargs):
        """Initialise l'instance.

        Args:
            **kwargs: Valeur explicite pour n'importe quel champ (core ou
                spécifique à un protocole), par son nom d'attribut. Tout
                champ non fourni prend sa valeur par défaut (déclarée dans
                ``_CORE_FIELDS`` pour les champs core, ou par le plugin
                propriétaire pour les champs protocole-spécifiques).
        """
        logger.debug("Host.__init__: entrée")
        for field_name, default in self._CORE_FIELDS:
            setattr(self, field_name, kwargs.get(field_name, default))
        if isinstance(self.tunnel, str):
            self.tunnel = self.tunnel.split(",")
        for field_name, default in _protocol_field_defaults():
            setattr(self, field_name, kwargs.get(field_name, default))
        logger.debug("Host.__init__: fin")

    def __repr__(self):
        """Retourne une representation textuelle de l'hote.

        Returns:
            str: Representation de l'hote.
        """
        logger.debug("Host.__repr__: entrée")
        logger.debug("Host.__repr__: sortie")
        return "group=[%s],\t name=[%s],\t host=[%s],\t type=[%s]" % (
            self.group,
            self.name,
            self.host,
            self.type,
        )

    def tunnel_as_string(self):
        """Retourne la definition des tunnels SSH sous forme de chaine.

        Returns:
            str: Chaine de tunnels formatee.
        """
        logger.debug("Host.tunnel_as_string: entrée")
        logger.debug("Host.tunnel_as_string: sortie")
        return ",".join(self.tunnel)

    def clone(self):
        """Cree une copie profonde de l'hote.

        Returns:
            Host: Nouvel hote identique.
        """
        logger.debug("Host.clone: entrée")
        kwargs = {name: getattr(self, name, default) for name, default in self._CORE_FIELDS}
        kwargs["tunnel"] = self.tunnel_as_string()
        for name, default in _protocol_field_defaults():
            kwargs[name] = getattr(self, name, default)
        logger.debug("Host.clone: sortie")
        return Host(**kwargs)


class HostUtils:
    """Utilitaires pour charger/sauvegarder les paramètres d'hôtes depuis/vers INI."""

    @staticmethod
    def get_val(cp, section, name, default):
        """Retourne la valeur d'un champ de l'hote avec valeur par defaut.

        Args:
            cp (configparser.ConfigParser): Objet de configuration source.
            section (str): Nom de la section INI a lire.
            name (str): Nom du champ a lire dans la section.
            default (str | bool): Valeur par defaut si le champ est absent ;
                son type (``bool`` ou non) determine si la lecture passe par
                ``cp.getboolean()`` ou ``cp.get()``.

        Returns:
            str | bool: Valeur du champ, ou ``default`` si absent/illisible.
        """
        logger.debug("HostUtils.get_val: entrée")
        try:
            logger.debug("HostUtils.get_val: sortie")
            return cp.get(section, name) if type(default) != bool else cp.getboolean(section, name)
        except BaseException as exc:
            logger.debug("HostUtils.get_val: {} : {}", type(exc).__name__, exc)
            logger.debug("HostUtils.get_val: sortie")
            return default

    @staticmethod
    def load_host_from_ini(cp, section, pwd=""):
        """Charge les parametres d'un hote depuis une section INI.

        Args:
            cp (configparser.ConfigParser): Objet de configuration source.
            section (str): Nom de la section INI a charger.
            pwd (str, optional): Mot de passe maitre pour dechiffrer le
                champ ``pass``. Recupere via ``get_password()`` si vide
                (defaut).

        Returns:
            Host: Instance peuplee a partir de la section INI.
        """
        logger.debug("HostUtils.load_host_from_ini: entrée")
        from gcm4_core import decrypt, get_password
        from utils import conf

        if pwd == "":
            pwd = get_password()
        kwargs = {
            "group": HostUtils.get_val(cp, section, "group", ""),
            "name": HostUtils.get_val(cp, section, "name", ""),
            "host": HostUtils.get_val(cp, section, "host", ""),
            "user": HostUtils.get_val(cp, section, "user", getpass.getuser()),
            "password": decrypt(
                pwd, HostUtils.get_val(cp, section, "pass", ""), version=conf.VERSION
            ),
            "description": HostUtils.get_val(cp, section, "description", ""),
            "private_key": HostUtils.get_val(cp, section, "private_key", ""),
            "port": HostUtils.get_val(cp, section, "port", "22"),
            "tunnel": HostUtils.get_val(cp, section, "tunnel", ""),
            "type": HostUtils.get_val(cp, section, "type", "ssh"),
            "commands": HostUtils.get_val(cp, section, "commands", "")
            .replace("\x00", "\n")
            .replace("\\n", "\n"),
            "keep_alive": HostUtils.get_val(cp, section, "keepalive", ""),
            "font_color": HostUtils.get_val(cp, section, "font-color", ""),
            "back_color": HostUtils.get_val(cp, section, "back-color", ""),
            "x11": HostUtils.get_val(cp, section, "x11", False),
            "agent": HostUtils.get_val(cp, section, "agent", False),
            "compression": HostUtils.get_val(cp, section, "compression", False),
            "compressionLevel": HostUtils.get_val(cp, section, "compression-level", ""),
            "extra_params": HostUtils.get_val(cp, section, "extra_params", ""),
            "log": HostUtils.get_val(cp, section, "log", False),
            "backspace_key": int(
                HostUtils.get_val(cp, section, "backspace-key", int(Vte.EraseBinding.AUTO))
            ),
            "delete_key": int(
                HostUtils.get_val(cp, section, "delete-key", int(Vte.EraseBinding.AUTO))
            ),
            "term": HostUtils.get_val(cp, section, "term", ""),
            "protocol": HostUtils.get_val(cp, section, "protocol", "ssh"),
            "auto_close_tab": HostUtils.get_val(cp, section, "auto-close-tab", ""),
            "workspace": HostUtils.get_val(cp, section, "workspace", False),
            "pre_connect_command": HostUtils.get_val(cp, section, "pre-connect-command", ""),
            "post_connect_command": HostUtils.get_val(cp, section, "post-connect-command", ""),
        }
        for field_name, default in _protocol_field_defaults():
            kwargs[field_name] = HostUtils.get_val(cp, section, field_name, default)
        logger.debug("HostUtils.load_host_from_ini: sortie")
        return Host(**kwargs)

    @staticmethod
    def save_host_to_ini(cp, section, host, pwd=""):
        """Sauvegarde les parametres d'un hote dans une section INI.

        Args:
            cp (configparser.ConfigParser): Objet de configuration
                destination.
            section (str): Nom de la section INI a ecrire.
            host (Host): Hote dont les champs sont serialises.
            pwd (str, optional): Mot de passe maitre pour chiffrer le
                champ ``pass``. Recupere via ``get_password()`` si vide
                (defaut).
        """
        logger.debug("HostUtils.save_host_to_ini: entrée")
        from gcm4_core import encrypt, get_password

        def _s(v):
            """Coerce any value to str safely (None → '')."""
            logger.debug("HostUtils.save_host_to_ini._s: entrée")
            logger.debug("HostUtils.save_host_to_ini._s: sortie")
            return "" if v is None else str(v)

        if pwd == "":
            pwd = get_password()
        cp.set(section, "group", _s(host.group))
        cp.set(section, "name", _s(host.name))
        cp.set(section, "description", _s(host.description))
        cp.set(section, "host", _s(host.host))
        cp.set(section, "user", _s(host.user))
        cp.set(section, "pass", encrypt(pwd, host.password))
        cp.set(section, "private_key", _s(host.private_key))
        cp.set(section, "port", _s(host.port))
        cp.set(section, "tunnel", host.tunnel_as_string())
        cp.set(section, "type", _s(host.type))
        commands = host.commands or ""
        cp.set(section, "commands", commands.replace("\n", "\\n"))
        cp.set(section, "keepalive", str(host.keep_alive))
        cp.set(section, "font-color", str(host.font_color))
        cp.set(section, "back-color", str(host.back_color))
        cp.set(section, "x11", str(host.x11))
        cp.set(section, "agent", str(host.agent))
        cp.set(section, "compression", str(host.compression))
        cp.set(section, "compression-level", str(host.compressionLevel))
        cp.set(section, "extra_params", str(host.extra_params))
        cp.set(section, "log", str(host.log))
        cp.set(section, "backspace-key", str(host.backspace_key))
        cp.set(section, "delete-key", str(host.delete_key))
        cp.set(section, "term", str(host.term))
        cp.set(section, "protocol", str(getattr(host, "protocol", "ssh")))
        cp.set(section, "auto-close-tab", _s(getattr(host, "auto_close_tab", "")))
        cp.set(section, "workspace", str(getattr(host, "workspace", False)))
        cp.set(section, "pre-connect-command", _s(getattr(host, "pre_connect_command", "")))
        cp.set(section, "post-connect-command", _s(getattr(host, "post_connect_command", "")))
        for field_name, default in _protocol_field_defaults():
            cp.set(section, field_name, str(getattr(host, field_name, default)))
        logger.debug("HostUtils.save_host_to_ini: fin")
