# SPDX-License-Identifier: GPL-3.0-or-later
"""Outils SSH GTK4 de gcm4 (#97) : sélecteur de clé, éditeur de
``~/.ssh/config`` (personnel + partagé), et à venir gestionnaire de clés.

Portage GTK4 des fichiers GTK3 de la racine (``key_picker_dialog.py``,
``ssh_config_editor.py``, ``ssh_key_manager_dialog.py``), qui restent en
place pour l'application GTK3 jusqu'à #180. La logique sans GTK (liste des
clés, empreintes, permissions, parsing de ``~/.ssh/config``, formulaire et
redirections de l'éditeur) est dans
:mod:`core`.

**Pas de ``PLUGIN`` ici** (décision du 28/10/2026 sur #97) : le contrat v1
refuse un plugin sans protocole, et la session SSH elle-même est portée
par #396. D'ici là, chargé en mode source comme ``gcm4_plugin_ssh.gtk4``,
le dossier ``ssh`` apparaît en erreur de chargement dans Extensions (état
transitoire documenté).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")  # déjà fixé par gcm4 ; déclare le fichier GTK4 (#178)
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, GObject, Gtk, Pango  # noqa: E402
from loguru import logger  # noqa: E402

from gcm4.core.i18n import _  # noqa: E402
from gcm4.ui import dialogues  # noqa: E402

from . import core  # noqa: E402

__all__ = [
    "CIBLES",
    "TYPES_REDIRECTION",
    "KeyPickerDialog",
    "LigneCle",
    "LigneHote",
    "LigneRedirection",
    "SshConfigEditor",
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
