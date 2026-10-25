"""Onglets et titre de la fenêtre : options de base reprises de Wconfig (#247), sans GTK.

Mêmes clés et même sens que l'application GTK3 (``[options]``) :

- ``cycle-tabs`` : passer de la dernière page à la première (et inversement)
  avec « onglet suivant / précédent » (raccourcis, molette sur les onglets) ;
  désactivée, on s'arrête aux extrémités (``Gtk.Notebook.next_page``) ;
- ``update-title`` / ``app-title`` : titre de la fenêtre. ``app-title`` vide
  (ou égal au nom de l'application, ce que GTK3 écrit par défaut) donne le
  nom de l'application ; avec ``update-title``, le titre de l'onglet courant
  est ajouté : « Gnome Connection Manager - web ».

Les confirmations de fermeture d'onglet (``confirm-close-tab``,
``confirm-close-tab-middle``) et les rayures de l'arbre
(``disable-hosts-stripes``) n'ont pas de logique propre : la fenêtre lit
directement ``AppConfig``.
"""

from __future__ import annotations

from loguru import logger

#: Nom de l'application : titre par défaut de la fenêtre (``app_name`` en GTK3).
NOM_APPLICATION = "Gnome Connection Manager"

#: Sens de :func:`voisin`.
SUIVANT = 1
PRECEDENT = -1


def voisin(courant: int, total: int, sens: int, boucler: bool) -> int:
    """Page à afficher après « onglet suivant » (``sens=1``) ou « précédent » (``-1``).

    Args:
        courant (int): Page courante (``-1`` : aucune).
        total (int): Nombre de pages.
        sens (int): :data:`SUIVANT` ou :data:`PRECEDENT`.
        boucler (bool): Option ``cycle-tabs`` : la dernière page mène à la
            première et inversement.

    Returns:
        int: Page à afficher ; ``courant`` si rien ne change (extrémité sans
        bouclage, une seule page) ; ``-1`` sans page.

    Raises:
        ValueError: ``sens`` différent de 1 et -1.
    """
    logger.debug("voisin: {}/{} sens={} boucler={}", courant, total, sens, boucler)
    if sens not in (SUIVANT, PRECEDENT):
        logger.debug("voisin: sens invalide")
        raise ValueError(f"sens : 1 ou -1 attendu, pas {sens!r}")
    if total <= 0:
        logger.debug("voisin: aucune page")
        return -1
    if not 0 <= courant < total:
        resultat = 0 if sens == SUIVANT else total - 1
        logger.debug("voisin: aucune page courante, {}", resultat)
        return resultat
    cible = courant + sens
    if 0 <= cible < total:
        resultat = cible
    else:
        resultat = cible % total if boucler else courant
    logger.debug("voisin: {}", resultat)
    return resultat


def titre_application(app_title: str) -> str:
    """Titre de base : ``app-title`` s'il est renseigné, sinon :data:`NOM_APPLICATION`.

    Args:
        app_title (str): Valeur de ``[options] app-title``.

    Returns:
        str: Titre sans espaces superflus.
    """
    logger.trace("titre_application: {!r}", app_title)
    resultat = app_title.strip() or NOM_APPLICATION
    logger.trace("titre_application: {!r}", resultat)
    return resultat


def titre_fenetre(app_title: str, dynamique: bool, onglet: str | None) -> str:
    """Titre de la fenêtre principale.

    Args:
        app_title (str): ``[options] app-title``.
        dynamique (bool): ``[options] update-title``.
        onglet (str | None): Titre de l'onglet courant (``None`` ou vide :
            page d'accueil, aucun onglet de connexion).

    Returns:
        str: « titre - onglet » si ``dynamique`` et un onglet est affiché,
        sinon le titre de base (format GTK3, ``on_tab_focus``).
    """
    logger.debug("titre_fenetre: dynamique={} onglet={!r}", dynamique, onglet)
    base = titre_application(app_title)
    texte = (onglet or "").strip()
    resultat = f"{base} - {texte}" if dynamique and texte else base
    logger.debug("titre_fenetre: {!r}", resultat)
    return resultat
