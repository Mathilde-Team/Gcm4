# SPDX-License-Identifier: GPL-3.0-or-later
"""Préférences de la base GTK4 : pages Général et Apparence (lot 5a, #147).

``Adw.PreferencesWindow`` sans bouton OK : chaque changement est appliqué
tout de suite et enregistré aussitôt (:func:`gcm4.core.preferences.save_preference`,
écriture atomique). Un échec d'écriture (disque plein, fichier corrompu)
n'annule pas le changement en cours de session ; il est signalé par un
toast et journalisé.

- **Général** : confirmation à la fermeture, vérification des mises à jour,
  restauration des onglets ouverts ; onglets (#247) : confirmation avant de
  fermer une console (bouton, clic du milieu), bouclage onglet suivant /
  précédent ;
- **Apparence** : thème (action ``app.theme``, même état que le menu
  Affichage), panneau visible (action ``win.toggle-panel``, comme F9),
  police de l'arbre des hôtes (CSS sur la classe ``gcm-arbre``) ; groupe
  Fenêtre principale (#247) : rayures de l'arbre, titre dynamique et titre
  de la fenêtre, appliqués tout de suite (``MainWindow.appliquer_options``).

Les options ``[options]`` sont aussi mises à jour dans ``Config.options``
de la fenêtre principale : sinon ``save_config`` (modification de l'arbre)
réécrirait l'ancienne valeur. Sans configuration chargée (fichier
illisible), la page Général et le groupe Fenêtre principale sont inactifs :
rien n'est jamais écrit.

Lot 5b (#148) :

- **Raccourcis** : raccourcis de la base en lecture (``gcm4.core.preferences.raccourcis``).
  ``Gtk.ShortcutsWindow`` est déprécié depuis GTK 4.18 et ``Adw.ShortcutsDialog``
  demande libadwaita 1.8 : une page des préférences, libellés de
  ``Gtk.accelerator_get_label``, fonctionne de GTK 4.8 à 4.22 ;
- **Journalisation** : niveau par défaut (``[options] log-level``), appliqué
  tout de suite sauf si ``--debug`` ou ``GCM_LOG_LEVEL``/``GCM_DEBUG`` priment ;
  bouton d'ouverture du dossier des journaux ;
- **Extensions** : chaque plugin découvert (nom, version, source, chargé ou
  en erreur avec sa raison) ; la page de réglages d'un plugin
  (``build_preferences_page()``) est ajoutée après les pages de la base. Une
  exception du plugin est journalisée et affichée, sans fermer la fenêtre.

Compatibilité : ``Gtk.FontDialogButton`` (GTK ≥ 4.10), sinon ``Gtk.FontButton`` ;
``Gtk.FileLauncher`` (GTK ≥ 4.10), sinon ``Gio.AppInfo.launch_default_for_uri`` ;
chemin des journaux sélectionnable avec libadwaita ≥ 1.3 seulement
(``set_subtitle_selectable``), le bouton « Ouvrir » reste disponible en 1.2.
"""

from __future__ import annotations

import os

from gi.repository import Adw, Gio, GLib, Gtk, Pango
from loguru import logger

import logging_config
from gcm4.core import comportement_onglets, menu
from gcm4.core import preferences as prefs_core
from gcm4.core import raccourcis as raccourcis_core
from gcm4.core.config import ConfigError
from gcm4.core.i18n import N_, _
from gcm4.ui import mises_a_jour
from gcm4.ui import raccourcis as raccourcis_ui
from gcm4.ui.mot_de_passe_maitre import GroupeMotDePasseMaitre

#: Modes du thème, dans l'ordre de la liste déroulante.
MODES = ("system", "light", "dark")

#: Options de la page Général : (attribut de Preferences et d'AppConfig, libellé).
OPTIONS_GENERALES = (
    ("confirm_exit", N_("Confirm on exit")),
    ("check_updates", N_("Check updates")),
    ("restore_open_tabs", N_("Restore open tabs from the previous session on startup")),
    # Onglets (#247), libellés de Wconfig (GTK3).
    ("confirm_close_tab", N_("Confirm on close console")),
    ("confirm_close_tab_middle", N_("Confirm on close console with middle click")),
    ("cycle_tabs", N_("Circle around tabs when going to the previous/next tab")),
)

#: Options de la page Apparence, groupe « Fenêtre principale » (#247).
OPTIONS_FENETRE = (
    ("disable_hosts_stripes", N_("Disable alternating stripes in hosts window")),
    ("update_title", N_("Dynamic title")),
)


def css_police(description: str) -> str:
    """Description Pango (``"Sans Bold 11"``) → règle CSS de l'arbre ; vide → ``""``."""
    logger.trace("css_police: {!r}", description)
    if not description.strip():
        logger.trace("css_police: police du système")
        return ""
    desc = Pango.FontDescription.from_string(description)
    regles = []
    famille = desc.get_family()
    if famille:
        regles.append('font-family: "{}";'.format(famille.replace('"', "")))
    if desc.get_size() > 0:
        unite = "px" if desc.get_size_is_absolute() else "pt"
        regles.append(f"font-size: {desc.get_size() / Pango.SCALE:g}{unite};")
    regles.append(f"font-weight: {int(desc.get_weight())};")
    style = {Pango.Style.ITALIC: "italic", Pango.Style.OBLIQUE: "oblique"}.get(
        desc.get_style(), "normal"
    )
    regles.append(f"font-style: {style};")
    resultat = ".gcm-arbre { " + " ".join(regles) + " }\n"
    logger.trace("css_police: {} règle(s)", len(regles))
    return resultat


def _bouton_police() -> Gtk.Widget:
    """Bouton de choix de police (``FontDialogButton`` ≥ 4.10)."""
    logger.trace("_bouton_police")
    if hasattr(Gtk, "FontDialogButton"):
        bouton = Gtk.FontDialogButton(dialog=Gtk.FontDialog(title=_("Select font")))
    else:  # GTK < 4.10
        bouton = Gtk.FontButton(title=_("Select font"))
    bouton.set_valign(Gtk.Align.CENTER)
    logger.trace("_bouton_police: {}", type(bouton).__name__)
    return bouton


def _lire_police(bouton: Gtk.Widget) -> str:
    """Police choisie dans le bouton, en description Pango."""
    logger.trace("_lire_police")
    if hasattr(bouton, "get_font_desc"):
        desc = bouton.get_font_desc()
        resultat = desc.to_string() if desc is not None else ""
    else:
        resultat = bouton.get_font() or ""
    logger.trace("_lire_police: {!r}", resultat)
    return resultat


def _ecrire_police(bouton: Gtk.Widget, description: str) -> None:
    """Affiche ``description`` dans le bouton."""
    logger.trace("_ecrire_police: {!r}", description)
    if hasattr(bouton, "set_font_desc"):
        bouton.set_font_desc(Pango.FontDescription.from_string(description))
    else:
        bouton.set_font(description)
    logger.trace("_ecrire_police: fait")


def libelle_touches(touches: tuple[str, ...]) -> str:
    """Accélérateurs GTK → texte lisible et traduit (``Ctrl+Q``), séparés par « / »."""
    logger.trace("libelle_touches: {}", touches)
    libelles = []
    for accel in touches:
        ok, cle, mods = Gtk.accelerator_parse(accel)
        libelles.append(Gtk.accelerator_get_label(cle, mods) if ok and cle else accel)
    resultat = " / ".join(libelles)
    logger.trace("libelle_touches: {!r}", resultat)
    return resultat


def lancer_dossier(parent: Gtk.Window | None, dossier: str) -> None:
    """Ouvre ``dossier`` dans le gestionnaire de fichiers (asynchrone).

    Raises:
        GLib.Error: lancement impossible (GTK < 4.10 ; sinon erreur journalisée
            par le rappel).
    """
    logger.debug("lancer_dossier: {}", dossier)
    fichier = Gio.File.new_for_path(dossier)
    if hasattr(Gtk, "FileLauncher"):

        def fin(lanceur: Gtk.FileLauncher, resultat: Gio.AsyncResult) -> None:
            logger.debug("lancer_dossier.fin: entrée")
            try:
                lanceur.launch_finish(resultat)
            except GLib.Error as exc:
                logger.warning("lancer_dossier: {} ({})", dossier, exc.message)
            logger.debug("lancer_dossier.fin: fait")

        Gtk.FileLauncher.new(fichier).launch(parent, None, fin)
    else:  # GTK < 4.10
        Gio.AppInfo.launch_default_for_uri(fichier.get_uri(), None)
    logger.debug("lancer_dossier: demandé")


class PreferencesWindow(Adw.PreferencesWindow):
    """Pages Général et Apparence ; chaque changement est appliqué et enregistré."""

    def __init__(self, application: Adw.Application, fenetre: Gtk.Window | None) -> None:
        """Construit les pages depuis le fichier de configuration de l'application.

        Args:
            application: ``Gcm4Application`` (``config_file``, action ``theme``,
                :meth:`appliquer_police_arbre`).
            fenetre: fenêtre principale (``config``, action ``toggle-panel``),
                ou ``None``.
        """
        logger.debug("PreferencesWindow.__init__: entrée")
        super().__init__(
            title=_("Settings"), modal=True, transient_for=fenetre, application=application
        )
        self.set_search_enabled(False)  # peu de réglages : la recherche n'apporte rien
        self.application = application
        self.fenetre = fenetre
        self.config_file: str = application.config_file
        self.valeurs = prefs_core.load_preferences(self.config_file)
        etat_panneau = self._etat_action(fenetre, "toggle-panel")
        if etat_panneau is not None:  # la fenêtre fait foi (F9 non encore enregistré)
            self.valeurs.show_panel = etat_panneau.get_boolean()
        etat_theme = self._etat_action(application, "theme")
        if etat_theme is not None:
            self.valeurs.theme = etat_theme.get_string()
        self.erreur: str | None = None  # dernier échec d'écriture (tests)
        self.add(self._page_generale())
        self.add(self._page_apparence())
        self.add(self._page_raccourcis())
        self.add(self._page_journalisation())
        self.add(self._page_securite())
        self.add(self._page_extensions())
        for page in self.pages_plugins:
            self.add(page)
        logger.debug("PreferencesWindow.__init__: prête")

    @staticmethod
    def _etat_action(groupe: object, nom: str) -> GLib.Variant | None:
        """État de l'action ``nom`` de ``groupe`` (fenêtre ou application), sinon ``None``."""
        logger.trace("_etat_action: {}", nom)
        action = groupe.lookup_action(nom) if hasattr(groupe, "lookup_action") else None
        etat = action.get_state() if action is not None else None
        logger.trace("_etat_action: {}", etat)
        return etat

    # --- construction ----------------------------------------------------------

    def _page_generale(self) -> Adw.PreferencesPage:
        """Page Général : interrupteurs des options de la base."""
        logger.debug("_page_generale: entrée")
        page = Adw.PreferencesPage(title=_("General"), icon_name="preferences-system-symbolic")
        groupe = Adw.PreferencesGroup()
        self.interrupteurs: dict[str, Gtk.Switch] = {}
        for nom, libelle in OPTIONS_GENERALES:
            self._ligne_option(groupe, nom, libelle)
        if getattr(self.fenetre, "config", None) is None:  # illisible : jamais d'écriture
            groupe.set_sensitive(False)
        page.add(groupe)
        logger.debug("_page_generale: fait")
        return page

    def _ligne_option(self, groupe: Adw.PreferencesGroup, nom: str, libelle: str) -> None:
        """Interrupteur d'une option ``[options]`` (``AppConfig``), ajouté à ``groupe``."""
        logger.debug("_ligne_option: {}", nom)
        config = getattr(self.fenetre, "config", None)
        interrupteur = Gtk.Switch(valign=Gtk.Align.CENTER)
        valeur = getattr(config.options, nom) if config is not None else None
        interrupteur.set_active(getattr(self.valeurs, nom) if valeur is None else bool(valeur))
        ligne = Adw.ActionRow(title=_(libelle))
        ligne.add_suffix(interrupteur)
        ligne.set_activatable_widget(interrupteur)
        interrupteur.connect("notify::active", self._on_option, nom)
        groupe.add(ligne)
        self.interrupteurs[nom] = interrupteur
        logger.debug("_ligne_option: fait")

    def _page_apparence(self) -> Adw.PreferencesPage:
        """Page Apparence : thème, panneau, police de l'arbre."""
        logger.debug("_page_apparence: entrée")
        page = Adw.PreferencesPage(
            title=_("Appearance"), icon_name="applications-graphics-symbolic"
        )
        groupe = Adw.PreferencesGroup()

        self.theme = Adw.ComboRow(title=_("Theme"))
        self.theme.set_model(Gtk.StringList.new([_("System"), _("Light"), _("Dark")]))
        mode = self.valeurs.theme if self.valeurs.theme in MODES else "system"
        self.theme.set_selected(MODES.index(mode))
        self.theme.connect("notify::selected", self._on_theme)
        groupe.add(self.theme)

        self.panneau = Gtk.Switch(valign=Gtk.Align.CENTER, active=self.valeurs.show_panel)
        ligne = Adw.ActionRow(title=_("Show Panel"))
        ligne.add_suffix(self.panneau)
        ligne.set_activatable_widget(self.panneau)
        self.panneau.connect("notify::active", self._on_panneau)
        groupe.add(ligne)

        self.police_systeme = Gtk.Switch(
            valign=Gtk.Align.CENTER, active=not self.valeurs.tree_font
        )
        ligne = Adw.ActionRow(title=_("Use system font"))
        ligne.add_suffix(self.police_systeme)
        ligne.set_activatable_widget(self.police_systeme)
        groupe.add(ligne)
        self.police = _bouton_police()
        _ecrire_police(self.police, self.valeurs.tree_font or "Sans 11")
        self.ligne_police = Adw.ActionRow(title=_("Tree font"))
        self.ligne_police.add_suffix(self.police)
        self.ligne_police.set_activatable_widget(self.police)
        self.ligne_police.set_sensitive(bool(self.valeurs.tree_font))
        groupe.add(self.ligne_police)
        self.police_systeme.connect("notify::active", self._on_police)
        signal = "notify::font-desc" if hasattr(self.police, "get_font_desc") else "font-set"
        self.police.connect(signal, self._on_police)
        page.add(groupe)
        page.add(self._groupe_fenetre())
        logger.debug("_page_apparence: fait")
        return page

    def _groupe_fenetre(self) -> Adw.PreferencesGroup:
        """Apparence > Fenêtre principale : rayures de l'arbre, titre (#247).

        Appliqué tout de suite (``MainWindow.appliquer_options``). Titre vide :
        nom de l'application, comme GTK3.
        """
        logger.debug("_groupe_fenetre: entrée")
        groupe = Adw.PreferencesGroup(title=_("Main window"))
        for nom, libelle in OPTIONS_FENETRE:
            self._ligne_option(groupe, nom, libelle)
        config = getattr(self.fenetre, "config", None)
        titre = config.options.app_title if config is not None else self.valeurs.app_title
        if titre.strip() == comportement_onglets.NOM_APPLICATION:
            titre = ""  # valeur écrite par GTK3 pour « titre par défaut »
        self.titre = Adw.EntryRow(title=_("Title"), show_apply_button=True)
        self.titre.set_text(titre.strip())
        self.titre.connect("apply", self._on_titre)
        groupe.add(self.titre)
        if config is None:  # illisible : jamais d'écriture
            groupe.set_sensitive(False)
        logger.debug("_groupe_fenetre: fait")
        return groupe

    def _appliquer(self) -> None:
        """Répercute les options d'affichage sur la fenêtre principale, si elle le permet."""
        logger.debug("_appliquer: entrée")
        appliquer = getattr(self.fenetre, "appliquer_options", None)
        if callable(appliquer):
            appliquer()
        logger.debug("_appliquer: fait")

    def _on_titre(self, ligne: Adw.EntryRow) -> None:
        """Titre de la fenêtre validé (bouton ou Entrée) : modèle, fichier, fenêtre."""
        logger.debug("_on_titre: entrée")
        config = getattr(self.fenetre, "config", None)
        if config is None:
            logger.debug("_on_titre: aucune configuration chargée")
            return
        valeur = ligne.get_text().strip()
        config.options.app_title = valeur  # save_config réécrit [options] depuis le modèle
        self._enregistrer("app_title", valeur)
        self._appliquer()
        logger.debug("_on_titre: {!r}", valeur)

    def _raccourcis_effectifs(self) -> dict[str, tuple[str, ...]]:
        """Accélérateurs de la fenêtre : ceux de l'application, sinon relus dans le fichier."""
        logger.debug("_raccourcis_effectifs: entrée")
        lire = getattr(self.application, "raccourcis_effectifs", None)
        if callable(lire):
            resultat: dict[str, tuple[str, ...]] = lire()
        else:
            section = raccourcis_core.lire(self.config_file)
            resultat = raccourcis_core.effectifs(section, raccourcis_ui.resoudre)
        logger.debug("_raccourcis_effectifs: {} action(s)", len(resultat))
        return resultat

    def _page_raccourcis(self) -> Adw.PreferencesPage:
        """Page Raccourcis : ceux de la fenêtre se modifient (#247), ceux de l'arbre sont fixes.

        Les raccourcis de l'arbre des hôtes sont posés par l'arbre lui-même
        (``menu_arbre``) et non par l'application : ils restent en lecture.
        """
        logger.debug("_page_raccourcis: entrée")
        page = Adw.PreferencesPage(
            title=_("Shortcuts"), icon_name="preferences-desktop-keyboard-shortcuts-symbolic"
        )
        groupes = {
            prefs_core.FENETRE: Adw.PreferencesGroup(title=_("Main window")),
            prefs_core.ARBRE: Adw.PreferencesGroup(title=_("Hosts")),
        }
        groupes[prefs_core.FENETRE].set_description(
            _("Click a shortcut to change it, or press Backspace in the dialog to disable it.")
        )
        self.lignes_raccourcis: list[Adw.ActionRow] = []
        self.touches_raccourcis: dict[str, Gtk.Label] = {}
        self.saisie: raccourcis_ui.SaisieRaccourci | None = None  # dialogue ouvert (tests)
        for raccourci in prefs_core.raccourcis(self._raccourcis_effectifs()):
            ligne = Adw.ActionRow(title=_(raccourci.libelle))
            touches = Gtk.Label(label=self._libelle_raccourci(raccourci.touches))
            touches.add_css_class("dim-label")
            ligne.add_suffix(touches)
            if raccourci.action is not None:
                ligne.set_activatable(True)
                ligne.connect("activated", self._on_raccourci, raccourci.action)
                self.touches_raccourcis[raccourci.action] = touches
            groupes[raccourci.portee].add(ligne)
            self.lignes_raccourcis.append(ligne)
        for groupe in groupes.values():
            page.add(groupe)
        logger.debug("_page_raccourcis: {} raccourci(s)", len(self.lignes_raccourcis))
        return page

    @staticmethod
    def _libelle_raccourci(touches: tuple[str, ...]) -> str:
        """Libellé des touches ; aucune : « Disabled »."""
        logger.trace("_libelle_raccourci: {}", touches)
        return libelle_touches(touches) if touches else _("Disabled")

    def _suspendre_raccourcis(self, suspendre: bool) -> None:
        """Pendant la saisie, retire les accélérateurs de l'application (Ctrl+Q ne quitte pas).

        Rétablis ensuite depuis le fichier (``appliquer_raccourcis``).
        """
        logger.debug("_suspendre_raccourcis: {}", suspendre)
        appliquer = getattr(self.application, "appliquer_raccourcis", None)
        if not callable(appliquer):
            logger.debug("_suspendre_raccourcis: application sans raccourcis modifiables")
            return
        if suspendre:
            for action in menu.RACCOURCIS:
                self.application.set_accels_for_action(action, [])
        else:
            appliquer()
        logger.debug("_suspendre_raccourcis: fait")

    def _on_raccourci(self, _ligne: Adw.ActionRow, action: str) -> None:
        """Ligne d'un raccourci de la fenêtre activée : dialogue de saisie."""
        logger.debug("_on_raccourci: {}", action)
        if (
            self.saisie is not None
            and not self.saisie.traite
            and self.saisie.dialogue.get_mapped()
        ):
            logger.debug("_on_raccourci: saisie déjà ouverte")
            return
        actuels = self._raccourcis_effectifs()
        section = raccourcis_core.lire(self.config_file)

        def rappel(reponse: str, combinaison: raccourcis_core.Combinaison | None) -> None:
            logger.debug("_on_raccourci.rappel: {} {}", reponse, combinaison)
            self._enregistrer_raccourci(action, reponse, combinaison)
            logger.debug("_on_raccourci.rappel: fait")

        self._suspendre_raccourcis(True)
        self.saisie = raccourcis_ui.SaisieRaccourci(
            self, action, actuels, raccourcis_core.personnalise(section, action), rappel
        )
        self.saisie.dialogue.connect("response", lambda *_a: self._suspendre_raccourcis(False))
        logger.debug("_on_raccourci: dialogue ouvert")

    def _enregistrer_raccourci(
        self, action: str, reponse: str, combinaison: raccourcis_core.Combinaison | None
    ) -> None:
        """Écrit ``[shortcuts]`` (« set » ou « reset »), réapplique, met la ligne à jour."""
        logger.debug("_enregistrer_raccourci: {} {}", action, reponse)
        try:
            if reponse == "reset":
                raccourcis_core.reinitialiser(self.config_file, action)
            else:
                raccourcis_core.enregistrer(self.config_file, action, combinaison)
        except ConfigError as exc:
            logger.warning("_enregistrer_raccourci: {} non enregistré ({})", action, exc)
            self.erreur = str(exc)
            message = _("Preferences not saved: {error}").format(error=exc)
            if hasattr(self, "add_toast"):
                self.add_toast(Adw.Toast(title=message, timeout=5))
            logger.debug("_enregistrer_raccourci: échec signalé")
            return
        self._suspendre_raccourcis(False)
        effectifs = self._raccourcis_effectifs()
        self.touches_raccourcis[action].set_label(self._libelle_raccourci(effectifs[action]))
        logger.debug("_enregistrer_raccourci: {}", effectifs[action])

    def _page_journalisation(self) -> Adw.PreferencesPage:
        """Page Journalisation : niveau par défaut et dossier des journaux."""
        logger.debug("_page_journalisation: entrée")
        page = Adw.PreferencesPage(title=_("Logging"), icon_name="text-x-generic-symbolic")
        groupe = Adw.PreferencesGroup()
        args = getattr(self.application, "args", None)
        _niveau, self.source_journal = prefs_core.niveau_journal(
            bool(getattr(args, "debug", False)), self.valeurs.log_level, os.environ
        )
        self.niveau = Adw.ComboRow(title=_("Default log level"))
        self.niveau.set_model(Gtk.StringList.new(list(prefs_core.NIVEAUX_JOURNAL)))
        self.niveau.set_selected(prefs_core.NIVEAUX_JOURNAL.index(self.valeurs.log_level))
        if self.source_journal != "preferences":
            self.niveau.set_subtitle(_("Overridden for this session by --debug or GCM_LOG_LEVEL."))
        self.niveau.connect("notify::selected", self._on_niveau)
        groupe.add(self.niveau)

        config_dir = getattr(args, "config_dir", None) or os.path.dirname(self.config_file)
        self.dossier_journaux = os.path.dirname(logging_config.app_log_path(config_dir))
        ligne = Adw.ActionRow(title=_("Log folder"), subtitle=self.dossier_journaux)
        if hasattr(ligne, "set_subtitle_selectable"):  # libadwaita ≥ 1.3 (#223)
            ligne.set_subtitle_selectable(True)
        self.bouton_journaux = Gtk.Button(label=_("Open"), valign=Gtk.Align.CENTER)
        self.bouton_journaux.connect("clicked", self._on_ouvrir_journaux)
        ligne.add_suffix(self.bouton_journaux)
        groupe.add(ligne)
        page.add(groupe)
        logger.debug("_page_journalisation: source={}", self.source_journal)
        return page

    def _page_securite(self) -> Adw.PreferencesPage:
        """Page Sécurité : mot de passe maître facultatif (#98)."""
        logger.debug("_page_securite: entrée")
        page = Adw.PreferencesPage(title=_("Security"), icon_name="dialog-password-symbolic")
        chargee = getattr(self.fenetre, "config", None) is not None
        self.mot_de_passe_maitre = GroupeMotDePasseMaitre(
            getattr(self.fenetre, "chiffrement", None) if chargee else None,
            getattr(self.fenetre, "notifier", None),
        )
        page.add(self.mot_de_passe_maitre)
        logger.debug("_page_securite: fait (configuration chargée={})", chargee)
        return page

    def _page_extensions(self) -> Adw.PreferencesPage:
        """Page Extensions : plugins découverts ; pages de réglages des plugins."""
        logger.debug("_page_extensions: entrée")
        page = Adw.PreferencesPage(title=_("Extensions"), icon_name="application-x-addon-symbolic")
        groupe = Adw.PreferencesGroup()
        self.pages_plugins: list[Adw.PreferencesPage] = []
        self.lignes_extensions: dict[str, Adw.ActionRow] = {}
        registre = getattr(self.application, "registre", None)
        etats = list(getattr(registre, "etats", []))
        if not etats:
            groupe.set_description(_("No extension found."))
        for etat in etats:
            ligne = Adw.ActionRow(title=etat.name)
            ligne.set_use_markup(False)  # raison d'erreur : texte libre
            details = [_("Version {version}").format(version=etat.version)] if etat.version else []
            details.append(etat.source)
            if etat.erreur is not None:
                details.append(etat.erreur)
            elif etat.plugin is not None:
                details += self._page_du_plugin(etat.name, etat.plugin)
            ligne.set_subtitle(" — ".join(details))
            statut = Gtk.Label(label=_("Loaded") if etat.charge else _("Error"))
            statut.add_css_class("success" if etat.charge else "error")
            ligne.add_suffix(statut)
            groupe.add(ligne)
            self.lignes_extensions[f"{etat.name}:{etat.source}"] = ligne
        page.add(groupe)
        logger.debug("_page_extensions: {} extension(s)", len(etats))
        return page

    def _page_du_plugin(self, nom: str, plugin: object) -> list[str]:
        """Ajoute la page ``build_preferences_page()`` du plugin ; renvoie l'erreur éventuelle."""
        logger.debug("_page_du_plugin: {}", nom)
        try:
            widget = plugin.build_preferences_page()
        except Exception as exc:  # code tiers : ne doit pas fermer les préférences
            logger.exception("_page_du_plugin: {} a échoué", nom)
            logger.debug("_page_du_plugin: erreur")
            return [_("Preferences page unavailable: {error}").format(error=exc)]
        if widget is None:
            logger.debug("_page_du_plugin: aucune page")
            return []
        if isinstance(widget, Adw.PreferencesPage):
            page = widget
            if not page.get_title():
                page.set_title(nom)
        else:
            page = Adw.PreferencesPage(title=nom, icon_name="application-x-addon-symbolic")
            groupe = Adw.PreferencesGroup()
            groupe.add(widget)
            page.add(groupe)
        self.pages_plugins.append(page)
        logger.debug("_page_du_plugin: page ajoutée")
        return []

    # --- application immédiate ---------------------------------------------------

    def _on_niveau(self, ligne: Adw.ComboRow, _pspec: object) -> None:
        """Niveau de journal : enregistré ; appliqué si rien ne prime (#130)."""
        logger.debug("_on_niveau: entrée")
        choisi = ligne.get_selected()
        if choisi >= len(prefs_core.NIVEAUX_JOURNAL):
            logger.debug("_on_niveau: aucune sélection")
            return
        niveau = prefs_core.NIVEAUX_JOURNAL[choisi]
        if self.source_journal == "preferences":
            config_dir = os.path.dirname(self.dossier_journaux)
            logging_config.configure_logging(niveau, config_dir=config_dir, force=True)
        self._enregistrer("log_level", niveau)
        logger.debug("_on_niveau: {}", niveau)

    def _on_ouvrir_journaux(self, _bouton: Gtk.Button) -> None:
        """Ouvre le dossier des journaux (créé au besoin) ; échec signalé."""
        logger.debug("_on_ouvrir_journaux: {}", self.dossier_journaux)
        try:
            os.makedirs(self.dossier_journaux, mode=0o700, exist_ok=True)
            lancer_dossier(self, self.dossier_journaux)
        except (OSError, GLib.Error) as exc:
            logger.warning("_on_ouvrir_journaux: {}", exc)
            self.erreur = str(exc)
            if hasattr(self, "add_toast"):
                message = _("Unable to open the folder: {error}").format(error=exc)
                self.add_toast(Adw.Toast(title=message, timeout=5))
        logger.debug("_on_ouvrir_journaux: fait")

    def _enregistrer(self, nom: str, valeur: bool | str) -> bool:
        """Écrit la préférence ; un échec est signalé (toast) sans annuler le changement."""
        logger.debug("_enregistrer: {}", nom)
        try:
            prefs_core.save_preference(self.config_file, nom, valeur)
        except ConfigError as exc:
            logger.warning("_enregistrer: {} non enregistrée ({})", nom, exc)
            self.erreur = str(exc)
            message = _("Preferences not saved: {error}").format(error=exc)
            if hasattr(self, "add_toast"):
                self.add_toast(Adw.Toast(title=message, timeout=5))
            logger.debug("_enregistrer: échec signalé")
            return False
        setattr(self.valeurs, nom, valeur)
        logger.debug("_enregistrer: fait")
        return True

    def _on_option(self, interrupteur: Gtk.Switch, _pspec: object, nom: str) -> None:
        """Interrupteur de la page Général : modèle en mémoire puis fichier."""
        logger.debug("_on_option: entrée")
        valeur = interrupteur.get_active()
        logger.debug("_on_option: {} = {}", nom, valeur)
        config = getattr(self.fenetre, "config", None)
        if config is None:
            logger.debug("_on_option: aucune configuration chargée")
            return
        setattr(config.options, nom, valeur)  # save_config réécrit [options] depuis le modèle
        if self._enregistrer(nom, valeur) and nom == "check_updates" and valeur:
            # Activer l'option vaut accord explicite (#245).
            mises_a_jour.enregistrer_accord(self.config_file, True)
        if nom in dict(OPTIONS_FENETRE):
            self._appliquer()
        logger.debug("_on_option: fait")

    def _on_theme(self, ligne: Adw.ComboRow, _pspec: object) -> None:
        """Thème : l'action ``app.theme`` l'applique et l'enregistre (comme le menu)."""
        logger.debug("_on_theme: entrée")
        choisi = ligne.get_selected()
        logger.debug("_on_theme: {}", choisi)
        if choisi >= len(MODES):
            logger.debug("_on_theme: aucune sélection")
            return
        mode = MODES[choisi]
        self.application.change_action_state("theme", GLib.Variant("s", mode))
        self.valeurs.theme = mode
        logger.debug("_on_theme: {}", mode)

    def _on_panneau(self, interrupteur: Gtk.Switch, _pspec: object) -> None:
        """Panneau visible : même action que F9, puis enregistrement."""
        logger.debug("_on_panneau: entrée")
        valeur = interrupteur.get_active()
        logger.debug("_on_panneau: {}", valeur)
        if self.fenetre is not None and self.fenetre.lookup_action("toggle-panel") is not None:
            self.fenetre.change_action_state("toggle-panel", GLib.Variant.new_boolean(valeur))
        self._enregistrer("show_panel", valeur)
        logger.debug("_on_panneau: fait")

    def _on_police(self, *_args: object) -> None:
        """Police de l'arbre : appliquée tout de suite, puis enregistrée."""
        logger.debug("_on_police: entrée")
        systeme = self.police_systeme.get_active()
        self.ligne_police.set_sensitive(not systeme)
        description = "" if systeme else _lire_police(self.police)
        appliquer = getattr(self.application, "appliquer_police_arbre", None)
        if callable(appliquer):
            appliquer(description)
        self._enregistrer("tree_font", description)
        logger.debug("_on_police: {!r}", description)
