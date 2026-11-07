# SPDX-License-Identifier: GPL-3.0-or-later
"""Menu principal et raccourcis de gcm4, décrits sans GTK (#134).

Structure reprise de ``Wmain._build_primary_menu`` (GTK3), limitée à la
base sans plugins (#118) : ce qui sert au terminal, au cluster ou aux
outils de plugins arrivera avec eux. ``gcm4.ui`` construit le
``Gio.Menu`` à partir de ces données, qui restent testables sans display.
Libellés : msgids anglais de l'ancienne interface, marqués ``N_`` pour
l'extraction (traduits à l'affichage par ``gcm4.ui``).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from loguru import logger

from gcm4.core.i18n import N_


@dataclass(frozen=True)
class Entree:
    """Ligne de menu : msgid du libellé, nom d'action (``app.x`` ou ``win.x``).

    ``cible`` : valeur de l'action à état (entrée radio), ``None`` sinon.
    """

    libelle: str
    action: str
    cible: str | None = None


@dataclass(frozen=True)
class SousMenu:
    """Sous-menu : msgid du titre et sections (séparées visuellement)."""

    titre: str
    sections: tuple[tuple[Entree, ...], ...]


MENU_PRINCIPAL: tuple[SousMenu, ...] = (
    SousMenu(
        N_("File"),
        (
            # Import/export des hôtes et copie de la configuration (#153).
            (
                Entree(N_("Import servers"), "win.import-hosts"),
                Entree(N_("Export servers"), "win.export-hosts"),
                Entree(N_("Save configuration as…"), "win.save-config-as"),
            ),
            (Entree(N_("Quit"), "app.quit"),),
        ),
    ),
    SousMenu(N_("Edit"), ((Entree(N_("Settings"), "app.preferences"),),)),
    SousMenu(
        N_("View"),
        (
            (Entree(N_("Show Panel"), "win.toggle-panel"), Entree(N_("Search"), "win.search")),
            # Onglets (#247) : bouclage selon [options] cycle-tabs.
            (
                Entree(N_("Previous tab"), "win.previous-tab"),
                Entree(N_("Next tab"), "win.next-tab"),
            ),
            # Thème (#135) : action à état app.theme, une entrée radio par mode.
            (
                Entree(N_("System"), "app.theme", "system"),
                Entree(N_("Light"), "app.theme", "light"),
                Entree(N_("Dark"), "app.theme", "dark"),
            ),
        ),
    ),
    SousMenu(
        N_("Help"),
        (
            # Aide (#86) : guide utilisateur en ligne et fenêtre des raccourcis.
            (
                Entree(N_("User Guide"), "app.help"),
                Entree(N_("Keyboard Shortcuts"), "app.shortcuts"),
            ),
            (Entree(N_("About"), "app.about"),),
        ),
    ),
)

#: Guide utilisateur ouvert par Aide > Guide utilisateur (F1, #86) : la
#: documentation du dépôt, rien à maintenir en double dans l'application.
#: ``docs/guide/utilisateur.md`` n'est qu'une inclusion MkDocs de
#: ``Documentation-fr.md`` (une ligne sur GitHub, aucun site Pages publié) :
#: on ouvre donc le fichier inclus, rendu par GitHub.
GUIDE_UTILISATEUR = "https://github.com/Mathilde-Team/Gcm4/blob/main/Documentation-fr.md"

RACCOURCIS: dict[str, tuple[str, ...]] = {
    "app.quit": ("<Control>q",),
    "app.preferences": ("<Control>comma",),
    "app.help": ("F1",),
    "app.shortcuts": ("<Control>question",),
    "win.toggle-panel": ("F9",),
    "win.search": ("<Control>f",),
    # GTK3 : CTRL+SHIFT+TAB / CTRL+TAB ; Ctrl+Page précédente/suivante comme Gtk.Notebook.
    "win.previous-tab": ("<Control>Page_Up", "<Shift><Control>Tab"),
    "win.next-tab": ("<Control>Page_Down", "<Control>Tab"),
}


def actions_du_menu(menu: tuple[SousMenu, ...] | None = None) -> list[str]:
    """Toutes les actions citées par le menu, dans l'ordre d'apparition."""
    logger.debug("actions_du_menu: entrée")
    if menu is None:
        menu = MENU_PRINCIPAL
    actions = list(
        dict.fromkeys(e.action for sm in menu for section in sm.sections for e in section)
    )
    logger.debug("actions_du_menu: {}", actions)
    return actions


def action_outil(name: str) -> str:
    """Nom de l'action GTK de l'outil ``name`` : ``win.outil-<name>`` (caractères sûrs)."""
    logger.trace("action_outil: {!r}", name)
    sur = "".join(c if (c.isalnum() and c.isascii()) or c == "-" else "-" for c in name.lower())
    resultat = f"win.outil-{sur}"
    logger.trace("action_outil: {}", resultat)
    return resultat


def menu_avec_outils(
    menu: tuple[SousMenu, ...] | None = None,
    outils: Iterable[Any] = (),
) -> tuple[SousMenu, ...]:
    """Menu complété des entrées des plugins outils (#73).

    Chaque outil devient une entrée d'action ``win.outil-<name>`` (le libellé
    du menu est traduit à l'affichage par ``gcm4.ui``, comme ``MENU_PRINCIPAL``) :

    - ``menu_section = "import"`` ou ``"export"`` : nouvelle section du menu
      Fichier, après les imports d'hôtes ;
    - ``"outils"`` (défaut) : sous-menu « Tools » ajouté en fin de menu,
      seulement s'il y a au moins un outil — un menu sans outil est
      inchangé. Le msgid du sous-menu est ``"Tools"``.

    Args:
        menu: menu de base (défaut : :data:`MENU_PRINCIPAL`).
        outils: plugins outils chargés (attributs ``name``, ``menu_label``,
            ``menu_section``) ; tout autre objet de cet shape convient aux tests.
    """
    logger.debug("menu_avec_outils: {} outil(s)", len(list(outils)))
    if menu is None:
        menu = MENU_PRINCIPAL
    ajoutes: dict[str, list[Entree]] = {"import": [], "export": [], "outils": []}
    for outil in outils:
        ajoutes.setdefault(getattr(outil, "menu_section", "outils"), []).append(
            Entree(getattr(outil, "menu_label", outil.name), action_outil(outil.name))
        )
    if not any(ajoutes.values()):
        logger.debug("menu_avec_outils: aucun outil, menu inchangé")
        return menu
    resultat = list(menu)
    fichier = next(i for i, sm in enumerate(resultat) if sm.titre == N_("File"))
    sections_fichier = list(resultat[fichier].sections)
    if ajoutes["import"] or ajoutes["export"]:
        sections_fichier.append((*ajoutes["import"], *ajoutes["export"]))
        resultat[fichier] = SousMenu(resultat[fichier].titre, tuple(sections_fichier))
    if ajoutes["outils"]:
        resultat.append(SousMenu(N_("Tools"), (tuple(ajoutes["outils"]),)))
    logger.debug("menu_avec_outils: {} sous-menu(s)", len(resultat))
    return tuple(resultat)
