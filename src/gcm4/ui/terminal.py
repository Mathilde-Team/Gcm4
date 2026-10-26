"""Terminal VTE GTK4 commun aux plugins terminal (telnet, local…), #295.

Repris de ``plugins/telnet/gtk4.py`` (#290) pour ne pas recopier la même
session dans chaque plugin terminal. La logique sans GTK est dans
:mod:`gcm4.core.terminal`.

**Seuls les plugins importent ce module**, jamais la base : il exige VTE
GTK4 (``Vte-3.91``), absent de Rocky Linux 9 et 10 (#223). Sur ces
systèmes, l'import échoue dans le plugin, qui est signalé en erreur dans
Préférences > Extensions sans empêcher gcm4 de démarrer.
"""

from __future__ import annotations

import os
import signal
from collections.abc import Callable

import gi

# GTK 4.0 est fixé par ``gcm4.ui`` (#119). VTE est fixé ici et non dans
# ``gcm4/ui/__init__.py`` : la base doit démarrer sans VTE GTK4.
gi.require_version("Vte", "3.91")

from gi.repository import Gdk, GLib, Gtk, Vte  # noqa: E402
from loguru import logger  # noqa: E402

from gcm4.core import terminal  # noqa: E402
from gcm4.core.i18n import N_, _  # noqa: E402
from gcm4.core.model import Host  # noqa: E402

#: Palette Tango (16 couleurs) appliquée avec les couleurs de l'hôte, comme
#: GTK3 ; GTK3 répétait par erreur ``#729FCF`` en 14ᵉ couleur (magenta
#: clair), corrigé ici en ``#AD7FA8``.
PALETTE = (
    "#000000",
    "#CC0000",
    "#4E9A06",
    "#C4A000",
    "#3465A4",
    "#75507B",
    "#06989A",
    "#D3D7CF",
    "#555753",
    "#EF2929",
    "#8AE234",
    "#FCE94F",
    "#729FCF",
    "#AD7FA8",
    "#34E2E2",
    "#EEEEEC",
)

#: Libellés des liaisons d'effacement, dans l'ordre de ``Vte.EraseBinding``
#: (mêmes chaînes que la page « Avancé » de GTK3, déjà traduites).
LIAISONS = (
    N_("Automatic"),
    N_("ASCII Backspace \t[Ctrl+H 0x08]"),
    N_("ASCII Delete    \t[Ctrl+? 0x7F]"),
    N_("Delete Sequence \t[@7]"),
    N_("TTY Erase"),
)


def rgba(texte: str) -> Gdk.RGBA | None:
    """``Gdk.RGBA`` de ``texte`` (``#rrggbb``…), ``None`` si illisible."""
    logger.trace("rgba: {!r}", texte)
    couleur = Gdk.RGBA()
    resultat = couleur if texte and couleur.parse(texte) else None
    logger.trace("rgba: {}", resultat is not None)
    return resultat


class SessionTerminal:
    """Onglet terminal : un ``Vte.Terminal`` et son défilement ; ``close()`` arrête le programme.

    Attributes:
        terminal: le ``Vte.Terminal``.
        widget: ce que gcm4 place dans l'onglet.
        pid: processus lancé ; 0 avant la fin du lancement et après sa fin.
        fermee: onglet fermé (``close()`` appelé).
        code_de_sortie: statut ``waitpid`` du programme ; ``None`` tant qu'il tourne.
    """

    def __init__(
        self,
        host: Host,
        protocole: str,
        lancement: terminal.Lancement,
        log,
        debut_commandes_ms: int,
    ) -> None:
        """Construit le terminal et lance ``lancement`` (asynchrone, rien ne bloque).

        Args:
            host: hôte ouvert (couleurs, options ``host.protocol_options[protocole]``).
            protocole: protocole du plugin (options et messages du journal).
            lancement: commande à lancer.
            log: journal du plugin (``context.log``).
            debut_commandes_ms: délai du premier envoi de ``commands``.
        """
        logger.debug("SessionTerminal.__init__: {} {!r}", protocole, host.path)
        self.log = log
        self.protocole = protocole
        self.pid = 0
        self.fermee = False
        self.code_de_sortie: int | None = None
        #: Envois de commandes pas encore partis : rang → source GLib.
        self._minuteries: dict[int, int] = {}
        options = host.protocol_options.get(protocole, {})
        self.terminal = Vte.Terminal()
        self.terminal.set_hexpand(True)
        self.terminal.set_vexpand(True)
        self.terminal.set_backspace_binding(
            Vte.EraseBinding(terminal.liaison(options, terminal.OPT_RETOUR_ARRIERE))
        )
        self.terminal.set_delete_binding(
            Vte.EraseBinding(terminal.liaison(options, terminal.OPT_SUPPRESSION))
        )
        #: Encodage effectif de l'onglet (#85), aussi celui des messages de gcm4.
        self.encodage = self._appliquer_encodage(terminal.encodage(options))
        avant, fond = rgba(host.font_color), rgba(host.back_color)
        if avant is not None and fond is not None:
            self.terminal.set_colors(avant, fond, [rgba(c) for c in PALETTE])
        self.terminal.connect("child-exited", self._fin)
        self.widget = Gtk.ScrolledWindow(child=self.terminal)
        self.widget.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._lancer(lancement, terminal.environnement(options))
        commandes = terminal.lire_commandes(options.get(terminal.OPT_COMMANDES, ""))
        for rang, (delai, texte) in enumerate(terminal.etapes(commandes, debut_commandes_ms)):
            self._minuteries[rang] = GLib.timeout_add(delai, self._envoyer, rang, texte)
        logger.debug("SessionTerminal.__init__: {} envoi(s) programmé(s)", len(self._minuteries))

    def _appliquer_encodage(self, nom: str) -> str:
        """Règle l'encodage de VTE ; refusé (VTE sans ICU) : UTF-8, signalé dans l'onglet.

        Returns:
            L'encodage effectif.
        """
        logger.debug("SessionTerminal._appliquer_encodage: {}", nom)
        if nom == terminal.ENCODAGE_DEFAUT:
            logger.debug("SessionTerminal._appliquer_encodage: défaut")
            return nom
        try:
            # Déprécié depuis VTE 0.54 mais seul moyen de lire un flux non UTF-8.
            self.terminal.set_encoding(nom)
        except GLib.Error as exc:
            self.log.warning("{}: encodage {} refusé : {}", self.protocole, nom, exc.message)
            message = _("Encoding {name} is not available, UTF-8 is used.").format(name=nom)
            self.terminal.feed(f"{message}\r\n".encode())
            logger.debug("SessionTerminal._appliquer_encodage: refusé")
            return terminal.ENCODAGE_DEFAUT
        logger.debug("SessionTerminal._appliquer_encodage: fait")
        return nom

    def _afficher(self, texte: str) -> None:
        """Écrit un message de gcm4 dans le terminal, dans l'encodage de l'onglet."""
        logger.debug("SessionTerminal._afficher: {} caractère(s)", len(texte))
        self.terminal.feed(terminal.encoder(texte, self.encodage))
        logger.debug("SessionTerminal._afficher: fait")

    def _lancer(self, lancement: terminal.Lancement, envv: list[str]) -> None:
        """Lance la commande ; le mot de passe éventuel part dans un tube vers le descripteur 3."""
        logger.debug("SessionTerminal._lancer: {!r}", lancement)
        fds: list[int] = []
        if lancement.mot_de_passe:
            lecture, ecriture = os.pipe()
            try:
                os.write(ecriture, lancement.mot_de_passe.encode("utf-8") + b"\n")
            finally:
                os.close(ecriture)
            fds = [lecture]
        # VTE prend possession des descripteurs de ``fds`` et les ferme
        # après le lancement, réussi ou non.
        self.terminal.spawn_with_fds_async(
            Vte.PtyFlags.DEFAULT,
            None,
            lancement.argv,
            envv,
            fds,
            [3] * len(fds),
            GLib.SpawnFlags.DEFAULT,
            None,
            None,
            -1,
            None,
            self._lance,
        )
        logger.debug("SessionTerminal._lancer: demandé")

    def _lance(self, _terminal, pid: int, erreur, *_args) -> None:
        """Fin du lancement asynchrone : garde le pid, ou affiche l'erreur dans le terminal."""
        logger.debug("SessionTerminal._lance: pid={}", pid)
        if erreur is not None:
            self.log.warning("{}: lancement impossible : {}", self.protocole, erreur.message)
            self._afficher(f"{erreur.message}\r\n")
        else:
            self.pid = pid
            if self.fermee:
                logger.debug("SessionTerminal._lance: onglet déjà fermé")
                self._arreter()
        logger.debug("SessionTerminal._lance: fait")

    def _envoyer(self, rang: int, texte: str) -> bool:
        """Envoie ``texte`` au programme (commandes après ouverture) ; minuterie à usage unique."""
        logger.debug("SessionTerminal._envoyer: {} caractère(s)", len(texte))
        self._minuteries.pop(rang, None)
        if not self.fermee and self.code_de_sortie is None:
            self.terminal.feed_child(texte.encode("utf-8"))
        logger.debug("SessionTerminal._envoyer: fait")
        return GLib.SOURCE_REMOVE

    def _fin(self, _terminal, statut: int) -> None:
        """Fin du programme : l'onglet reste ouvert avec « Session ended »."""
        logger.debug("SessionTerminal._fin: statut {}", statut)
        self.code_de_sortie = statut
        self.pid = 0
        self._oublier_minuteries()
        self._afficher("\r\n" + _("Session ended") + "\r\n")
        logger.debug("SessionTerminal._fin: fait")

    def _oublier_minuteries(self) -> None:
        """Annule les envois de commandes pas encore partis."""
        logger.debug("SessionTerminal._oublier_minuteries: {}", len(self._minuteries))
        for source in self._minuteries.values():
            GLib.source_remove(source)
        self._minuteries.clear()
        logger.debug("SessionTerminal._oublier_minuteries: fait")

    def _arreter(self) -> None:
        """Envoie SIGHUP au programme, comme à la fermeture d'un terminal."""
        logger.debug("SessionTerminal._arreter: pid={}", self.pid)
        if self.pid > 0:
            try:
                os.kill(self.pid, signal.SIGHUP)
            except ProcessLookupError:
                logger.debug("SessionTerminal._arreter: déjà terminé")
        logger.debug("SessionTerminal._arreter: fait")

    def close(self) -> None:
        """Onglet fermé : envois annulés, programme arrêté."""
        logger.debug("SessionTerminal.close: entrée")
        self.fermee = True
        self._oublier_minuteries()
        self._arreter()
        logger.debug("SessionTerminal.close: fait")


def zone_options(options: dict[str, str], changed: Callable[[], None]) -> Gtk.Widget:
    """Commandes après ouverture, touches d'effacement, ``TERM`` et encodage (page « Avancé » de GTK3).

    Args:
        options: copie des options du protocole, modifiée à chaque saisie.
        changed: appelé après chaque modification de ``options``.

    Returns:
        Une boîte verticale : case et texte des commandes, aide ``##D=``,
        grille des liaisons, de ``TERM`` et de l'encodage (#85).
    """
    logger.debug("zone_options: entrée")
    boite = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)

    texte = terminal.lire_commandes(options.get(terminal.OPT_COMMANDES, ""))
    case = Gtk.CheckButton(label=_("Send commands after login"), active=bool(texte))
    vue = Gtk.TextView(monospace=True, sensitive=bool(texte))
    vue.get_buffer().set_text(texte)
    defilement = Gtk.ScrolledWindow(child=vue, min_content_height=80)
    defilement.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
    aide = Gtk.Label(xalign=0, wrap=True)
    aide.set_markup(
        _("<span size='smaller'>Use ##D=milliseconds to add a delay.\nEx: ##D=1000</span>")
    )

    def commandes(*_args) -> None:
        """Case ou texte modifié : ``commands`` vide quand la case est décochée (GTK3)."""
        logger.debug("zone_options.commandes: entrée")
        vue.set_sensitive(case.get_active())
        tampon = vue.get_buffer()
        brut = tampon.get_text(tampon.get_start_iter(), tampon.get_end_iter(), False)
        options[terminal.OPT_COMMANDES] = (
            terminal.ecrire_commandes(brut) if case.get_active() else ""
        )
        changed()
        logger.debug("zone_options.commandes: fait")

    case.connect("toggled", commandes)
    vue.get_buffer().connect("changed", commandes)
    for enfant in (case, defilement, aide):
        boite.append(enfant)

    grille = Gtk.Grid(column_spacing=12, row_spacing=6)
    libelles = Gtk.StringList.new([_(texte_liaison) for texte_liaison in LIAISONS])
    for ligne, (cle, titre) in enumerate(
        (
            (terminal.OPT_RETOUR_ARRIERE, _("Backspace:")),
            (terminal.OPT_SUPPRESSION, _("Delete:")),
        )
    ):
        liste = Gtk.DropDown(model=libelles, hexpand=True)
        liste.set_selected(terminal.liaison(options, cle))

        def choisi(widget, _param, cle=cle) -> None:
            """Liaison choisie, enregistrée en entier comme GTK3."""
            logger.debug("zone_options.choisi: {}", cle)
            options[cle] = str(widget.get_selected())
            changed()
            logger.debug("zone_options.choisi: fait")

        liste.connect("notify::selected", choisi)
        grille.attach(Gtk.Label(label=titre, xalign=0), 0, ligne, 1, 1)
        grille.attach(liste, 1, ligne, 1, 1)

    term = Gtk.Entry(
        text=options.get(terminal.OPT_TERM, ""), placeholder_text=terminal.TERM_DEFAUT
    )

    def saisie(*_args) -> None:
        """``TERM`` saisi."""
        logger.debug("zone_options.saisie: entrée")
        options[terminal.OPT_TERM] = term.get_text().strip()
        changed()
        logger.debug("zone_options.saisie: fait")

    term.connect("changed", saisie)
    grille.attach(Gtk.Label(label=_("TERM"), xalign=0), 0, 2, 1, 1)
    grille.attach(term, 1, 2, 1, 1)

    encodages = Gtk.DropDown(
        model=Gtk.StringList.new(list(terminal.ENCODAGES)),
        hexpand=True,
        enable_search=True,
        tooltip_text=_("Character encoding of the remote system; UTF-8 if unsure."),
    )
    encodages.set_selected(terminal.ENCODAGES.index(terminal.encodage(options)))

    def encodage_choisi(widget, _param) -> None:
        """Encodage choisi ; UTF-8 (défaut) n'est pas écrit dans la configuration."""
        logger.debug("zone_options.encodage_choisi: entrée")
        nom = terminal.ENCODAGES[widget.get_selected()]
        options[terminal.OPT_ENCODAGE] = "" if nom == terminal.ENCODAGE_DEFAUT else nom
        changed()
        logger.debug("zone_options.encodage_choisi: {}", nom)

    encodages.connect("notify::selected", encodage_choisi)
    grille.attach(Gtk.Label(label=_("Encoding:"), xalign=0), 0, 3, 1, 1)
    grille.attach(encodages, 1, 3, 1, 1)
    boite.append(grille)
    logger.debug("zone_options: fait")
    return boite
