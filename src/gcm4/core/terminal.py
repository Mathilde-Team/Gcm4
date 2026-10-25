"""Logique commune aux plugins terminal (telnet, local…), sans GTK (#295).

Repris de ``plugins/plugin_telnet.py`` et ``plugins/plugin_local.py`` (GTK3),
qui dupliquaient cette logique :

- options d'hôte, mêmes clés INI que GTK3 : :data:`OPT_COMMANDES`,
  :data:`OPT_RETOUR_ARRIERE`, :data:`OPT_SUPPRESSION`, :data:`OPT_TERM` ;
- ``commands`` : lignes envoyées après l'ouverture, au bout d'un délai propre
  au plugin ; une ligne ``##D=<ms>`` ajoute un délai avant les suivantes.

La partie VTE est dans ``gcm4.ui.terminal``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from loguru import logger

#: Commandes envoyées après l'ouverture (fin de ligne écrite ``\n`` dans l'INI).
OPT_COMMANDES = "commands"
#: Liaison de la touche Retour arrière (``Vte.EraseBinding``, 0 à 4).
OPT_RETOUR_ARRIERE = "backspace-key"
#: Liaison de la touche Suppr (``Vte.EraseBinding``, 0 à 4).
OPT_SUPPRESSION = "delete-key"
#: Valeur de ``TERM`` transmise au programme ; vide : ``xterm``.
OPT_TERM = "term"

#: ``TERM`` quand l'option est vide (``widgets.vte_run`` de GTK3).
TERM_DEFAUT = "xterm"

#: Nombre de valeurs de ``Vte.EraseBinding`` (AUTO, ASCII_BACKSPACE,
#: ASCII_DELETE, DELETE_SEQUENCE, TTY), dans l'ordre des listes de GTK3.
NB_LIAISONS = 5

#: Préfixe d'une ligne de délai dans ``commands``.
PREFIXE_DELAI = "##D="


@dataclass(frozen=True)
class Lancement:
    """Commande à lancer dans le terminal.

    Attributes:
        argv: programme et arguments (``argv[0]`` est le chemin du programme).
        mot_de_passe: à écrire sur le descripteur 3 du programme ; vide : aucun
            descripteur supplémentaire.
    """

    argv: list[str]
    mot_de_passe: str = ""

    def __repr__(self) -> str:
        """Représentation sans le mot de passe (journal)."""
        return (
            f"Lancement(argv={self.argv!r}, mot_de_passe={'***' if self.mot_de_passe else ''!r})"
        )


def environnement(options: Mapping[str, str]) -> list[str]:
    """Variables ajoutées à l'environnement du terminal : ``TERM`` seulement.

    Le reste de l'environnement de gcm4 est hérité (VTE l'ajoute).
    """
    logger.debug("environnement: entrée")
    term = options.get(OPT_TERM, "").strip() or TERM_DEFAUT
    logger.debug("environnement: TERM={}", term)
    return [f"TERM={term}"]


def liaison(options: Mapping[str, str], cle: str) -> int:
    """Liaison d'effacement de ``cle`` ; valeur absente ou invalide : 0 (automatique, comme GTK3)."""
    logger.debug("liaison: {}", cle)
    brut = options.get(cle, "").strip()
    resultat = int(brut) if brut.isdigit() and int(brut) < NB_LIAISONS else 0
    logger.debug("liaison: {}", resultat)
    return resultat


def lire_commandes(brut: str) -> str:
    r"""Texte de ``commands`` tel qu'édité, depuis sa forme INI (``\n`` ou NUL : fin de ligne)."""
    logger.debug("lire_commandes: {} caractère(s)", len(brut))
    resultat = brut.replace("\x00", "\n").replace("\\n", "\n")
    logger.debug("lire_commandes: fait")
    return resultat


def ecrire_commandes(texte: str) -> str:
    r"""Forme INI de ``commands`` (une ligne : fins de ligne écrites ``\n``, comme GTK3)."""
    logger.debug("ecrire_commandes: {} caractère(s)", len(texte))
    resultat = texte.replace("\r\n", "\n").replace("\n", "\\n")
    logger.debug("ecrire_commandes: fait")
    return resultat


def est_delai(ligne: str) -> bool:
    """Vrai pour une ligne ``##D=<millisecondes>``."""
    logger.trace("est_delai: {!r}", ligne)
    resultat = ligne.startswith(PREFIXE_DELAI) and ligne[len(PREFIXE_DELAI) :].isdigit()
    logger.trace("est_delai: {}", resultat)
    return resultat


def etapes(commandes: str, debut_ms: int) -> list[tuple[int, str]]:
    r"""Envois de ``commands`` : ``(délai depuis l'ouverture en ms, texte)``.

    Comme GTK3 : les lignes consécutives partent ensemble, séparées et
    terminées par ``\r`` ; une ligne ``##D=<ms>`` repousse les suivantes.

    Args:
        commandes: texte édité (voir :func:`lire_commandes`).
        debut_ms: délai du premier envoi (3000 pour telnet, 700 pour local).
    """
    logger.debug("etapes: {} caractère(s), début {} ms", len(commandes), debut_ms)
    resultat: list[tuple[int, str]] = []
    instant = debut_ms
    lignes: list[str] = []
    for ligne in commandes.splitlines():
        if est_delai(ligne):
            if lignes:
                resultat.append((instant, "\r".join(lignes) + "\r"))
                lignes = []
            instant += int(ligne[len(PREFIXE_DELAI) :])
        else:
            lignes.append(ligne)
    if lignes:
        resultat.append((instant, "\r".join(lignes) + "\r"))
    logger.debug("etapes: {} envoi(s)", len(resultat))
    return resultat
