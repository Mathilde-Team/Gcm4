# SPDX-License-Identifier: GPL-3.0-or-later
"""Contrats plugin pour GCM.

Ce module définit DEUX familles de plugins, chacune avec son propre contrat
et son propre registre à autoload, toutes deux découvertes dans les mêmes
fichiers ``plugin_*.py`` :

- ``ConnectionPlugin`` / ``PluginRegistry`` : une session interactive *par
  hôte* (un onglet = une connexion) — SSH, VNC, SPICE, RDP, Serial, IPMI
  SOL, Web... Découverte via la fonction de niveau module ``get_plugin()``.
- ``BatchPlugin`` / ``BatchPluginRegistry`` : un outil *hors session*, sans
  notion d'hôte unique (ex. déploiement de configuration en masse via
  Netmiko) — typiquement une seule entrée de menu « Outils » qui ouvre son
  propre onglet/dialogue singleton. Découverte via la fonction de niveau
  module ``get_batch_plugin()``.

Un même fichier ``plugin_*.py`` ne définit en pratique que l'une des deux
fonctions d'entrée (jamais les deux), mais rien ne l'interdit. Les deux
registres ignorent silencieusement un module qui n'expose pas *leur* point
d'entrée — ce n'est pas une erreur, c'est juste un plugin de l'autre
famille. Seuls un échec d'import du module ou un échec d'appel du point
d'entrée *attendu* sont journalisés en warning avec la cause, pour ne
jamais faire échouer le démarrage de l'application à cause d'un plugin
cassé.

Voir la discussion d'architecture et le diagramme de classes associé pour le
contexte complet (périmètre cœur vs plugin).
"""

from __future__ import annotations

import importlib
from abc import ABC, abstractmethod
from collections.abc import Callable
from functools import wraps
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    # Uniquement pour le type-checker : `from __future__ import annotations`
    # rend déjà toutes les annotations de ce module paresseuses (chaînes non
    # évaluées à l'exécution), donc aucun `gi.require_version()`/import Gtk
    # réel n'est nécessaire ici. Ce verrou de version GTK3, hérité, empêchait
    # tout plugin GTK4 de se charger dans le même process — voir
    # `proposition-architecture-plugins-gtk4.md` §4 et
    # `docs/gtk4-migration.md`. Chaque plugin (futur `__init__.py` par
    # dossier) reste seul responsable de choisir sa propre version GTK.
    from gi.repository import Gtk

__all__ = ["ConnectionPlugin", "PluginRegistry", "BatchPlugin", "BatchPluginRegistry"]

#: Nom de la fonction de niveau module que chaque ``plugin_*.py`` doit
#: exposer pour etre decouvert automatiquement par ``PluginRegistry.autoload``
#: (protocoles de connexion, un onglet = une session par hôte).
PLUGIN_ENTRY_POINT = "get_plugin"

#: Nom de la fonction de niveau module que chaque ``plugin_*.py`` doit
#: exposer pour etre decouvert automatiquement par
#: ``BatchPluginRegistry.autoload`` (outils hors session, ex. déploiement en
#: masse). Distinct de ``PLUGIN_ENTRY_POINT`` afin que les deux familles de
#: plugins cohabitent dans les mêmes fichiers ``plugin_*.py`` sans ambiguïté.
BATCH_PLUGIN_ENTRY_POINT = "get_batch_plugin"

#: Prefixe de nom de fichier identifiant un module plugin candidat.
PLUGIN_MODULE_PREFIX = "plugin_"


def _attach_debug_tracing(plugin_obj: object, scope: str) -> None:
    """Ajoute un logger.debug d'entree sur toutes les methodes d'un plugin.

    Instrumentation centralisee pour garantir une trace minimale homogène
    sans devoir dupliquer ``logger.debug(...)`` manuellement dans chaque
    methode de chaque plugin.

    Args:
        plugin_obj: Instance de plugin a instrumenter.
        scope: Prefixe de trace (``ConnectionPlugin`` ou ``BatchPlugin``).
    """
    logger.debug("_attach_debug_tracing: entrée")
    if getattr(plugin_obj, "_gcm_debug_tracing_attached", False):
        logger.debug("_attach_debug_tracing: sortie")
        return

    cls_name = type(plugin_obj).__name__
    for attr_name in dir(plugin_obj):
        if attr_name.startswith("__"):
            continue
        try:
            bound = getattr(plugin_obj, attr_name)
        except Exception as exc:
            logger.debug("trace plugin | attribut {} illisible : {}", attr_name, exc)
            continue
        if not callable(bound):
            continue
        if attr_name in {"app"}:
            continue

        @wraps(bound)
        def _wrapped(*args, __bound=bound, __name=attr_name, **kwargs):
            logger.debug(
                f"{scope}.{cls_name}.{__name} | args={len(args)} kwargs={list(kwargs.keys())}"
            )
            return __bound(*args, **kwargs)

        try:
            setattr(plugin_obj, attr_name, _wrapped)
        except Exception as exc:
            logger.debug("trace plugin | attribut {} non remplaçable : {}", attr_name, exc)
            continue

    setattr(plugin_obj, "_gcm_debug_tracing_attached", True)  # noqa: B010 (attribut dynamique)
    logger.debug("_attach_debug_tracing: fin")


def _discover_plugin_modules(directory: Path, package: str | None, self_module_name: str):
    """Générateur interne partagé par les deux registres.

    Parcourt ``directory`` à la recherche de fichiers ``plugin_*.py``
    (``plugin_base.py`` lui-même exclu) et importe chacun, dans l'ordre
    alphabétique. Un échec d'import est journalisé avec sa cause précise et
    le module concerné est simplement sauté — un plugin cassé (les deux
    familles confondues) ne doit jamais empêcher les autres, ni le
    démarrage de l'application.

    Args:
        directory: Dossier a scanner pour les fichiers ``plugin_*.py``.
        package: Prefixe de paquet Python optionnel pour l'import.
        self_module_name: Nom de module a exclure du scan (l'appelant).

    Yields:
        tuple[str, str, module | None, str | None]: ``(nom_court,
        nom_complet, module_importe_ou_None, cause_si_echec_import)``. Sur
        echec d'import, ``module`` vaut ``None`` et ``cause`` contient le
        message d'erreur — a l'appelant de decider s'il doit l'enregistrer
        comme echec (les deux registres le font, l'import etant ambigu
        quant a la famille de plugin visee).
    """
    logger.debug("_discover_plugin_modules: entrée")
    for module_path in sorted(directory.glob(f"{PLUGIN_MODULE_PREFIX}*.py")):
        module_name = module_path.stem
        if module_name == self_module_name:
            continue  # ne jamais se re-scanner soi-meme (plugin_base.py)

        full_name = f"{package}.{module_name}" if package else module_name
        try:
            module = importlib.import_module(full_name)
        except Exception as exc:
            logger.exception(f"autoload plugin | echec import {full_name}")
            yield module_name, full_name, None, str(exc)
            continue

        yield module_name, full_name, module, None
    logger.debug("_discover_plugin_modules: fin")


class ConnectionPlugin(ABC):
    """Contrat qu'un plugin de protocole doit implémenter.

    Un plugin encapsule TOUT ce qui est spécifique à un protocole de
    connexion : le widget d'onglet actif (la session elle-même), la page de
    champs du dialogue Edit Host, et la logique de lecture/écriture de ces
    champs vers/depuis un objet ``Host``. Le cœur de l'application
    (``Wmain``, ``Whost``) ne connaît que cette interface, jamais les
    attributs spécifiques d'un protocole donné (ex. ``rdp_domain``,
    ``serial_baud``...).

    Attributes:
        protocol_id: Identifiant court et stable du protocole (ex. ``"web"``),
            doit correspondre à ``Host.protocol`` et à l'``id`` utilisé dans
            le combo de sélection de type de connexion.
        display_name: Nom affiché à l'utilisateur (ex. ``"Web"``).
        default_port: Port par défaut suggéré pour ce protocole (``None`` si
            non pertinent, ex. Serial).
        icon_name: Nom d'icône symbolique GTK (ou chaîne vide).
        ui_order: Rang d'affichage dans les listes/menus énumérant tous les
            protocoles (ex. combo de type de connexion) — permet au cœur de
            l'application (``Wmain``/``Whost``) de construire ces listes en
            itérant simplement ``PluginRegistry.all()`` sans jamais coder en
            dur un ordre ni un identifiant de protocole. À valeur égale,
            l'ordre alphabétique de ``protocol_id`` départage.
        is_terminal: ``True`` si l'onglet de connexion de ce plugin est un
            terminal VTE (expose un attribut ``.terminal``) nécessitant le
            câblage générique des signaux terminal (focus, sélection, clic,
            sortie du process...). ``False`` pour les protocoles à widget
            graphique autonome (RDP/VNC/SPICE/Web...).
        self_scrolling: ``True`` si le widget retourné par ``build_tab``
            gère déjà son propre défilement/dimensionnement et ne doit donc
            pas être enveloppé dans un ``Gtk.ScrolledWindow`` par le cœur de
            l'application avant insertion dans le notebook de sessions.
    """

    protocol_id: str = ""
    display_name: str = ""
    default_port: int | None = None
    icon_name: str = ""
    ui_order: int = 100
    is_terminal: bool = False
    self_scrolling: bool = False

    #: Référence vers l'instance ``Wmain`` en cours d'exécution, injectée par
    #: ``PluginRegistry.bind_app`` juste après l'``autoload``. Un plugin ne
    #: doit JAMAIS faire ``from gnome_connection_manager import wMain`` lui-
    #: même : ce module est parfois chargé une seconde fois sous un nom de
    #: module distinct (cas où ``gnome_connection_manager.py`` est lancé
    #: directement comme ``__main__``), ce qui casse l'accès au singleton.
    #: Utiliser ``self.app`` à la place est toujours sûr.
    app: object | None = None

    @abstractmethod
    def build_tab(self, host, get_password: Callable[[], str] | None = None) -> Gtk.Widget:
        """Construit le widget d'onglet vivant (la session active) pour *host*.

        Args:
            host: Instance ``Host`` à connecter.
            get_password: Callable retournant le mot de passe déchiffré à la
                demande (protocoles qui en ont besoin — VNC/SPICE/RDP). Peut
                être ``None`` pour les protocoles qui n'en ont pas besoin.

        Returns:
            Un ``Gtk.Widget`` prêt à être ajouté au notebook de sessions.
        """
        raise NotImplementedError

    @abstractmethod
    def build_edit_page(self) -> Gtk.Widget:
        """Construit la page de champs (Edit Host) spécifique à ce protocole.

        Appelée une seule fois par instance de plugin ; la même page est
        réutilisée (rechargée/sauvegardée) à chaque ouverture du dialogue
        Edit Host pour ce protocole.

        Returns:
            Un ``Gtk.Widget`` autonome (typiquement un ``Gtk.Grid``) à
            insérer dans le notebook du dialogue Edit Host.
        """
        raise NotImplementedError

    @abstractmethod
    def load_host_fields(self, host) -> None:
        """Remplit la page d'édition (retournée par :meth:`build_edit_page`)
        avec les valeurs actuelles de *host*.

        Args:
            host: Instance ``Host`` dont les champs spécifiques au protocole
                doivent être affichés.
        """
        raise NotImplementedError

    @abstractmethod
    def save_host_fields(self, host) -> None:
        """Reporte les valeurs de la page d'édition dans *host*.

        Args:
            host: Instance ``Host`` à mettre à jour depuis le formulaire.
        """
        raise NotImplementedError

    def host_fields(self) -> list[tuple[str, object]]:
        """Déclare les attributs ``Host`` spécifiques à ce protocole.

        Chaque plugin qui a besoin de stocker des paramètres propres sur
        ``Host`` (ex. ``rdp_domain``, ``vnc_viewer``...) les déclare ici,
        sous forme de ``(nom_attribut, valeur_par_defaut)``. C'est le SEUL
        endroit où ces noms de champs doivent exister : ``models.Host``
        (champs core mis à part) et le cœur de l'application les découvrent
        ensuite dynamiquement via ``PluginRegistry.all_host_fields()``,
        sans jamais coder en dur un nom de protocole ou de champ.

        Returns:
            Liste de tuples ``(nom_attribut, valeur_par_defaut)``. Vide par
            défaut — un protocole sans paramètre propre (telnet, ipmi,
            local...) n'a rien à déclarer.
        """
        return []

    def build_folder_context_menu_items(self) -> list[tuple[str, Callable[[], None]]]:
        """Déclare des items supplémentaires pour le menu contextuel du
        panneau de serveurs (clic droit sur un hôte de l'arbre).

        Chaque item est un tuple ``(libellé déjà traduit, callback)``, le
        callback étant appelé sans argument (même convention que
        ``menu_actions()``). C'est le SEUL endroit où un protocole peut
        ajouter une action à ce menu — le cœur de l'application
        (``Wmain.createMenu``) ne nomme plus aucun protocole, il se
        contente d'agréger ce que chaque plugin déclare ici.

        Returns:
            Liste de tuples ``(libellé, callback)``. Vide par défaut — un
            protocole sans action propre à ce menu n'a rien à déclarer.
        """
        return []

    def validate(self) -> list[str]:
        """Valide les champs actuellement saisis dans la page d'édition.

        Returns:
            Liste de messages d'erreur (vide si tout est valide). Le
            comportement par défaut ne valide rien — un plugin peut
            surcharger cette méthode s'il a des contraintes propres.
        """
        return []

    def menu_actions(self) -> list:
        """Actions de menu additionnelles spécifiques à ce protocole.

        Returns:
            Liste de tuples ``(action_name, label, callback)`` à exposer,
            par exemple, dans le menu contextuel de l'onglet. Vide par
            défaut.
        """
        return []

    def patch_edit_host_dialog(self, builder: object, host_section: str, cp: object) -> None:
        """Adapte le dialogue générique d'édition d'hôte (``wHost``) pour ce protocole.

        Point d'extension appelé pour CHAQUE plugin chargé, à chaque
        ouverture du dialogue d'édition d'un hôte (cf. ``Whost.init`` dans
        ``gnome_connection_manager.py``) — c'est le SEUL endroit où un
        protocole peut modifier l'apparence/sensibilité de widgets du
        dialogue partagé. Le cœur ne nomme plus aucun protocole ici, il se
        contente d'itérer ``plugin_registry.all()``.

        Comportement par défaut : no-op — un protocole sans besoin
        particulier (VNC, RDP, telnet...) n'a rien à faire. Seul SSH
        surcharge cette méthode aujourd'hui (grisage des champs + notice
        rouge quand l'hôte est géré par un fichier ``ssh_config`` externe,
        cf. ``SshPlugin.patch_edit_host_dialog``).

        Args:
            builder: Objet exposant ``get_object(widget_id)`` (adaptateur
                vers ``Whost.get_widget`` côté cœur — ``Whost`` n'utilise
                plus de ``Gtk.Builder`` réel).
            host_section: Nom de la section ``configparser`` de l'hôte
                actuellement édité (ex. ``"host 3"``).
            cp: ``gcm.conf`` déjà chargé (``configparser.RawConfigParser``).
        """
        return


class PluginRegistry:
    """Registre central des ``ConnectionPlugin`` disponibles.

    Example:
        registry = PluginRegistry()
        registry.register(WebPlugin())
        plugin = registry.get("web")
        tab = plugin.build_tab(host)
    """

    def __init__(self) -> None:
        """Initialise un registre vide, sans plugin enregistré."""
        logger.debug("PluginRegistry.__init__: entrée")
        self._plugins: dict[str, ConnectionPlugin] = {}
        logger.debug("PluginRegistry.__init__: fin")

    def register(self, plugin: ConnectionPlugin) -> None:
        """Enregistre un plugin sous son ``protocol_id``.

        Args:
            plugin: Instance de plugin à enregistrer.

        Raises:
            ValueError: Si ``protocol_id`` est vide ou déjà enregistré.
        """
        logger.debug("PluginRegistry.register: entrée")
        if not plugin.protocol_id:
            raise ValueError(f"protocol_id manquant sur {type(plugin).__name__}")
        if plugin.protocol_id in self._plugins:
            raise ValueError(f"protocol_id déjà enregistré : {plugin.protocol_id!r}")
        _attach_debug_tracing(plugin, "ConnectionPlugin")
        self._plugins[plugin.protocol_id] = plugin
        logger.debug(
            f"PluginRegistry.register | protocol_id={plugin.protocol_id} class={type(plugin).__name__}"
        )
        logger.debug("PluginRegistry.register: fin")

    def get(self, protocol_id: str) -> ConnectionPlugin | None:
        """Retourne le plugin enregistré pour *protocol_id*, ou ``None``.

        Args:
            protocol_id: Identifiant de protocole (ex. ``"web"``).
        """
        return self._plugins.get(protocol_id)

    def all(self) -> list[ConnectionPlugin]:
        """Retourne tous les plugins enregistrés."""
        return list(self._plugins.values())

    def all_sorted(self) -> list[ConnectionPlugin]:
        """Retourne tous les plugins enregistrés, triés pour affichage.

        Ordre de tri : ``ui_order`` croissant, puis ``protocol_id``
        alphabétique à égalité. Permet au cœur de l'application de
        construire des listes/menus énumérant tous les protocoles (ex.
        combo de type de connexion dans le dialogue Edit Host) sans jamais
        coder en dur un identifiant ou un ordre de protocole — chaque
        plugin déclare lui-même sa place via ``ui_order``.
        """
        return sorted(self._plugins.values(), key=lambda p: (p.ui_order, p.protocol_id))

    def bind_app(self, app: object) -> None:
        """Injecte la référence de l'application dans chaque plugin enregistré.

        À appeler une fois, juste après ``autoload()``, avec l'instance
        ``Wmain`` en cours d'exécution. Remplace le besoin, pour un plugin,
        de faire ``from gnome_connection_manager import wMain`` — un import
        fragile qui peut échouer si le module principal a été chargé une
        seconde fois sous un nom de module distinct (cas où
        ``gnome_connection_manager.py`` tourne comme ``__main__``).

        Args:
            app: Instance ``Wmain`` (ou tout objet jouant ce rôle, utile pour
                les tests) à exposer aux plugins via ``self.app``.
        """
        logger.debug("PluginRegistry.bind_app: entrée")
        for plugin in self._plugins.values():
            plugin.app = app
        logger.debug("PluginRegistry.bind_app: fin")

    def protocol_ids(self) -> list[str]:
        """Retourne la liste des ``protocol_id`` enregistrés."""
        return list(self._plugins.keys())

    def all_host_fields(self) -> list[tuple[str, object]]:
        """Agrège les champs ``Host`` déclarés par tous les plugins enregistrés.

        Parcourt ``all_sorted()`` (ordre stable, déterministe) et concatène
        le résultat de ``ConnectionPlugin.host_fields()`` de chacun. Permet à
        ``models.Host`` d'apprendre l'intégralité des attributs
        spécifiques-protocole (noms + valeurs par défaut) sans qu'aucun nom
        de champ ou de protocole ne soit jamais codé en dur ailleurs que
        dans le plugin qui le possède.

        Returns:
            Liste de tuples ``(nom_attribut, valeur_par_defaut)``, tous
            plugins confondus.
        """
        logger.debug("PluginRegistry.all_host_fields: entrée")
        fields: list[tuple[str, object]] = []
        for plugin in self.all_sorted():
            fields.extend(plugin.host_fields())
        logger.debug("PluginRegistry.all_host_fields: sortie")
        return fields

    def __contains__(self, protocol_id: str) -> bool:
        """Teste si un plugin est enregistré pour ``protocol_id`` (opérateur ``in``).

        Args:
            protocol_id (str): Identifiant de protocole à tester.

        Returns:
            bool: ``True`` si un plugin est enregistré sous cet identifiant.
        """
        return protocol_id in self._plugins

    def __len__(self) -> int:
        """Retourne le nombre de plugins enregistrés (opérateur ``len()``).

        Returns:
            int: Nombre de plugins actuellement enregistrés.
        """
        return len(self._plugins)

    def autoload(
        self, directory: str | Path | None = None, package: str | None = None
    ) -> list[str]:
        """Decouvre et enregistre automatiquement tous les plugins ``plugin_*.py``.

        Chaque fichier ``plugin_*.py`` trouve dans *directory* doit exposer
        une fonction de niveau module nommee ``get_plugin`` (sans argument)
        qui retourne soit une instance de ``ConnectionPlugin``, soit un
        iterable de plusieurs instances (utile a un module qui fournirait
        plusieurs protocoles). C'est le SEUL contrat requis : plus aucun
        import ni enregistrement manuel n'est necessaire dans le fichier
        principal pour ajouter un nouveau protocole — il suffit de deposer
        un fichier ``plugin_xxx.py`` a cote des autres.

        Un module sans ``get_plugin`` (ou dont l'import/l'appel echoue) est
        ignore avec un avertissement journalise plutot que de faire echouer
        le demarrage de l'application : un plugin cassé ne doit jamais
        empecher les autres de se charger.

        Args:
            directory: Dossier a scanner pour les fichiers ``plugin_*.py``.
                Par defaut, le dossier contenant ce fichier (``plugin_base.py``),
                ce qui correspond a l'usage actuel de GCM (tous les modules
                plugin_*.py vivent a plat a cote du fichier principal).
            package: Nom de paquet Python a prefixer lors de l'import (ex.
                ``"gcm.plugins"``) si les plugins sont un jour deplaces dans
                un sous-paquet. ``None`` (par defaut) importe chaque module
                comme module top-level, ce qui correspond a la disposition
                actuelle du projet (tout sur un ``sys.path`` plat).

        Returns:
            La liste des ``protocol_id`` nouvellement enregistres, dans
            l'ordre alphabetique des fichiers decouverts.
        """
        logger.debug("PluginRegistry.autoload: entrée")
        if directory is None:
            directory = Path(__file__).resolve().parent
        else:
            directory = Path(directory)

        self_module_name = Path(__file__).stem
        loaded: list[str] = []

        for _module_name, full_name, module, _import_error in _discover_plugin_modules(
            directory, package, self_module_name
        ):
            if module is None:
                continue  # echec d'import deja journalise par le generateur

            entry_point = getattr(module, PLUGIN_ENTRY_POINT, None)
            if not callable(entry_point):
                if callable(getattr(module, BATCH_PLUGIN_ENTRY_POINT, None)):
                    # Pas un plugin de connexion : c'est un BatchPlugin, une
                    # autre famille de plugin geree par BatchPluginRegistry.
                    # Ce n'est pas une erreur, on ne journalise donc rien ici.
                    continue
                logger.warning(
                    f"PluginRegistry.autoload | {full_name} ignore : pas de fonction "
                    f"'{PLUGIN_ENTRY_POINT}()' de niveau module"
                )
                continue

            try:
                result = entry_point()
            except Exception:
                logger.exception(
                    f"PluginRegistry.autoload | echec {full_name}.{PLUGIN_ENTRY_POINT}()"
                )
                continue

            candidates = result if isinstance(result, (list, tuple)) else [result]
            for plugin in candidates:
                if plugin is None:
                    continue
                try:
                    self.register(plugin)
                except ValueError:
                    logger.exception(
                        f"PluginRegistry.autoload | enregistrement refuse pour {full_name}"
                    )
                    continue
                loaded.append(plugin.protocol_id)

        logger.debug("PluginRegistry.autoload: sortie")
        return loaded


class BatchPlugin(ABC):
    """Contrat qu'un plugin « outil hors session » doit implémenter.

    Contrairement a ``ConnectionPlugin``, un ``BatchPlugin`` n'est pas
    rattache a un ``Host`` unique : c'est un outil declenche depuis le menu
    (typiquement « Outils »), qui ouvre son propre onglet ou dialogue et
    gere lui-meme son cycle de vie (ex. deploiement de configuration en
    masse via Netmiko sur plusieurs hotes a la fois).

    Attributes:
        tool_id: Identifiant court et stable de l'outil (ex.
            ``"netmiko-push"``), utilise comme nom d'action GTK
            (``win.<tool_id>``) et comme cle de registre.
        display_name: Libelle affiche dans le menu (ex.
            ``"Déploiement Netmiko…"``).
        icon_name: Nom d'icone symbolique GTK (ou chaine vide).
    """

    tool_id: str = ""
    display_name: str = ""
    icon_name: str = ""

    #: Section de menu ou placer l'entree generee pour ce plugin. ``"tools"``
    #: (par defaut) va dans la section « Outils ». ``"import"`` va dans le
    #: sous-menu « Imports » du menu Fichier, a cote de « Import servers ».
    #: Toute autre valeur reconnue par l'appelant (``Wmain._build_primary_menu``)
    #: peut etre ajoutee sans toucher a ce contrat.
    menu_section: str = "tools"

    #: Reference vers l'instance ``Wmain`` en cours d'execution, injectee
    #: par ``BatchPluginRegistry.bind_app`` juste apres l'``autoload``. Meme
    #: regle que pour ``ConnectionPlugin.app`` : ne jamais importer ``wMain``
    #: soi-meme depuis le module principal.
    app: object | None = None

    @abstractmethod
    def activate(self) -> None:
        """Declenche l'outil (appele quand l'utilisateur choisit l'entree de
        menu correspondante). A l'implementation de gerer l'ouverture de son
        propre onglet/dialogue et de lever une exception explicite en cas de
        probleme — ``BatchPluginRegistry``/l'appelant se chargent de
        journaliser la cause et d'avertir l'utilisateur.
        """
        raise NotImplementedError


class BatchPluginRegistry:
    """Registre central des ``BatchPlugin`` disponibles.

    Miroir de ``PluginRegistry`` pour la seconde famille de plugins (outils
    hors session). Voir le docstring de module pour la distinction entre les
    deux registres.

    Example:
        registry = BatchPluginRegistry()
        registry.autoload()
        registry.bind_app(wmain_instance)
        plugin = registry.get("netmiko-push")
        if plugin is not None:
            plugin.activate()
    """

    def __init__(self) -> None:
        """Initialise un registre de ``BatchPlugin`` vide, sans erreur de chargement."""
        logger.debug("BatchPluginRegistry.__init__: entrée")
        self._plugins: dict[str, BatchPlugin] = {}
        #: ``{nom_de_module: cause}`` rempli par ``autoload()`` à chaque
        #: échec réel (import, appel du point d'entrée, enregistrement en
        #: double) — PAS pour un module qui appartient simplement à l'autre
        #: famille de plugin. Permet à l'appelant (``Wmain``) d'avertir
        #: visiblement l'utilisateur avec la cause précise, en plus du
        #: warning déjà journalisé par ``autoload()`` lui-même.
        self.load_errors: dict[str, str] = {}
        logger.debug("BatchPluginRegistry.__init__: fin")

    def register(self, plugin: BatchPlugin) -> None:
        """Enregistre un plugin sous son ``tool_id``.

        Args:
            plugin: Instance de plugin a enregistrer.

        Raises:
            ValueError: Si ``tool_id`` est vide ou deja enregistre.
        """
        logger.debug("BatchPluginRegistry.register: entrée")
        if not plugin.tool_id:
            raise ValueError(f"tool_id manquant sur {type(plugin).__name__}")
        if plugin.tool_id in self._plugins:
            raise ValueError(f"tool_id déjà enregistré : {plugin.tool_id!r}")
        _attach_debug_tracing(plugin, "BatchPlugin")
        self._plugins[plugin.tool_id] = plugin
        logger.debug(
            f"BatchPluginRegistry.register | tool_id={plugin.tool_id} class={type(plugin).__name__}"
        )
        logger.debug("BatchPluginRegistry.register: fin")

    def get(self, tool_id: str) -> BatchPlugin | None:
        """Retourne le plugin enregistre pour *tool_id*, ou ``None``."""
        return self._plugins.get(tool_id)

    def all(self) -> list[BatchPlugin]:
        """Retourne tous les plugins enregistres, dans l'ordre d'enregistrement."""
        return list(self._plugins.values())

    def bind_app(self, app: object) -> None:
        """Injecte la reference de l'application dans chaque plugin enregistre.

        A appeler une fois, juste apres ``autoload()``, avec l'instance
        ``Wmain`` en cours d'execution (meme role que
        ``PluginRegistry.bind_app``).
        """
        logger.debug("BatchPluginRegistry.bind_app: entrée")
        for plugin in self._plugins.values():
            plugin.app = app
        logger.debug("BatchPluginRegistry.bind_app: fin")

    def tool_ids(self) -> list[str]:
        """Retourne la liste des ``tool_id`` enregistres."""
        return list(self._plugins.keys())

    def __contains__(self, tool_id: str) -> bool:
        """Teste si un outil est enregistré pour ``tool_id`` (opérateur ``in``).

        Args:
            tool_id (str): Identifiant d'outil à tester.

        Returns:
            bool: ``True`` si un plugin est enregistré sous cet identifiant.
        """
        return tool_id in self._plugins

    def __len__(self) -> int:
        """Retourne le nombre d'outils enregistrés (opérateur ``len()``).

        Returns:
            int: Nombre de ``BatchPlugin`` actuellement enregistrés.
        """
        return len(self._plugins)

    def autoload(
        self, directory: str | Path | None = None, package: str | None = None
    ) -> list[str]:
        """Decouvre et enregistre automatiquement tous les ``BatchPlugin``.

        Chaque fichier ``plugin_*.py`` trouve dans *directory* peut exposer
        une fonction de niveau module nommee ``get_batch_plugin`` (sans
        argument) qui retourne soit une instance de ``BatchPlugin``, soit un
        iterable de plusieurs instances.

        Un module sans ``get_batch_plugin`` est ignore silencieusement s'il
        expose ``get_plugin`` (c'est alors un ``ConnectionPlugin``, gere par
        l'autre registre — pas une erreur). Dans tous les autres cas
        problematiques — import du module en echec, ``get_batch_plugin()``
        absent alors qu'aucun ``get_plugin`` n'est present non plus, appel
        de l'entree de point en echec, ou enregistrement refuse (``tool_id``
        en double) — un warning est journalise avec la cause precise, et le
        module concerne est saute sans faire echouer le demarrage de
        l'application.

        Args:
            directory: Dossier a scanner (par defaut, celui de
                ``plugin_base.py``, meme convention que ``PluginRegistry``).
            package: Prefixe de paquet optionnel pour l'import.

        Returns:
            La liste des ``tool_id`` nouvellement enregistres.
        """
        logger.debug("BatchPluginRegistry.autoload: entrée")
        if directory is None:
            directory = Path(__file__).resolve().parent
        else:
            directory = Path(directory)

        self_module_name = Path(__file__).stem
        loaded: list[str] = []

        for module_name, full_name, module, import_error in _discover_plugin_modules(
            directory, package, self_module_name
        ):
            if module is None:
                self.load_errors[module_name] = f"echec import : {import_error}"
                continue

            entry_point = getattr(module, BATCH_PLUGIN_ENTRY_POINT, None)
            if not callable(entry_point):
                if callable(getattr(module, PLUGIN_ENTRY_POINT, None)):
                    # Pas un BatchPlugin : c'est un ConnectionPlugin, gere
                    # par PluginRegistry. Pas une erreur, rien a journaliser.
                    continue
                cause = f"pas de fonction '{BATCH_PLUGIN_ENTRY_POINT}()' de niveau module"
                logger.warning(f"BatchPluginRegistry.autoload | {full_name} ignore : {cause}")
                self.load_errors[module_name] = cause
                continue

            try:
                result = entry_point()
            except Exception as exc:
                cause = f"echec de {BATCH_PLUGIN_ENTRY_POINT}() : {exc}"
                logger.exception(f"BatchPluginRegistry.autoload | {cause} ({full_name})")
                self.load_errors[module_name] = cause
                continue

            candidates = result if isinstance(result, (list, tuple)) else [result]
            for plugin in candidates:
                if plugin is None:
                    continue
                try:
                    self.register(plugin)
                except ValueError as exc:
                    cause = f"enregistrement refuse : {exc}"
                    logger.exception(f"BatchPluginRegistry.autoload | {cause} ({full_name})")
                    self.load_errors[module_name] = cause
                    continue
                loaded.append(plugin.tool_id)

        logger.debug("BatchPluginRegistry.autoload: sortie")
        return loaded
