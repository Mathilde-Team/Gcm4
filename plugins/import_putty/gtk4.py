# SPDX-License-Identifier: GPL-3.0-or-later
"""Outil « Import from PuTTY sessions » (contrat v1, #436, portage #179).

Portage GTK4 de ``plugins/plugin_import_putty.py`` (``BatchPlugin`` GTK3,
supprimé) : un plugin outil ouvre un onglet de choix du dossier des
sessions, montre le résumé du scan et la stratégie de conflit (les trois
mêmes que l'import de fichiers, #153), puis importe via le contexte du
plugin (``import_hosts``, #436). Le scan/parsing (zéro GTK) reste dans
:mod:`core`, inchangé ; PuTTY ne stocke jamais de mot de passe en clair,
les sessions importées n'en portent donc aucun.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")  # déjà fixé par gcm4 ; déclare le fichier GTK4 (#178)

from gi.repository import Gtk  # noqa: E402
from loguru import logger  # noqa: E402

from gcm4.core.exchange import Conflit, dict_vers_hote
from gcm4.core.i18n import N_, _  # noqa: E402
from gcm4.core.plugins import SECTION_IMPORT, ToolPlugin  # noqa: E402
from gcm4.core.sauvegarde import bilan
from gcm4.ui import fichiers  # noqa: E402

from . import core  # noqa: E402

if TYPE_CHECKING:
    from gcm4.core.exchange import Rapport
    from gcm4.core.model import Host

#: Stratégies proposées, dans l'ordre affiché (la première par défaut) —
#: les mêmes que l'import de fichiers (``gcm4.ui.echange``, #153).
STRATEGIES: tuple[Conflit, ...] = (Conflit.IGNORER, Conflit.RENOMMER, Conflit.REMPLACER)


def _libelle(conflit: Conflit) -> str:
    """Libellé traduit d'une stratégie de conflit (mêmes msgid que #153)."""
    logger.trace("_libelle: {}", conflit.value)
    libelles = {
        Conflit.IGNORER: _("Keep the existing host"),
        Conflit.RENOMMER: _("Rename the imported host"),
        Conflit.REMPLACER: _("Replace the existing host"),
    }
    logger.trace("_libelle: fait")
    return libelles[conflit]


class OngletImportPutty(Gtk.Box):
    """Onglet d'import PuTTY : choix du dossier, résumé, stratégie, bouton.

    Le sélecteur de dossier passe par :func:`gcm4.ui.fichiers.choisir`
    (GTK 4.10 ou repli 4.8, Debian 12 compris), asynchrone : rien ne
    bloque la boucle GTK.
    """

    def __init__(self, context: object) -> None:
        """Construit l'onglet vide : rien n'est scanné avant le choix du dossier."""
        logger.debug("OngletImportPutty.__init__: entrée")
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.set_margin_top(12)
        self.set_margin_bottom(12)
        self.set_margin_start(12)
        self.set_margin_end(12)
        self.context = context
        #: Dossier des sessions choisi (``None`` : aucun).
        self.dossier: Path | None = None
        #: Hôtes issus du scan du dossier choisi.
        self.hotes: list[Host] = []
        #: Sessions ignorées par le scan, avec la raison.
        self.ignores: list[str] = []

        ligne = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.etiquette_dossier = Gtk.Label(xalign=0, wrap=True)
        self._maj_dossier()
        bouton_dossier = Gtk.Button(label=_("Choose a folder…"))
        bouton_dossier.connect("clicked", self._on_choisir_dossier)
        ligne.append(self.etiquette_dossier)
        ligne.append(bouton_dossier)
        self.append(ligne)

        self.etiquette_resume = Gtk.Label(xalign=0, wrap=True)
        self.etiquette_resume.add_css_class("dim-label")
        self.append(self.etiquette_resume)

        self.append(Gtk.Label(label=_("If a host already exists:"), xalign=0))
        self.boutons_conflit: dict[Conflit, Gtk.CheckButton] = {}
        premier: Gtk.CheckButton | None = None
        for conflit in STRATEGIES:
            bouton = Gtk.CheckButton(label=_libelle(conflit))
            if premier is None:
                premier = bouton
                bouton.set_active(True)
            else:
                bouton.set_group(premier)
            self.boutons_conflit[conflit] = bouton
            self.append(bouton)

        self.bouton_importer = Gtk.Button(label=_("Import"))
        self.bouton_importer.add_css_class("suggested-action")
        self.bouton_importer.set_sensitive(False)
        self.bouton_importer.connect("clicked", self._on_importer)
        self.append(self.bouton_importer)
        logger.debug("OngletImportPutty.__init__: fait")

    def conflit_choisi(self) -> Conflit:
        """Stratégie cochée (``IGNORER`` par défaut)."""
        logger.trace("conflit_choisi")
        resultat = next(
            (c for c, b in self.boutons_conflit.items() if b.get_active()), Conflit.IGNORER
        )
        logger.trace("conflit_choisi: {}", resultat.value)
        return resultat

    def _maj_dossier(self) -> None:
        """Libellé du dossier choisi (ou du défaut, s'il existe)."""
        logger.trace("_maj_dossier")
        dossier = self.dossier
        if dossier is None and core.DEFAULT_SESSIONS_DIR.is_dir():
            dossier = core.DEFAULT_SESSIONS_DIR
        self.etiquette_dossier.set_label(
            _("PuTTY sessions folder: {folder}").format(folder=str(dossier) if dossier else "—")
        )
        logger.trace("_maj_dossier: fait")

    def _on_choisir_dossier(self, _bouton: Gtk.Button) -> None:
        """Ouvre le sélecteur de dossier (asynchrone, jamais bloquant)."""
        logger.debug("_on_choisir_dossier: entrée")
        parent = self.get_root()
        if not isinstance(parent, Gtk.Window):
            logger.debug("_on_choisir_dossier: pas de fenêtre parente, ignoré")
            return
        fichiers.choisir(
            parent,
            fichiers.DOSSIER,
            _("Select PuTTY sessions folder"),
            self._dossier_choisi,
        )
        logger.debug("_on_choisir_dossier: sélecteur ouvert")

    def _dossier_choisi(self, chemin: str | None) -> None:
        """Scan du dossier choisi et mise à jour du résumé ; ``None`` : annulé."""
        logger.debug("_dossier_choisi: {!r}", chemin)
        if not chemin:
            logger.debug("_dossier_choisi: annulé")
            return
        self.dossier = Path(chemin)
        self._maj_dossier()
        try:
            lignes, self.ignores = core.scan_putty_sessions(self.dossier)
            self.hotes = [dict_vers_hote(ligne, ou=ligne["name"]) for ligne in lignes]
        except Exception as exc:  # noqa: BLE001
            logger.exception("import PuTTY : erreur de scan de {}", chemin)
            self.context.notify(_("PuTTY import error: {exc}").format(exc=exc))
            self.hotes = []
            self.ignores = []
        self._maj_resume()
        self.bouton_importer.set_sensitive(bool(self.hotes))
        logger.debug("_dossier_choisi: {} hôte(s)", len(self.hotes))

    def _maj_resume(self) -> None:
        """Résumé du scan : hôtes importables et sessions ignorées."""
        logger.trace("_maj_resume")
        if self.dossier is None:
            self.etiquette_resume.set_label("")
        elif not self.hotes and not self.ignores:
            self.etiquette_resume.set_label(
                _("No importable SSH/Telnet session found in this folder.")
            )
        else:
            texte = _("Sessions found: {count}.").format(count=len(self.hotes))
            if self.ignores:
                texte += "\n" + _(
                    "{count} session(s) ignored (unsupported protocol or missing host)."
                ).format(count=len(self.ignores))
            self.etiquette_resume.set_label(texte)
        logger.trace("_maj_resume: fait")

    def _on_importer(self, _bouton: Gtk.Button) -> None:
        """Importe les hôtes scannés selon la stratégie cochée et notifie le bilan."""
        logger.debug("_on_importer: entrée")
        if not self.hotes or self.dossier is None:
            logger.debug("_on_importer: rien à importer")
            return
        try:
            rapport: Rapport = self.context.import_hosts(self.hotes, self.conflit_choisi())
        except Exception as exc:  # noqa: BLE001
            logger.exception("import PuTTY : échec de l'import")
            self.context.notify(_("PuTTY import error: {exc}").format(exc=exc))
            logger.debug("_on_importer: échec notifié")
            return
        self.context.notify(bilan(self.dossier.name, rapport))
        self.bouton_importer.set_sensitive(False)
        logger.debug("_on_importer: {} ajouté(s)", len(rapport.ajoutes))


class ImportPuttyPlugin(ToolPlugin):
    """« Import from PuTTY sessions » : onglet de scan d'un dossier de sessions."""

    name = "import-putty"
    version = "1.0"
    menu_label = N_("Import from PuTTY sessions…")
    menu_section = SECTION_IMPORT
    icon_name = "text-x-generic-symbolic"

    def activate(self) -> None:
        """Ouvre l'onglet d'import (le dossier par défaut est proposé)."""
        logger.debug("ImportPuttyPlugin.activate: entrée")
        self.context.open_tab(_("Import from PuTTY sessions"), OngletImportPutty(self.context))
        logger.debug("ImportPuttyPlugin.activate: fait")


PLUGIN = ImportPuttyPlugin
