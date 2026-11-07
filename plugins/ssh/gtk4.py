# SPDX-License-Identifier: GPL-3.0-or-later
"""Outils SSH GTK4 de gcm4 (#97) : sélecteur de clé, éditeur de
``~/.ssh/config`` (personnel + partagé) et gestionnaire de clés.

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
import threading
from collections.abc import Callable
from dataclasses import dataclass
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
from gcm4.ui import dialogues, fichiers  # noqa: E402
from gcm4.ui.terminal import SessionTerminal, zone_options  # noqa: E402

from . import core, session  # noqa: E402

__all__ = [
    "CIBLES",
    "PAGE_AUTHORIZED_KEYS",
    "PAGE_KNOWN_HOSTS",
    "PAGE_ORPHELINES",
    "PAGE_PRIVEES",
    "TAILLES_RSA",
    "TAILLE_RSA_DEFAUT",
    "TYPES_CLE",
    "TYPES_REDIRECTION",
    "Element",
    "GenererCleDialog",
    "KeyPickerDialog",
    "LigneCle",
    "LigneGestionCle",
    "LigneHote",
    "LigneRedirection",
    "PLUGIN",
    "ResultatsKnownHostsDialog",
    "SshConfigEditor",
    "SshKeyManager",
    "SshPlugin",
    "SshSession",
    "SshTestDialog",
    "Tableau",
    "VuePubliqueDialog",
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
            gestionnaire: Ouvre le gestionnaire de clés ; ``None`` ouvre un
                :class:`SshKeyManager` transitoire de l'éditeur (un seul).
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
        self._gestionnaire = gestionnaire if gestionnaire is not None else self.ouvrir_gestionnaire
        self.gestionnaire_cles: SshKeyManager | None = None
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

    def ouvrir_gestionnaire(self) -> None:
        """« ⚙ » par défaut : présente le gestionnaire de clés (créé au besoin).

        Une seule fenêtre par éditeur ; fermée, elle est recréée au clic
        suivant.
        """
        logger.debug("SshConfigEditor.ouvrir_gestionnaire: entrée")
        if self.gestionnaire_cles is None:
            self.gestionnaire_cles = SshKeyManager(parent=self)
            self.gestionnaire_cles.connect("close-request", self._on_gestionnaire_ferme)
        self.gestionnaire_cles.present()
        logger.debug("SshConfigEditor.ouvrir_gestionnaire: sortie")

    def _on_gestionnaire_ferme(self, _fenetre: Gtk.Window) -> bool:
        """Gestionnaire fermé : le prochain « ⚙ » en ouvre un nouveau."""
        logger.debug("SshConfigEditor._on_gestionnaire_ferme: entrée")
        self.gestionnaire_cles = None
        logger.debug("SshConfigEditor._on_gestionnaire_ferme: sortie")
        return False

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


# ---------------------------------------------------------------------------
# Gestionnaire de clés (#97, 3/3) — portage de ssh_key_manager_dialog.py
# ---------------------------------------------------------------------------

#: Types proposés à la génération (``dsa`` : refusé par OpenSSH ≥ 9.8).
TYPES_CLE: tuple[str, ...] = ("ed25519", "rsa", "ecdsa", "ed25519-sk", "ecdsa-sk")

#: Tailles RSA proposées (``ssh-keygen`` refuse moins de 1024 bits).
TAILLES_RSA: tuple[str, ...] = ("2048", "3072", "4096", "8192", "16384")

#: Taille RSA par défaut (index dans :data:`TAILLES_RSA`).
TAILLE_RSA_DEFAUT = 2

#: Onglets du gestionnaire, dans l'ordre.
PAGE_PRIVEES, PAGE_ORPHELINES, PAGE_KNOWN_HOSTS, PAGE_AUTHORIZED_KEYS = range(4)


class Element(GObject.Object):
    """Valeur Python quelconque rangée dans un ``Gio.ListStore``."""

    def __init__(self, valeur: object) -> None:
        """Enveloppe ``valeur``."""
        logger.trace("Element.__init__")
        super().__init__()
        self.valeur = valeur
        logger.trace("__init__: sortie")


def _comparer(texte: Callable[[object], str]) -> Callable[[Element, Element, object], int]:
    """Comparateur de :class:`Element` sur ``texte(valeur)`` (tri des colonnes)."""
    logger.trace("_comparer")

    def comparer(premier: Element, second: Element, _donnees: object) -> int:
        logger.trace("comparer: entrée")
        a, b = texte(premier.valeur), texte(second.valeur)
        logger.trace("comparer: sortie")
        return (a > b) - (a < b)

    logger.trace("_comparer: sortie")
    return comparer


class Tableau:
    """``Gtk.ColumnView`` à sélection simple, colonnes texte triables.

    Remplace les ``Gtk.TreeView`` + ``Gtk.ListStore`` de la version GTK3.
    """

    def __init__(
        self,
        colonnes: list[tuple[str, Callable[[object], str], bool]],
        alerte: Callable[[object], bool] | None = None,
        coche: tuple[str, Callable[[object], bool], Callable[[object], None]] | None = None,
    ) -> None:
        """Construit la vue.

        Args:
            colonnes: ``(titre, texte de la cellule, extensible)`` par colonne.
            alerte: Ligne à signaler (classe CSS ``error``, ex. clé expirée).
            coche: Première colonne à case à cocher : ``(titre, état, rappel au clic)`` ;
                la case reprend son état, le rappel décide de la suite.
        """
        logger.debug("Tableau.__init__: entrée | {} colonne(s)", len(colonnes))
        self.modele = Gio.ListStore.new(Element)
        self.vue = Gtk.ColumnView(vexpand=True)
        trie = Gtk.SortListModel.new(self.modele, self.vue.get_sorter())
        self.selection = Gtk.SingleSelection(autoselect=False, can_unselect=True)
        self.selection.set_model(trie)
        self.vue.set_model(self.selection)
        self._alerte = alerte
        if coche is not None:
            self.vue.append_column(self._colonne_coche(*coche))
        for titre, texte, extensible in colonnes:
            self.vue.append_column(self._colonne_texte(titre, texte, extensible))
        self.defilement = Gtk.ScrolledWindow(child=self.vue, vexpand=True, has_frame=True)
        self.selection.set_selected(Gtk.INVALID_LIST_POSITION)
        logger.debug("Tableau.__init__: sortie")

    def _colonne_texte(
        self, titre: str, texte: Callable[[object], str], extensible: bool
    ) -> Gtk.ColumnViewColumn:
        """Colonne d'étiquettes, triable sur son texte."""
        logger.debug("_colonne_texte: entrée | {}", titre)
        fabrique = Gtk.SignalListItemFactory()

        def preparer(_fabrique: Gtk.SignalListItemFactory, item: Gtk.ListItem) -> None:
            logger.trace("preparer: entrée")
            item.set_child(Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END))
            logger.trace("preparer: sortie")

        def lier(_fabrique: Gtk.SignalListItemFactory, item: Gtk.ListItem) -> None:
            logger.trace("lier: entrée")
            valeur = item.get_item().valeur
            etiquette = item.get_child()
            etiquette.set_text(texte(valeur))
            etiquette.set_tooltip_text(texte(valeur) or None)
            if self._alerte is not None and self._alerte(valeur):
                etiquette.add_css_class("error")
            else:
                etiquette.remove_css_class("error")
            logger.trace("lier: sortie")

        fabrique.connect("setup", preparer)
        fabrique.connect("bind", lier)
        colonne = Gtk.ColumnViewColumn(
            title=titre, factory=fabrique, expand=extensible, resizable=True
        )
        colonne.set_sorter(Gtk.CustomSorter.new(_comparer(texte), None))
        logger.debug("_colonne_texte: sortie")
        return colonne

    def _colonne_coche(
        self, titre: str, etat: Callable[[object], bool], rappel: Callable[[object], None]
    ) -> Gtk.ColumnViewColumn:
        """Colonne de cases à cocher ; un clic appelle ``rappel(valeur)``."""
        logger.debug("_colonne_coche: entrée | {}", titre)
        fabrique = Gtk.SignalListItemFactory()

        def basculee(case: Gtk.CheckButton) -> None:
            logger.trace("basculee: entrée")
            if case.liaison:
                logger.trace("basculee: sortie")
                return
            case.liaison = True
            case.set_active(etat(case.valeur))  # l'état ne change qu'après écriture
            case.liaison = False
            rappel(case.valeur)
            logger.trace("basculee: sortie")

        def preparer(_fabrique: Gtk.SignalListItemFactory, item: Gtk.ListItem) -> None:
            logger.trace("preparer: entrée")
            case = Gtk.CheckButton(halign=Gtk.Align.CENTER)
            case.liaison = False
            case.valeur = None
            case.connect("toggled", basculee)
            item.set_child(case)
            logger.trace("preparer: sortie")

        def lier(_fabrique: Gtk.SignalListItemFactory, item: Gtk.ListItem) -> None:
            logger.trace("lier: entrée")
            case = item.get_child()
            case.valeur = item.get_item().valeur
            case.liaison = True
            case.set_active(etat(case.valeur))
            case.liaison = False
            logger.trace("lier: sortie")

        fabrique.connect("setup", preparer)
        fabrique.connect("bind", lier)
        colonne = Gtk.ColumnViewColumn(title=titre, factory=fabrique)
        colonne.set_sorter(Gtk.CustomSorter.new(_comparer(lambda v: str(etat(v))), None))
        logger.debug("_colonne_coche: sortie")
        return colonne

    def remplir(self, valeurs: list[object]) -> None:
        """Remplace le contenu ; plus rien n'est sélectionné."""
        logger.debug("remplir: entrée | {}", len(valeurs))
        self.modele.splice(0, self.modele.get_n_items(), [Element(v) for v in valeurs])
        self.selection.set_selected(Gtk.INVALID_LIST_POSITION)
        logger.debug("remplir: sortie")

    def valeurs(self) -> list[object]:
        """Valeurs dans l'ordre affiché (tri compris)."""
        logger.debug("valeurs: entrée")
        modele = self.selection.get_model()
        resultat = [modele.get_item(i).valeur for i in range(modele.get_n_items())]
        logger.debug("valeurs: sortie | {}", len(resultat))
        return resultat

    def choisie(self) -> object | None:
        """Valeur sélectionnée, ou ``None``."""
        logger.debug("choisie: entrée")
        item = self.selection.get_selected_item()
        valeur = item.valeur if item is not None else None
        logger.debug("choisie: sortie | {}", valeur is not None)
        return valeur


def _texte_tampon(vue: Gtk.TextView) -> str:
    """Texte complet d'un ``Gtk.TextView``."""
    logger.trace("_texte_tampon")
    tampon = vue.get_buffer()
    logger.trace("_texte_tampon: sortie")
    return tampon.get_text(tampon.get_start_iter(), tampon.get_end_iter(), False)


def _raccourci(fenetre: Gtk.Widget, declencheur: str, rappel: Callable[..., bool]) -> None:
    """Ajoute le raccourci ``declencheur`` (« Escape », « <Control>n »…) à ``fenetre``."""
    logger.trace("_raccourci: {}", declencheur)
    controleur = Gtk.ShortcutController()
    controleur.add_shortcut(
        Gtk.Shortcut(
            trigger=Gtk.ShortcutTrigger.parse_string(declencheur),
            action=Gtk.CallbackAction.new(rappel),
        )
    )
    fenetre.add_controller(controleur)
    logger.trace("_raccourci: sortie")


class GenererCleDialog(Gtk.Window):
    """Paramètres de ``ssh-keygen`` (et de la signature par la CA désignée)."""

    def __init__(
        self,
        parent: Gtk.Window,
        ca: str | None,
        rappel: Callable[[dict[str, object]], None],
    ) -> None:
        """Construit le formulaire.

        Args:
            parent: Gestionnaire de clés (modale).
            ca: Nom de la CA désignée ; ``None`` masque la signature.
            rappel: Reçoit :meth:`options` quand l'utilisateur valide.
        """
        logger.debug("GenererCleDialog.__init__: entrée | ca={}", ca is not None)
        super().__init__(
            title=_("Generate SSH Key"),
            transient_for=parent,
            modal=True,
            destroy_with_parent=True,
            default_width=440,
        )
        self._rappel = rappel
        commentaire = core.commentaire_par_defaut()
        grille = Gtk.Grid(
            column_spacing=10,
            row_spacing=8,
            margin_top=12,
            margin_bottom=12,
            margin_start=16,
            margin_end=16,
        )

        def ligne(rang: int, libelle: str, champ: Gtk.Widget) -> Gtk.Label:
            logger.trace("ligne: entrée")
            etiquette = Gtk.Label(label=libelle, xalign=1)
            grille.attach(etiquette, 0, rang, 1, 1)
            grille.attach(champ, 1, rang, 1, 1)
            logger.trace("ligne: sortie")
            return etiquette

        self.type = Gtk.DropDown.new_from_strings(list(TYPES_CLE))
        self.type.connect("notify::selected", self._on_type)
        ligne(0, _("Key type:"), self.type)
        self.taille = Gtk.DropDown.new_from_strings(list(TAILLES_RSA))
        self.taille.set_selected(TAILLE_RSA_DEFAUT)
        self.etiquette_taille = ligne(1, _("RSA size:"), self.taille)
        self.nom = Gtk.Entry(text="id_ed25519", hexpand=True)
        self.nom.set_tooltip_text(_("Saved in ~/.ssh/ — leave blank for default name"))
        ligne(2, _("File name:"), self.nom)
        self.commentaire = Gtk.Entry(text=commentaire, hexpand=True)
        ligne(3, _("Comment:"), self.commentaire)
        self.phrase = Gtk.PasswordEntry(
            show_peek_icon=True, hexpand=True, placeholder_text=_("(empty = no passphrase)")
        )
        ligne(4, _("Passphrase:"), self.phrase)
        info = Gtk.Label(xalign=0, wrap=True)
        info.set_markup(
            "<small><i>"
            + _("Keys are saved in <b>~/.ssh/</b> with permissions 600.")
            + "</i></small>"
        )
        grille.attach(info, 0, 5, 2, 1)

        self.signer = Gtk.CheckButton(
            label=_("Sign with designated CA ({name})").format(name=ca or ""),
            visible=ca is not None,
        )
        self.signer.connect("toggled", self._on_signer)
        grille.attach(self.signer, 0, 6, 2, 1)
        self.identite = Gtk.Entry(text=commentaire, hexpand=True)
        self.principaux = Gtk.Entry(text=commentaire.split("@", 1)[0], hexpand=True)
        self.validite = Gtk.Entry(text="+52w", hexpand=True)
        self.validite.set_tooltip_text(
            _("ssh-keygen -V syntax, e.g. +52w (1 year), +30d, or 'always:forever'")
        )
        self.champs_signature: list[Gtk.Widget] = []
        for rang, (libelle, champ) in enumerate(
            (
                (_("Certificate identity:"), self.identite),
                (_("Principals (comma-separated):"), self.principaux),
                (_("Validity:"), self.validite),
            ),
            start=7,
        ):
            self.champs_signature += [ligne(rang, libelle, champ), champ]

        boutons = Gtk.Box(spacing=6, halign=Gtk.Align.END, margin_top=6)
        annuler = Gtk.Button(label=_("_Cancel"), use_underline=True)
        annuler.connect("clicked", lambda _b: self.close())
        self.bouton_generer = Gtk.Button(label=_("⚙ Generate"))
        self.bouton_generer.add_css_class("suggested-action")
        self.bouton_generer.connect("clicked", self._on_generer)
        boutons.append(annuler)
        boutons.append(self.bouton_generer)
        grille.attach(boutons, 0, 10, 2, 1)
        self.set_child(grille)
        _raccourci(self, "Escape", lambda *_a: self.close() or True)
        self._on_type(self.type, None)
        self._on_signer(self.signer)
        logger.debug("GenererCleDialog.__init__: sortie")

    def type_choisi(self) -> str:
        """Type de clé sélectionné."""
        logger.debug("type_choisi: entrée")
        type_ = TYPES_CLE[self.type.get_selected()]
        logger.debug("type_choisi: sortie | {}", type_)
        return type_

    def _on_type(self, _choix: Gtk.DropDown, _pspec: GObject.ParamSpec | None) -> None:
        """Taille RSA visible pour ``rsa`` seulement ; nom de fichier ``id_<type>``."""
        logger.debug("_on_type: entrée")
        type_ = self.type_choisi()
        self.etiquette_taille.set_visible(type_ == "rsa")
        self.taille.set_visible(type_ == "rsa")
        self.nom.set_text(f"id_{type_}")
        logger.debug("_on_type: sortie")

    def _on_signer(self, case: Gtk.CheckButton) -> None:
        """Champs du certificat visibles quand la signature est cochée."""
        logger.debug("_on_signer: entrée | {}", case.get_active())
        for champ in self.champs_signature:
            champ.set_visible(case.get_active())
        logger.debug("_on_signer: sortie")

    def options(self) -> dict[str, object]:
        """Paramètres saisis (valeurs par défaut pour les champs vides)."""
        logger.debug("options: entrée")
        type_ = self.type_choisi()
        commentaire = self.commentaire.get_text().strip() or core.commentaire_par_defaut()
        options: dict[str, object] = {
            "type": type_,
            "taille": int(TAILLES_RSA[self.taille.get_selected()]) if type_ == "rsa" else 0,
            "nom": self.nom.get_text().strip() or f"id_{type_}",
            "commentaire": commentaire,
            "phrase": self.phrase.get_text(),
            "signer": self.signer.get_visible() and self.signer.get_active(),
            "identite": self.identite.get_text().strip() or commentaire,
            "principaux": self.principaux.get_text().strip() or commentaire.split("@", 1)[0],
            "validite": self.validite.get_text().strip() or "+52w",
        }
        logger.debug("options: sortie | type={} signer={}", type_, options["signer"])
        return options

    def _on_generer(self, _bouton: Gtk.Button) -> None:
        """« ⚙ Generate » : transmet les options et ferme."""
        logger.debug("_on_generer: entrée")
        options = self.options()
        self.close()
        self._rappel(options)
        logger.debug("_on_generer: sortie")


class VuePubliqueDialog(Gtk.Window):
    """Contenu complet d'une clé publique, avec copie (non modale)."""

    def __init__(self, parent: Gtk.Window, nom: str, contenu: str) -> None:
        """Affiche ``contenu`` (clé ``nom``)."""
        logger.debug("VuePubliqueDialog.__init__: entrée | {}", nom)
        super().__init__(
            title=_("Public Key — {name}").format(name=nom),
            transient_for=parent,
            destroy_with_parent=True,
            default_width=640,
            default_height=220,
        )
        self.contenu = contenu
        boite = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=6,
            margin_top=8,
            margin_bottom=8,
            margin_start=8,
            margin_end=8,
        )
        self.texte = Gtk.TextView(editable=False, wrap_mode=Gtk.WrapMode.CHAR, monospace=True)
        self.texte.get_buffer().set_text(contenu)
        boite.append(Gtk.ScrolledWindow(child=self.texte, vexpand=True, has_frame=True))
        boutons = Gtk.Box(spacing=6, halign=Gtk.Align.END)
        self.bouton_copier = Gtk.Button(label=_("⎘ Copy to clipboard"))
        self.bouton_copier.connect("clicked", lambda _b: self.get_clipboard().set(self.contenu))
        fermer = Gtk.Button(label=_("_Close"), use_underline=True)
        fermer.connect("clicked", lambda _b: self.close())
        boutons.append(self.bouton_copier)
        boutons.append(fermer)
        boite.append(boutons)
        self.set_child(boite)
        _raccourci(self, "Escape", lambda *_a: self.close() or True)
        logger.debug("VuePubliqueDialog.__init__: sortie")


class ResultatsKnownHostsDialog(Gtk.Window):
    """Résultat du test des hôtes connus contre ``known_hosts``."""

    def __init__(
        self, parent: Gtk.Window, cible: str, resultats: list[tuple[str, bool, str]]
    ) -> None:
        """Affiche ``resultats`` (``(hôte, trouvé, ligne)``) pour la cible ``cible``."""
        logger.debug("ResultatsKnownHostsDialog.__init__: entrée | {}", len(resultats))
        trouves = sum(1 for _h, trouve, _i in resultats if trouve)
        super().__init__(
            title=_("Known Hosts Test — {n}/{total} found on {target}").format(
                n=trouves, total=len(resultats), target=cible
            ),
            transient_for=parent,
            modal=True,
            destroy_with_parent=True,
            default_width=720,
            default_height=420,
        )
        self.tableau = Tableau(
            [
                (_("Host"), lambda r: r[0], True),
                (_("Found"), lambda r: "✓" if r[1] else "✗", False),
                (_("Info (matching known_hosts line)"), lambda r: r[2], True),
            ],
            alerte=lambda r: not r[1],
        )
        self.tableau.remplir(list(resultats))
        boite = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=6,
            margin_top=8,
            margin_bottom=8,
            margin_start=8,
            margin_end=8,
        )
        boite.append(self.tableau.defilement)
        fermer = Gtk.Button(label=_("_Close"), use_underline=True, halign=Gtk.Align.END)
        fermer.connect("clicked", lambda _b: self.close())
        boite.append(fermer)
        self.set_child(boite)
        _raccourci(self, "Escape", lambda *_a: self.close() or True)
        logger.debug("ResultatsKnownHostsDialog.__init__: sortie")


@dataclass(frozen=True)
class LigneGestionCle:
    """Une clé de l'onglet Private keys ou Orphan Public keys."""

    cle: core.CleSsh
    details: core.DetailsCle

    @property
    def publique(self) -> Path | None:
        """Fichier de clé publique à copier ou afficher."""
        logger.trace("LigneGestionCle.publique")
        return self.cle.chemin if self.cle.est_publique else self.cle.publique

    @property
    def expiree(self) -> bool:
        """Certificat expiré."""
        logger.trace("LigneGestionCle.expiree")
        return self.details.certificat is not None and self.details.certificat.expire

    def validite(self) -> str:
        """Colonne « Validity »."""
        logger.trace("LigneGestionCle.validite")
        certificat = self.details.certificat
        if certificat is None:
            logger.trace("validite: sortie")
            return "—"
        if certificat.fin is None:
            logger.trace("validite: sortie")
            return _("forever")
        if certificat.fin == "?":
            logger.trace("validite: sortie")
            return "?"
        logger.trace("validite: sortie")
        return _("until {end}").format(end=certificat.fin)

    def signataire(self) -> str:
        """Colonne « Signed by »."""
        logger.trace("LigneGestionCle.signataire")
        if self.details.certificat is None:
            logger.trace("signataire: sortie")
            return "—"
        logger.trace("signataire: sortie")
        return self.details.certificat.ca or _("unknown CA")

    def creation(self) -> str:
        """Colonne « Created »."""
        logger.trace("LigneGestionCle.creation")
        date = self.details.creation
        logger.trace("creation: sortie")
        return date.strftime("%Y-%m-%d %H:%M") if date is not None else "?"

    def permissions(self) -> str:
        """Colonne « Perms »."""
        logger.trace("LigneGestionCle.permissions")
        return f"{self.cle.permissions} {'✓' if self.cle.permissions_ok else '⚠'}"


def _colonnes_cles() -> list[tuple[str, Callable[[object], str], bool]]:
    """Colonnes communes des onglets Private keys et Orphan Public keys."""
    logger.trace("_colonnes_cles")
    return [
        (_("Name"), lambda ligne: ligne.cle.nom, True),
        (_("Type"), lambda ligne: ligne.details.type, False),
        (_("Fingerprint"), lambda ligne: ligne.cle.empreinte, True),
        (_("Created"), LigneGestionCle.creation, False),
        (_("Validity"), LigneGestionCle.validite, False),
        (_("Signed by"), LigneGestionCle.signataire, True),
        (_("Perms"), LigneGestionCle.permissions, False),
    ]


class SshKeyManager(Gtk.Window):
    """Gestionnaire de clés SSH (portage GTK4 de ``ssh_key_manager_dialog.py``).

    Quatre onglets : clés privées et publiques orphelines de ``~/.ssh/``
    (toujours locales), ``known_hosts`` et ``authorized_keys`` de la cible
    (machine locale, ou compte distant ``user@hôte[:port]`` par ssh). Barre
    de la CA de signature, barre d'actions propre à l'onglet affiché,
    messages en toasts. Les accès à la cible (test de connexion, lecture,
    écriture), la génération de clé et le test des known_hosts tournent
    dans un fil d'exécution : l'interface ne gèle pas.
    """

    def __init__(
        self,
        parent: Gtk.Window | None = None,
        dossier: Path | None = None,
        hotes_gcm: Callable[[], list[str]] | None = None,
    ) -> None:
        """Construit la fenêtre et charge les quatre onglets.

        Args:
            parent: Fenêtre parente.
            dossier: Dossier des clés (``~/.ssh/`` par défaut).
            hotes_gcm: Hôtes de gcm4 à tester contre ``known_hosts``, en plus
                des alias de ``~/.ssh/config``.
        """
        logger.debug("SshKeyManager.__init__: entrée")
        super().__init__(
            title=_("SSH Key Manager"),
            destroy_with_parent=True,
            default_width=1180,
            default_height=640,
        )
        if parent is not None:
            self.set_transient_for(parent)
        self.dossier = dossier if dossier is not None else Path.home() / ".ssh"
        self.config_ca = self.dossier / ".gcm_ca_config.json"
        self.local = core.FichiersLocaux(self.dossier)
        self.distant: core.FichiersDistants | None = None
        self._hotes_gcm = hotes_gcm
        self.ca = core.lire_config_ca(self.config_ca)
        self.travaux = 0
        self.ferme = False
        self.dialogue: object | None = None
        self.messages_cible: dict[int, str] = {}

        racine = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        racine.append(self._construire_cible())
        racine.append(Gtk.Separator())
        racine.append(self._construire_ca())
        racine.append(Gtk.Separator())
        self.onglets = Gtk.Notebook(margin_start=8, margin_end=8, margin_top=8, vexpand=True)
        self.privees = Tableau(
            _colonnes_cles()
            + [
                (
                    _("Orphan Public key"),
                    lambda ligne: "✓ .pub" if ligne.cle.publique else "✗ none",
                    False,
                )
            ],
            alerte=lambda ligne: ligne.expiree,
        )
        self.orphelines = Tableau(_colonnes_cles(), alerte=lambda ligne: ligne.expiree)
        self.known_hosts = Tableau(
            [
                (_("#"), lambda e: f"{e.ligne:6d}", False),
                (_("Host"), lambda e: _("(hashed)") if e.hache else e.hotes, True),
                (_("Type"), lambda e: e.type, False),
                (_("Fingerprint"), lambda e: e.empreinte, True),
                (_("Hashed"), lambda e: "✓" if e.hache else "", False),
            ]
        )
        self.authorized_keys = Tableau(
            [
                (_("Type"), lambda e: e.type, False),
                (_("Fingerprint"), lambda e: e.empreinte, True),
                (_("Comment"), lambda e: e.commentaire, True),
            ],
            coche=(_("Enabled"), lambda e: e.active, self._on_basculer_authorized_key),
        )
        for tableau, titre in (
            (self.privees, _("Private keys")),
            (self.orphelines, _("Orphan Public keys")),
            (self.known_hosts, _("Known Hosts")),
            (self.authorized_keys, _("Authorized Keys")),
        ):
            self.onglets.append_page(tableau.defilement, Gtk.Label(label=titre))
        for tableau in (self.privees, self.orphelines):
            tableau.selection.connect("notify::selected", lambda *_a: self.actualiser_boutons())
        racine.append(self.onglets)
        racine.append(Gtk.Separator())
        racine.append(
            Gtk.ScrolledWindow(
                child=self._construire_actions(), vscrollbar_policy=Gtk.PolicyType.NEVER
            )
        )
        self.toasts = Adw.ToastOverlay(child=racine)
        self.set_child(self.toasts)
        self.onglets.connect("switch-page", self._on_onglet)

        _raccourci(self, "Escape", lambda *_a: self.close() or True)
        _raccourci(self, "<Control>n", lambda *_a: self._on_generer(None) or True)
        _raccourci(self, "<Control>i", lambda *_a: self._on_importer(None) or True)
        _raccourci(self, "Delete", lambda *_a: self._on_supprimer_page() or True)
        self.connect("close-request", self._on_fermeture)

        self.afficher_ca()
        self.charger_cles()
        self.charger_cible()
        self._on_onglet(self.onglets, None, PAGE_PRIVEES)
        logger.info("SshKeyManager | ouvert dossier={}", self.dossier)
        logger.debug("SshKeyManager.__init__: sortie")

    # -- construction -------------------------------------------------------

    def _construire_cible(self) -> Gtk.Widget:
        """Barre de la cible de Known Hosts / Authorized Keys."""
        logger.debug("_construire_cible: entrée")
        barre = Gtk.Box(spacing=6, margin_top=6, margin_bottom=6, margin_start=8, margin_end=8)
        barre.append(Gtk.Label(label=_("Remote target (Known Hosts / Authorized Keys):")))
        self.saisie_cible = Gtk.Entry(
            hexpand=True, placeholder_text=_("user@host or root@host[:port]")
        )
        self.saisie_cible.connect("activate", self._on_connecter)
        barre.append(self.saisie_cible)
        self.bouton_connecter = self._bouton(barre, _("🔌 Connect"), self._on_connecter)
        self.bouton_local = self._bouton(barre, _("🏠 Local"), self._on_local)
        self.etat_cible = Gtk.Label(margin_start=6)
        barre.append(self.etat_cible)
        self._afficher_cible()
        logger.debug("_construire_cible: sortie")
        return barre

    def _construire_ca(self) -> Gtk.Widget:
        """Barre de la CA de signature (toujours locale)."""
        logger.debug("_construire_ca: entrée")
        barre = Gtk.Box(spacing=6, margin_top=6, margin_bottom=6, margin_start=8, margin_end=8)
        barre.append(Gtk.Label(label=_("Signing CA:")))
        self.etat_ca = Gtk.Label(hexpand=True, xalign=0)
        barre.append(self.etat_ca)
        self.bouton_designer_ca = self._bouton(
            barre,
            _("🔏 Designate"),
            self._on_designer_ca,
            _("Choose an existing local key pair as the signing CA"),
        )
        self.bouton_importer_ca = self._bouton(
            barre,
            _("⬆ Import CA"),
            self._on_importer_ca,
            _("Import an external CA key pair into ~/.ssh/ and designate it"),
        )
        self.bouton_supprimer_ca = self._bouton(
            barre,
            _("🗑 Delete CA"),
            self._on_supprimer_ca,
            _("Permanently delete the designated CA key pair"),
            destructif=True,
        )
        logger.debug("_construire_ca: sortie")
        return barre

    @staticmethod
    def _bouton(
        boite: Gtk.Box,
        libelle: str,
        rappel: Callable[[Gtk.Button], None],
        info: str | None = None,
        destructif: bool = False,
    ) -> Gtk.Button:
        """Ajoute un bouton d'action à ``boite``."""
        logger.trace("_bouton: {}", libelle)
        bouton = Gtk.Button(label=libelle, tooltip_text=info)
        if destructif:
            bouton.add_css_class("destructive-action")
        bouton.connect("clicked", rappel)
        boite.append(bouton)
        logger.trace("_bouton: sortie")
        return bouton

    def _construire_actions(self) -> Gtk.Widget:
        """Barre d'actions : un groupe par type d'onglet, et « Close »."""
        logger.debug("_construire_actions: entrée")
        barre = Gtk.Box(spacing=6, margin_top=6, margin_bottom=6, margin_start=8, margin_end=8)
        b = self._bouton
        self.actions_cles = boite = Gtk.Box(spacing=6)
        self.bouton_generer = b(
            boite,
            _("⚙ Generate"),
            self._on_generer,
            _("Generate a new SSH key pair (ssh-keygen)"),
        )
        self.bouton_importer = b(
            boite,
            _("↑ Import"),
            self._on_importer,
            _("Import an existing private key file into ~/.ssh/"),
        )
        self.bouton_copier = b(
            boite,
            _("⎘ Copy public key"),
            self._on_copier_publique,
            _("Copy the public key content to clipboard"),
        )
        self.bouton_voir = b(
            boite,
            _("👁 View public key"),
            self._on_voir_publique,
            _("Show the full public key content in a dedicated window"),
        )
        self.bouton_renommer = b(
            boite,
            _("✎ Rename"),
            self._on_renommer,
            _("Rename this key (and its .pub / certificate, if any)"),
        )
        self.bouton_phrase = b(
            boite,
            _("🔒 Passphrase"),
            self._on_phrase,
            _("Change the passphrase of this private key (ssh-keygen -p)"),
        )
        self.bouton_agent = b(
            boite,
            _("🔑 Agent"),
            self._on_agent,
            _("Load/unload this key in ssh-agent (ssh-add / ssh-add -d)"),
        )
        self.bouton_reveler = b(
            boite, _("📂 Reveal"), self._on_reveler, _("Open ~/.ssh/ in the file manager")
        )
        self.bouton_supprimer = b(
            boite,
            _("🗑 Delete"),
            self._on_supprimer,
            _("Permanently delete the selected key"),
            destructif=True,
        )

        self.actions_known_hosts = boite = Gtk.Box(spacing=6)
        self.bouton_rafraichir_kh = b(boite, _("⟳ Refresh"), lambda _b: self.charger_cible())
        self.bouton_copier_empreinte = b(boite, _("⎘ Copy fingerprint"), self._on_copier_empreinte)
        self.bouton_tester_kh = b(
            boite,
            _("🧪 Test known hosts"),
            self._on_tester_known_hosts,
            _("Check ~/.ssh/config (and GCM hosts, if wired in) against known_hosts"),
        )
        self.bouton_supprimer_kh = b(
            boite, _("🗑 Delete"), self._on_supprimer_known_host, destructif=True
        )

        self.actions_authorized_keys = boite = Gtk.Box(spacing=6)
        self.bouton_rafraichir_ak = b(boite, _("⟳ Refresh"), lambda _b: self.charger_cible())
        self.bouton_ajouter_ak = b(boite, _("＋ Add"), self._on_ajouter_authorized_key)
        self.bouton_supprimer_ak = b(
            boite, _("🗑 Delete"), self._on_supprimer_authorized_key, destructif=True
        )

        for groupe in (self.actions_cles, self.actions_known_hosts, self.actions_authorized_keys):
            barre.append(groupe)
        self.bouton_fermer = Gtk.Button(label=_("Close"), hexpand=True, halign=Gtk.Align.END)
        self.bouton_fermer.connect("clicked", lambda _b: self.close())
        barre.append(self.bouton_fermer)
        logger.debug("_construire_actions: sortie")
        return barre

    # -- outils -------------------------------------------------------------

    def notifier(self, message: str) -> None:
        """Message éphémère ; les messages se succèdent dans le ToastOverlay.

        Le toast précédent n'est jamais écarté à la main : ``dismiss()`` sur un
        toast en cours d'affichage fait planter libadwaita 1.2 à la fermeture de
        la fenêtre (``adw_animation_skip`` sur un objet libéré, Debian 12) —
        le ToastOverlay enchaîne lui-même les messages de sa file.
        """
        logger.debug("notifier: entrée | {}", message)
        toast = Adw.Toast.new(message)
        toast.set_timeout(4)
        self.toasts.add_toast(toast)
        self.dernier_message = message
        logger.debug("notifier: sortie")

    def confirmer(self, corps: str, suite: Callable[[], None]) -> None:
        """Question (``corps`` en balisage Pango) ; ``suite()`` si confirmée."""
        logger.debug("confirmer: entrée")

        def reponse(identifiant: str) -> None:
            logger.debug("confirmer.reponse: {}", identifiant)
            self.dialogue = None
            if identifiant == "ok":
                suite()
            logger.trace("reponse: sortie")

        dialogue = dialogues.creer(
            self,
            _("Confirm"),
            corps,
            [("cancel", _("_Cancel"), None), ("ok", _("Confirm"), None)],
            "ok",
            "cancel",
            reponse,
        )
        dialogue.set_body_use_markup(True)
        self.dialogue = dialogue
        logger.debug("confirmer: sortie")

    def demander(
        self,
        titre: str,
        corps: str,
        bouton: str,
        extra: Gtk.Widget,
        suite: Callable[[], None],
    ) -> None:
        """Dialogue de saisie (``extra``) ; ``suite()`` si validé par ``bouton``."""
        logger.debug("demander: entrée | {}", titre)

        def reponse(identifiant: str) -> None:
            logger.debug("demander.reponse: {}", identifiant)
            self.dialogue = None
            if identifiant == "ok":
                suite()
            logger.trace("reponse: sortie")

        self.dialogue = dialogues.creer(
            self,
            titre,
            corps,
            [
                ("cancel", _("_Cancel"), None),
                ("ok", bouton, Adw.ResponseAppearance.SUGGESTED),
            ],
            "ok",
            "cancel",
            reponse,
            extra=extra,
        )
        logger.debug("demander: sortie")

    def en_fond(
        self,
        travail: Callable[[], object],
        fin: Callable[[object, core.ErreurCle | None], None],
    ) -> None:
        """``travail()`` dans un fil d'exécution, puis ``fin(résultat, erreur)`` dans GTK.

        ``fin`` n'est pas appelé si la fenêtre a été fermée entre-temps.
        """
        logger.debug("en_fond: entrée | {} en cours", self.travaux)
        self.travaux += 1

        def terminer(resultat: object, erreur: core.ErreurCle | None) -> bool:
            logger.debug("en_fond.terminer: erreur={}", erreur is not None)
            self.travaux -= 1
            if not self.ferme:
                fin(resultat, erreur)
            logger.trace("terminer: sortie")
            return GLib.SOURCE_REMOVE

        def executer() -> None:
            logger.debug("en_fond.executer: début")
            try:
                resultat, erreur = travail(), None
            except core.ErreurCle as exc:
                logger.warning("en_fond.executer: {}", exc)
                resultat, erreur = None, exc
            GLib.idle_add(terminer, resultat, erreur)
            logger.trace("executer: sortie")

        threading.Thread(target=executer, daemon=True).start()
        logger.debug("en_fond: sortie")

    def _on_fermeture(self, _fenetre: Gtk.Window) -> bool:
        """Les travaux encore en cours n'afficheront plus rien ; le dialogue ouvert est fermé.

        Le dialogue est détruit avant la fenêtre : laissé à
        ``destroy_with_parent``, un dialogue encore affiché (résultats du test
        de ``known_hosts``…) fait planter GTK 4.8 (Debian 12).
        """
        logger.debug("_on_fermeture: entrée | {} travail(aux)", self.travaux)
        self.ferme = True
        dialogue, self.dialogue = self.dialogue, None
        if isinstance(dialogue, Gtk.Window) and dialogue.get_visible():
            dialogue.destroy()
        logger.debug("_on_fermeture: sortie")
        return False

    def fichiers(self) -> core.FichiersLocaux | core.FichiersDistants:
        """Fichiers de la cible active (distante si connectée)."""
        logger.trace("fichiers")
        return self.distant if self.distant is not None else self.local

    @staticmethod
    def libelle(cible: core.FichiersLocaux | core.FichiersDistants) -> str:
        """« local machine » ou ``user@hôte[:port]``."""
        logger.trace("libelle")
        return cible.cible.affichage if cible.distant else _("local machine")

    def _afficher_cible(self) -> None:
        """Libellé de la cible active, en gras."""
        logger.debug("_afficher_cible: entrée")
        texte = GLib.markup_escape_text(self.libelle(self.fichiers()))
        self.etat_cible.set_markup(f"<b>{texte}</b>")
        logger.debug("_afficher_cible: sortie")

    def _copier(self, texte: str) -> None:
        """Met ``texte`` dans le presse-papiers."""
        logger.debug("_copier: entrée | {} caractère(s)", len(texte))
        self.get_clipboard().set(texte)
        logger.debug("_copier: sortie")

    @staticmethod
    def message_erreur(exc: core.ErreurCle) -> str:
        """Détail affichable d'une :class:`core.ErreurCle`."""
        logger.trace("message_erreur: {}", exc.code)
        return (exc.detail or exc.code)[:120]

    def _on_onglet(self, _onglets: Gtk.Notebook, _page: Gtk.Widget | None, numero: int) -> None:
        """Barre d'actions de l'onglet affiché."""
        logger.debug("_on_onglet: entrée | {}", numero)
        self.actions_cles.set_visible(numero in (PAGE_PRIVEES, PAGE_ORPHELINES))
        self.actions_known_hosts.set_visible(numero == PAGE_KNOWN_HOSTS)
        self.actions_authorized_keys.set_visible(numero == PAGE_AUTHORIZED_KEYS)
        self.actualiser_boutons(numero)
        if numero in self.messages_cible:
            self.notifier(self.messages_cible[numero])
        logger.debug("_on_onglet: sortie")

    # -- clés locales -------------------------------------------------------

    def charger_cles(self, notifier: bool = True) -> None:
        """Relit ``~/.ssh/`` : onglets Private keys et Orphan Public keys.

        Args:
            notifier: Annonce le nombre de clés ; ``False`` après une action,
                dont le message (« Key imported… ») reste affiché.
        """
        logger.debug("charger_cles: entrée")
        if not self.dossier.is_dir():
            self.privees.remplir([])
            self.orphelines.remplir([])
            self.notifier(_("~/.ssh/ not found — no keys loaded"))
            self.actualiser_boutons()
            logger.debug("charger_cles: sortie | dossier absent")
            return
        lignes = [LigneGestionCle(c, core.details_cle(c)) for c in core.lister_cles(self.dossier)]
        privees = [ligne for ligne in lignes if not ligne.cle.est_publique]
        orphelines = [ligne for ligne in lignes if ligne.cle.est_publique]
        self.privees.remplir(privees)
        self.orphelines.remplir(orphelines)
        if notifier:
            self.notifier(
                _("{priv} private key(s), {pub} Orphan Public key(s) loaded from ~/.ssh/").format(
                    priv=len(privees), pub=len(orphelines)
                )
            )
        self.actualiser_boutons()
        logger.debug("charger_cles: sortie | {} + {}", len(privees), len(orphelines))

    def _tableau_cles(self, page: int | None = None) -> Tableau | None:
        """Tableau de clés de l'onglet ``page`` (affiché par défaut), sinon ``None``."""
        logger.trace("_tableau_cles")
        page = self.onglets.get_current_page() if page is None else page
        logger.trace("_tableau_cles: sortie")
        return {PAGE_PRIVEES: self.privees, PAGE_ORPHELINES: self.orphelines}.get(page)

    def cle_choisie(self) -> LigneGestionCle | None:
        """Clé sélectionnée dans l'onglet affiché (Private ou Orphan)."""
        logger.debug("cle_choisie: entrée")
        tableau = self._tableau_cles()
        ligne = tableau.choisie() if tableau is not None else None
        logger.debug("cle_choisie: sortie | {}", ligne is not None)
        return ligne

    def actualiser_boutons(self, page: int | None = None) -> None:
        """Boutons des clés actifs selon la sélection."""
        logger.debug("actualiser_boutons: entrée")
        tableau = self._tableau_cles(page)
        ligne = tableau.choisie() if tableau is not None else None
        publique = ligne is not None and ligne.publique is not None
        privee = ligne is not None and not ligne.cle.est_publique
        self.bouton_copier.set_sensitive(publique)
        self.bouton_voir.set_sensitive(publique)
        self.bouton_renommer.set_sensitive(ligne is not None)
        self.bouton_phrase.set_sensitive(privee)
        self.bouton_agent.set_sensitive(privee)
        self.bouton_supprimer.set_sensitive(ligne is not None)
        logger.debug("actualiser_boutons: sortie")

    def _on_generer(self, _bouton: Gtk.Button | None) -> None:
        """« ⚙ Generate » (Ctrl+N) : formulaire de génération."""
        logger.debug("_on_generer: entrée")
        ca = self.ca.name if self.ca is not None and self.ca.exists() else None
        self.dialogue = GenererCleDialog(self, ca, self.generer)
        self.dialogue.present()
        logger.debug("_on_generer: sortie")

    def generer(self, options: dict[str, object]) -> None:
        """Génère la clé (et la signe si demandé) en arrière-plan."""
        logger.debug("generer: entrée | type={}", options["type"])
        chemin = core.nom_libre(self.dossier, str(options["nom"]))
        ca = self.ca if options["signer"] else None

        def travail() -> tuple[Path, Path | None, core.ErreurCle | None]:
            logger.trace("travail: entrée")
            core.generer_cle(
                chemin,
                str(options["type"]),
                int(options["taille"]),
                str(options["commentaire"]),
                str(options["phrase"]),
            )
            if ca is None:
                logger.trace("travail: sortie")
                return chemin, None, None
            try:
                certificat = core.signer_cle(
                    chemin.with_name(chemin.name + ".pub"),
                    ca,
                    str(options["identite"]),
                    str(options["principaux"]),
                    str(options["validite"]),
                )
            except core.ErreurCle as exc:
                logger.warning("generer: signature impossible : {}", exc)
                return chemin, None, exc
            logger.trace("travail: sortie")
            return chemin, certificat, None

        self.en_fond(travail, lambda r, e: self._cle_generee(r, e, options, ca))
        logger.debug("generer: sortie | {}", chemin.name)

    def _cle_generee(
        self,
        resultat: tuple[Path, Path | None, core.ErreurCle | None] | None,
        erreur: core.ErreurCle | None,
        options: dict[str, object],
        ca: Path | None,
    ) -> None:
        """Message de fin de génération / signature, puis liste relue."""
        logger.debug("_cle_generee: entrée | erreur={}", erreur is not None)
        if erreur is not None:
            if erreur.code == "absent":
                self.notifier(_("ssh-keygen not found — install openssh-client"))
            elif erreur.code == "echec":
                self.notifier(_("ssh-keygen failed: {err}").format(err=erreur.detail[:80]))
            else:
                self.notifier(_("Generation failed: {err}").format(err=erreur.code))
            logger.debug("_cle_generee: sortie | échec")
            return
        chemin, certificat, signature = resultat
        logger.info("SshKeyManager | clé générée {}", chemin.name)
        if signature is not None and signature.code == "delai":
            self.notifier(
                _(
                    "CA signing timed out — CA key may require a passphrase "
                    "(unsupported non-interactively)"
                )
            )
        elif signature is not None:
            self.notifier(_("CA signing failed: {err}").format(err=self.message_erreur(signature)))
        elif certificat is not None:
            self.notifier(
                _("Key signed by CA {ca} (identity={id})").format(
                    ca=ca.name, id=options["identite"]
                )
            )
        else:
            self.notifier(_("Key generated: {name}").format(name=chemin.name))
        self.charger_cles(notifier=False)
        logger.debug("_cle_generee: sortie")

    def _on_importer(self, _bouton: Gtk.Button | None) -> None:
        """« ↑ Import » (Ctrl+I) : choix d'une clé privée à copier dans ``~/.ssh/``."""
        logger.debug("_on_importer: entrée")
        self.dialogue = fichiers.choisir(
            self, fichiers.OUVRIR, _("Import Private Key"), self.importer, filtrer=False
        )
        logger.debug("_on_importer: sortie")

    def _si_absent(self, source: Path, suite: Callable[[], None]) -> None:
        """``suite()``, après confirmation si ``~/.ssh/`` a déjà un fichier de ce nom."""
        logger.debug("_si_absent: entrée")
        if (self.dossier / source.name).exists():
            self.confirmer(
                GLib.markup_escape_text(
                    _("File {name} already exists in ~/.ssh/ — overwrite?").format(
                        name=source.name
                    )
                ),
                suite,
            )
        else:
            suite()
        logger.debug("_si_absent: sortie")

    def importer(self, chemin: str | None) -> None:
        """Importe la paire ``chemin`` (``None`` : sélecteur annulé)."""
        logger.debug("importer: entrée | {}", chemin is not None)
        if chemin is not None:
            source = Path(chemin)
            self._si_absent(source, lambda: self._importer(source))
        logger.debug("importer: sortie")

    def _importer(self, source: Path) -> None:
        """Copie la paire ``source`` dans ``~/.ssh/``."""
        logger.debug("_importer: entrée")
        try:
            core.importer_paire(source, self.dossier)
        except core.ErreurCle as exc:
            logger.error("_importer: {}", exc)
            self.notifier(_("Import failed: {err}").format(err=self.message_erreur(exc)))
            logger.trace("_importer: sortie")
            return
        logger.info("SshKeyManager | clé importée {}", source.name)
        self.notifier(_("Key imported: {name}").format(name=source.name))
        self.charger_cles(notifier=False)
        logger.debug("_importer: sortie")

    def _lire_publique(self, ligne: LigneGestionCle | None, absente: str) -> str | None:
        """Contenu de la clé publique de ``ligne`` ; ``None`` (et message) sinon."""
        logger.debug("_lire_publique: entrée")
        if ligne is None or ligne.publique is None:
            self.notifier(absente)
            logger.debug("_lire_publique: sortie | aucune")
            return None
        try:
            texte = ligne.publique.read_text(encoding="utf-8").strip()
        except OSError as exc:
            logger.error("_lire_publique: {}", exc)
            self.notifier(_("Cannot read public key: {err}").format(err=exc))
            logger.trace("_lire_publique: sortie")
            return None
        logger.debug("_lire_publique: sortie | {} caractère(s)", len(texte))
        return texte

    def _on_copier_publique(self, _bouton: Gtk.Button) -> None:
        """« ⎘ Copy public key »."""
        logger.debug("_on_copier_publique: entrée")
        texte = self._lire_publique(
            self.cle_choisie(), _("No Public key available for selected entry")
        )
        if texte is not None:
            self._copier(texte)
            self.notifier(_("Public key copied to clipboard"))
        logger.debug("_on_copier_publique: sortie")

    def _on_voir_publique(self, _bouton: Gtk.Button) -> None:
        """« 👁 View public key »."""
        logger.debug("_on_voir_publique: entrée")
        ligne = self.cle_choisie()
        texte = self._lire_publique(ligne, _("No public key available for selected entry"))
        if texte is not None:
            self.dialogue = VuePubliqueDialog(self, ligne.cle.nom, texte)
            self.dialogue.present()
        logger.debug("_on_voir_publique: sortie")

    def _on_renommer(self, _bouton: Gtk.Button) -> None:
        """« ✎ Rename » : nouveau nom de la clé (et de sa .pub / son certificat)."""
        logger.debug("_on_renommer: entrée")
        ligne = self.cle_choisie()
        if ligne is None:
            self.notifier(_("No key selected"))
            logger.debug("_on_renommer: sortie | aucune clé")
            return
        self.saisie_nom = Gtk.Entry(text=ligne.cle.nom)
        self.demander(
            _("Rename Key"),
            _("New name for {name}:").format(name=ligne.cle.nom),
            _("_Rename"),
            self.saisie_nom,
            lambda: self.renommer(ligne, self.saisie_nom.get_text().strip()),
        )
        logger.debug("_on_renommer: sortie")

    def renommer(self, ligne: LigneGestionCle, nouveau: str) -> None:
        """Renomme ``ligne`` en ``nouveau`` (rien si le nom ne change pas)."""
        logger.debug("renommer: entrée")
        ancien = ligne.cle.nom
        if not nouveau or nouveau == ancien:
            logger.debug("renommer: sortie | inchangé")
            return
        try:
            core.renommer_cle(ligne.cle, nouveau)
        except core.ErreurCle as exc:
            logger.error("renommer: {}", exc)
            if exc.code == "nom":
                self.notifier(_("Invalid name — must not contain a path separator"))
            elif exc.code == "existe":
                self.notifier(_("A file named {name} already exists").format(name=exc.detail))
            else:
                self.notifier(_("Rename failed: {err}").format(err=self.message_erreur(exc)))
            logger.trace("renommer: sortie")
            return
        logger.info("SshKeyManager | {} renommée {}", ancien, nouveau)
        self.notifier(_("Renamed {old} to {new}").format(old=ancien, new=nouveau))
        self.charger_cles(notifier=False)
        logger.debug("renommer: sortie")

    def _cle_privee(self) -> LigneGestionCle | None:
        """Clé privée sélectionnée ; ``None`` (et message) sinon."""
        logger.debug("_cle_privee: entrée")
        ligne = self.cle_choisie()
        if ligne is None or ligne.cle.est_publique:
            self.notifier(_("No private key selected"))
            ligne = None
        logger.debug("_cle_privee: sortie | {}", ligne is not None)
        return ligne

    def _on_phrase(self, _bouton: Gtk.Button) -> None:
        """« 🔒 Passphrase » : ancienne, nouvelle et confirmation."""
        logger.debug("_on_phrase: entrée")
        ligne = self._cle_privee()
        if ligne is None:
            logger.debug("_on_phrase: sortie | aucune clé privée")
            return
        grille = Gtk.Grid(column_spacing=10, row_spacing=8)
        self.phrases: list[Gtk.PasswordEntry] = []
        for rang, (libelle, indication) in enumerate(
            (
                (_("Current passphrase:"), _("(empty if none)")),
                (_("New passphrase:"), _("(empty = remove passphrase)")),
                (_("Confirm new passphrase:"), ""),
            )
        ):
            champ = Gtk.PasswordEntry(
                show_peek_icon=True, hexpand=True, placeholder_text=indication
            )
            grille.attach(Gtk.Label(label=libelle, xalign=1), 0, rang, 1, 1)
            grille.attach(champ, 1, rang, 1, 1)
            self.phrases.append(champ)
        self.demander(
            _("Change Passphrase — {name}").format(name=ligne.cle.nom),
            "",
            _("_Change"),
            grille,
            lambda: self.changer_phrase(ligne, *(c.get_text() for c in self.phrases)),
        )
        logger.debug("_on_phrase: sortie")

    def changer_phrase(
        self, ligne: LigneGestionCle, ancienne: str, nouvelle: str, confirmation: str
    ) -> None:
        """``ssh-keygen -p`` si la confirmation correspond (phrases jamais journalisées)."""
        logger.debug("changer_phrase: entrée")
        if nouvelle != confirmation:
            self.notifier(_("New passphrase and confirmation do not match"))
            logger.debug("changer_phrase: sortie | confirmation différente")
            return
        try:
            core.changer_phrase(ligne.cle.chemin, ancienne, nouvelle)
        except core.ErreurCle as exc:
            logger.warning("changer_phrase: {}", exc.code)
            self.notifier(
                _("Passphrase change failed: {err}").format(err=self.message_erreur(exc))
            )
            logger.trace("changer_phrase: sortie")
            return
        logger.info("SshKeyManager | phrase de passe changée pour {}", ligne.cle.nom)
        self.notifier(_("Passphrase changed for {name}").format(name=ligne.cle.nom))
        logger.debug("changer_phrase: sortie")

    def _on_agent(self, _bouton: Gtk.Button) -> None:
        """« 🔑 Agent » : ajoute ou retire la clé de ssh-agent, après confirmation."""
        logger.debug("_on_agent: entrée")
        ligne = self._cle_privee()
        if ligne is None:
            logger.debug("_on_agent: sortie | aucune clé privée")
            return
        if ligne.cle.publique is None:
            self.notifier(_("No public key available to identify this key in the agent"))
            logger.debug("_on_agent: sortie | pas de .pub")
            return
        nom = GLib.markup_escape_text(ligne.cle.nom)
        if core.cle_dans_agent(ligne.cle.publique):
            self.confirmer(
                _("Remove {name} from ssh-agent?").format(name=nom),
                lambda: self.retirer_agent(ligne),
            )
        else:
            self.confirmer(
                _("Add {name} to ssh-agent?").format(name=nom), lambda: self.ajouter_agent(ligne)
            )
        logger.debug("_on_agent: sortie")

    def ajouter_agent(self, ligne: LigneGestionCle) -> None:
        """``ssh-add`` de la clé."""
        logger.debug("ajouter_agent: entrée")
        try:
            core.ajouter_agent(ligne.cle.chemin)
        except core.ErreurCle as exc:
            logger.warning("ajouter_agent: {}", exc)
            if exc.code == "delai":
                self.notifier(
                    _(
                        "ssh-add timed out — the key may require a passphrase "
                        "(no terminal available here)"
                    )
                )
            elif exc.code == "echec":
                self.notifier(
                    _("Failed to add to agent (passphrase required?): {err}").format(
                        err=exc.detail[:100]
                    )
                )
            else:
                self.notifier(_("ssh-add failed: {err}").format(err=exc.code))
            logger.trace("ajouter_agent: sortie")
            return
        self.notifier(_("Added to ssh-agent"))
        logger.debug("ajouter_agent: sortie")

    def retirer_agent(self, ligne: LigneGestionCle) -> None:
        """``ssh-add -d`` de la clé."""
        logger.debug("retirer_agent: entrée")
        try:
            core.retirer_agent(ligne.cle.publique)
        except core.ErreurCle as exc:
            logger.warning("retirer_agent: {}", exc)
            if exc.code == "echec":
                self.notifier(_("Failed to remove from agent: {err}").format(err=exc.detail[:100]))
            else:
                self.notifier(_("ssh-add failed: {err}").format(err=exc.code))
            logger.trace("retirer_agent: sortie")
            return
        self.notifier(_("Removed from ssh-agent"))
        logger.debug("retirer_agent: sortie")

    def _on_reveler(self, _bouton: Gtk.Button) -> None:
        """« 📂 Reveal » : ``~/.ssh/`` dans le gestionnaire de fichiers."""
        logger.debug("_on_reveler: entrée")
        try:
            Gio.AppInfo.launch_default_for_uri(
                Gio.File.new_for_path(str(self.dossier)).get_uri(), None
            )
        except GLib.Error as exc:
            logger.error("_on_reveler: {}", exc.message)
            self.notifier(_("Cannot open file manager: {err}").format(err=exc.message))
        logger.debug("_on_reveler: sortie")

    def _on_supprimer(self, _bouton: Gtk.Button | None) -> None:
        """« 🗑 Delete » : supprime la clé (et sa .pub, son certificat) après confirmation."""
        logger.debug("_on_supprimer: entrée")
        ligne = self.cle_choisie()
        if ligne is None:
            logger.debug("_on_supprimer: sortie | aucune clé")
            return
        self.confirmer(
            _(
                "Permanently delete <b>{name}</b> (and its Orphan Public key if present)?\n"
                "<b>This cannot be undone.</b>"
            ).format(name=GLib.markup_escape_text(ligne.cle.nom)),
            lambda: self.supprimer(ligne),
        )
        logger.debug("_on_supprimer: sortie")

    def supprimer(self, ligne: LigneGestionCle) -> None:
        """Supprime ``ligne``."""
        logger.debug("supprimer: entrée")
        try:
            core.supprimer_cle(ligne.cle)
        except core.ErreurCle as exc:
            logger.error("supprimer: {}", exc)
            self.notifier(_("Delete failed: {err}").format(err=self.message_erreur(exc)))
            logger.trace("supprimer: sortie")
            return
        logger.info("SshKeyManager | clé supprimée {}", ligne.cle.nom)
        self.notifier(_("Key deleted: {name}").format(name=ligne.cle.nom))
        self.charger_cles(notifier=False)
        logger.debug("supprimer: sortie")

    def _on_supprimer_page(self) -> None:
        """Touche Suppr : suppression propre à l'onglet affiché."""
        logger.debug("_on_supprimer_page: entrée")
        page = self.onglets.get_current_page()
        if page == PAGE_KNOWN_HOSTS:
            self._on_supprimer_known_host(None)
        elif page == PAGE_AUTHORIZED_KEYS:
            self._on_supprimer_authorized_key(None)
        else:
            self._on_supprimer(None)
        logger.debug("_on_supprimer_page: sortie")

    # -- CA de signature ----------------------------------------------------

    def afficher_ca(self) -> None:
        """Libellé de la CA désignée (nom, type, empreinte)."""
        logger.debug("afficher_ca: entrée")
        if self.ca is None:
            self.etat_ca.set_markup(
                "<i>{}</i>".format(
                    GLib.markup_escape_text(_("(none — keys are generated unsigned)"))
                )
            )
            logger.debug("afficher_ca: sortie | aucune")
            return
        publique = self.ca.with_name(self.ca.name + ".pub")
        empreinte, type_ = core.empreinte_et_type(publique if publique.exists() else self.ca)
        detail = f" — {type_} {empreinte}" if empreinte else ""
        self.etat_ca.set_markup(
            f"<b>{GLib.markup_escape_text(self.ca.name)}</b>{GLib.markup_escape_text(detail)}"
        )
        logger.debug("afficher_ca: sortie | {}", self.ca.name)

    def _designer(self, ca: Path | None) -> None:
        """Enregistre ``ca`` comme CA désignée (``None`` : aucune)."""
        logger.debug("_designer: entrée | {}", ca is not None)
        self.ca = ca
        core.ecrire_config_ca(self.config_ca, ca)
        self.afficher_ca()
        logger.debug("_designer: sortie")

    def _on_designer_ca(self, _bouton: Gtk.Button) -> None:
        """« 🔏 Designate » : choix d'une paire locale existante."""
        logger.debug("_on_designer_ca: entrée")
        self.dialogue = fichiers.choisir(
            self, fichiers.OUVRIR, _("Designate Signing CA"), self.designer_ca, filtrer=False
        )
        logger.debug("_on_designer_ca: sortie")

    def designer_ca(self, chemin: str | None) -> None:
        """Désigne la clé privée ``chemin``, qui doit avoir sa ``.pub``."""
        logger.debug("designer_ca: entrée | {}", chemin is not None)
        if chemin is None:
            logger.debug("designer_ca: sortie | annulé")
            return
        prive = Path(chemin)
        if prive.name.endswith(".pub"):
            self.notifier(_("Select the private key file, not the .pub file"))
            logger.debug("designer_ca: sortie | .pub choisie")
            return
        if not prive.with_name(prive.name + ".pub").exists():
            self.notifier(
                _("No matching .pub file found next to {name} — cannot use as CA").format(
                    name=prive.name
                )
            )
            logger.debug("designer_ca: sortie | pas de .pub")
            return
        self._designer(prive)
        logger.info("SshKeyManager | CA désignée {}", prive)
        self.notifier(_("Designated CA: {name}").format(name=prive.name))
        logger.debug("designer_ca: sortie")

    def _on_importer_ca(self, _bouton: Gtk.Button) -> None:
        """« ⬆ Import CA » : copie une paire externe dans ``~/.ssh/`` et la désigne."""
        logger.debug("_on_importer_ca: entrée")
        self.dialogue = fichiers.choisir(
            self, fichiers.OUVRIR, _("Import CA Private Key"), self.importer_ca, filtrer=False
        )
        logger.debug("_on_importer_ca: sortie")

    def importer_ca(self, chemin: str | None) -> None:
        """Importe la CA ``chemin`` (confirmation si le nom existe déjà)."""
        logger.debug("importer_ca: entrée | {}", chemin is not None)
        if chemin is None:
            logger.debug("importer_ca: sortie | annulé")
            return
        source = Path(chemin)
        if not source.with_name(source.name + ".pub").exists():
            self.notifier(
                _("No matching .pub file found next to {name} — cannot import as CA").format(
                    name=source.name
                )
            )
            logger.debug("importer_ca: sortie | pas de .pub")
            return
        self._si_absent(source, lambda: self._importer_ca(source))
        logger.debug("importer_ca: sortie")

    def _importer_ca(self, source: Path) -> None:
        """Copie la paire ``source`` et la désigne."""
        logger.debug("_importer_ca: entrée")
        try:
            ca = core.importer_paire(source, self.dossier, exiger_publique=True)
        except core.ErreurCle as exc:
            logger.error("_importer_ca: {}", exc)
            self.notifier(_("CA import failed: {err}").format(err=self.message_erreur(exc)))
            logger.trace("_importer_ca: sortie")
            return
        self._designer(ca)
        logger.info("SshKeyManager | CA importée et désignée {}", ca)
        self.notifier(_("CA imported and designated: {name}").format(name=ca.name))
        self.charger_cles(notifier=False)
        logger.debug("_importer_ca: sortie")

    def _on_supprimer_ca(self, _bouton: Gtk.Button) -> None:
        """« 🗑 Delete CA » : supprime la paire de la CA, après confirmation."""
        logger.debug("_on_supprimer_ca: entrée")
        if self.ca is None:
            self.notifier(_("No CA currently designated"))
            logger.debug("_on_supprimer_ca: sortie | aucune")
            return
        self.confirmer(
            _(
                "Permanently delete the CA key pair <b>{name}</b>?\n"
                "Certificates already signed by this CA will no longer be trustable to verify.\n"
                "<b>This cannot be undone.</b>"
            ).format(name=GLib.markup_escape_text(self.ca.name)),
            self.supprimer_ca,
        )
        logger.debug("_on_supprimer_ca: sortie")

    def supprimer_ca(self) -> None:
        """Supprime la clé privée et publique de la CA désignée."""
        logger.debug("supprimer_ca: entrée")
        nom = self.ca.name
        try:
            self.ca.unlink(missing_ok=True)
            self.ca.with_name(nom + ".pub").unlink(missing_ok=True)
        except OSError as exc:
            logger.error("supprimer_ca: {}", exc)
            self.notifier(_("CA deletion failed: {err}").format(err=exc))
            logger.trace("supprimer_ca: sortie")
            return
        self._designer(None)
        logger.info("SshKeyManager | CA supprimée {}", nom)
        self.notifier(_("CA deleted: {name}").format(name=nom))
        self.charger_cles(notifier=False)
        logger.debug("supprimer_ca: sortie")

    # -- cible : known_hosts / authorized_keys -------------------------------

    def _on_connecter(self, _widget: Gtk.Widget) -> None:
        """« 🔌 Connect » (ou Entrée) : teste la cible saisie avant de l'utiliser."""
        logger.debug("_on_connecter: entrée")
        texte = self.saisie_cible.get_text().strip()
        if not texte:
            self._on_local(None)
            logger.debug("_on_connecter: sortie | vide, retour en local")
            return
        cible = core.analyser_cible(texte)
        if cible is None:
            self.notifier(_("Invalid target — expected user@host or user@host:port"))
            logger.debug("_on_connecter: sortie | invalide")
            return
        distant = core.FichiersDistants(cible)
        self.notifier(_("Testing connection to {t}…").format(t=cible.affichage))
        self.bouton_connecter.set_sensitive(False)
        self.en_fond(distant.tester, lambda erreur, _e: self._connexion_testee(distant, erreur))
        logger.debug("_on_connecter: sortie")

    def _connexion_testee(self, distant: core.FichiersDistants, erreur: str | None) -> None:
        """Résultat du test : la cible devient active, ou message d'échec."""
        logger.debug("_connexion_testee: entrée | erreur={}", erreur is not None)
        self.bouton_connecter.set_sensitive(True)
        if erreur is not None:
            logger.warning("SshKeyManager | {} injoignable : {}", distant.cible.affichage, erreur)
            self.notifier(_("Connection failed: {err}").format(err=erreur[:120]))
            logger.trace("_connexion_testee: sortie")
            return
        self.distant = distant
        self._afficher_cible()
        logger.info("SshKeyManager | connecté à {}", distant.cible.affichage)
        self.notifier(
            _("Connected to {t} — Known Hosts / Authorized Keys now read from there").format(
                t=distant.cible.affichage
            )
        )
        self.charger_cible(notifier=False)
        logger.debug("_connexion_testee: sortie")

    def _on_local(self, _widget: Gtk.Widget | None) -> None:
        """« 🏠 Local » : retour à la machine locale."""
        logger.debug("_on_local: entrée")
        self.distant = None
        self.saisie_cible.set_text("")
        self._afficher_cible()
        self.notifier(_("Switched back to local machine"))
        self.charger_cible(notifier=False)
        logger.debug("_on_local: sortie")

    def charger_cible(self, notifier: bool = True) -> None:
        """Relit ``known_hosts`` et ``authorized_keys`` de la cible, en arrière-plan."""
        logger.debug("charger_cible: entrée")
        cible = self.fichiers()
        self.en_fond(
            lambda: (cible.lire("known_hosts"), cible.lire("authorized_keys")),
            lambda contenus, _e: self._cible_lue(cible, contenus, notifier),
        )
        logger.debug("charger_cible: sortie")

    def _cible_lue(
        self,
        cible: core.FichiersLocaux | core.FichiersDistants,
        contenus: tuple[str | None, str | None],
        notifier: bool,
    ) -> None:
        """Remplit les onglets Known Hosts et Authorized Keys (si la cible n'a pas changé)."""
        logger.debug("_cible_lue: entrée")
        if cible is not self.fichiers():
            logger.debug("_cible_lue: sortie | cible changée entre-temps")
            return
        libelle = self.libelle(cible)
        known_hosts, authorized_keys = contenus
        entrees_kh = core.lire_known_hosts(known_hosts) if known_hosts is not None else []
        entrees_ak = (
            core.lire_authorized_keys(authorized_keys) if authorized_keys is not None else []
        )
        self.known_hosts.remplir(entrees_kh)
        self.authorized_keys.remplir(entrees_ak)
        if known_hosts is None:
            message_kh = _("known_hosts not found on {t}").format(t=libelle)
        else:
            message_kh = _("{n} known_hosts entries loaded from {t}").format(
                n=len(entrees_kh), t=libelle
            )
        if authorized_keys is None:
            message_ak = _("authorized_keys not found on {t}").format(t=libelle)
        else:
            message_ak = _("{n} authorized_keys entries loaded from {t}").format(
                n=len(entrees_ak), t=libelle
            )
        self.messages_cible = {PAGE_KNOWN_HOSTS: message_kh, PAGE_AUTHORIZED_KEYS: message_ak}
        page = self.onglets.get_current_page()
        if notifier and page in self.messages_cible:
            self.notifier(self.messages_cible[page])
        logger.debug("_cible_lue: sortie | {} + {}", len(entrees_kh), len(entrees_ak))

    def confirmer_root(
        self,
        cible: core.FichiersLocaux | core.FichiersDistants,
        action: str,
        suite: Callable[[], None],
    ) -> None:
        """Confirmation de plus avant d'écrire sur le compte root (local ou distant)."""
        logger.debug("confirmer_root: entrée")
        if cible.utilisateur() != "root":
            suite()
            logger.debug("confirmer_root: sortie | pas root")
            return
        self.confirmer(
            _(
                "⚠ You are about to {action} on the <b>root</b> account of {target}.\n"
                "This can affect SSH access to the whole system.\n<b>Are you sure?</b>"
            ).format(
                action=GLib.markup_escape_text(action),
                target=GLib.markup_escape_text(self.libelle(cible)),
            ),
            suite,
        )
        logger.debug("confirmer_root: sortie | confirmation demandée")

    def modifier(
        self,
        cible: core.FichiersLocaux | core.FichiersDistants,
        nom: str,
        mode: int,
        transformation: Callable[[str], str | None],
        messages: tuple[str, str, str],
    ) -> None:
        """Relit ``nom``, applique ``transformation`` et réécrit, en arrière-plan.

        Args:
            cible: Fichiers de la cible au moment de l'action.
            nom: ``known_hosts`` ou ``authorized_keys``.
            mode: Permissions du fichier réécrit.
            transformation: Nouveau texte, ou ``None`` si la ligne visée a changé.
            messages: ``(succès, échec d'écriture, fichier modifié entre-temps)``.
        """
        logger.debug("modifier: entrée | {}", nom)

        def travail() -> bool | None:
            logger.trace("travail: entrée")
            nouveau = transformation(cible.lire(nom) or "")
            logger.trace("travail: sortie")
            return None if nouveau is None else cible.ecrire(nom, nouveau, mode)

        def fin(ecrit: bool | None, _erreur: core.ErreurCle | None) -> None:
            logger.debug("modifier.fin: {}", ecrit)
            succes, echec, change = messages
            if ecrit is None:
                self.notifier(change)
            else:
                self.notifier(succes if ecrit else echec)
            self.charger_cible(notifier=False)
            logger.trace("fin: sortie")

        self.en_fond(travail, fin)
        logger.debug("modifier: sortie")

    def _on_copier_empreinte(self, _bouton: Gtk.Button) -> None:
        """« ⎘ Copy fingerprint » de l'entrée known_hosts sélectionnée."""
        logger.debug("_on_copier_empreinte: entrée")
        entree = self.known_hosts.choisie()
        if entree is None:
            self.notifier(_("No known_hosts entry selected"))
        elif not entree.empreinte:
            self.notifier(_("No fingerprint available for this entry"))
        else:
            self._copier(entree.empreinte)
            self.notifier(_("Fingerprint copied to clipboard"))
        logger.debug("_on_copier_empreinte: sortie")

    def hotes_candidats(self) -> list[str]:
        """Alias de ``~/.ssh/config`` puis hôtes de gcm4, sans doublon."""
        logger.debug("hotes_candidats: entrée")
        hotes = core.hotes_ssh_config(self.dossier / "config")
        if self._hotes_gcm is not None:
            try:
                supplementaires = self._hotes_gcm() or []
            except Exception as exc:  # rappel fourni par l'appelant
                logger.warning("hotes_candidats: hôtes de gcm4 indisponibles : {}", exc)
                supplementaires = []
            for hote in supplementaires:
                if hote and hote not in hotes:
                    hotes.append(hote)
        logger.debug("hotes_candidats: sortie | {}", len(hotes))
        return hotes

    def _on_tester_known_hosts(self, _bouton: Gtk.Button) -> None:
        """« 🧪 Test known hosts » : chaque hôte candidat est-il dans known_hosts ?"""
        logger.debug("_on_tester_known_hosts: entrée")
        hotes = self.hotes_candidats()
        if not hotes:
            self.notifier(
                _("No candidate hosts found in ~/.ssh/config (and no GCM host list wired in)")
            )
            logger.debug("_on_tester_known_hosts: sortie | aucun hôte")
            return
        cible = self.fichiers()

        def travail() -> list[tuple[str, bool, str]] | None:
            logger.trace("travail: entrée")
            texte = cible.lire("known_hosts")
            logger.trace("travail: sortie")
            return None if texte is None else core.tester_known_hosts(texte, hotes)

        self.en_fond(travail, lambda resultats, _e: self._known_hosts_testes(cible, resultats))
        logger.debug("_on_tester_known_hosts: sortie")

    def _known_hosts_testes(
        self,
        cible: core.FichiersLocaux | core.FichiersDistants,
        resultats: list[tuple[str, bool, str]] | None,
    ) -> None:
        """Fenêtre des résultats, ou message si la cible n'a pas de known_hosts."""
        logger.debug("_known_hosts_testes: entrée")
        if resultats is None:
            self.notifier(_("known_hosts not found on {t}").format(t=self.libelle(cible)))
            logger.debug("_known_hosts_testes: sortie | pas de known_hosts")
            return
        logger.info(
            "SshKeyManager | test known_hosts {} : {}/{}",
            self.libelle(cible),
            sum(1 for _h, trouve, _i in resultats if trouve),
            len(resultats),
        )
        self.dialogue = ResultatsKnownHostsDialog(self, self.libelle(cible), resultats)
        self.dialogue.present()
        logger.debug("_known_hosts_testes: sortie")

    def _retrait(
        self,
        entree: core.EntreeKnownHosts | core.EntreeAuthorizedKeys | None,
        nom: str,
        mode: int,
        textes: tuple[str, str, str, str, str],
    ) -> None:
        """Retire ``entree`` de ``nom`` après confirmation (et confirmation root).

        Args:
            entree: Entrée sélectionnée, ou ``None``.
            nom: ``known_hosts`` ou ``authorized_keys``.
            mode: Permissions du fichier réécrit.
            textes: ``(aucune sélection, question, action root, succès, échec)``.
        """
        logger.debug("_retrait: entrée | {}", nom)
        aucune, question, action, succes, echec = textes
        if entree is None:
            self.notifier(aucune)
            logger.debug("_retrait: sortie | aucune entrée")
            return
        cible = self.fichiers()
        messages = (succes, echec, _("Entry not found (file may have changed) — refreshing"))
        self.confirmer(
            question,
            lambda: self.confirmer_root(
                cible,
                action,
                lambda: self.modifier(
                    cible, nom, mode, lambda t: core.retirer_ligne(t, entree.brut), messages
                ),
            ),
        )
        logger.debug("_retrait: sortie")

    def _on_supprimer_known_host(self, _bouton: Gtk.Button | None) -> None:
        """« 🗑 Delete » (Known Hosts) : retire l'entrée sélectionnée."""
        logger.debug("_on_supprimer_known_host: entrée")
        self._retrait(
            self.known_hosts.choisie(),
            "known_hosts",
            0o644,
            (
                _("No known_hosts entry selected"),
                _("Remove this known_hosts entry?\n<b>This cannot be undone.</b>"),
                _("remove a known_hosts entry"),
                _("known_hosts entry removed"),
                _("Failed to update known_hosts"),
            ),
        )
        logger.debug("_on_supprimer_known_host: sortie")

    def _on_supprimer_authorized_key(self, _bouton: Gtk.Button | None) -> None:
        """« 🗑 Delete » (Authorized Keys) : retire l'entrée sélectionnée."""
        logger.debug("_on_supprimer_authorized_key: entrée")
        self._retrait(
            self.authorized_keys.choisie(),
            "authorized_keys",
            0o600,
            (
                _("No authorized_keys entry selected"),
                _("Remove this authorized_keys entry?\n<b>This cannot be undone.</b>"),
                _("remove an authorized_keys entry"),
                _("authorized_keys entry removed"),
                _("Failed to update authorized_keys"),
            ),
        )
        logger.debug("_on_supprimer_authorized_key: sortie")

    def _on_basculer_authorized_key(self, entree: core.EntreeAuthorizedKeys) -> None:
        """Case « Enabled » : commente ou décommente l'entrée après confirmation(s)."""
        logger.debug("_on_basculer_authorized_key: entrée | ligne {}", entree.ligne)
        action = _("Disable") if entree.active else _("Re-enable")
        cible = self.fichiers()
        messages = (
            _("authorized_keys entry updated"),
            _("Failed to update authorized_keys"),
            _("File changed on disk — refreshing"),
        )
        self.confirmer(
            _("{action} this authorized_keys entry?").format(action=action),
            lambda: self.confirmer_root(
                cible,
                _("enable/disable an authorized_keys entry"),
                lambda: self.modifier(
                    cible,
                    "authorized_keys",
                    0o600,
                    lambda t: core.basculer_ligne(t, entree.ligne, entree.brut),
                    messages,
                ),
            ),
        )
        logger.debug("_on_basculer_authorized_key: sortie")

    def _on_ajouter_authorized_key(self, _bouton: Gtk.Button) -> None:
        """« ＋ Add » : ligne de clé publique à ajouter à authorized_keys."""
        logger.debug("_on_ajouter_authorized_key: entrée")
        self.saisie_cle = Gtk.TextView(wrap_mode=Gtk.WrapMode.CHAR, monospace=True)
        defilement = Gtk.ScrolledWindow(
            child=self.saisie_cle, min_content_height=100, min_content_width=480, has_frame=True
        )
        self.demander(
            _("Add Authorized Key"),
            _("Paste the full public key line (options, type, base64 key, comment):"),
            _("_Add"),
            defilement,
            lambda: self.ajouter_authorized_key(_texte_tampon(self.saisie_cle).strip()),
        )
        logger.debug("_on_ajouter_authorized_key: sortie")

    def ajouter_authorized_key(self, ligne: str) -> None:
        """Ajoute ``ligne`` à authorized_keys si c'est une clé publique."""
        logger.debug("ajouter_authorized_key: entrée | {} caractère(s)", len(ligne))
        if not ligne:
            logger.debug("ajouter_authorized_key: sortie | vide")
            return
        if not core.est_ligne_cle_publique(ligne):
            self.notifier(_("This does not look like a valid public key line"))
            logger.debug("ajouter_authorized_key: sortie | invalide")
            return
        cible = self.fichiers()
        messages = (_("Key added to authorized_keys"), _("Failed to add key"), "")
        self.confirmer_root(
            cible,
            _("add a key to authorized_keys"),
            lambda: self.modifier(
                cible,
                "authorized_keys",
                0o600,
                lambda t: core.ajouter_ligne(t, ligne),
                messages,
            ),
        )
        logger.debug("ajouter_authorized_key: sortie")


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
