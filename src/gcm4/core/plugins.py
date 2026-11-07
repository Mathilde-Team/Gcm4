# SPDX-License-Identifier: GPL-3.0-or-later
"""Contrat v1 entre la base gcm4 et ses plugins de connexion (#149), sans GTK.

La référence pour écrire un plugin est ``docs/plugins.md`` ; ce module en est
la version typée. GTK n'est importé que pour l'annotation
(``TYPE_CHECKING``) : le contrat se charge et se teste sans GTK.

Un plugin est une sous-classe de :class:`ConnectionPlugin` :

- **identité** : :attr:`~ConnectionPlugin.name`, :attr:`~ConnectionPlugin.version`,
  :attr:`~ConnectionPlugin.protocols`, :meth:`~ConnectionPlugin.default_port`,
  :meth:`~ConnectionPlugin.requires_address` ;
- **hôte** : :meth:`~ConnectionPlugin.default_options`,
  :meth:`~ConnectionPlugin.validate_options`,
  :meth:`~ConnectionPlugin.build_host_options_widget` ;
- **connexion** : :meth:`~ConnectionPlugin.open` rend une :class:`Session`
  (contenu d'onglet et fermeture) ;
- **facultatif** : :meth:`~ConnectionPlugin.build_preferences_page`,
  :meth:`~ConnectionPlugin.context_menu_items`.

Un plugin peut aussi être un **outil** : une sous-classe de
:class:`ToolPlugin` (entrée de menu Import/Export/Outils, sans protocole),
même version de contrat.

Le plugin ne lit aucun global (consignes §2) : tout ce dont il a besoin de
l'application lui est passé par un :class:`PluginContext`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar, Protocol, runtime_checkable

from loguru import logger

from gcm4.core.exchange import Conflit
from gcm4.core.i18n import _

if TYPE_CHECKING:
    from gi.repository import Gtk

    from gcm4.core.model import Host
    from gcm4.core.validation import FieldError

#: Version du contrat. Un plugin déclare celle qu'il implémente
#: (:attr:`ConnectionPlugin.api_version`) ; toute autre valeur est refusée.
API_VERSION = 1

# --- Plugins outils ----------------------------------------------------------

#: Sections de menu où une entrée d'outil peut être placée.
SECTION_IMPORT = "import"
SECTION_EXPORT = "export"
SECTION_OUTILS = "outils"
SECTIONS_OUTILS = (SECTION_IMPORT, SECTION_EXPORT, SECTION_OUTILS)


class PluginContext(Protocol):
    """Services de l'application offerts à un plugin (injectés, jamais globaux)."""

    #: Journal loguru lié au plugin (``logger.bind(plugin=name)``).
    log: Any

    def config(self) -> Mapping[str, str]:
        """Configuration du plugin en lecture seule (section ``[plugin <name>]``)."""
        ...

    def ask_password(self, host: Host, done: Callable[[str | None], None]) -> None:
        """Demande un mot de passe pour ``host`` ; ``done(None)`` si l'utilisateur annule.

        Asynchrone : ``done`` est appelé plus tard, depuis la boucle GTK.
        """
        ...

    def notify(self, message: str) -> None:
        """Message bref à l'utilisateur (toast de la fenêtre principale)."""
        ...

    def open_tab(self, title: str, widget: Gtk.Widget) -> None:
        """Ouvre un onglet supplémentaire (le contenu d'une connexion passe par :meth:`ConnectionPlugin.open`)."""
        ...

    def import_hosts(self, hotes: Sequence[Host], conflit: Conflit = Conflit.IGNORER) -> Any:
        """Importe des hôtes dans la base de l'outil (contrat v1, #436).

        Le magasin est complété selon ``conflit``, ``gcm4.conf`` est
        enregistré et l'arbre rechargé ; renvoie le bilan
        (:class:`~gcm4.core.exchange.Rapport`). C'est le chemin des imports
        d'outils (sessions PuTTY, hyperviseurs…), en écho de
        ``win.import-hosts`` pour les fichiers (#153).

        Écriture impossible : rien n'est importé (magasin inchangé) et
        :class:`~gcm4.core.exchange.ExchangeError` est levée — l'outil la
        notifie.
        """
        ...


@runtime_checkable
class Session(Protocol):
    """Connexion ouverte : contenu d'onglet et fermeture."""

    #: Contenu de l'onglet, possédé par la base une fois rendu.
    widget: Gtk.Widget

    def close(self) -> None:
        """Ferme la connexion et libère ses ressources ; appelé une fois, onglet fermé."""
        ...


@dataclass(frozen=True)
class MenuItem:
    """Entrée de menu contextuel fournie par un plugin pour un hôte."""

    label: str
    activate: Callable[[], None]


class ConnectionPlugin(ABC):
    """Plugin de connexion, contrat v1. Voir ``docs/plugins.md``."""

    #: Version du contrat implémentée (doit valoir :data:`API_VERSION`).
    api_version: ClassVar[int] = API_VERSION
    #: Identifiant stable, minuscules (``"demo"``, ``"ssh"``) ; sert de section de configuration.
    name: ClassVar[str] = ""
    #: Version du plugin, affichée dans Préférences > Extensions.
    version: ClassVar[str] = ""
    #: Protocoles gérés (valeurs de ``Host.protocol``), au moins un.
    protocols: ClassVar[tuple[str, ...]] = ()

    def __init__(self, context: PluginContext) -> None:
        """Le contexte est donné à la construction et gardé pour toute la vie du plugin."""
        logger.debug("ConnectionPlugin.__init__: {!r}", self.name)
        self.context = context
        logger.debug("ConnectionPlugin.__init__: fait")

    # --- identité --------------------------------------------------------------

    def default_port(self, protocol: str) -> int | None:
        """Port proposé pour ``protocol`` quand le champ est vide (``None`` : aucun)."""
        logger.trace("default_port: {!r}", protocol)
        return None

    def requires_address(self, protocol: str) -> bool:
        """Vrai si ``protocol`` a besoin d'une adresse (défaut : vrai).

        Facultatif (#297, validé par l'auteure) : un protocole sans hôte
        distant (terminal local) rend ``False`` — la fenêtre d'hôte
        n'exige alors pas d'adresse et masque le champ, comme GTK3
        (``show_host = not is_local``). Sans plugin, l'adresse reste
        requise. La valeur par défaut garde la compatibilité des plugins
        existants (``api_version`` reste 1, même pattern que
        :meth:`default_port`).
        """
        logger.trace("requires_address: {!r}", protocol)
        return True

    # --- hôte -----------------------------------------------------------------

    def default_options(self, protocol: str) -> dict[str, str]:
        """Options d'un nouvel hôte de ce protocole (valeurs texte, clés INI)."""
        logger.trace("default_options: {!r}", protocol)
        return {}

    def validate_options(self, protocol: str, options: Mapping[str, str]) -> list[FieldError]:
        """Erreurs des options ; ``FieldError.champ`` est la clé de l'option."""
        logger.trace("validate_options: {!r}", protocol)
        return []

    @abstractmethod
    def build_host_options_widget(
        self, host: Host, options: dict[str, str], changed: Callable[[], None]
    ) -> Gtk.Widget:
        """Zone d'options du dialogue d'hôte.

        Args:
            host: hôte édité (lecture seule : champs communs).
            options: **copie** des options de ``host.protocol`` à modifier sur place ;
                la base l'enregistre seulement si l'utilisateur valide.
            changed: à appeler après chaque modification (revalidation, bouton
                Enregistrer, confirmation de fermeture).
        """

    # --- connexion ------------------------------------------------------------

    @abstractmethod
    def open(self, host: Host) -> Session:
        """Ouvre une connexion vers ``host`` et rend sa session (contenu d'onglet).

        Lever une exception signale un échec : la base l'affiche et le
        journalise, sans onglet.
        """

    # --- facultatif -----------------------------------------------------------

    def build_preferences_page(self) -> Gtk.Widget | None:
        """Page de réglages du plugin (Préférences > Extensions) ; ``None`` : aucune."""
        logger.trace("build_preferences_page: {!r}", self.name)
        return None

    def context_menu_items(self, host: Host) -> list[MenuItem]:
        """Entrées supplémentaires du menu contextuel d'un hôte de ce plugin."""
        logger.trace("context_menu_items: {!r}", self.name)
        return []


class ToolPlugin(ABC):
    """Plugin outil, contrat v1. Voir ``docs/plugins.md``.

    Contrairement à :class:`ConnectionPlugin`, un outil n'est rattaché à
    aucun protocole ni hôte : c'est une entrée de menu (typiquement le menu
    Outils, ou la section Import/Export du menu Fichier) qui ouvre son
    propre onglet ou dialogue et gère son cycle de vie — déploiement de
    configuration en masse, import de sessions PuTTY, inventaire
    d'hyperviseur… Le portage GTK3 des plugins ``BatchPlugin`` passe par
    ici (``docs/gtk4-plugins-portage.md``).
    """

    #: Version du contrat implémentée (doit valoir :data:`API_VERSION`).
    api_version: ClassVar[int] = API_VERSION
    #: Identifiant stable, minuscules (``"netmiko-push"``) ; sert de section
    #: de configuration, de nom d'action (``win.outil-<name>``) et de clé de registre.
    name: ClassVar[str] = ""
    #: Version du plugin, affichée dans Préférences > Extensions.
    version: ClassVar[str] = ""
    #: Msgid anglais du libellé de menu, marqué ``N_`` dans le plugin.
    menu_label: ClassVar[str] = ""
    #: Section de menu : :data:`SECTION_OUTILS` (défaut), :data:`SECTION_IMPORT`
    #: ou :data:`SECTION_EXPORT` (menu Fichier, à côté des imports d'hôtes).
    menu_section: ClassVar[str] = SECTION_OUTILS
    #: Nom d'icône symbolique GTK de l'entrée de menu (facultatif).
    icon_name: ClassVar[str] = ""

    def __init__(self, context: PluginContext) -> None:
        """Le contexte est donné à la construction et gardé pour toute la vie du plugin."""
        logger.debug("ToolPlugin.__init__: {!r}", self.name)
        self.context = context
        logger.debug("ToolPlugin.__init__: fait")

    @abstractmethod
    def activate(self) -> None:
        """Déclenche l'outil (l'utilisateur a choisi l'entrée de menu).

        Ouvre un onglet ou un dialogue ; lever une exception signale un
        échec : la base l'affiche (notification) et le journalise.
        """

    # --- facultatif -----------------------------------------------------------

    def build_preferences_page(self) -> Any | None:
        """Page de réglages du plugin (Préférences > Extensions) ; ``None`` : aucune."""
        logger.trace("build_preferences_page: {!r}", self.name)
        return None


def incompatibilite_outil(classe: type) -> str | None:
    """Raison traduite pour laquelle ``classe`` n'est pas un outil v1 utilisable, sinon ``None``.

    Vérifie l'héritage de :class:`ToolPlugin`, la version du contrat,
    l'identité (``name`` en minuscules sans espace), le libellé et la
    section de menu, et l'absence de méthode abstraite non implémentée.
    """
    logger.debug("incompatibilite_outil: {!r}", getattr(classe, "__name__", classe))
    raison: str | None = None
    if not (isinstance(classe, type) and issubclass(classe, ToolPlugin)):
        raison = _("It is not a tool plugin.")
    elif classe.api_version != API_VERSION:
        raison = _(
            "It implements plugin API version {found}; this application supports version {expected}."
        ).format(found=classe.api_version, expected=API_VERSION)
    elif not classe.name or classe.name != classe.name.strip().lower() or " " in classe.name:
        raison = _("Its name must be a non-empty lowercase identifier.")
    elif not classe.menu_label:
        raison = _("It declares no menu label.")
    elif classe.menu_section not in SECTIONS_OUTILS:
        raison = _("Its menu section must be one of: {sections}.").format(
            sections=", ".join(SECTIONS_OUTILS)
        )
    elif getattr(classe, "__abstractmethods__", None):
        raison = _("It does not implement: {methods}.").format(
            methods=", ".join(sorted(classe.__abstractmethods__))
        )
    logger.debug("incompatibilite_outil: {}", raison)
    return raison


def incompatibilite(classe: type) -> str | None:
    """Raison traduite pour laquelle ``classe`` n'est pas un plugin v1 utilisable, sinon ``None``.

    Vérifie l'héritage de :class:`ConnectionPlugin`, la version du contrat,
    l'identité (``name`` en minuscules sans espace, ``protocols`` non vide)
    et l'absence de méthode abstraite non implémentée.
    """
    logger.debug("incompatibilite: {!r}", getattr(classe, "__name__", classe))
    raison: str | None = None
    if not (isinstance(classe, type) and issubclass(classe, ConnectionPlugin)):
        raison = _("It is not a connection plugin.")
    elif classe.api_version != API_VERSION:
        raison = _(
            "It implements plugin API version {found}; this application supports version {expected}."
        ).format(found=classe.api_version, expected=API_VERSION)
    elif not classe.name or classe.name != classe.name.strip().lower() or " " in classe.name:
        raison = _("Its name must be a non-empty lowercase identifier.")
    elif not classe.protocols or not all(isinstance(p, str) and p for p in classe.protocols):
        raison = _("It declares no protocol.")
    elif getattr(classe, "__abstractmethods__", None):
        raison = _("It does not implement: {methods}.").format(
            methods=", ".join(sorted(classe.__abstractmethods__))
        )
    logger.debug("incompatibilite: {}", raison)
    return raison
