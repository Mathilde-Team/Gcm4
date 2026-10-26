"""Plugin serial de gcm4 (contrat v1, ``docs/plugins.md``) : port série dans un terminal VTE GTK4.

Portage de ``plugins/plugin_serial.py`` (GTK3, #179, étape 2 de
``docs/gtk4-plugins-portage.md``) ; la logique sans GTK est dans
:mod:`core` (détection picocom/minicom/screen, construction de la
commande, templates constructeur), la session VTE et la zone d'options
communes aux plugins terminal dans :mod:`gcm4.ui.terminal`. Chargé en
mode source comme ``gcm4_plugin_serial.gtk4`` (#288).

Écarts avec GTK3, voulus ou en attente de la base :

- la session démarre à l'ouverture de l'hôte (comme local, telnet et
  ipmisol) : les boutons Connecter/Déconnecter, l'état et le libellé
  « binaire détecté » de l'onglet GTK3 ne sont pas repris — se
  déconnecter revient à fermer l'onglet ;
- les paramètres série (débit, bits, parité, flux, outil, template) sont
  dans la zone d'options du protocole (en GTK3 : combos de l'onglet,
  écrits dans l'hôte à la connexion) ; le champ Port de l'hôte reste lu
  comme débit pour les configurations GTK3 (héritage) ;
- le template de l'hôte GTK3 (``host.type``) n'est pas repris : le
  modèle GTK4 n'a pas ce champ, le template se choisit dans la zone
  d'options ;
- police et couleurs par défaut des préférences globales GTK3
  (``[options]``) ne sont pas lues : le contrat v1 ne donne au plugin que
  sa section ``[plugin serial]``. Les couleurs de l'hôte sont appliquées.
"""

from __future__ import annotations

import os
from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")  # déjà fixé par gcm4 ; déclare le fichier GTK4 (#178)
gi.require_version("Vte", "3.91")

from gi.repository import Gtk  # noqa: E402
from loguru import logger  # noqa: E402

from gcm4.core.i18n import _  # noqa: E402
from gcm4.core.model import Host  # noqa: E402
from gcm4.core.plugins import ConnectionPlugin  # noqa: E402
from gcm4.core.terminal import Lancement  # noqa: E402
from gcm4.ui.terminal import SessionTerminal, zone_options  # noqa: E402

from . import core  # noqa: E402

#: Délai du premier envoi de ``commands`` (ms), comme telnet : la console
#: série n'envoie rien d'elle-même, les commandes sont optionnelles.
DELAIS_COMMANDES_MS = 3000

_LIBELLES_PARITE = ("None", "Even", "Odd")
_LIBELLES_FLUX = ("None", "XON/XOFF (software)", "RTS/CTS (hardware)", "DTR/DSR")


class SerialSession(SessionTerminal):
    """Onglet série : session terminal commune, commandes envoyées au bout de 3 s."""

    def __init__(self, host: Host, lancement: Lancement, log) -> None:
        """Construit le terminal et lance l'outil série (asynchrone, rien ne bloque).

        Args:
            host: hôte ouvert (couleurs, options ``serial``).
            lancement: commande à lancer (:func:`core.commande`).
            log: journal du plugin.
        """
        logger.debug("SerialSession.__init__: {!r}", host.path)
        super().__init__(host, "serial", lancement, log, DELAIS_COMMANDES_MS)
        logger.debug("SerialSession.__init__: fait")


def _combo(
    cle: str,
    valeurs: tuple[str, ...],
    libelles: tuple[str, ...] | None,
    options: dict[str, str],
    changed: Callable[[], None],
) -> tuple[Gtk.DropDown, Gtk.StringList]:
    """Combo d'une option série : écrit la valeur choisie dans ``options``."""
    logger.debug("_combo: {!r}", cle)
    liste = Gtk.StringList.new(
        [_(libelles[i]) if libelles else valeur for i, valeur in enumerate(valeurs)]
    )
    combo = Gtk.DropDown(model=liste, hexpand=True)
    courante = options.get(cle, "") or valeurs[0]
    combo.set_selected(valeurs.index(courante) if courante in valeurs else 0)

    def choisi(widget, _param) -> None:
        """Valeur choisie, enregistrée comme les autres options du protocole."""
        logger.debug("_combo.choisi: {}={}", cle, valeurs[widget.get_selected()])
        options[cle] = valeurs[widget.get_selected()]
        changed()
        logger.debug("_combo.choisi: fait")

    combo.connect("notify::selected", choisi)
    return combo, liste


def _zone_serial(options: dict[str, str], changed: Callable[[], None]) -> Gtk.Widget:
    """Paramètres série : template, débit, bits, parité, stop, flux, outil."""
    logger.debug("_zone_serial: entrée")
    boite = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    grille = Gtk.Grid(column_spacing=12, row_spacing=6)

    noms_templates = Gtk.StringList.new(list(core.TEMPLATES))
    combo_template = Gtk.DropDown(model=noms_templates, hexpand=True)
    template_courant = options.get("template", "") or next(iter(core.TEMPLATES))
    if template_courant not in core.TEMPLATES:
        template_courant = next(iter(core.TEMPLATES))
    combo_template.set_selected(list(core.TEMPLATES).index(template_courant))

    combos: dict[str, Gtk.DropDown] = {}
    for cle, valeurs, libelles in (
        ("baud", core.DEBITS, None),
        ("databits", core.BITS_DONNEES, None),
        ("parity", core.PARITES, _LIBELLES_PARITE),
        ("stopbits", core.BITS_STOP, None),
        ("flow", core.FLUX, _LIBELLES_FLUX),
        ("tool", core.OUTILS, None),
    ):
        combos[cle] = _combo(cle, valeurs, libelles, options, changed)[0]

    def template_choisi(widget, _param) -> None:
        """Template sélectionné : ses paramètres remplissent les autres options."""
        logger.debug("_zone_serial.template_choisi: entrée")
        nom = list(core.TEMPLATES)[widget.get_selected()]
        options["template"] = nom
        valeurs_combo = {
            "baud": core.DEBITS,
            "flow": core.FLUX,
            "parity": core.PARITES,
            "databits": core.BITS_DONNEES,
            "stopbits": core.BITS_STOP,
        }
        for cle, valeur in zip(
            ("baud", "flow", "parity", "databits", "stopbits"),
            core.TEMPLATES[nom],
            strict=True,
        ):
            options[cle] = valeur
            if valeur in valeurs_combo[cle]:
                combos[cle].set_selected(valeurs_combo[cle].index(valeur))
        changed()
        logger.debug("_zone_serial.template_choisi: fait")

    combo_template.connect("notify::selected", template_choisi)

    for ligne, (titre, combo) in enumerate(
        (
            (_("Template:"), combo_template),
            (_("Baud rate:"), combos["baud"]),
            (_("Data bits:"), combos["databits"]),
            (_("Parity:"), combos["parity"]),
            (_("Stop bits:"), combos["stopbits"]),
            (_("Flow control:"), combos["flow"]),
            (_("Tool"), combos["tool"]),
        )
    ):
        grille.attach(Gtk.Label(label=titre, xalign=0), 0, ligne, 1, 1)
        grille.attach(combo, 1, ligne, 1, 1)
    boite.append(grille)
    logger.debug("_zone_serial: fait")
    return boite


class SerialPlugin(ConnectionPlugin):
    """Protocole ``serial`` : port série local via picocom, minicom ou screen."""

    name = "serial"
    version = "1.0"
    protocols = ("serial",)

    def default_port(self, protocol: str) -> int | None:
        """Pas de port réseau : le champ Port sert de débit hérité (lu, pas proposé)."""
        logger.debug("SerialPlugin.default_port: {!r}", protocol)
        return None

    def build_host_options_widget(
        self, host: Host, options: dict[str, str], changed: Callable[[], None]
    ) -> Gtk.Widget:
        """Paramètres série, puis commandes, touches d'effacement et ``TERM``."""
        logger.debug("SerialPlugin.build_host_options_widget: {!r}", host.path)
        zone = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        zone.append(_zone_serial(options, changed))
        zone.append(zone_options(options, changed))
        logger.debug("SerialPlugin.build_host_options_widget: fait")
        return zone

    def open(self, host: Host) -> SerialSession:
        """Lance l'outil série dans un terminal.

        Raises:
            FileNotFoundError: aucun de picocom, minicom et screen n'est
                installé.
            ValueError: paramètres supplémentaires mal formés.
        """
        logger.debug("SerialPlugin.open: {!r}", host.path)
        parametres = core.parametres(host.protocol_options.get("serial", {}), host.port)
        chemin = core.outil(os.environ, parametres["tool"])
        if chemin is None:
            logger.debug("SerialPlugin.open: aucun outil série")
            raise FileNotFoundError(
                _("{program} not found").format(program="picocom/minicom/screen")
            )
        argv = core.commande(chemin, host.host, parametres, host.extra_params)
        session = SerialSession(host, Lancement(argv), self.context.log)
        logger.debug("SerialPlugin.open: session ouverte")
        return session


PLUGIN = SerialPlugin
