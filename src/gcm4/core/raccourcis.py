"""Raccourcis de la fenêtre principale modifiables : section ``[shortcuts]`` (#247), sans GTK.

Format du fichier, repris de GTK3 (``Wconfig``, ``get_key_name``) :
``console_next = CTRL+PAGE_DOWN`` — modificateurs ``CTRL``, ``SHIFT``, ``ALT``,
``SUPER`` dans cet ordre, puis le nom de la touche (``Gdk.keyval_name``) en
majuscules. Une valeur écrite par gcm4 est donc relue par GTK3 et
inversement.

- Clés partagées avec GTK3 : ``console_previous``, ``console_next``. GTK3
  écrit toujours ces clés, avec ses valeurs par défaut (``CTRL+SHIFT+TAB``,
  ``CTRL+TAB``) : cette valeur-là vaut « par défaut » et garde les deux
  raccourcis de gcm4 (Ctrl+Tab **et** Ctrl+Page suivante).
- Actions propres à gcm4 : clés préfixées ``gcm4_`` (``gcm4_quit``…), que
  GTK3 ignore à la lecture et ne réécrit pas (retour aux valeurs par défaut
  après un enregistrement par GTK3).
- Valeur vide : raccourci désactivé. Clé absente : valeurs par défaut de
  :data:`gcm4.core.menu.RACCOURCIS`. Valeur illisible : par défaut, avec un
  avertissement dans le journal.

Les autres clés de la section (raccourcis du terminal ``copy``, ``paste``…,
commandes personnalisées ``shortcutN`` / ``commandN``) appartiennent au
plugin terminal : jamais lues ni modifiées ici, conservées telles quelles.

Le nom canonique d'une touche (« Page_Down » pour « PAGE_DOWN ») dépend de
GDK : l'interface fournit la fonction ``resoudre``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

from loguru import logger

from gcm4.core import menu, window_state
from gcm4.core.config import SECTION_SHORTCUTS, update_keys

SECTION = SECTION_SHORTCUTS

#: Modificateurs GTK3, dans l'ordre d'écriture de ``get_key_name``.
MODIFICATEURS = ("CTRL", "SHIFT", "ALT", "SUPER")
_VERS_ACCELERATEUR = {"CTRL": "<Control>", "SHIFT": "<Shift>", "ALT": "<Alt>", "SUPER": "<Super>"}
_DEPUIS_ACCELERATEUR = {
    "control": "CTRL",
    "ctrl": "CTRL",
    "primary": "CTRL",
    "shift": "SHIFT",
    "alt": "ALT",
    "mod1": "ALT",
    "super": "SUPER",
}

#: Action -> clé de ``[shortcuts]``.
CLES: dict[str, str] = {
    "app.quit": "gcm4_quit",
    "app.preferences": "gcm4_preferences",
    "win.toggle-panel": "gcm4_toggle_panel",
    "win.search": "gcm4_search",
    "win.previous-tab": "console_previous",
    "win.next-tab": "console_next",
}

#: Valeurs que GTK3 écrit par défaut (``loadConfig``) : relues comme « par défaut ».
DEFAUTS_GTK3: dict[str, str] = {
    "console_previous": "CTRL+SHIFT+TAB",
    "console_next": "CTRL+TAB",
}

#: Résolution d'un nom de touche en majuscules vers son nom GDK canonique.
Resolveur = Callable[[str], "str | None"]


@dataclass(frozen=True)
class Combinaison:
    """Une combinaison de touches : modificateurs GTK3 et nom canonique de la touche."""

    #: Sous-ensemble ordonné de :data:`MODIFICATEURS`.
    modificateurs: tuple[str, ...]
    #: Nom GDK canonique (« Tab », « a », « Page_Down », « F11 »).
    touche: str

    @property
    def gtk3(self) -> str:
        """Notation du fichier : ``CTRL+SHIFT+TAB``."""
        return "+".join((*self.modificateurs, self.touche.upper()))

    @property
    def accelerateur(self) -> str:
        """Accélérateur GTK : ``<Control><Shift>Tab``."""
        return "".join(_VERS_ACCELERATEUR[m] for m in self.modificateurs) + self.touche


def _ordonner(modificateurs: Iterable[str]) -> tuple[str, ...]:
    """Modificateurs dans l'ordre GTK3, sans doublon."""
    logger.trace("_ordonner: entrée")
    presents = set(modificateurs)
    resultat = tuple(m for m in MODIFICATEURS if m in presents)
    logger.trace("_ordonner: {}", resultat)
    return resultat


def depuis_accelerateur(accelerateur: str) -> Combinaison:
    """``"<Control><Shift>Tab"`` -> :class:`Combinaison`.

    Raises:
        ValueError: modificateur inconnu ou touche absente.
    """
    logger.trace("depuis_accelerateur: {!r}", accelerateur)
    reste = accelerateur.strip()
    modificateurs = []
    while reste.startswith("<"):
        fin = reste.find(">")
        if fin < 0:
            logger.trace("depuis_accelerateur: chevron non fermé")
            raise ValueError(f"accélérateur invalide : {accelerateur!r}")
        nom = reste[1:fin].lower()
        if nom not in _DEPUIS_ACCELERATEUR:
            logger.trace("depuis_accelerateur: modificateur inconnu")
            raise ValueError(f"modificateur inconnu : {nom!r}")
        modificateurs.append(_DEPUIS_ACCELERATEUR[nom])
        reste = reste[fin + 1 :]
    if not reste:
        logger.trace("depuis_accelerateur: touche absente")
        raise ValueError(f"touche absente : {accelerateur!r}")
    resultat = Combinaison(_ordonner(modificateurs), reste)
    logger.trace("depuis_accelerateur: {}", resultat)
    return resultat


def depuis_gtk3(texte: str, resoudre: Resolveur) -> Combinaison:
    """``"CTRL+PAGE_DOWN"`` -> :class:`Combinaison` (nom de touche résolu par ``resoudre``).

    Raises:
        ValueError: texte vide, modificateur inconnu ou touche inconnue.
    """
    logger.trace("depuis_gtk3: {!r}", texte)
    morceaux = [m.strip() for m in texte.strip().split("+")]
    if not morceaux or not morceaux[-1]:
        logger.trace("depuis_gtk3: touche absente")
        raise ValueError(f"touche absente : {texte!r}")
    *modificateurs, nom = (m.upper() for m in morceaux)
    inconnus = [m for m in modificateurs if m not in MODIFICATEURS]
    if inconnus:
        logger.trace("depuis_gtk3: modificateur inconnu")
        raise ValueError(f"modificateur inconnu : {inconnus[0]!r}")
    touche = resoudre(nom)
    if not touche:
        logger.trace("depuis_gtk3: touche inconnue")
        raise ValueError(f"touche inconnue : {nom!r}")
    resultat = Combinaison(_ordonner(modificateurs), touche)
    logger.trace("depuis_gtk3: {}", resultat)
    return resultat


def _par_defaut(cle: str, valeur: str) -> bool:
    """Valeur écrite par GTK3 pour « par défaut » (comparaison sans casse ni espaces)."""
    logger.trace("_par_defaut: {} = {!r}", cle, valeur)
    normale = "+".join(m.strip() for m in valeur.upper().split("+"))
    resultat = DEFAUTS_GTK3.get(cle) == normale
    logger.trace("_par_defaut: {}", resultat)
    return resultat


def personnalise(section: Mapping[str, str], action: str) -> bool:
    """La valeur enregistrée de ``action`` remplace-t-elle ses valeurs par défaut ?"""
    logger.trace("personnalise: {}", action)
    valeur = section.get(CLES[action])
    resultat = valeur is not None and not _par_defaut(CLES[action], valeur)
    logger.trace("personnalise: {}", resultat)
    return resultat


def effectifs(section: Mapping[str, str], resoudre: Resolveur) -> dict[str, tuple[str, ...]]:
    """Accélérateurs à poser pour chaque action de :data:`gcm4.core.menu.RACCOURCIS`.

    Args:
        section (Mapping[str, str]): Contenu de ``[shortcuts]`` (clés en minuscules).
        resoudre (Resolveur): Nom de touche en majuscules -> nom GDK canonique.

    Returns:
        dict[str, tuple[str, ...]]: Action -> accélérateurs ; ``()`` : désactivé.
    """
    logger.debug("effectifs: {} clé(s)", len(section))
    resultat: dict[str, tuple[str, ...]] = {}
    for action, defaut in menu.RACCOURCIS.items():
        cle = CLES[action]
        valeur = section.get(cle)
        if valeur is None or _par_defaut(cle, valeur):
            resultat[action] = defaut
        elif not valeur.strip():
            resultat[action] = ()
        else:
            try:
                resultat[action] = (depuis_gtk3(valeur, resoudre).accelerateur,)
            except ValueError as exc:
                logger.warning("effectifs: [{}] {} illisible ({}), par défaut", SECTION, cle, exc)
                resultat[action] = defaut
    logger.debug("effectifs: {} personnalisé(s)", sum(personnalise(section, a) for a in CLES))
    return resultat


def valeur_a_ecrire(combinaison: Combinaison | None) -> str:
    """Texte de ``[shortcuts]`` : notation GTK3, ou vide pour « désactivé »."""
    logger.trace("valeur_a_ecrire: {}", combinaison)
    return "" if combinaison is None else combinaison.gtk3


def conflit(
    combinaison: Combinaison,
    action: str,
    actuels: Mapping[str, tuple[str, ...]],
    autres: Iterable[tuple[str, str]] = (),
) -> str | None:
    """Premier propriétaire de ``combinaison`` autre que ``action``.

    Args:
        combinaison (Combinaison): Combinaison proposée.
        action (str): Action modifiée (ses propres raccourcis ne comptent pas).
        actuels (Mapping[str, tuple[str, ...]]): Action -> accélérateurs posés.
        autres (Iterable[tuple[str, str]]): (accélérateurs séparés par « | »,
            libellé) des raccourcis non modifiables, ceux de l'arbre des hôtes.

    Returns:
        str | None: Action ou libellé du propriétaire ; ``None`` si libre.
    """
    logger.debug("conflit: {} pour {}", combinaison.gtk3, action)
    cible = combinaison.gtk3
    candidats = [
        (proprietaire, accel)
        for proprietaire, accels in actuels.items()
        if proprietaire != action
        for accel in accels
    ]
    candidats += [
        (libelle, accel) for declencheur, libelle in autres for accel in declencheur.split("|")
    ]
    for proprietaire, accel in candidats:
        if depuis_accelerateur(accel).gtk3 == cible:
            logger.debug("conflit: {}", proprietaire)
            return proprietaire
    logger.debug("conflit: aucun")
    return None


def lire(config_file: str) -> dict[str, str]:
    """Contenu de ``[shortcuts]`` ; fichier absent ou illisible : vide (valeurs par défaut)."""
    logger.debug("lire: {}", config_file)
    cp = window_state._lire(config_file)
    resultat = dict(cp.items(SECTION)) if cp.has_section(SECTION) else {}
    logger.debug("lire: {} clé(s)", len(resultat))
    return resultat


def enregistrer(config_file: str, action: str, combinaison: Combinaison | None) -> None:
    """Enregistre le raccourci de ``action`` (``None`` : désactivé), sans toucher au reste.

    Raises:
        KeyError: action non modifiable.
        ConfigError: fichier corrompu (jamais réécrit) ou écriture impossible.
    """
    logger.debug("enregistrer: {} {}", action, combinaison)
    update_keys(config_file, SECTION, {CLES[action]: valeur_a_ecrire(combinaison)})
    logger.debug("enregistrer: fait")


def reinitialiser(config_file: str, action: str) -> None:
    """Retire la clé de ``action`` : retour aux valeurs par défaut.

    Raises:
        KeyError: action non modifiable.
        ConfigError: fichier corrompu (jamais réécrit) ou écriture impossible.
    """
    logger.debug("reinitialiser: {}", action)
    update_keys(config_file, SECTION, {CLES[action]: None})
    logger.debug("reinitialiser: fait")
