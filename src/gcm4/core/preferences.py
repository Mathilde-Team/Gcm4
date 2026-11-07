# SPDX-License-Identifier: GPL-3.0-or-later
"""Préférences générales de la base GTK4, sans GTK (lot 5a, #147).

Deux rôles :

- :data:`INVENTAIRE` classe **chaque** option du dialogue GTK3 ``Wconfig``
  (``gnome_connection_manager.py``) : base, terminal (futur plugin terminal)
  ou protocole/plugin. ``tests/test_gcm4_preferences.py`` relit ``Wconfig``
  et échoue si une option n'y est pas classée (critère d'acceptation de
  #147 : aucune option ne disparaît sans classement explicite) ;
- :class:`Preferences`, :func:`load_preferences` et :func:`save_preference`
  lisent et écrivent les options des pages **Général** et **Apparence**,
  avec les clés de l'application GTK3 (``[options]``, ``[window]``) pour
  passer de l'une à l'autre sans rien perdre.

Écriture : :func:`gcm4.core.config.update_keys` (lecture stricte, fichier
temporaire 0600, ``fsync``, ``os.replace``, ``.bak``) ; seule la clé
modifiée change, le reste du fichier est conservé. Les clés de
``[options]`` sont aussi écrites par ``save_config`` depuis
``Config.options`` : l'appelant met donc à jour le modèle en mémoire en
même temps (voir ``gcm4.ui.preferences``).
"""

from __future__ import annotations

import configparser
from collections.abc import Mapping
from dataclasses import dataclass

from loguru import logger

from gcm4.core import comportement_onglets, menu, theme, window_state
from gcm4.core.config import SECTION_OPTIONS, update_keys
from gcm4.core.i18n import N_

# --- inventaire de Wconfig ------------------------------------------------------

#: Option de la base GTK4.
BASE = "base"
#: Option des terminaux : fournie par le futur plugin terminal (VTE).
TERMINAL = "terminal"
#: Option d'un protocole ou d'un plugin : fournie par ce plugin.
PROTOCOLE = "protocole"
CLASSEMENTS = (BASE, TERMINAL, PROTOCOLE)


@dataclass(frozen=True)
class OptionWconfig:
    """Une option de ``Wconfig`` et sa destination dans gcm4."""

    #: Nom dans ``Wconfig`` (``conf.X``, ou nom d'onglet pour les onglets entiers).
    nom: str
    #: ``section``/``clé`` INI (vide pour un onglet sans clé unique).
    cle: str
    #: :data:`BASE`, :data:`TERMINAL` ou :data:`PROTOCOLE`.
    classement: str
    #: Où l'option vit dans gcm4 (page, lot ou issue de suivi).
    destination: str


#: Toutes les options de ``Wconfig`` (onglets Général, Couleurs, Raccourcis
#: et onglets ajoutés par les plugins), dans l'ordre du dialogue GTK3.
INVENTAIRE: tuple[OptionWconfig, ...] = (
    # Onglet « General » (Wconfig.addParam)
    OptionWconfig("WORD_SEPARATORS", "options/word-separators", TERMINAL, "plugin terminal"),
    OptionWconfig("BUFFER_LINES", "options/buffer-lines", TERMINAL, "plugin terminal"),
    OptionWconfig("TRANSPARENCY", "options/transparency", TERMINAL, "plugin terminal"),
    OptionWconfig("TERM", "options/term", TERMINAL, "plugin terminal"),
    OptionWconfig(
        "LOG_PATH", "options/log-path", TERMINAL, "plugin terminal (journal des sessions)"
    ),
    OptionWconfig("CONTINUOUS_TAB_LOG", "options/continuous-tab-log", TERMINAL, "plugin terminal"),
    OptionWconfig("STARTUP_LOCAL", "options/startup-local", PROTOCOLE, "plugin local"),
    OptionWconfig("LOG_LOCAL", "options/log-local", PROTOCOLE, "plugin local"),
    OptionWconfig(
        "PASTE_ON_RIGHT_CLICK", "options/paste-right-click", TERMINAL, "plugin terminal"
    ),
    OptionWconfig(
        "AUTO_COPY_SELECTION", "options/auto-copy-selection", TERMINAL, "plugin terminal"
    ),
    OptionWconfig(
        "CONFIRM_ON_CLOSE_TAB", "options/confirm-close-tab", BASE, "page Général (#247)"
    ),
    OptionWconfig(
        "CONFIRM_ON_CLOSE_TAB_MIDDLE",
        "options/confirm-close-tab-middle",
        BASE,
        "page Général (#247)",
    ),
    OptionWconfig("CYCLE_TABS", "options/cycle-tabs", BASE, "page Général (#247)"),
    OptionWconfig("DISABLE_SHORTCUTS", "options/disable-shortcuts", TERMINAL, "plugin terminal"),
    OptionWconfig(
        "DISABLE_NOTIFICATIONS", "options/disable-notifications", TERMINAL, "plugin terminal"
    ),
    OptionWconfig(
        "RESTORE_OPEN_TABS", "options/restore-open-tabs", BASE, "page Général (#147), #246"
    ),
    OptionWconfig("AUTO_CLOSE_TAB", "options/auto-close-tab", TERMINAL, "plugin terminal"),
    OptionWconfig("CONFIRM_ON_EXIT", "options/confirm-exit", BASE, "page Général (#147)"),
    OptionWconfig("CHECK_UPDATES", "options/check-updates", BASE, "page Général (#147), #245"),
    OptionWconfig(
        "DISABLE_HOSTS_STRIPES",
        "options/disable-hosts-stripes",
        BASE,
        "page Apparence (#247)",
    ),
    OptionWconfig("UPDATE_TITLE", "options/update-title", BASE, "page Apparence (#247)"),
    OptionWconfig("APP_TITLE", "options/app-title", BASE, "page Apparence (#247)"),
    # Onglet « Colors »
    OptionWconfig("FONT_COLOR", "options/font-color", TERMINAL, "plugin terminal"),
    OptionWconfig("BACK_COLOR", "options/back-color", TERMINAL, "plugin terminal"),
    OptionWconfig("FONT", "options/font", TERMINAL, "plugin terminal (police VTE)"),
    OptionWconfig("THEME_MODE", "window/theme-mode", BASE, "page Apparence (#147)"),
    OptionWconfig("DARK_MODE", "window/dark-mode", BASE, "écrite avec theme-mode (#135)"),
    # Onglet « Shortcuts »
    OptionWconfig(
        "shortcuts",
        "shortcuts/*",
        BASE,
        "page Raccourcis (#148) ; fenêtre modifiable (#247) ; terminal : plugin terminal",
    ),
    OptionWconfig("custom commands", "shortcuts/*", TERMINAL, "plugin terminal"),
    # Onglets ajoutés par les plugins et modèles série
    OptionWconfig("build_preferences_tab", "", PROTOCOLE, "plugin.build_preferences_page (#148)"),
    OptionWconfig("SerialTemplatesTab", "", PROTOCOLE, "plugin série"),
)

# --- préférences des pages Général et Apparence ----------------------------------

#: Police de l'arbre des hôtes (nouvelle clé gcm4, absente de GTK3) :
#: description Pango (``"Sans 11"``), vide = police du système.
CLE_POLICE_ARBRE = "tree-font"


@dataclass
class Preferences:
    """Options des pages Général et Apparence (valeurs par défaut de GTK3)."""

    confirm_exit: bool = True
    check_updates: bool = True
    restore_open_tabs: bool = False
    theme: str = "system"
    show_panel: bool = True
    tree_font: str = ""
    log_level: str = "INFO"
    confirm_close_tab: bool = True
    confirm_close_tab_middle: bool = True
    cycle_tabs: bool = True
    disable_hosts_stripes: bool = False
    update_title: bool = False
    #: Icône de la zone de notification (StatusNotifierItem, #83).
    tray_icon: bool = False
    #: Vide = nom de l'application (GTK3 écrit ce nom quand le titre est vide).
    app_title: str = ""


#: Préférence → (section, clé) INI. ``theme`` passe par :func:`theme.save_theme`.
CLES: dict[str, tuple[str, str]] = {
    "confirm_exit": (SECTION_OPTIONS, "confirm-exit"),
    "check_updates": (SECTION_OPTIONS, "check-updates"),
    "restore_open_tabs": (SECTION_OPTIONS, "restore-open-tabs"),
    "theme": (window_state.SECTION, theme.CLE_THEME),
    "show_panel": (window_state.SECTION, window_state.CLE_PANNEAU_VISIBLE),
    "tree_font": (window_state.SECTION, CLE_POLICE_ARBRE),
    "log_level": (SECTION_OPTIONS, "log-level"),
    "confirm_close_tab": (SECTION_OPTIONS, "confirm-close-tab"),
    "confirm_close_tab_middle": (SECTION_OPTIONS, "confirm-close-tab-middle"),
    "cycle_tabs": (SECTION_OPTIONS, "cycle-tabs"),
    "disable_hosts_stripes": (SECTION_OPTIONS, "disable-hosts-stripes"),
    "update_title": (SECTION_OPTIONS, "update-title"),
    "tray_icon": (SECTION_OPTIONS, "tray-icon"),
    "app_title": (SECTION_OPTIONS, "app-title"),
}

#: Préférences booléennes lues par :func:`load_preferences`.
BOOLEENS = (
    "confirm_exit",
    "check_updates",
    "restore_open_tabs",
    "show_panel",
    "confirm_close_tab",
    "confirm_close_tab_middle",
    "cycle_tabs",
    "disable_hosts_stripes",
    "update_title",
    "tray_icon",
)

#: Niveaux proposés dans Préférences > Journalisation (#130, #148).
NIVEAUX_JOURNAL = ("TRACE", "DEBUG", "INFO", "WARNING", "ERROR")
#: Variables d'environnement prioritaires sur la préférence (``logging_config``).
VARIABLES_JOURNAL = ("GCM_LOG_LEVEL", "GCM_DEBUG")


def _booleen(cp: configparser.RawConfigParser, section: str, cle: str, defaut: bool) -> bool:
    """Booléen INI ; absent ou invalide → ``defaut`` (avertissement si invalide)."""
    logger.trace("_booleen: [{}] {}", section, cle)
    try:
        resultat = cp.getboolean(section, cle, fallback=defaut)
    except ValueError:
        logger.warning("_booleen: [{}] {} invalide, {} par défaut", section, cle, defaut)
        resultat = defaut
    logger.trace("_booleen: {}", resultat)
    return resultat


def load_preferences(config_file: str) -> Preferences:
    """Préférences enregistrées ; fichier absent ou illisible → valeurs par défaut."""
    logger.debug("load_preferences: {}", config_file)
    cp = window_state._lire(config_file)
    prefs = Preferences()
    for nom in BOOLEENS:
        section, cle = CLES[nom]
        setattr(prefs, nom, _booleen(cp, section, cle, getattr(prefs, nom)))
    prefs.theme = theme.load_theme(config_file)
    section, cle = CLES["tree_font"]
    prefs.tree_font = cp.get(section, cle, fallback="").strip()
    section, cle = CLES["log_level"]
    niveau = cp.get(section, cle, fallback=prefs.log_level).strip().upper()
    if niveau in NIVEAUX_JOURNAL:
        prefs.log_level = niveau
    else:
        logger.warning("load_preferences: log-level {!r} inconnu, INFO", niveau)
    section, cle = CLES["app_title"]
    titre = cp.get(section, cle, fallback="").strip()
    # GTK3 écrit le nom de l'application quand le titre est vide : même sens.
    prefs.app_title = "" if titre == comportement_onglets.NOM_APPLICATION else titre
    logger.debug("load_preferences: {}", prefs)
    return prefs


def save_preference(config_file: str, nom: str, valeur: bool | str) -> None:
    """Enregistre une préférence sans toucher au reste du fichier.

    Raises:
        ValueError: préférence inconnue, type ou thème invalide.
        ConfigError: fichier corrompu (jamais réécrit) ou écriture impossible.
    """
    logger.debug("save_preference: {} {}", config_file, nom)
    if nom not in CLES:
        raise ValueError(nom)
    attendu = type(getattr(Preferences(), nom))
    if type(valeur) is not attendu:
        raise ValueError(f"{nom} : {attendu.__name__} attendu")
    if nom == "log_level" and valeur not in NIVEAUX_JOURNAL:
        raise ValueError(f"niveau inconnu : {valeur!r}")
    if nom == "theme":
        theme.save_theme(config_file, str(valeur))  # écrit aussi dark-mode (GTK3)
        logger.debug("save_preference: thème enregistré")
        return
    section, cle = CLES[nom]
    texte = str(valeur).strip() if isinstance(valeur, str) else str(valeur)
    update_keys(config_file, section, {cle: texte})
    logger.debug("save_preference: [{}] {} enregistrée", section, cle)


def niveau_journal(
    debug_cli: bool, preference: str, environ: Mapping[str, str]
) -> tuple[str, str]:
    """Niveau de journal effectif et sa source : ``cli``, ``env`` ou ``preferences``.

    Priorité : ``--debug``, puis ``GCM_LOG_LEVEL`` / ``GCM_DEBUG``
    (``logging_config``), puis la préférence enregistrée.
    """
    logger.debug("niveau_journal: debug={} préférence={}", debug_cli, preference)
    if debug_cli:
        logger.debug("niveau_journal: DEBUG (ligne de commande)")
        return "DEBUG", "cli"
    if any(environ.get(v, "").strip() for v in VARIABLES_JOURNAL):
        logger.debug("niveau_journal: environnement")
        return "", "env"  # logging_config.level_from_env() décide
    niveau = preference if preference in NIVEAUX_JOURNAL else "INFO"
    logger.debug("niveau_journal: {} (préférences)", niveau)
    return niveau, "preferences"


def libelles_actions() -> dict[str, str]:
    """Action du menu principal -> libellé (msgid) ; action sans entrée : son nom."""
    logger.debug("libelles_actions: entrée")
    libelles = {
        e.action: e.libelle for sm in menu.MENU_PRINCIPAL for sec in sm.sections for e in sec
    }
    resultat = {action: libelles.get(action, action) for action in menu.RACCOURCIS}
    logger.debug("libelles_actions: {}", len(resultat))
    return resultat


@dataclass(frozen=True)
class Raccourci:
    """Raccourci clavier de la base, affiché dans Préférences > Raccourcis."""

    #: Accélérateurs GTK (``"<Control>q"``) ; le premier est affiché en premier.
    touches: tuple[str, ...]
    #: Libellé (msgid, traduit à l'affichage).
    libelle: str
    #: :data:`FENETRE` ou :data:`ARBRE`.
    portee: str
    #: Action modifiable (``win.next-tab``…) ; ``None`` : raccourci de l'arbre, fixe.
    action: str | None = None


FENETRE = "fenetre"
ARBRE = "arbre"

#: Raccourcis de la liste des hôtes (posés par ``gcm4.ui.menu_arbre``).
RACCOURCIS_ARBRE: tuple[tuple[str, str], ...] = (
    ("Menu|<Shift>F10", N_("Open the context menu")),
    ("Delete|KP_Delete", N_("Remove")),
    ("F2", N_("Rename the group or edit the host")),
    ("Return", N_("Connect")),
)


def raccourcis(effectifs: Mapping[str, tuple[str, ...]] | None = None) -> list[Raccourci]:
    """Raccourcis de la base : ceux du menu principal puis ceux de l'arbre.

    Args:
        effectifs (Mapping[str, tuple[str, ...]] | None): Accélérateurs posés
            (``gcm4.core.raccourcis.effectifs``) ; ``None`` : valeurs par défaut.
    """
    logger.debug("raccourcis: entrée")
    libelles = libelles_actions()
    poses = menu.RACCOURCIS if effectifs is None else effectifs
    resultat = [
        Raccourci(tuple(poses.get(action, defaut)), libelles[action], FENETRE, action)
        for action, defaut in menu.RACCOURCIS.items()
    ]
    resultat += [
        Raccourci(tuple(declencheur.split("|")), libelle, ARBRE)
        for declencheur, libelle in RACCOURCIS_ARBRE
    ]
    logger.debug("raccourcis: {}", len(resultat))
    return resultat
