# SPDX-License-Identifier: GPL-3.0-or-later
"""Outils SSH GTK4 de gcm4 (#97) : sélecteur de clé, et à venir éditeur de
``~/.ssh/config`` et gestionnaire de clés.

Portage GTK4 des fichiers GTK3 de la racine (``key_picker_dialog.py``,
``ssh_config_editor.py``, ``ssh_key_manager_dialog.py``), qui restent en
place pour l'application GTK3 jusqu'à #180. La logique sans GTK (liste des
clés, empreintes, permissions, parsing de ``~/.ssh/config``) est dans
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
from gi.repository import GObject, Gtk, Pango  # noqa: E402
from loguru import logger  # noqa: E402

from gcm4.core.i18n import _  # noqa: E402

from . import core  # noqa: E402

__all__ = ["KeyPickerDialog", "LigneCle"]


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
