# SPDX-License-Identifier: GPL-3.0-or-later
"""Choix d'un fichier ou d'un dossier, asynchrone, GTK 4.0 à 4.22 (#153).

``Gtk.FileDialog`` (GTK ≥ 4.10) quand il existe, sinon
``Gtk.FileChooserNative`` (GTK 4.0–4.8, Debian 12). Les deux passent par
le portail de fichiers sous Flatpak et n'ouvrent aucune boucle imbriquée :
``rappel(chemin)`` est appelé plus tard, avec ``None`` si l'utilisateur
annule.
"""

from __future__ import annotations

from gi.repository import Gio, GLib, Gtk
from loguru import logger

from gcm4.core.i18n import _

#: Modes de :func:`choisir`.
OUVRIR = "ouvrir"
ENREGISTRER = "enregistrer"
DOSSIER = "dossier"

#: Rappel : chemin choisi, ou ``None`` (annulé, ou fichier non local).
#: Alias en chaîne : jamais évalué à l'exécution — « str | None » (PEP 604,
#: exigé par ruff) casserait le démarrage sous Python 3.9 du job
#: « gcm4 sur Rocky Linux 9 », et ``from __future__ import annotations``
#: ne protège que les annotations, pas les assignations de module.
Rappel = "Callable[[str | None], None]"

#: (libellé du filtre, motifs) des fichiers d'échange d'hôtes.
FILTRES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("JSON", ("*.json",)),
    ("CSV", ("*.csv",)),
)


def _filtres() -> list[Gtk.FileFilter]:
    """Filtres JSON et CSV."""
    logger.trace("_filtres")
    resultat = []
    for nom, motifs in FILTRES:
        filtre = Gtk.FileFilter()
        filtre.set_name(nom)
        for motif in motifs:
            filtre.add_pattern(motif)
        resultat.append(filtre)
    logger.trace("_filtres: {}", len(resultat))
    return resultat


def _chemin(fichier: Gio.File | None) -> str | None:
    """Chemin local de ``fichier`` (``None`` s'il n'en a pas)."""
    logger.trace("_chemin")
    resultat = fichier.get_path() if fichier is not None else None
    logger.trace("_chemin: {!r}", resultat)
    return resultat


def choisir(
    parent: Gtk.Window, mode: str, titre: str, rappel: Rappel, nom: str = ""
) -> Gtk.FileDialog | Gtk.FileChooserNative:
    """Ouvre le sélecteur ``mode`` (:data:`OUVRIR`, :data:`ENREGISTRER`, :data:`DOSSIER`).

    Args:
        parent (Gtk.Window): Fenêtre parente (sélecteur modal).
        mode (str): Ouvrir un fichier, enregistrer un fichier, choisir un dossier.
        titre (str): Titre traduit du sélecteur.
        rappel (Rappel): Reçoit le chemin choisi, ou ``None``.
        nom (str): Nom proposé en mode :data:`ENREGISTRER`.

    Returns:
        Gtk.FileDialog | Gtk.FileChooserNative: Le sélecteur (déjà affiché).
    """
    logger.debug("choisir: {} {!r}", mode, titre)
    filtres = _filtres() if mode != DOSSIER else []
    if hasattr(Gtk, "FileDialog"):
        dialogue = Gtk.FileDialog(title=titre, modal=True)
        if filtres:
            liste = Gio.ListStore.new(Gtk.FileFilter)
            for filtre in filtres:
                liste.append(filtre)
            dialogue.set_filters(liste)
        if nom:
            dialogue.set_initial_name(nom)

        def fin(source: Gtk.FileDialog, resultat: Gio.AsyncResult) -> None:
            logger.debug("choisir.fin: {}", mode)
            try:
                if mode == OUVRIR:
                    fichier = source.open_finish(resultat)
                elif mode == ENREGISTRER:
                    fichier = source.save_finish(resultat)
                else:
                    fichier = source.select_folder_finish(resultat)
            except GLib.Error as exc:  # annulé (ou portail indisponible)
                logger.debug("choisir.fin: aucun choix ({})", exc.message)
                fichier = None
            rappel(_chemin(fichier))
            logger.debug("choisir.fin: fait")

        lancer = {OUVRIR: dialogue.open, ENREGISTRER: dialogue.save}.get(
            mode, dialogue.select_folder
        )
        lancer(parent, None, fin)
        logger.debug("choisir: Gtk.FileDialog")
        return dialogue
    action = {
        OUVRIR: Gtk.FileChooserAction.OPEN,
        ENREGISTRER: Gtk.FileChooserAction.SAVE,
    }.get(mode, Gtk.FileChooserAction.SELECT_FOLDER)
    natif = Gtk.FileChooserNative.new(titre, parent, action, None, _("Cancel"))
    natif.set_modal(True)
    for filtre in filtres:
        natif.add_filter(filtre)
    if nom:
        natif.set_current_name(nom)

    def reponse(source: Gtk.FileChooserNative, code: int) -> None:
        logger.debug("choisir.reponse: {}", code)
        accepte = code == Gtk.ResponseType.ACCEPT
        rappel(_chemin(source.get_file()) if accepte else None)
        source.destroy()
        logger.debug("choisir.reponse: fait")

    natif.connect("response", reponse)
    natif.show()
    logger.debug("choisir: Gtk.FileChooserNative (GTK < 4.10)")
    return natif
