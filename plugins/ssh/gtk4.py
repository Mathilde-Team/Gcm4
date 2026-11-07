# SPDX-License-Identifier: GPL-3.0-or-later
"""Outils SSH GTK4 de gcm4 (#97) : sélecteur de clé, éditeur de
``~/.ssh/config`` (personnel + partagé), et à venir gestionnaire de clés.

Portage GTK4 des fichiers GTK3 de la racine (``key_picker_dialog.py``,
``ssh_config_editor.py``, ``ssh_key_manager_dialog.py``), qui restent en
place pour l'application GTK3 jusqu'à #180. La logique sans GTK (liste des
clés, empreintes, permissions, parsing de ``~/.ssh/config``, formulaire et
redirections de l'éditeur) est dans :mod:`core`, celle de la session
(commande ``ssh``, expect, redirections de ports) dans :mod:`session`
(#396).

Le ``PLUGIN`` (session SSH en terminal VTE, #396) revendique le protocole
``ssh`` ; la zone d'options de l'hôte reprend les pages « Advanced » et
« Port forwarding » de GTK3, la zone commune des plugins terminal
(commandes, ``TERM``, encodage, touches) en plus. SSHFS et le menu des
outils arrivent avec la partie 3 de #396.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")  # déjà fixé par gcm4 ; déclare le fichier GTK4 (#178)
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, GObject, Gtk, Pango  # noqa: E402
from loguru import logger  # noqa: E402

from gcm4.core.i18n import _  # noqa: E402
from gcm4.core.model import Host  # noqa: E402
from gcm4.core.plugins import ConnectionPlugin  # noqa: E402
from gcm4.core.terminal import Lancement  # noqa: E402
from gcm4.ui import dialogues  # noqa: E402
from gcm4.ui.terminal import SessionTerminal, zone_options  # noqa: E402

from . import core, session  # noqa: E402

__all__ = [
    "CIBLES",
    "TYPES_REDIRECTION",
    "KeyPickerDialog",
    "LigneCle",
    "LigneHote",
    "LigneRedirection",
    "PLUGIN",
    "SshConfigEditor",
    "SshPlugin",
    "SshSession",
    "SshTestDialog",
]


class LigneCle(Gtk.ListBoxRow):
    """Ligne du sélecteur : icône, nom, type, empreinte, permissions."""

    def __init__(self, cle: core.CleSsh) -> None:
        """Construit la ligne de ``cle``."""
        logger.debug("LigneCle.__init__: entrée | {}", cle.nom)
        super().__init__()
        self.cle = cle
        if cle.est_publique:
            icone = "dialog-information-symbolic"
        elif cle.permissions_ok:
            icone = "channel-secure-symbolic"
        else:
            icone = "channel-insecure-symbolic"
        boite = Gtk.Box(spacing=12, margin_top=6, margin_bottom=6, margin_start=6, margin_end=6)
        boite.append(Gtk.Image(icon_name=icone))
        self.nom = Gtk.Label(label=cle.nom, xalign=0, hexpand=True)
        if not cle.permissions_ok and not cle.est_publique:
            # Clé privée lisible par d'autres : ssh la refusera.
            self.nom.add_css_class("warning")
            self.nom.set_tooltip_text(_("Permissions too open: ssh will refuse this key"))
        boite.append(self.nom)
        self.type = Gtk.Label(
            label=_("public") if cle.est_publique else _("private"), width_chars=8
        )
        boite.append(self.type)
        self.empreinte = Gtk.Label(
            label=cle.empreinte, ellipsize=Pango.EllipsizeMode.END, width_chars=20, xalign=0
        )
        self.empreinte.add_css_class("monospace")
        boite.append(self.empreinte)
        marque = "✓" if cle.permissions_ok else "⚠"
        self.permissions = Gtk.Label(label=f"{cle.permissions} {marque}", width_chars=7)
        boite.append(self.permissions)
        self.set_child(boite)
        logger.debug("LigneCle.__init__: sortie")


class KeyPickerDialog(Gtk.Window):
    """Choix d'une ou plusieurs clés de ``~/.ssh/`` (portage GTK4 de ``key_picker_dialog.py``).

    « Use » (ou double-clic, Entrée) émet ``key-selected`` avec le(s)
    chemin(s) de clé privée, joints par ``", "`` (convention des champs
    multi-valeurs comme ``IdentityFile``), puis ferme. « Generate… » appelle
    ``gestionnaire`` (ouverture du gestionnaire de clés) ; la liste est
    rechargée quand le sélecteur redevient la fenêtre active. Échap ou
    « Cancel » ferment sans rien émettre.

    Signals:
        key-selected (str): chemin(s) de clé privée choisi(s).
    """

    __gsignals__ = {"key-selected": (GObject.SignalFlags.RUN_LAST, None, (str,))}

    def __init__(
        self,
        parent: Gtk.Window | None = None,
        gestionnaire: Callable[[], None] | None = None,
        dossier: Path | None = None,
    ) -> None:
        """Construit le sélecteur et charge les clés.

        Args:
            parent: Fenêtre parente (modale).
            gestionnaire: Ouvre le gestionnaire de clés ; ``None`` masque « Generate… ».
            dossier: Dossier des clés (``~/.ssh/`` par défaut).
        """
        logger.debug("KeyPickerDialog.__init__: entrée")
        super().__init__(
            title=_("Select SSH Key"),
            modal=True,
            destroy_with_parent=True,
            default_width=560,
            default_height=400,
        )
        if parent is not None:
            self.set_transient_for(parent)
        self._gestionnaire = gestionnaire
        self._dossier = dossier
        self._attente_retour = False

        boite = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=6,
            margin_top=12,
            margin_bottom=12,
            margin_start=12,
            margin_end=12,
        )
        consigne = Gtk.Label(xalign=0, wrap=True)
        consigne.set_markup(
            _(
                "Keys found in <b>~/.ssh/</b> — select one (or several with Ctrl/Shift-click) "
                "then click <i>Use</i>:"
            )
        )
        boite.append(consigne)
        self.liste = Gtk.ListBox(
            selection_mode=Gtk.SelectionMode.MULTIPLE, activate_on_single_click=False
        )
        self.liste.connect("selected-rows-changed", self._on_selection)
        self.liste.connect("row-activated", self._on_ligne_activee)
        self.defilement = Gtk.ScrolledWindow(child=self.liste, vexpand=True, has_frame=True)
        boite.append(self.defilement)
        self.vide = Gtk.Label(justify=Gtk.Justification.CENTER, vexpand=True)
        self.vide.set_markup(
            _("<i>No SSH key found in ~/.ssh/.\nClick <b>Generate…</b> to create one.</i>")
        )
        boite.append(self.vide)

        boutons = Gtk.Box(spacing=6, halign=Gtk.Align.END, margin_top=6)
        self.bouton_annuler = Gtk.Button(label=_("_Cancel"), use_underline=True)
        self.bouton_annuler.connect("clicked", lambda _b: self.close())
        self.bouton_generer = Gtk.Button(label=_("⚙ Generate…"), visible=gestionnaire is not None)
        self.bouton_generer.connect("clicked", self._on_generer)
        self.bouton_utiliser = Gtk.Button(label=_("_Use"), use_underline=True, sensitive=False)
        self.bouton_utiliser.add_css_class("suggested-action")
        self.bouton_utiliser.connect("clicked", self._on_utiliser)
        for bouton in (self.bouton_annuler, self.bouton_generer, self.bouton_utiliser):
            boutons.append(bouton)
        boite.append(boutons)
        self.set_child(boite)

        raccourcis = Gtk.ShortcutController()
        raccourcis.add_shortcut(
            Gtk.Shortcut(
                trigger=Gtk.ShortcutTrigger.parse_string("Escape"),
                action=Gtk.CallbackAction.new(self._on_echap),
            )
        )
        self.add_controller(raccourcis)
        self.connect("notify::is-active", self._on_actif)
        self.charger()
        logger.debug("KeyPickerDialog.__init__: sortie")

    def charger(self) -> None:
        """(Re)lit le dossier des clés et remplit la liste."""
        logger.debug("charger: entrée")
        while (ligne := self.liste.get_row_at_index(0)) is not None:
            self.liste.remove(ligne)
        cles = core.lister_cles(self._dossier)
        for cle in cles:
            self.liste.append(LigneCle(cle))
        self.defilement.set_visible(bool(cles))
        self.vide.set_visible(not cles)
        self.bouton_utiliser.set_sensitive(False)
        logger.debug("charger: sortie | {} clé(s)", len(cles))

    def lignes(self) -> list[LigneCle]:
        """Lignes de la liste, dans l'ordre."""
        logger.debug("lignes: entrée")
        resultat: list[LigneCle] = []
        while (ligne := self.liste.get_row_at_index(len(resultat))) is not None:
            resultat.append(ligne)
        logger.debug("lignes: sortie | {}", len(resultat))
        return resultat

    def _on_selection(self, liste: Gtk.ListBox) -> None:
        """« Use » actif dès qu'une ligne est choisie."""
        logger.debug("_on_selection: entrée")
        nombre = len(liste.get_selected_rows())
        self.bouton_utiliser.set_sensitive(nombre > 0)
        logger.debug("_on_selection: sortie | {} ligne(s)", nombre)

    def _on_ligne_activee(self, _liste: Gtk.ListBox, ligne: LigneCle) -> None:
        """Double-clic ou Entrée : cette seule clé, puis fermeture."""
        logger.debug("_on_ligne_activee: entrée | {}", ligne.cle.nom)
        self._emettre([ligne])
        logger.debug("_on_ligne_activee: sortie")

    def _on_utiliser(self, _bouton: Gtk.Button) -> None:
        """« Use » : les clés sélectionnées, dans l'ordre de la liste."""
        logger.debug("_on_utiliser: entrée")
        choisies = [ligne for ligne in self.lignes() if ligne.is_selected()]
        if choisies:
            self._emettre(choisies)
        logger.debug("_on_utiliser: sortie | {} clé(s)", len(choisies))

    def _emettre(self, lignes: list[LigneCle]) -> None:
        """Émet ``key-selected`` puis ferme le sélecteur."""
        logger.debug("_emettre: entrée | {} ligne(s)", len(lignes))
        resultat = ", ".join(core.chemin_prive(ligne.cle) for ligne in lignes)
        logger.info("KeyPickerDialog | key-selected {} clé(s)", len(lignes))
        self.emit("key-selected", resultat)
        self.close()
        logger.debug("_emettre: sortie")

    def _on_generer(self, _bouton: Gtk.Button) -> None:
        """Ouvre le gestionnaire de clés ; ce sélecteur cesse d'être modal entre-temps.

        Sinon la modalité bloquerait les clics dans le gestionnaire. La
        modalité et la liste sont rétablies au retour (:meth:`_on_actif`).
        """
        logger.debug("_on_generer: entrée")
        self.set_modal(False)
        self._attente_retour = True
        self._gestionnaire()
        logger.debug("_on_generer: sortie")

    def _on_actif(self, _fenetre: Gtk.Window, _pspec: GObject.ParamSpec) -> None:
        """Retour au sélecteur après « Generate… » : modal à nouveau, liste relue."""
        logger.debug("_on_actif: entrée | actif={}", self.is_active())
        if self._attente_retour and self.is_active():
            self._attente_retour = False
            self.set_modal(True)
            self.charger()
        logger.debug("_on_actif: sortie")

    def _on_echap(self, *_args) -> bool:
        """Échap : fermeture sans choix."""
        logger.debug("_on_echap: entrée")
        self.close()
        logger.debug("_on_echap: sortie")
        return True


# ---------------------------------------------------------------------------
# Éditeur de ~/.ssh/config (personnel + partagé)
# ---------------------------------------------------------------------------

#: Cibles de l'éditeur, dans l'ordre des sélecteurs « File: ».
CIBLES: tuple[str, ...] = ("personal", "shared")


def _libelles_cibles() -> dict[str, str]:
    """Libellés (traduits) des deux fichiers édités."""
    logger.debug("_libelles_cibles: entrée")
    libelles = {
        "personal": _("Personal (~/.ssh/config)"),
        "shared": _("Shared — bastion, all users (/etc/ssh/ssh_config.d/config_gcm.conf)"),
    }
    logger.debug("_libelles_cibles: sortie")
    return libelles


def _libelles_champs() -> dict[str, str]:
    """Libellés (traduits) des champs du formulaire."""
    logger.debug("_libelles_champs: entrée")
    libelles = {cle: _(cle) for cle in core.CHAMPS_EDITEUR[1:]}
    libelles["Host"] = _("Alias(es)")
    logger.debug("_libelles_champs: sortie")
    return libelles


def _exemples_champs() -> dict[str, str]:
    """Textes d'exemple (traduits) des champs du formulaire."""
    logger.debug("_exemples_champs: entrée")
    oui_non = _("yes ou no")
    exemples = {
        "Host": _("ex : web1 ou web1 web1.example.com"),
        "HostName": _("ex : 192.168.1.10 ou example.com"),
        "User": _("ex : root"),
        "Port": _("ex : 22"),
        "IdentityFile": _("ex : ~/.ssh/id_ed25519, ~/.ssh/id_rsa"),
        "ProxyJump": _("ex : user@bastion.example.com, user@server1.example.com"),
        "ProxyCommand": _("ex : ssh -W %h:%p bastion, ou corkscrew proxy 8080 %h %p"),
        "ForwardAgent": oui_non,
        "ServerAliveInterval": _("ex : 60"),
        "ServerAliveCountMax": _("ex : 3"),
        "Compression": oui_non,
        "StrictHostKeyChecking": _("yes / no / ask / accept-new"),
        "UserKnownHostsFile": _("ex : ~/.ssh/known_hosts"),
        "ConnectTimeout": _("ex : 10"),
        "RequestTTY": _("yes / no / force / auto"),
        "RemoteCommand": _("ex : tmux attach || tmux new"),
        "ControlMaster": _("auto / yes / no"),
        "ControlPersist": _("ex : 10m"),
        "PubkeyAuthentication": oui_non,
        "PasswordAuthentication": oui_non,
        "IdentitiesOnly": oui_non,
        "LogLevel": _("QUIET/ERROR/INFO/VERBOSE/DEBUG"),
        "AddKeysToAgent": _("yes / no / ask / confirm"),
    }
    logger.debug("_exemples_champs: sortie")
    return exemples


def _messages_erreur() -> dict[str, str]:
    """Codes de :func:`core.erreurs_formulaire` → message traduit."""
    logger.debug("_messages_erreur: entrée")
    messages = {
        "alias": _("At least one alias is required"),
        "port": _("Port must be an integer between 1 and 65535"),
    }
    logger.debug("_messages_erreur: sortie")
    return messages


def _libelles_redirections() -> dict[str, str]:
    """Type de redirection → libellé court (colonne « Type »)."""
    logger.debug("_libelles_redirections: entrée")
    libelles = {"dynamic": _("Dynamic"), "local": _("Local"), "remote": _("Remote")}
    logger.debug("_libelles_redirections: sortie")
    return libelles


#: Types de redirection, dans l'ordre du sélecteur « Type ».
TYPES_REDIRECTION: tuple[str, ...] = ("dynamic", "local", "remote")


class LigneHote(Gtk.ListBoxRow):
    """Hôte de la liste de gauche ; 🌐 signale le fichier partagé."""

    def __init__(self, hote: core.SSHHost, cible: str) -> None:
        """Construit la ligne de ``hote`` (fichier ``cible``)."""
        logger.debug("LigneHote.__init__: entrée | {} ({})", hote.patterns, cible)
        super().__init__()
        self.hote = hote
        self.cible = cible
        alias = " ".join(hote.patterns)
        badge = "🌐 " if cible == "shared" else ""
        self.etiquette = Gtk.Label(label=f"{badge}{alias}", xalign=0, margin_start=6)
        self.etiquette.set_ellipsize(Pango.EllipsizeMode.END)
        self.set_child(self.etiquette)
        machine = hote.get_option("HostName") or ""
        info = f"{alias}\n{machine}" if machine else alias
        self.set_tooltip_text(f"{info}\n[{_libelles_cibles()[cible]}]")
        logger.debug("LigneHote.__init__: sortie")


class LigneRedirection(Gtk.ListBoxRow):
    """Redirection de port de l'hôte courant : type, ports, cible."""

    def __init__(self, redirection: core.Redirection) -> None:
        """Construit la ligne de ``redirection``."""
        logger.debug("LigneRedirection.__init__: entrée | {}", redirection.brut)
        super().__init__()
        self.redirection = redirection
        boite = Gtk.Box(spacing=12, margin_top=3, margin_bottom=3, margin_start=6, margin_end=6)
        cible = (
            f"{redirection.hote}:{redirection.distant}" if redirection.type != "dynamic" else ""
        )
        for texte, largeur in (
            (_libelles_redirections()[redirection.type], 10),
            (redirection.local, 8),
            (cible, 0),
        ):
            boite.append(Gtk.Label(label=texte, xalign=0, width_chars=largeur))
        self.set_child(boite)
        logger.debug("LigneRedirection.__init__: sortie")


class SshConfigEditor(Gtk.Window):
    """Éditeur de ``~/.ssh/config`` et du fichier partagé (bastion).

    Deux onglets, comme en GTK3 : « Visual » (liste d'hôtes filtrable,
    formulaire, redirections de port, déplacement d'un hôte entre les deux
    fichiers) et « Raw / Diff » (texte brut du fichier choisi et diff avec
    le disque). « Save » écrit les deux fichiers ; ``SSHConfigParser.write``
    ne réécrit pas un fichier inchangé et passe par ``pkexec`` pour le
    fichier partagé.
    """

    def __init__(
        self,
        parent: Gtk.Window | None = None,
        gestionnaire: Callable[[], None] | None = None,
        chemin_personnel: Path | None = None,
        chemin_partage: Path | None = None,
    ) -> None:
        """Lit les deux fichiers et construit l'éditeur.

        Args:
            parent: Fenêtre parente (modale).
            gestionnaire: Ouvre le gestionnaire de clés ; ``None`` masque « ⚙ ».
            chemin_personnel: ``~/.ssh/config`` par défaut.
            chemin_partage: :data:`core.SHARED_SSH_CONFIG_PATH` par défaut.
        """
        logger.debug("SshConfigEditor.__init__: entrée")
        super().__init__(
            title=_("Edit SSH config (personal + shared)"),
            modal=True,
            destroy_with_parent=True,
            default_width=940,
            default_height=620,
        )
        if parent is not None:
            self.set_transient_for(parent)
        self._gestionnaire = gestionnaire
        self.analyseurs: dict[str, core.SSHConfigParser] = {
            "personal": core.SSHConfigParser(config_path=chemin_personnel),
            "shared": core.SSHConfigParser(
                config_path=chemin_partage or core.SHARED_SSH_CONFIG_PATH
            ),
        }
        for analyseur in self.analyseurs.values():
            analyseur.parse()
        self.hote: core.SSHHost | None = None
        self.cible_hote: str | None = None
        self.redirections: list[core.Redirection] = []
        self.cible_brute = "personal"
        self._chargement = False

        self.toasts = Adw.ToastOverlay()
        racine = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        racine.append(self._construire_erreurs())
        self.onglets = Gtk.Notebook(vexpand=True)
        self.onglets.append_page(self._construire_visuel(), Gtk.Label(label=_("Visual")))
        self.onglets.append_page(self._construire_brut(), Gtk.Label(label=_("Raw / Diff")))
        # Branché après les append_page : pas de « switch-page » de construction.
        self.onglets.connect("switch-page", self._on_onglet)
        racine.append(self.onglets)
        racine.append(self._construire_actions())
        self.toasts.set_child(racine)
        self.set_child(self.toasts)

        self.remplir_liste()
        self.synchroniser_brut()
        self.valider()
        chemin = self.analyseurs["personal"].config_path
        if not chemin.exists():
            self.notifier(
                _("{path} does not exist yet — it will be created when saving.").format(
                    path=chemin
                )
            )
        logger.debug("SshConfigEditor.__init__: sortie")

    # -- construction ------------------------------------------------------

    def _construire_erreurs(self) -> Gtk.Widget:
        """Bandeau d'erreurs (validation, écriture), masqué par défaut."""
        logger.debug("_construire_erreurs: entrée")
        boite = Gtk.Box(spacing=6, margin_top=6, margin_bottom=6, margin_start=12, margin_end=12)
        self.erreur = Gtk.Label(xalign=0, wrap=True, hexpand=True, selectable=True)
        self.erreur.add_css_class("error")
        boite.append(self.erreur)
        fermer = Gtk.Button(icon_name="window-close-symbolic", tooltip_text=_("Close"))
        fermer.add_css_class("flat")
        fermer.connect("clicked", lambda _b: self.bandeau.set_reveal_child(False))
        boite.append(fermer)
        self.bandeau = Gtk.Revealer(child=boite, reveal_child=False)
        logger.debug("_construire_erreurs: sortie")
        return self.bandeau

    def _construire_actions(self) -> Gtk.Widget:
        """Boutons « Cancel » et « Save »."""
        logger.debug("_construire_actions: entrée")
        boite = Gtk.Box(
            spacing=6, halign=Gtk.Align.END, margin_top=6, margin_bottom=12, margin_end=12
        )
        self.bouton_annuler = Gtk.Button(label=_("✕ Cancel"))
        self.bouton_annuler.connect("clicked", lambda _b: self.close())
        self.bouton_enregistrer = Gtk.Button(label=_("💾 Save"))
        self.bouton_enregistrer.add_css_class("suggested-action")
        self.bouton_enregistrer.connect("clicked", self._on_enregistrer)
        boite.append(self.bouton_annuler)
        boite.append(self.bouton_enregistrer)
        logger.debug("_construire_actions: sortie")
        return boite

    def _construire_visuel(self) -> Gtk.Widget:
        """Onglet « Visual » : liste d'hôtes à gauche, formulaire à droite."""
        logger.debug("_construire_visuel: entrée")
        gauche = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_start=6)
        self.recherche = Gtk.SearchEntry(placeholder_text=_("Filter hosts…"))
        self.recherche.connect("search-changed", lambda _e: self.liste.invalidate_filter())
        gauche.append(self.recherche)
        self.liste = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.liste.set_filter_func(self._filtrer)
        self.liste.connect("row-selected", self._on_hote_choisi)
        gauche.append(Gtk.ScrolledWindow(child=self.liste, vexpand=True, has_frame=True))
        boutons = Gtk.Box(spacing=3)
        self.bouton_ajouter = Gtk.Button(label=_("＋ Add"), tooltip_text=_("Add a new empty host"))
        self.bouton_ajouter.connect("clicked", self._on_ajouter)
        self.bouton_retirer = Gtk.Button(
            label=_("✕ Remove"), tooltip_text=_("Remove selected host")
        )
        self.bouton_retirer.connect("clicked", self._on_retirer)
        self.bouton_tester = Gtk.Button(
            label=_("⚡ Test"), tooltip_text=_("Test SSH connection to selected host")
        )
        self.bouton_tester.connect("clicked", self._on_tester)
        for bouton in (self.bouton_ajouter, self.bouton_retirer, self.bouton_tester):
            boutons.append(bouton)
        gauche.append(boutons)
        self.bouton_copier = Gtk.Button(
            label=_("Copy ssh cmd"), tooltip_text=_("Copy full ssh command to clipboard")
        )
        self.bouton_copier.connect("clicked", self._on_copier)
        gauche.append(self.bouton_copier)

        self.pile = Gtk.Stack(hexpand=True)
        self.pile.add_named(Gtk.Label(label=_("← Select or create a host")), "vide")
        self.pile.add_named(self._construire_formulaire(), "formulaire")
        self.pile.set_visible_child_name("vide")
        self._activer_boutons_hote()

        panneau = Gtk.Paned(
            start_child=gauche, end_child=self.pile, position=260, margin_top=6, margin_bottom=6
        )
        logger.debug("_construire_visuel: sortie")
        return panneau

    def _construire_formulaire(self) -> Gtk.Widget:
        """Formulaire d'un hôte : fichier, propriétés, redirections."""
        logger.debug("_construire_formulaire: entrée")
        boite = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_end=6)
        ligne = Gtk.Box(spacing=6, margin_start=6)
        ligne.append(Gtk.Label(label=_("File:")))
        libelles = _libelles_cibles()
        self.choix_fichier = Gtk.DropDown.new_from_strings([libelles[c] for c in CIBLES])
        self.choix_fichier.connect("notify::selected", self._on_fichier_change)
        ligne.append(self.choix_fichier)
        boite.append(ligne)
        sous_onglets = Gtk.Notebook(vexpand=True)
        sous_onglets.append_page(self._construire_proprietes(), Gtk.Label(label=_("Properties")))
        sous_onglets.append_page(
            self._construire_redirections(), Gtk.Label(label=_("Redirection de port"))
        )
        boite.append(sous_onglets)
        logger.debug("_construire_formulaire: sortie")
        return boite

    def _construire_proprietes(self) -> Gtk.Widget:
        """Grille des 23 champs, avec un message d'erreur sous chaque champ."""
        logger.debug("_construire_proprietes: entrée")
        grille = Gtk.Grid(
            column_spacing=8,
            row_spacing=2,
            margin_top=8,
            margin_bottom=8,
            margin_start=8,
            margin_end=8,
        )
        self.champs: dict[str, Gtk.Entry] = {}
        self.erreurs_champs: dict[str, Gtk.Label] = {}
        libelles = _libelles_champs()
        exemples = _exemples_champs()
        for rang, cle in enumerate(core.CHAMPS_EDITEUR):
            grille.attach(Gtk.Label(label=libelles[cle], xalign=1), 0, 2 * rang, 1, 1)
            champ = Gtk.Entry(hexpand=True, placeholder_text=exemples[cle])
            champ.connect("changed", self._on_champ_change)
            self.champs[cle] = champ
            if cle in core.CHAMPS_MULTIPLES:
                champ.set_tooltip_text(
                    _(
                        "Multiple values possible: separate them with commas "
                        "(one line per value in the file)"
                    )
                )
            if cle == "IdentityFile":
                boite = Gtk.Box(spacing=3)
                boite.append(champ)
                self.bouton_choisir_cle = Gtk.Button(
                    label=_("🔑 Pick…"), tooltip_text=_("Browse existing SSH keys in ~/.ssh/")
                )
                self.bouton_choisir_cle.connect("clicked", self._on_choisir_cle)
                boite.append(self.bouton_choisir_cle)
                self.bouton_gestionnaire = Gtk.Button(
                    label=_("⚙"),
                    tooltip_text=_("Open SSH Key Manager (generate / import / delete)"),
                    visible=self._gestionnaire is not None,
                )
                self.bouton_gestionnaire.connect("clicked", lambda _b: self._gestionnaire())
                boite.append(self.bouton_gestionnaire)
                grille.attach(boite, 1, 2 * rang, 1, 1)
            else:
                grille.attach(champ, 1, 2 * rang, 1, 1)
            erreur = Gtk.Label(xalign=0, visible=False)
            erreur.add_css_class("error")
            self.erreurs_champs[cle] = erreur
            grille.attach(erreur, 1, 2 * rang + 1, 1, 1)
        logger.debug("_construire_proprietes: sortie")
        return Gtk.ScrolledWindow(child=grille, vexpand=True)

    def _construire_redirections(self) -> Gtk.Widget:
        """Saisie et liste des redirections de port de l'hôte courant."""
        logger.debug("_construire_redirections: entrée")
        boite = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=6,
            margin_top=8,
            margin_bottom=8,
            margin_start=8,
            margin_end=8,
        )
        saisie = Gtk.Box(spacing=6)
        self.type_redirection = Gtk.DropDown.new_from_strings(
            [_("Dynamic (SOCKS)"), _("Local (-L)"), _("Remote (-R)")]
        )
        self.type_redirection.connect("notify::selected", self._on_type_redirection)
        saisie.append(self.type_redirection)
        self.port_local = Gtk.SpinButton.new_with_range(1, 65535, 1)
        self.port_local.set_value(1080)
        self.port_local.set_tooltip_text(_("Local Port"))
        saisie.append(self.port_local)
        self.hote_distant = Gtk.Entry(placeholder_text=_("Remote Host"), hexpand=True)
        saisie.append(self.hote_distant)
        self.port_distant = Gtk.SpinButton.new_with_range(1, 65535, 1)
        self.port_distant.set_value(80)
        self.port_distant.set_tooltip_text(_("Remote Port"))
        saisie.append(self.port_distant)
        self.bouton_ajouter_redirection = Gtk.Button(label=_("＋ Add"))
        self.bouton_ajouter_redirection.connect("clicked", self._on_ajouter_redirection)
        saisie.append(self.bouton_ajouter_redirection)
        boite.append(saisie)
        self.liste_redirections = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        boite.append(
            Gtk.ScrolledWindow(child=self.liste_redirections, vexpand=True, has_frame=True)
        )
        self.bouton_retirer_redirection = Gtk.Button(label=_("✕ Remove"), halign=Gtk.Align.START)
        self.bouton_retirer_redirection.connect("clicked", self._on_retirer_redirection)
        boite.append(self.bouton_retirer_redirection)
        self._on_type_redirection(self.type_redirection, None)
        logger.debug("_construire_redirections: sortie")
        return boite

    def _construire_brut(self) -> Gtk.Widget:
        """Onglet « Raw / Diff » : fichier choisi, texte brut, diff."""
        logger.debug("_construire_brut: entrée")
        boite = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=6,
            margin_top=6,
            margin_bottom=6,
            margin_start=6,
            margin_end=6,
        )
        ligne = Gtk.Box(spacing=6)
        ligne.append(Gtk.Label(label=_("File:")))
        libelles = _libelles_cibles()
        self.choix_brut = Gtk.DropDown.new_from_strings([libelles[c] for c in CIBLES])
        self.choix_brut.connect("notify::selected", self._on_cible_brute)
        ligne.append(self.choix_brut)
        boite.append(ligne)
        boite.append(
            Gtk.Label(
                label=_("Edit raw ssh_config — changes here override the Visual tab on Save."),
                xalign=0,
                wrap=True,
            )
        )
        self.texte_brut = Gtk.TextView(monospace=True)
        self.texte_brut.get_buffer().connect("changed", lambda _t: self.rafraichir_diff())
        boite.append(Gtk.ScrolledWindow(child=self.texte_brut, vexpand=True, has_frame=True))
        ligne = Gtk.Box(spacing=6)
        ligne.append(Gtk.Label(label=_("Diff (original → current):"), xalign=0, hexpand=True))
        rafraichir = Gtk.Button(label=_("⟳ Refresh diff"))
        rafraichir.connect("clicked", lambda _b: self.rafraichir_diff())
        ligne.append(rafraichir)
        boite.append(ligne)
        self.texte_diff = Gtk.TextView(monospace=True, editable=False, cursor_visible=False)
        tampon = self.texte_diff.get_buffer()
        tampon.create_tag("added", foreground="#26a269")
        tampon.create_tag("removed", foreground="#c01c28")
        tampon.create_tag("header", foreground="#1c71d8", weight=Pango.Weight.BOLD)
        boite.append(Gtk.ScrolledWindow(child=self.texte_diff, vexpand=True, has_frame=True))
        logger.debug("_construire_brut: sortie")
        return boite

    # -- liste d'hôtes -----------------------------------------------------

    def lignes(self) -> list[LigneHote]:
        """Lignes de la liste d'hôtes, filtrées ou non, dans l'ordre."""
        logger.debug("SshConfigEditor.lignes: entrée")
        resultat: list[LigneHote] = []
        while (ligne := self.liste.get_row_at_index(len(resultat))) is not None:
            resultat.append(ligne)
        logger.debug("SshConfigEditor.lignes: sortie | {}", len(resultat))
        return resultat

    def remplir_liste(self) -> None:
        """(Re)construit la liste : hôtes personnels, puis partagés ; garde la sélection."""
        logger.debug("remplir_liste: entrée")
        courant = self.hote
        self._chargement = True
        self._vider_liste()
        for cible in CIBLES:
            for hote in self.analyseurs[cible].config.hosts:
                ligne = LigneHote(hote, cible)
                self.liste.append(ligne)
                if hote is courant:
                    self.liste.select_row(ligne)
        self._chargement = False
        logger.debug("remplir_liste: sortie | {} ligne(s)", len(self.lignes()))

    def _vider_liste(self) -> None:
        """Vide la liste d'hôtes (GTK < 4.12, sans ``remove_all``)."""
        logger.debug("_vider_liste: entrée")
        while (ligne := self.liste.get_row_at_index(0)) is not None:
            self.liste.remove(ligne)
        logger.debug("_vider_liste: sortie")

    def _filtrer(self, ligne: LigneHote) -> bool:
        """Filtre : le texte recherché apparaît dans un alias ou le HostName."""
        logger.debug("_filtrer: entrée")
        texte = self.recherche.get_text().strip().lower()
        machine = (ligne.hote.get_option("HostName") or "").lower()
        visible = (
            not texte or texte in machine or any(texte in p.lower() for p in ligne.hote.patterns)
        )
        logger.debug("_filtrer: sortie | {}", visible)
        return visible

    def _on_hote_choisi(self, _liste: Gtk.ListBox, ligne: LigneHote | None) -> None:
        """Nouvel hôte choisi : enregistre le formulaire de l'ancien, charge le nouveau."""
        logger.debug("_on_hote_choisi: entrée | chargement={}", self._chargement)
        if self._chargement:
            logger.debug("_on_hote_choisi: sortie | reconstruction de la liste")
            return
        self.appliquer_formulaire()
        if ligne is None:
            self.hote = None
            self.cible_hote = None
            self.pile.set_visible_child_name("vide")
        else:
            self.charger_hote(ligne.hote, ligne.cible)
        self._activer_boutons_hote()
        logger.debug("_on_hote_choisi: sortie")

    def _activer_boutons_hote(self) -> None:
        """« Remove », « Test » et « Copy ssh cmd » n'agissent que sur un hôte choisi."""
        logger.debug("_activer_boutons_hote: entrée")
        for bouton in (self.bouton_retirer, self.bouton_tester, self.bouton_copier):
            bouton.set_sensitive(self.hote is not None)
        logger.debug("_activer_boutons_hote: sortie")

    # -- formulaire --------------------------------------------------------

    def charger_hote(self, hote: core.SSHHost, cible: str) -> None:
        """Affiche ``hote`` (du fichier ``cible``) dans le formulaire."""
        logger.debug("charger_hote: entrée | {} ({})", hote.patterns, cible)
        self.hote = hote
        self.cible_hote = cible
        self._chargement = True
        for cle, valeur in core.valeurs_formulaire(hote).items():
            self.champs[cle].set_text(valeur)
            self.erreurs_champs[cle].set_visible(False)
        self.choix_fichier.set_selected(CIBLES.index(cible))
        self.redirections = core.redirections_de(hote)
        self._afficher_redirections()
        self._chargement = False
        self.pile.set_visible_child_name("formulaire")
        logger.debug("charger_hote: sortie")

    def valeurs(self) -> dict[str, str]:
        """Valeurs saisies dans le formulaire."""
        logger.debug("valeurs: entrée")
        resultat = {cle: champ.get_text() for cle, champ in self.champs.items()}
        logger.debug("valeurs: sortie")
        return resultat

    def _afficher_erreurs(self, erreurs: dict[str, str]) -> None:
        """Affiche sous chaque champ son erreur, masque les autres."""
        logger.debug("_afficher_erreurs: entrée | {}", sorted(erreurs))
        messages = _messages_erreur()
        for cle, etiquette in self.erreurs_champs.items():
            code = erreurs.get(cle)
            etiquette.set_text(messages[code] if code else "")
            etiquette.set_visible(code is not None)
        logger.debug("_afficher_erreurs: sortie")

    def appliquer_formulaire(self) -> bool:
        """Écrit le formulaire et les redirections dans l'hôte courant.

        Returns:
            ``False`` si un champ est en erreur (il n'est alors pas écrit).
        """
        logger.debug("appliquer_formulaire: entrée")
        if self.hote is None:
            logger.debug("appliquer_formulaire: sortie | aucun hôte")
            return True
        erreurs = core.appliquer_formulaire(self.hote, self.valeurs())
        core.appliquer_redirections(self.hote, self.redirections)
        self._afficher_erreurs(erreurs)
        logger.debug("appliquer_formulaire: sortie | {} erreur(s)", len(erreurs))
        return not erreurs

    def _on_champ_change(self, _champ: Gtk.Entry) -> None:
        """Validation à la frappe (alias, port)."""
        logger.debug("_on_champ_change: entrée")
        if not self._chargement:
            self._afficher_erreurs(core.erreurs_formulaire(self.valeurs()))
        logger.debug("_on_champ_change: sortie")

    def _on_fichier_change(self, choix: Gtk.DropDown, _pspec: GObject.ParamSpec) -> None:
        """« File: » : déplace l'hôte courant vers l'autre fichier."""
        logger.debug("_on_fichier_change: entrée")
        if self._chargement or self.hote is None:
            logger.debug("_on_fichier_change: sortie | ignoré")
            return
        nouvelle = CIBLES[choix.get_selected()]
        if nouvelle == self.cible_hote:
            logger.debug("_on_fichier_change: sortie | même fichier")
            return
        self.analyseurs[self.cible_hote].config.remove_host(self.hote)
        self.analyseurs[nouvelle].config.add_host(self.hote)
        logger.info(
            "SshConfigEditor | hôte {} déplacé : {} → {}",
            self.hote.patterns,
            self.cible_hote,
            nouvelle,
        )
        self.cible_hote = nouvelle
        self.remplir_liste()
        logger.debug("_on_fichier_change: sortie")

    def _on_choisir_cle(self, _bouton: Gtk.Button) -> None:
        """« 🔑 Pick… » : sélecteur de clé, résultat ajouté à IdentityFile."""
        logger.debug("_on_choisir_cle: entrée")
        self.selecteur = KeyPickerDialog(parent=self, gestionnaire=self._gestionnaire)
        self.selecteur.connect("key-selected", self._on_cle_choisie)
        self.selecteur.present()
        logger.debug("_on_choisir_cle: sortie")

    def _on_cle_choisie(self, _selecteur: KeyPickerDialog, chemins: str) -> None:
        """Ajoute les clés choisies à IdentityFile, sans doublon."""
        logger.debug("_on_cle_choisie: entrée")
        champ = self.champs["IdentityFile"]
        actuelles = [v.strip() for v in champ.get_text().split(",") if v.strip()]
        for chemin in chemins.split(", "):
            if chemin not in actuelles:
                actuelles.append(chemin)
        champ.set_text(", ".join(actuelles))
        logger.debug("_on_cle_choisie: sortie | {} clé(s)", len(actuelles))

    # -- redirections ------------------------------------------------------

    def _afficher_redirections(self) -> None:
        """(Re)construit la liste des redirections de l'hôte courant."""
        logger.debug("_afficher_redirections: entrée")
        while (ligne := self.liste_redirections.get_row_at_index(0)) is not None:
            self.liste_redirections.remove(ligne)
        for redirection in self.redirections:
            self.liste_redirections.append(LigneRedirection(redirection))
        logger.debug("_afficher_redirections: sortie | {}", len(self.redirections))

    def _on_type_redirection(self, choix: Gtk.DropDown, _pspec: GObject.ParamSpec | None) -> None:
        """SOCKS : ni hôte ni port cible ; libellés du port selon le sens."""
        logger.debug("_on_type_redirection: entrée")
        type_ = TYPES_REDIRECTION[choix.get_selected()]
        self.hote_distant.set_sensitive(type_ != "dynamic")
        self.port_distant.set_sensitive(type_ != "dynamic")
        if type_ == "remote":
            self.port_local.set_tooltip_text(_("Remote listen port"))
            self.hote_distant.set_placeholder_text(_("Local target host"))
            self.port_distant.set_tooltip_text(_("Local target port"))
        else:
            self.port_local.set_tooltip_text(_("Local Port"))
            self.hote_distant.set_placeholder_text(_("Remote Host"))
            self.port_distant.set_tooltip_text(_("Remote Port"))
        logger.debug("_on_type_redirection: sortie | {}", type_)

    def _on_ajouter_redirection(self, _bouton: Gtk.Button) -> None:
        """Ajoute la redirection saisie (hôte cible obligatoire hors SOCKS)."""
        logger.debug("_on_ajouter_redirection: entrée")
        try:
            redirection = core.nouvelle_redirection(
                TYPES_REDIRECTION[self.type_redirection.get_selected()],
                self.port_local.get_value_as_int(),
                self.hote_distant.get_text(),
                self.port_distant.get_value_as_int(),
            )
        except ValueError as exc:
            self.notifier(_("Enter remote host"))
            logger.debug("_on_ajouter_redirection: sortie | refusée ({})", exc)
            return
        self.redirections.append(redirection)
        self.liste_redirections.append(LigneRedirection(redirection))
        logger.debug("_on_ajouter_redirection: sortie | {}", redirection.brut)

    def _on_retirer_redirection(self, _bouton: Gtk.Button) -> None:
        """Retire la redirection sélectionnée."""
        logger.debug("_on_retirer_redirection: entrée")
        ligne = self.liste_redirections.get_selected_row()
        if ligne is not None:
            self.redirections.remove(ligne.redirection)
            self.liste_redirections.remove(ligne)
        logger.debug("_on_retirer_redirection: sortie | retirée={}", ligne is not None)

    # -- actions sur l'hôte ------------------------------------------------

    def _on_ajouter(self, _bouton: Gtk.Button) -> None:
        """Nouvel hôte vide dans le fichier personnel, aussitôt choisi."""
        logger.debug("_on_ajouter: entrée")
        self.appliquer_formulaire()
        hote = core.SSHHost(patterns=["new-host"])
        hote.set_option("HostName", "")
        hote.set_option("User", "")
        self.analyseurs["personal"].config.add_host(hote)
        self.recherche.set_text("")
        self.remplir_liste()
        self.liste.select_row(self.lignes()[len(self.analyseurs["personal"].config.hosts) - 1])
        logger.debug("_on_ajouter: sortie")

    def _on_retirer(self, _bouton: Gtk.Button) -> None:
        """Retire l'hôte courant après confirmation."""
        logger.debug("_on_retirer: entrée")
        alias = " ".join(self.hote.patterns)
        chemin = self.analyseurs[self.cible_hote].config_path
        self.confirmation = dialogues.creer(
            self,
            _("Remove host?"),
            _("Remove «{alias}» from {path}?").format(alias=alias, path=chemin),
            [
                ("cancel", _("_Cancel"), None),
                ("remove", _("✕ Remove"), Adw.ResponseAppearance.DESTRUCTIVE),
            ],
            "cancel",
            "cancel",
            self._on_retrait_confirme,
        )
        logger.debug("_on_retirer: sortie")

    def _on_retrait_confirme(self, reponse: str) -> None:
        """Réponse à « Remove host? »."""
        logger.debug("_on_retrait_confirme: entrée | {}", reponse)
        if reponse == "remove" and self.hote is not None:
            self.analyseurs[self.cible_hote].config.remove_host(self.hote)
            logger.info("SshConfigEditor | hôte {} retiré", self.hote.patterns)
            self.hote = None
            self.cible_hote = None
            self.remplir_liste()
            self.pile.set_visible_child_name("vide")
            self._activer_boutons_hote()
        logger.debug("_on_retrait_confirme: sortie")

    def _on_tester(self, _bouton: Gtk.Button) -> None:
        """« ⚡ Test » : ``ssh -v`` non interactif vers l'alias courant."""
        logger.debug("_on_tester: entrée")
        self.test = SshTestDialog(self, self.hote.patterns[0])
        self.test.present()
        logger.debug("_on_tester: sortie")

    def _on_copier(self, _bouton: Gtk.Button) -> None:
        """« Copy ssh cmd » : commande équivalente dans le presse-papiers."""
        logger.debug("_on_copier: entrée")
        self.appliquer_formulaire()
        commande = core.commande_ssh(self.hote)
        self.get_clipboard().set(commande)
        self.notifier(_("Copied: {cmd}").format(cmd=commande))
        logger.debug("_on_copier: sortie")

    # -- onglet Raw / Diff -------------------------------------------------

    def _texte_brut(self) -> str:
        """Texte de l'onglet Raw."""
        logger.debug("_texte_brut: entrée")
        tampon = self.texte_brut.get_buffer()
        texte = tampon.get_text(tampon.get_start_iter(), tampon.get_end_iter(), False)
        logger.debug("_texte_brut: sortie")
        return texte

    def synchroniser_brut(self) -> None:
        """Onglet Raw ← modèle du fichier affiché."""
        logger.debug("synchroniser_brut: entrée | {}", self.cible_brute)
        contenu = self.analyseurs[self.cible_brute].config.generate_content()
        self.texte_brut.get_buffer().set_text(contenu)
        self.rafraichir_diff()
        logger.debug("synchroniser_brut: sortie")

    def synchroniser_modele(self) -> bool:
        """Modèle du fichier affiché ← onglet Raw.

        Returns:
            ``False`` si le texte n'a pas pu être analysé (erreur affichée).
        """
        logger.debug("synchroniser_modele: entrée | {}", self.cible_brute)
        try:
            lu = core.config_depuis_texte(self._texte_brut())
        except (OSError, ValueError) as exc:
            logger.warning("synchroniser_modele: texte Raw illisible | {}", exc)
            self.montrer_erreurs([f"{_libelles_cibles()[self.cible_brute]} : {exc}"])
            logger.debug("synchroniser_modele: sortie | échec")
            return False
        config = self.analyseurs[self.cible_brute].config
        config.hosts = lu.hosts
        config.global_options = lu.global_options
        config.include_directives = lu.include_directives
        if self.cible_hote == self.cible_brute:
            # L'objet de l'hôte courant a été remplacé par l'analyse du texte.
            self.hote = None
            self.cible_hote = None
            self.pile.set_visible_child_name("vide")
            self._activer_boutons_hote()
        logger.debug("synchroniser_modele: sortie | {} hôte(s)", len(lu.hosts))
        return True

    def rafraichir_diff(self) -> None:
        """Diff entre le fichier sur disque et le texte de l'onglet Raw."""
        logger.debug("rafraichir_diff: entrée")
        original = self.analyseurs[self.cible_brute].config.original_lines
        diff = core.lignes_diff(original, self._texte_brut())
        tampon = self.texte_diff.get_buffer()
        tampon.set_text("")
        if not diff:
            tampon.insert_with_tags_by_name(tampon.get_end_iter(), _("No changes."), "header")
        for ligne in diff:
            if ligne.startswith(("+++", "---", "@@")):
                tampon.insert_with_tags_by_name(tampon.get_end_iter(), ligne, "header")
            elif ligne.startswith("+"):
                tampon.insert_with_tags_by_name(tampon.get_end_iter(), ligne, "added")
            elif ligne.startswith("-"):
                tampon.insert_with_tags_by_name(tampon.get_end_iter(), ligne, "removed")
            else:
                tampon.insert(tampon.get_end_iter(), ligne)
        logger.debug("rafraichir_diff: sortie | {} ligne(s)", len(diff))

    def _on_cible_brute(self, choix: Gtk.DropDown, _pspec: GObject.ParamSpec) -> None:
        """Change le fichier de l'onglet Raw (le texte en cours est d'abord appliqué)."""
        logger.debug("_on_cible_brute: entrée")
        nouvelle = CIBLES[choix.get_selected()]
        if nouvelle != self.cible_brute and self.synchroniser_modele():
            self.cible_brute = nouvelle
            self.remplir_liste()
            self.synchroniser_brut()
        logger.debug("_on_cible_brute: sortie | {}", self.cible_brute)

    def _on_onglet(self, _onglets: Gtk.Notebook, _page: Gtk.Widget, numero: int) -> None:
        """Visual → Raw : formulaire puis texte ; Raw → Visual : texte puis liste."""
        logger.debug("_on_onglet: entrée | {}", numero)
        if numero == 1:
            self.appliquer_formulaire()
            self.synchroniser_brut()
        elif self.synchroniser_modele():
            self.remplir_liste()
        logger.debug("_on_onglet: sortie")

    # -- validation, écriture ----------------------------------------------

    def montrer_erreurs(self, erreurs: list[str]) -> None:
        """Bandeau d'erreurs : affiché avec ``erreurs``, masqué si la liste est vide."""
        logger.debug("montrer_erreurs: entrée | {}", len(erreurs))
        self.erreur.set_text("\n".join(erreurs))
        self.bandeau.set_reveal_child(bool(erreurs))
        logger.debug("montrer_erreurs: sortie")

    def valider(self) -> list[str]:
        """Valide les deux fichiers (doublons, ports, clés absentes)."""
        logger.debug("valider: entrée")
        libelles = _libelles_cibles()
        erreurs = [
            f"[{libelles[cible]}] {erreur}"
            for cible in CIBLES
            for erreur in self.analyseurs[cible].validate()
        ]
        self.montrer_erreurs(erreurs)
        logger.debug("valider: sortie | {} erreur(s)", len(erreurs))
        return erreurs

    def enregistrer(self) -> bool:
        """Écrit les deux fichiers.

        Depuis l'onglet Raw, le texte du fichier affiché est d'abord
        appliqué ; depuis Visual, le formulaire. Rien n'est écrit si l'un
        des deux est invalide.

        Returns:
            ``True`` si les deux fichiers ont été écrits.
        """
        logger.debug("enregistrer: entrée | onglet={}", self.onglets.get_current_page())
        if self.onglets.get_current_page() == 1:
            if not self.synchroniser_modele():
                logger.debug("enregistrer: sortie | texte Raw illisible")
                return False
        elif not self.appliquer_formulaire():
            self.valider()
            logger.debug("enregistrer: sortie | formulaire invalide")
            return False
        self.remplir_liste()
        libelles = _libelles_cibles()
        erreurs: list[str] = []
        for cible in CIBLES:
            analyseur = self.analyseurs[cible]
            try:
                analyseur.write()
            except OSError as exc:
                logger.error(
                    "enregistrer | écriture de {} impossible : {}", analyseur.config_path, exc
                )
                erreurs.append(f"{libelles[cible]} : {exc}")
            else:
                logger.info("SshConfigEditor | {} enregistré", analyseur.config_path)
        self.montrer_erreurs(erreurs)
        logger.debug("enregistrer: sortie | {} erreur(s)", len(erreurs))
        return not erreurs

    def _on_enregistrer(self, _bouton: Gtk.Button) -> None:
        """« 💾 Save » : écrit puis ferme, ou garde la fenêtre ouverte sur l'erreur."""
        logger.debug("_on_enregistrer: entrée")
        if self.enregistrer():
            self.close()
        logger.debug("_on_enregistrer: sortie")

    def notifier(self, message: str) -> None:
        """Message éphémère en bas de la fenêtre."""
        logger.debug("notifier: entrée")
        toast = Adw.Toast.new(message)
        toast.set_timeout(3)
        self.toasts.add_toast(toast)
        logger.debug("notifier: sortie")


class SshTestDialog(Gtk.Window):
    """Sortie de ``ssh -v`` (non interactif) vers un alias, sans bloquer l'interface."""

    #: Délai avant d'interrompre ssh (secondes).
    DELAI = 10

    def __init__(self, parent: Gtk.Window, alias: str) -> None:
        """Ouvre la fenêtre et lance ssh en arrière-plan.

        Args:
            parent: Fenêtre parente (modale).
            alias: Alias du bloc Host à tester.
        """
        logger.debug("SshTestDialog.__init__: entrée | {}", alias)
        super().__init__(
            title=_("Test connection — {alias}").format(alias=alias),
            transient_for=parent,
            modal=True,
            destroy_with_parent=True,
            default_width=560,
            default_height=300,
        )
        boite = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=6,
            margin_top=8,
            margin_bottom=8,
            margin_start=8,
            margin_end=8,
        )
        self.texte = Gtk.TextView(monospace=True, editable=False, cursor_visible=False)
        boite.append(Gtk.ScrolledWindow(child=self.texte, vexpand=True, has_frame=True))
        fermer = Gtk.Button(label=_("Close"), halign=Gtk.Align.END)
        fermer.connect("clicked", lambda _b: self.close())
        boite.append(fermer)
        self.set_child(boite)
        self.termine = False
        self.connect("close-request", self._on_fermeture)
        commande = core.commande_test(alias)
        self.ajouter(_("Running: {cmd}\n\n").format(cmd=" ".join(commande)))
        self.annulation = Gio.Cancellable()
        try:
            self.processus = Gio.Subprocess.new(
                commande, Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_MERGE
            )
        except GLib.Error as exc:
            logger.debug("SshTestDialog | lancement impossible : {}", exc.message)
            self.processus = None
            self.termine = True
            self.ajouter(_("\n[Error: {exc}]").format(exc=exc.message))
        else:
            self.processus.communicate_utf8_async(None, self.annulation, self._on_fini)
            self.minuterie = GLib.timeout_add_seconds(self.DELAI, self._on_delai)
        logger.debug("SshTestDialog.__init__: sortie")

    def ajouter(self, texte: str) -> None:
        """Ajoute ``texte`` à la fin de la sortie."""
        logger.debug("ajouter: entrée | {} caractère(s)", len(texte))
        tampon = self.texte.get_buffer()
        tampon.insert(tampon.get_end_iter(), texte)
        logger.debug("ajouter: sortie")

    def contenu(self) -> str:
        """Texte affiché."""
        logger.debug("contenu: entrée")
        tampon = self.texte.get_buffer()
        texte = tampon.get_text(tampon.get_start_iter(), tampon.get_end_iter(), False)
        logger.debug("contenu: sortie")
        return texte

    def _on_fini(self, processus: Gio.Subprocess, resultat: Gio.AsyncResult) -> None:
        """Fin de ssh (ou annulation à la fermeture) : sortie et code de retour."""
        logger.debug("_on_fini: entrée")
        try:
            _ok, sortie, _erreurs = processus.communicate_utf8_finish(resultat)
        except GLib.Error as exc:
            logger.debug("_on_fini: sortie | {}", exc.message)
            return
        self.termine = True
        GLib.source_remove(self.minuterie)
        self.ajouter(sortie or _("(no output)\n"))
        self.ajouter(_("\nExit code: {rc}").format(rc=processus.get_exit_status()))
        logger.debug("_on_fini: sortie | code={}", processus.get_exit_status())

    def _on_delai(self) -> bool:
        """Délai dépassé sans réponse de ssh : il est interrompu."""
        logger.debug("_on_delai: entrée")
        self.termine = True
        self.annulation.cancel()
        self.processus.force_exit()
        self.ajouter(_("\n[Timeout after 10 s]"))
        logger.debug("_on_delai: sortie")
        return GLib.SOURCE_REMOVE

    def _on_fermeture(self, _fenetre: Gtk.Window) -> bool:
        """Fermeture avant la fin : ssh est interrompu."""
        logger.debug("_on_fermeture: entrée | terminé={}", self.termine)
        if not self.termine:
            self.termine = True
            GLib.source_remove(self.minuterie)
            self.annulation.cancel()
            self.processus.force_exit()
        logger.debug("_on_fermeture: sortie")
        return False


# -- Session et plugin (#396, partie 2) ---------------------------------------


class SshSession(SessionTerminal):
    """Onglet ssh : session terminal commune, commandes envoyées au bout de 3 s."""

    def __init__(
        self,
        host: Host,
        lancement: Lancement,
        log,
        notifier: Callable[[str], None] | None = None,
    ) -> None:
        """Construit le terminal et lance ``lancement`` (asynchrone, rien ne bloque).

        Args:
            host: hôte ouvert (couleurs, options ``ssh``).
            lancement: commande à lancer (:func:`session.lancement`).
            log: journal du plugin.
            notifier: toast d'échec de connexion (#80, ``context.notify``).
        """
        logger.debug("SshSession.__init__: {!r}", host.path)
        super().__init__(host, "ssh", lancement, log, session.DELAI_COMMANDES_MS, notifier)
        logger.debug("SshSession.__init__: fait")


def _bascule_booleen(options: dict[str, str], cle: str, changed: Callable[[], None]) -> Callable:
    """Fabrique le gestionnaire d'une case à cocher d'option booléenne INI.

    Args:
        options: options du protocole, écrites par la case.
        cle: clé de l'option (``session.OPT_X11``…).
        changed: signalé à chaque écriture.

    Returns:
        Fonction connectée à ``toggled`` ; écrit ``True``/``False`` comme GTK3.
    """
    logger.debug("_bascule_booleen: entrée | {}", cle)

    def bascule(bouton: Gtk.CheckButton) -> None:
        """La case change : l'option suit (``True``/``False``, comme GTK3)."""
        logger.debug("bascule: {} -> {}", cle, bouton.get_active())
        options[cle] = "True" if bouton.get_active() else "False"
        changed()
        logger.debug("bascule: fait")

    logger.debug("_bascule_booleen: sortie")
    return bascule


def _page_avancee(options: dict[str, str], changed: Callable[[], None]) -> Gtk.Widget:
    """Page « Advanced » : keep-alive, X11, agent, compression, clé, options ``-o``.

    Reprend ``_build_advanced_page`` de GTK3, en écrivant directement les
    options du protocole (mêmes clés INI) ; « Extra ssh args » est un champ
    commun de l'hôte, édité par le dialogue principal.
    """
    logger.debug("_page_avancee: entrée | {} option(s)", len(options))
    grille = Gtk.Grid(
        column_spacing=6,
        row_spacing=4,
        margin_start=8,
        margin_end=8,
        margin_top=8,
        margin_bottom=8,
    )
    ligne = 0

    # Keep alive : case + délai ; « 0 » (ou absent) désactive.
    actif = options.get(session.OPT_KEEPALIVE, "").strip() not in ("", "0")
    delai_texte = options.get(session.OPT_KEEPALIVE, "").strip()
    case_keepalive = Gtk.CheckButton(label=_("Keep alive (seconds)"), active=actif)
    try:
        delai_valeur = int(delai_texte) if delai_texte else 120
    except ValueError:
        logger.debug("_page_avancee: keepalive illisible {!r} -> 120", delai_texte)
        delai_valeur = 120
    delai = Gtk.SpinButton()
    delai.set_adjustment(
        Gtk.Adjustment(value=delai_valeur, lower=0, upper=3600, step_increment=10)
    )
    delai.set_sensitive(actif)

    def bascule_keepalive(bouton: Gtk.CheckButton) -> None:
        """La case change : le délai suit, l'option vaut le délai ou « 0 »."""
        logger.debug("bascule_keepalive: {}", bouton.get_active())
        delai.set_sensitive(bouton.get_active())
        options[session.OPT_KEEPALIVE] = (
            str(int(delai.get_value())) if bouton.get_active() else "0"
        )
        changed()
        logger.debug("bascule_keepalive: fait")

    def delai_modifie(_bouton: Gtk.SpinButton) -> None:
        """Le délai change : l'option suit si le keep-alive est actif."""
        logger.debug("delai_modifie: {}", int(delai.get_value()))
        if case_keepalive.get_active():
            options[session.OPT_KEEPALIVE] = str(int(delai.get_value()))
            changed()
        logger.debug("delai_modifie: fait")

    case_keepalive.connect("toggled", bascule_keepalive)
    delai.connect("value-changed", delai_modifie)
    grille.attach(case_keepalive, 0, ligne, 1, 1)
    grille.attach(delai, 1, ligne, 1, 1)
    ligne += 1

    # X11 et agent : simples cases.
    for cle, libelle in (
        (session.OPT_X11, _("X11 forwarding (-X)")),
        (session.OPT_AGENT, _("Agent forwarding (-A)")),
    ):
        case = Gtk.CheckButton(label=libelle, active=session.booleen(options.get(cle, "")))
        case.connect("toggled", _bascule_booleen(options, cle, changed))
        grille.attach(case, 0, ligne, 2, 1)
        ligne += 1

    # Compression : case + niveau.
    compression = session.booleen(options.get(session.OPT_COMPRESSION, ""))
    case_compression = Gtk.CheckButton(label=_("Compression (-C)"), active=compression)
    niveau_texte = options.get(session.OPT_NIVEAU_COMPRESSION, "").strip()
    try:
        niveau_valeur = int(niveau_texte) if niveau_texte else 6
    except ValueError:
        logger.debug("_page_avancee: niveau de compression illisible {!r} -> 6", niveau_texte)
        niveau_valeur = 6
    niveau = Gtk.SpinButton()
    niveau.set_adjustment(Gtk.Adjustment(value=niveau_valeur, lower=1, upper=9, step_increment=1))
    niveau.set_sensitive(compression)
    case_compression.connect(
        "toggled", _bascule_booleen(options, session.OPT_COMPRESSION, changed)
    )

    def niveau_modifie(_bouton: Gtk.SpinButton) -> None:
        """Le niveau change : l'option suit si la compression est active."""
        logger.debug("niveau_modifie: {}", int(niveau.get_value()))
        if case_compression.get_active():
            options[session.OPT_NIVEAU_COMPRESSION] = str(int(niveau.get_value()))
            changed()
        logger.debug("niveau_modifie: fait")

    def sensibiliser_niveau(bouton: Gtk.CheckButton) -> None:
        """La compression change : le niveau redevient modifiable ou non."""
        logger.debug("sensibiliser_niveau: {}", bouton.get_active())
        niveau.set_sensitive(bouton.get_active())
        logger.debug("sensibiliser_niveau: fait")

    case_compression.connect("toggled", sensibiliser_niveau)
    niveau.connect("value-changed", niveau_modifie)
    grille.attach(case_compression, 0, ligne, 1, 1)
    grille.attach(niveau, 1, ligne, 1, 1)
    ligne += 1

    # Clé privée : chemin libre (``~`` compris, développé au lancement).
    grille.attach(Gtk.Label(label=_("Private key"), xalign=0), 0, ligne, 1, 1)
    saisie_cle = Gtk.Entry(
        hexpand=True, text=options.get(session.OPT_CLE, ""), placeholder_text="~/.ssh/id_ed25519"
    )

    def cle_modifiee(entree: Gtk.Entry) -> None:
        """La clé change : l'option suit."""
        logger.debug("cle_modifiee: {!r}", entree.get_text())
        options[session.OPT_CLE] = entree.get_text().strip()
        changed()
        logger.debug("cle_modifiee: fait")

    saisie_cle.connect("changed", cle_modifiee)
    grille.attach(saisie_cle, 1, ligne, 1, 1)
    ligne += 1

    # Options avancées : une saisie par option ``-o`` de GTK3.
    for cle_ini, option, aide in session.CHAMPS_AVANCES:
        grille.attach(Gtk.Label(label=option, xalign=0), 0, ligne, 1, 1)
        saisie = Gtk.Entry(hexpand=True, text=options.get(cle_ini, ""))
        if aide:
            saisie.set_tooltip_text(_(aide))

        def valeur_modifiee(entree: Gtk.Entry, cle_ini: str = cle_ini) -> None:
            """L'option ``-o`` change : l'option INI suit."""
            logger.debug("valeur_modifiee: {} -> {!r}", cle_ini, entree.get_text())
            options[cle_ini] = entree.get_text().strip()
            changed()
            logger.debug("valeur_modifiee: fait")

        saisie.connect("changed", valeur_modifiee)
        grille.attach(saisie, 1, ligne, 1, 1)
        ligne += 1

    defilement = Gtk.ScrolledWindow(
        child=grille,
        hscrollbar_policy=Gtk.PolicyType.NEVER,
        propagate_natural_height=True,
        max_content_height=360,
    )
    logger.debug("_page_avancee: sortie")
    return defilement


def _redirection_d_option(tunnel: str) -> core.Redirection | None:
    """Redirection d'affichage d'une entrée de l'option ``tunnel``, si elle est lisible.

    Args:
        tunnel: entrée au format GTK3 (``8080:web:80``, ``R:…``, ``8080:*:*``).

    Returns:
        La redirection, ou ``None`` si l'entrée est vide ou mal formée.
    """
    logger.debug("_redirection_d_option: {!r}", tunnel)
    brut = tunnel.strip()
    if not brut:
        logger.debug("_redirection_d_option: sortie | vide")
        return None
    type_ = "local"
    if brut.startswith("R:"):
        type_, brut = "remote", brut[2:]
    morceaux = brut.split(":", 2)
    if len(morceaux) != 3 or not morceaux[0]:
        logger.debug("_redirection_d_option: sortie | mal formée")
        return None
    if brut.endswith(":*:*"):
        resultat = core.Redirection("dynamic", morceaux[0], "", "", tunnel.strip())
    else:
        resultat = core.Redirection(type_, morceaux[0], morceaux[1], morceaux[2], tunnel.strip())
    logger.debug("_redirection_d_option: sortie | {}", resultat.brut)
    return resultat


def _page_redirections(options: dict[str, str], changed: Callable[[], None]) -> Gtk.Widget:
    """Page « Port forwarding » : formulaire d'ajout et liste des redirections.

    Reprend ``_build_port_forwarding_page`` de GTK3 : type (locale, distante,
    dynamique), ports, hôte cible, liste ; l'option ``tunnel`` est réécrite en
    entier à chaque ajout ou retrait.
    """
    logger.debug("_page_redirections: entrée | {} option(s)", len(options))
    boite = Gtk.Box(
        orientation=Gtk.Orientation.VERTICAL,
        spacing=6,
        margin_start=8,
        margin_end=8,
        margin_top=8,
        margin_bottom=8,
    )
    formulaire = Gtk.Grid(column_spacing=6, row_spacing=4)

    formulaire.attach(Gtk.Label(label=_("Type"), xalign=0), 0, 0, 1, 1)
    modeles_types = Gtk.StringList()
    for libelle in (_("Local (-L)"), _("Remote (-R)"), _("Dynamic / SOCKS (-D)")):
        modeles_types.append(libelle)
    type_choisi = Gtk.DropDown(model=modeles_types)
    formulaire.attach(type_choisi, 1, 0, 1, 1)

    libelle_local = Gtk.Label(label=_("Local Port"), xalign=0)
    formulaire.attach(libelle_local, 0, 1, 1, 1)
    port_local = Gtk.SpinButton()
    port_local.set_adjustment(Gtk.Adjustment(value=8080, lower=1, upper=65535, step_increment=1))
    formulaire.attach(port_local, 1, 1, 1, 1)

    libelle_hote = Gtk.Label(label=_("Remote Host"), xalign=0)
    formulaire.attach(libelle_hote, 0, 2, 1, 1)
    hote_cible = Gtk.Entry(hexpand=True)
    formulaire.attach(hote_cible, 1, 2, 1, 1)

    libelle_distant = Gtk.Label(label=_("Remote Port"), xalign=0)
    formulaire.attach(libelle_distant, 0, 3, 1, 1)
    port_distant = Gtk.SpinButton()
    port_distant.set_adjustment(Gtk.Adjustment(value=8080, lower=1, upper=65535, step_increment=1))
    formulaire.attach(port_distant, 1, 3, 1, 1)
    #: Le port distant a-t-il été touché ? Sinon, « hôte:port » dans le champ
    #: hôte se découpe, comme le laissait entendre le formulaire GTK3.
    distant_touche = False

    def distant_modifie(_bouton: Gtk.SpinButton) -> None:
        """Le port distant est modifié à la main : plus de découpage de l'hôte."""
        logger.debug("distant_modifie: {}", int(port_distant.get_value()))
        nonlocal distant_touche
        distant_touche = True
        logger.debug("distant_modifie: fait")

    port_distant.connect("value-changed", distant_modifie)

    def _type_courant() -> str:
        """Type sélectionné : ``local``, ``remote`` ou ``dynamic``."""
        logger.debug("_type_courant: entrée")
        resultat = ("local", "remote", "dynamic")[type_choisi.get_selected()]
        logger.debug("_type_courant: sortie | {}", resultat)
        return resultat

    def type_change(_menu: Gtk.DropDown, _param) -> None:
        """Le type change : libellés et sensibilité des champs, comme GTK3."""
        logger.debug("type_change: {}", _type_courant())
        courant = _type_courant()
        dynamique = courant == "dynamic"
        hote_cible.set_sensitive(not dynamique)
        port_distant.set_sensitive(not dynamique)
        if courant == "remote":
            libelle_local.set_text(_("Remote listen port"))
            libelle_hote.set_text(_("Local target host"))
            libelle_distant.set_text(_("Local target port"))
        else:
            libelle_local.set_text(_("Local Port"))
            libelle_hote.set_text(_("Remote Host"))
            libelle_distant.set_text(_("Remote Port"))
        logger.debug("type_change: fait")

    type_choisi.connect("notify::selected", type_change)

    liste = Gtk.ListBox()
    liste.set_vexpand(True)

    def _ecrire() -> None:
        """Réécrit l'option ``tunnel`` depuis les redirections de la liste."""
        logger.debug("_ecrire: entrée")
        tunnels = [rangée.redirection.brut for rangée in _rangées()]
        options[session.OPT_TUNNELS] = session.ecrire_tunnels(tunnels)
        logger.debug(
            "_ecrire: {} redirection(s) -> {!r}", len(tunnels), options[session.OPT_TUNNELS]
        )

    def _rangées() -> list[LigneRedirection]:
        """Lignes de redirection de la liste, dans l'ordre."""
        logger.debug("_rangées: entrée")
        enfant = liste.get_first_child()
        resultat = []
        while enfant is not None:
            if isinstance(enfant, LigneRedirection):
                resultat.append(enfant)
            enfant = enfant.get_next_sibling()
        logger.debug("_rangées: sortie | {} ligne(s)", len(resultat))
        return resultat

    for tunnel in session.lire_tunnels(options.get(session.OPT_TUNNELS, "")):
        redirection = _redirection_d_option(tunnel)
        if redirection is not None:
            liste.append(LigneRedirection(redirection))

    def ajouter(_bouton: Gtk.Button) -> None:
        """Ajoute la redirection saisie, comme GTK3 (doublon de port ignoré)."""
        logger.debug("ajouter: entrée | type={}", _type_courant())
        courant = _type_courant()
        local = str(int(port_local.get_value()))
        cible = hote_cible.get_text().strip()
        distant = str(int(port_distant.get_value()))
        if courant != "dynamic" and not distant_touche and cible.count(":") == 1:
            # « hôte:port » en un seul champ accepté, comme GTK3.
            machine, _sep, port = cible.rpartition(":")
            if port.isdigit():
                cible, distant = machine, port
        if courant == "dynamic":
            cible, distant = "*", "*"
        if cible == "":
            logger.debug("ajouter: sortie | hôte cible manquant")
            return
        if any(r.redirection.local == local for r in _rangées()):
            logger.debug("ajouter: sortie | port local déjà pris")
            return
        brut = (
            f"{local}:{cible}:{distant}" if courant != "remote" else f"R:{local}:{cible}:{distant}"
        )
        liste.append(LigneRedirection(core.Redirection(courant, local, cible, distant, brut)))
        _ecrire()
        changed()
        logger.debug("ajouter: fait | {}", brut)

    def retirer(_bouton: Gtk.Button) -> None:
        """Retire la redirection sélectionnée."""
        logger.debug("retirer: entrée")
        selectionnee = liste.get_selected_row()
        if selectionnee is not None:
            liste.remove(selectionnee)
            _ecrire()
            changed()
        logger.debug("retirer: fait")

    boutons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    bouton_ajouter = Gtk.Button(label=_("Add"))
    bouton_ajouter.connect("clicked", ajouter)
    bouton_retirer = Gtk.Button(label=_("Remove"))
    bouton_retirer.connect("clicked", retirer)
    boutons.append(bouton_ajouter)
    boutons.append(bouton_retirer)
    formulaire.attach(boutons, 0, 4, 2, 1)

    boite.append(formulaire)
    boite.append(liste)
    logger.debug("_page_redirections: sortie")
    return boite


class SshPlugin(ConnectionPlugin):
    """Protocole ``ssh`` : terminal VTE, mots de passe par expect, redirections."""

    name = "ssh"
    version = "1.0"
    protocols = ("ssh",)

    def default_port(self, protocol: str) -> int | None:
        """Port 22."""
        logger.debug("SshPlugin.default_port: {!r}", protocol)
        logger.debug("SshPlugin.default_port: {}", session.PORT_DEFAUT)
        return session.PORT_DEFAUT

    def build_host_options_widget(
        self, host: Host, options: dict[str, str], changed: Callable[[], None]
    ) -> Gtk.Widget:
        """Options ssh de l'hôte : Advanced, Port forwarding, puis zone commune."""
        logger.debug("SshPlugin.build_host_options_widget: {!r}", host.path)
        zone = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        carnet = Gtk.Notebook()
        carnet.append_page(_page_avancee(options, changed), Gtk.Label(label=_("Advanced")))
        carnet.append_page(
            _page_redirections(options, changed), Gtk.Label(label=_("Port forwarding"))
        )
        zone.append(carnet)
        zone.append(zone_options(options, changed))
        logger.debug("SshPlugin.build_host_options_widget: fait")
        return zone

    def open(self, host: Host) -> SshSession:
        """Lance ssh (ou expect) dans un terminal.

        Une clé privée renseignée mais introuvable est signalée ; ssh est
        lancé quand même, comme GTK3.

        Raises:
            FileNotFoundError: ``ssh``, ou ``expect`` pour un mot de passe,
                est introuvable dans ``PATH``.
            ValueError: paramètres supplémentaires mal formés.
        """
        logger.debug("SshPlugin.open: {!r}", host.path)
        ssh = shutil.which("ssh")
        if ssh is None:
            logger.debug("SshPlugin.open: ssh absent")
            raise FileNotFoundError(_("{program} not found").format(program="ssh"))
        options = host.protocol_options.get("ssh", {})
        cle = session.cle_absente(options)
        if cle is not None:
            logger.debug("SshPlugin.open: clé absente | {}", cle)
            self.context.notify(
                _(
                    "SSH private key file not found:\n\n{}\n\nPlease check the key path "
                    "configuration."
                ).format(cle)
            )
        try:
            lance = session.lancement(
                host.host,
                host.port,
                host.user,
                host.password,
                host.extra_params,
                options,
                ssh,
                shutil.which("expect"),
            )
        except FileNotFoundError as exc:
            logger.debug("SshPlugin.open: {} absent", exc.args[0])
            raise FileNotFoundError(_("{program} not found").format(program=exc.args[0])) from None
        session_ouverte = SshSession(host, lance, self.context.log, notifier=self.context.notify)
        logger.debug("SshPlugin.open: session ouverte")
        return session_ouverte


PLUGIN = SshPlugin
