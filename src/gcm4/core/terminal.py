# SPDX-License-Identifier: GPL-3.0-or-later
"""Logique commune aux plugins terminal (telnet, local…), sans GTK (#295).

Repris de ``plugins/plugin_telnet.py`` et ``plugins/plugin_local.py`` (GTK3),
qui dupliquaient cette logique :

- options d'hôte, mêmes clés INI que GTK3 : :data:`OPT_COMMANDES`,
  :data:`OPT_RETOUR_ARRIERE`, :data:`OPT_SUPPRESSION`, :data:`OPT_TERM` ;
  :data:`OPT_ENCODAGE` (#85) est propre à gcm4 ;
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

#: Encodage des caractères du terminal (#85) ; vide : :data:`ENCODAGE_DEFAUT`.
OPT_ENCODAGE = "encoding"

#: ``TERM`` quand l'option est vide (``widgets.vte_run`` de GTK3).
TERM_DEFAUT = "xterm"

#: Nombre de valeurs de ``Vte.EraseBinding`` (AUTO, ASCII_BACKSPACE,
#: ASCII_DELETE, DELETE_SEQUENCE, TTY), dans l'ordre des listes de GTK3.
NB_LIAISONS = 5

#: Encodage quand l'option est vide ou inconnue.
ENCODAGE_DEFAUT = "UTF-8"

#: Encodages proposés (#85), noms acceptés à la fois par VTE (ICU) et par
#: Python (``codecs``) : les messages de gcm4 écrits dans le terminal
#: (« Session ended »…) sont encodés avec le même nom.
ENCODAGES = (
    "UTF-8",
    "ISO-8859-1",
    "ISO-8859-15",
    "WINDOWS-1252",
    "ISO-8859-2",
    "WINDOWS-1250",
    "ISO-8859-5",
    "WINDOWS-1251",
    "KOI8-R",
    "KOI8-U",
    "ISO-8859-7",
    "ISO-8859-9",
    "CP437",
    "CP850",
    "SHIFT_JIS",
    "EUC-JP",
    "EUC-KR",
    "GB18030",
    "BIG5",
)

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


def encodage(options: Mapping[str, str]) -> str:
    """Encodage de l'hôte, nom de :data:`ENCODAGES` ; vide ou inconnu : :data:`ENCODAGE_DEFAUT`.

    La casse est ignorée (``iso-8859-1`` → ``ISO-8859-1``). Une valeur
    inconnue (fichier modifié à la main) est journalisée et remplacée par
    l'UTF-8 : l'onglet s'ouvre quand même.
    """
    logger.debug("encodage: entrée")
    brut = options.get(OPT_ENCODAGE, "").strip().upper()
    if not brut:
        resultat = ENCODAGE_DEFAUT
    elif brut in ENCODAGES:
        resultat = brut
    else:
        logger.warning("encodage: {!r} inconnu, {} utilisé", brut, ENCODAGE_DEFAUT)
        resultat = ENCODAGE_DEFAUT
    logger.debug("encodage: {}", resultat)
    return resultat


def encoder(texte: str, nom: str) -> bytes:
    """``texte`` encodé en ``nom`` pour être affiché dans le terminal ; caractère absent : ``?``."""
    logger.trace("encoder: {} caractère(s) en {}", len(texte), nom)
    resultat = texte.encode(nom, errors="replace")
    logger.trace("encoder: {} octet(s)", len(resultat))
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
