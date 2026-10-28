# SPDX-License-Identifier: GPL-3.0-or-later
"""Modèle de la base GTK4 : Host, Group, HostStore, sans GTK (#136).

Neutralité de protocole (CONSIGNES-AGENTS-IA.md §1) : ``Host`` ne porte
que les champs communs à tous les protocoles. Les options propres à un
protocole vivent dans ``host.protocol_options[protocole]`` et sont
conservées telles quelles, même si aucun plugin ne les comprend : ouvrir
puis enregistrer sa configuration sans plugin ne perd aucun champ.

Groupes : chemins ``a/b/c`` (``""`` = racine). Recherche insensible à la
casse et aux accents.
"""

from __future__ import annotations

import copy
import unicodedata
from dataclasses import dataclass, field

from loguru import logger

SEPARATEUR = "/"


def normaliser_chemin(chemin: str | None) -> str:
    """``" a//b/ "`` → ``"a/b"`` ; ``None`` ou vide → racine ``""``."""
    logger.trace("normaliser_chemin: {!r}", chemin)
    morceaux = [m.strip() for m in (chemin or "").split(SEPARATEUR)]
    resultat = SEPARATEUR.join(m for m in morceaux if m)
    logger.trace("normaliser_chemin: -> {!r}", resultat)
    return resultat


def parent(chemin: str) -> str:
    """Chemin du groupe parent (``"a/b"`` → ``"a"``, ``"a"`` → ``""``)."""
    logger.trace("parent: {!r}", chemin)
    resultat = chemin.rpartition(SEPARATEUR)[0]
    logger.trace("parent: -> {!r}", resultat)
    return resultat


def _plier(texte: str) -> str:
    """Forme de comparaison : sans accents, sans casse."""
    logger.trace("_plier: {!r}", texte)
    decompose = unicodedata.normalize("NFKD", texte)
    resultat = "".join(c for c in decompose if not unicodedata.combining(c)).casefold()
    logger.trace("_plier: -> {!r}", resultat)
    return resultat


@dataclass
class Host:
    """Hôte : champs communs ; options de protocole dans ``protocol_options``."""

    name: str
    group: str = ""
    host: str = ""
    port: int | None = None
    user: str = ""
    password: str = field(default="", repr=False)  # jamais dans un journal (#145)
    description: str = ""
    protocol: str = ""
    font_color: str = ""
    back_color: str = ""
    extra_params: str = ""
    workspace: bool = False
    auto_close_tab: str = ""
    pre_connect_command: str = ""
    post_connect_command: str = ""
    protocol_options: dict[str, dict[str, str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Normalise le chemin du groupe."""
        logger.trace("Host.__post_init__: {!r}", self.name)
        self.group = normaliser_chemin(self.group)
        logger.trace("Host.__post_init__: groupe {!r}", self.group)

    @property
    def path(self) -> str:
        """Identifiant ``groupe/nom`` (celui de la ligne de commande ``gcm4 groupe/nom``)."""
        logger.trace("Host.path: {!r}", self.name)
        resultat = f"{self.group}{SEPARATEUR}{self.name}" if self.group else self.name
        logger.trace("Host.path: -> {!r}", resultat)
        return resultat

    def clone(self) -> Host:
        """Copie indépendante (options de protocole comprises)."""
        logger.debug("Host.clone: {!r}", self.path)
        copie = copy.deepcopy(self)
        logger.debug("Host.clone: fait")
        return copie

    def options(self, protocole: str) -> dict[str, str]:
        """Options du protocole ``protocole`` (créées vides si absentes)."""
        logger.trace("Host.options: {!r}", protocole)
        resultat = self.protocol_options.setdefault(protocole, {})
        logger.trace("Host.options: {} option(s)", len(resultat))
        return resultat

    def correspond(self, recherche: str) -> bool:
        """Vrai si ``recherche`` figure dans le nom, l'adresse ou la description."""
        logger.trace("Host.correspond: {!r}", recherche)
        cle = _plier(recherche.strip())
        resultat = not cle or any(
            cle in _plier(v) for v in (self.name, self.host, self.description)
        )
        logger.trace("Host.correspond: -> {}", resultat)
        return resultat


@dataclass
class Group:
    """Groupe : chemin, couleur (``""`` = aucune), replié ou non dans l'arbre."""

    path: str
    color: str = ""
    collapsed: bool = False

    @property
    def name(self) -> str:
        """Dernier segment du chemin."""
        logger.trace("Group.name: {!r}", self.path)
        resultat = self.path.rpartition(SEPARATEUR)[2]
        logger.trace("Group.name: -> {!r}", resultat)
        return resultat


class ConflitDeNom(ValueError):
    """Un hôte ou un groupe de ce nom existe déjà à cet endroit."""


class HostStore:
    """Hôtes et groupes de la configuration ; opérations sans GTK."""

    def __init__(self) -> None:
        """Magasin vide."""
        logger.debug("HostStore.__init__")
        self.hosts: list[Host] = []
        self.groups: dict[str, Group] = {}
        logger.debug("HostStore.__init__: prêt")

    # --- groupes ---------------------------------------------------------

    def ensure_group(self, chemin: str) -> Group | None:
        """Crée le groupe ``chemin`` et ses parents s'ils manquent ; ``None`` pour la racine."""
        logger.debug("ensure_group: {!r}", chemin)
        chemin = normaliser_chemin(chemin)
        if not chemin:
            logger.debug("ensure_group: racine")
            return None
        morceaux = chemin.split(SEPARATEUR)
        for i in range(1, len(morceaux) + 1):
            partiel = SEPARATEUR.join(morceaux[:i])
            self.groups.setdefault(partiel, Group(partiel))
        logger.debug("ensure_group: {} groupe(s)", len(self.groups))
        return self.groups[chemin]

    def subgroups(self, chemin: str = "") -> list[Group]:
        """Sous-groupes directs de ``chemin``, triés par nom."""
        logger.debug("subgroups: {!r}", chemin)
        chemin = normaliser_chemin(chemin)
        resultat = sorted(
            (g for p, g in self.groups.items() if parent(p) == chemin),
            key=lambda g: _plier(g.name),
        )
        logger.debug("subgroups: {}", len(resultat))
        return resultat

    def hosts_in(self, chemin: str = "", recursive: bool = False) -> list[Host]:
        """Hôtes du groupe ``chemin`` (et de ses sous-groupes si ``recursive``), triés."""
        logger.debug("hosts_in: {!r} recursive={}", chemin, recursive)
        chemin = normaliser_chemin(chemin)
        resultat = sorted(
            (h for h in self.hosts if h.group == chemin or (recursive and _sous(h.group, chemin))),
            key=lambda h: (_plier(h.group), _plier(h.name)),
        )
        logger.debug("hosts_in: {}", len(resultat))
        return resultat

    def rename_group(self, ancien: str, nouveau: str) -> None:
        """Renomme ou déplace un groupe avec tout son contenu (``a/b`` → ``x/b``)."""
        logger.debug("rename_group: {!r} -> {!r}", ancien, nouveau)
        ancien, nouveau = normaliser_chemin(ancien), normaliser_chemin(nouveau)
        if ancien not in self.groups:
            raise KeyError(ancien)
        if not nouveau or _sous(nouveau, ancien) or nouveau == ancien:
            raise ValueError(f"déplacement impossible : {ancien!r} -> {nouveau!r}")
        if nouveau in self.groups:
            raise ConflitDeNom(nouveau)
        deplaces = {p: g for p, g in self.groups.items() if p == ancien or _sous(p, ancien)}
        for p, g in deplaces.items():
            del self.groups[p]
            g.path = nouveau + p[len(ancien) :]
            self.groups[g.path] = g
        self.ensure_group(parent(nouveau))
        for h in self.hosts:
            if h.group == ancien or _sous(h.group, ancien):
                h.group = nouveau + h.group[len(ancien) :]
        logger.debug("rename_group: {} groupe(s) déplacé(s)", len(deplaces))

    def remove_group(self, chemin: str, avec_contenu: bool = False) -> list[Host]:
        """Supprime un groupe ; refusé s'il n'est pas vide, sauf ``avec_contenu``. Renvoie les hôtes retirés."""
        logger.debug("remove_group: {!r} avec_contenu={}", chemin, avec_contenu)
        chemin = normaliser_chemin(chemin)
        if chemin not in self.groups:
            raise KeyError(chemin)
        contenu = self.hosts_in(chemin, recursive=True)
        sous_groupes = [p for p in self.groups if _sous(p, chemin)]
        if (contenu or sous_groupes) and not avec_contenu:
            raise ValueError(f"groupe non vide : {chemin!r}")
        for p in [chemin, *sous_groupes]:
            del self.groups[p]
        retires = {id(h) for h in contenu}
        self.hosts = [h for h in self.hosts if id(h) not in retires]
        logger.debug("remove_group: {} hôte(s) retiré(s)", len(contenu))
        return contenu

    # --- hôtes -----------------------------------------------------------

    def find(self, chemin_hote: str) -> Host | None:
        """Hôte d'identifiant ``groupe/nom`` (comparaison exacte), ou ``None``."""
        logger.debug("find: {!r}", chemin_hote)
        groupe, _, nom = normaliser_chemin(chemin_hote).rpartition(SEPARATEUR)
        resultat = next((h for h in self.hosts if h.group == groupe and h.name == nom), None)
        logger.debug("find: {}", resultat is not None)
        return resultat

    def _verifier_libre(self, groupe: str, nom: str, sauf: Host | None = None) -> None:
        logger.trace("_verifier_libre: {!r}/{!r}", groupe, nom)
        if not nom.strip():
            raise ValueError("nom d'hôte vide")
        if any(h is not sauf and h.group == groupe and h.name == nom for h in self.hosts):
            raise ConflitDeNom(f"{groupe}{SEPARATEUR}{nom}" if groupe else nom)
        logger.trace("_verifier_libre: libre")

    def add(self, host: Host) -> Host:
        """Ajoute ``host`` (son groupe est créé au besoin) ; nom déjà pris → ``ConflitDeNom``."""
        logger.debug("add: {!r}", host.path)
        self._verifier_libre(host.group, host.name)
        self.ensure_group(host.group)
        self.hosts.append(host)
        logger.debug("add: {} hôte(s)", len(self.hosts))
        return host

    def rename(self, host: Host, nom: str) -> None:
        """Renomme ``host`` dans son groupe."""
        logger.debug("rename: {!r} -> {!r}", host.path, nom)
        self._verifier_libre(host.group, nom, sauf=host)
        host.name = nom
        logger.debug("rename: fait")

    def move(self, host: Host, groupe: str) -> None:
        """Déplace ``host`` vers ``groupe`` (créé au besoin)."""
        logger.debug("move: {!r} -> {!r}", host.path, groupe)
        groupe = normaliser_chemin(groupe)
        self._verifier_libre(groupe, host.name, sauf=host)
        self.ensure_group(groupe)
        host.group = groupe
        logger.debug("move: fait")

    def remove(self, host: Host) -> None:
        """Retire ``host`` (son groupe reste, même vide)."""
        logger.debug("remove: {!r}", host.path)
        self.hosts = [h for h in self.hosts if h is not host]
        logger.debug("remove: {} hôte(s)", len(self.hosts))

    def nom_de_copie(self, host: Host) -> str:
        """Premier nom libre ``nom (2)``, ``nom (3)``… dans le groupe de ``host``."""
        logger.debug("nom_de_copie: {!r}", host.path)
        n = 2
        while any(h.group == host.group and h.name == f"{host.name} ({n})" for h in self.hosts):
            n += 1
        resultat = f"{host.name} ({n})"
        logger.debug("nom_de_copie: {!r}", resultat)
        return resultat

    def replace(self, original: Host, nouveau: Host) -> Host:
        """Met ``nouveau`` à la place de ``original`` (même rang ; groupe créé au besoin).

        Raises:
            ConflitDeNom: un autre hôte porte déjà ce nom dans le groupe visé.
            ValueError: ``original`` n'est pas dans le magasin, ou nom vide.
        """
        logger.debug("replace: {!r} -> {!r}", original.path, nouveau.path)
        rang = next((i for i, h in enumerate(self.hosts) if h is original), None)
        if rang is None:
            raise ValueError(f"hôte absent : {original.path!r}")
        nouveau.group = normaliser_chemin(nouveau.group)
        self._verifier_libre(nouveau.group, nouveau.name, sauf=original)
        self.ensure_group(nouveau.group)
        self.hosts[rang] = nouveau
        logger.debug("replace: rang {}", rang)
        return nouveau

    def duplicate(self, host: Host) -> Host:
        """Copie de ``host`` dans le même groupe, nommée ``nom (2)``, ``nom (3)``…"""
        logger.debug("duplicate: {!r}", host.path)
        copie = host.clone()
        copie.name = self.nom_de_copie(host)
        self.add(copie)
        logger.debug("duplicate: {!r}", copie.path)
        return copie

    def search(self, texte: str) -> list[Host]:
        """Hôtes dont le nom, l'adresse ou la description contient ``texte`` (casse et accents ignorés)."""
        logger.debug("search: {!r}", texte)
        resultat = [h for h in self.hosts_in("", recursive=True) if h.correspond(texte)]
        logger.debug("search: {} résultat(s)", len(resultat))
        return resultat


def _sous(chemin: str, ancetre: str) -> bool:
    """Vrai si ``chemin`` est strictement sous ``ancetre`` (tout chemin non vide est sous la racine)."""
    logger.trace("_sous: {!r} {!r}", chemin, ancetre)
    resultat = chemin != ancetre and (not ancetre or chemin.startswith(ancetre + SEPARATEUR))
    logger.trace("_sous: -> {}", resultat)
    return resultat
