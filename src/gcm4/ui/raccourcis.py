"""Raccourcis modifiables (#247) : noms de touches GDK, saisie d'une combinaison.

Le fichier garde la notation GTK3 en majuscules (``CTRL+PAGE_DOWN``) ; les
noms de touches GDK sont sensibles à la casse (``Page_Down``) et
``Gdk.keyval_from_name`` ne retrouve pas ``PAGE_DOWN``. :func:`resoudre`
interroge un index construit une fois sur les plages de keysyms de X11 / GDK
(latin, fonctions, pavé numérique, touches multimédia ``XF86``).

GTK3 compare le texte du fichier à celui de la touche pressée
(``get_key_name``) : les noms écrits sont ceux de GDK 3. GDK 4 nomme
``AudioPlay`` la touche que GDK 3 nomme ``XF86AudioPlay`` (les deux noms sont
acceptés à la lecture) : :func:`nom_touche` rétablit le préfixe. Maj+Tab
produit ``ISO_Left_Tab`` : ramené à ``Tab``, la forme des accélérateurs.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from functools import lru_cache

from gi.repository import Adw, Gdk, Gtk
from loguru import logger

from gcm4.core import preferences as prefs_core
from gcm4.core import raccourcis as raccourcis_core
from gcm4.core.i18n import _
from gcm4.core.raccourcis import Combinaison
from gcm4.ui import dialogues

#: Plages de keysyms parcourues pour l'index (bornes incluses).
_PLAGES = ((0x0020, 0x13FF), (0xFE00, 0xFFFF), (0x1008FE00, 0x1008FFFF))
#: Plage des keysyms ``XF86`` (touches multimédia, fabricants).
_XF86 = (0x10080000, 0x1008FFFF)

#: Masques retenus, dans l'ordre GTK3 (``CTRL``, ``SHIFT``, ``ALT``, ``SUPER``).
_MASQUES = (
    ("CTRL", Gdk.ModifierType.CONTROL_MASK),
    ("SHIFT", Gdk.ModifierType.SHIFT_MASK),
    ("ALT", Gdk.ModifierType.ALT_MASK),
    ("SUPER", Gdk.ModifierType.SUPER_MASK),
)

#: Touches qui, seules, ne font que modifier : la saisie attend la suivante.
_MODIFICATRICES = frozenset(
    {
        "Shift_L",
        "Shift_R",
        "Control_L",
        "Control_R",
        "Alt_L",
        "Alt_R",
        "Meta_L",
        "Meta_R",
        "Super_L",
        "Super_R",
        "Hyper_L",
        "Hyper_R",
        "ISO_Level3_Shift",
        "ISO_Level5_Shift",
        "Mode_switch",
        "Caps_Lock",
        "Num_Lock",
        "Shift_Lock",
    }
)

#: Sans modificateur : navigation du dialogue de saisie, laissée à GTK.
_NAVIGATION = frozenset(
    {
        "Escape",
        "Return",
        "KP_Enter",
        "ISO_Enter",
        "Tab",
        "space",
        "Up",
        "Down",
        "Left",
        "Right",
        "Home",
        "End",
        "Page_Up",
        "Page_Down",
    }
)

#: Entrée (clavier principal et pavé numérique) : valide une saisie prête.
_ENTREE = frozenset({Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_ISO_Enter})
#: Modificateurs qui font d'Entrée une combinaison (Ctrl+Entrée…).
_FORTS = Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK | Gdk.ModifierType.SUPER_MASK

#: Résultats de :func:`depuis_evenement`.
IGNOREE = "ignoree"
DESACTIVER = "desactiver"
INVALIDE = "invalide"
VALIDE = "valide"


def nom_touche(keyval: int) -> str | None:
    """Nom canonique d'une touche, tel que GDK 3 l'écrit ; ``None`` si sans nom.

    Minuscule pour une lettre (``A`` -> ``a``), ``Tab`` pour ``ISO_Left_Tab``,
    préfixe ``XF86`` pour les touches multimédia.
    """
    logger.trace("nom_touche: {}", keyval)
    keyval = Gdk.KEY_Tab if keyval == Gdk.KEY_ISO_Left_Tab else Gdk.keyval_to_lower(keyval)
    nom = Gdk.keyval_name(keyval)
    if not nom or nom.startswith("0x"):
        logger.trace("nom_touche: sans nom")
        return None
    if _XF86[0] <= keyval <= _XF86[1] and not nom.startswith("XF86"):
        nom = "XF86" + nom
    logger.trace("nom_touche: {!r}", nom)
    return nom


@lru_cache(maxsize=1)
def _index() -> dict[str, int]:
    """Nom en majuscules -> keyval, construit une fois (``XF86AUDIOPLAY`` et ``AUDIOPLAY``)."""
    logger.debug("_index: entrée")
    index: dict[str, int] = {}
    for debut, fin in _PLAGES:
        for keyval in range(debut, fin + 1):
            nom = Gdk.keyval_name(keyval)
            if not nom or nom.startswith("0x"):
                continue
            index.setdefault(nom.upper(), keyval)
            if _XF86[0] <= keyval <= _XF86[1] and not nom.startswith("XF86"):
                index.setdefault("XF86" + nom.upper(), keyval)
    logger.debug("_index: {} nom(s)", len(index))
    return index


def resoudre(nom: str) -> str | None:
    """Nom de touche GTK3 en majuscules (``PAGE_DOWN``) -> nom canonique (``Page_Down``).

    Voir :func:`nom_touche` ; nom inconnu : ``None``.
    """
    logger.trace("resoudre: {!r}", nom)
    keyval = Gdk.keyval_from_name(nom)
    if keyval in (0, Gdk.KEY_VoidSymbol):
        keyval = _index().get(nom.upper(), 0)
    resultat = nom_touche(keyval) if keyval else None
    logger.trace("resoudre: {!r}", resultat)
    return resultat


def _sans_modificateur(keyval: int) -> bool:
    """F1 à F35 et touches multimédia : utilisables seules (F9, F11…), elles ne tapent rien."""
    return Gdk.KEY_F1 <= keyval <= Gdk.KEY_F35 or _XF86[0] <= keyval <= _XF86[1]


def depuis_evenement(keyval: int, etat: Gdk.ModifierType) -> tuple[str, Combinaison | None]:
    """Touche pressée dans le dialogue de saisie -> (verdict, combinaison).

    Verdicts : :data:`IGNOREE` (modificateur seul, navigation sans
    modificateur : laissé à GTK), :data:`DESACTIVER` (Retour arrière seul),
    :data:`INVALIDE` (touche sans Ctrl, Alt ni Super, hors F1–F35 et touches
    multimédia : elle ne servirait plus dans le terminal), :data:`VALIDE`.
    """
    logger.debug("depuis_evenement: keyval={} etat={}", keyval, int(etat))
    nom = Gdk.keyval_name(keyval) or ""
    if nom in _MODIFICATRICES:
        logger.debug("depuis_evenement: modificateur seul")
        return IGNOREE, None
    modificateurs = tuple(m for m, masque in _MASQUES if etat & masque)
    nom = nom_touche(keyval) or ""
    keyval = Gdk.keyval_from_name(nom) if nom else 0
    forts = set(modificateurs) - {"SHIFT"}
    if not forts and nom in _NAVIGATION:
        logger.debug("depuis_evenement: navigation")
        return IGNOREE, None
    if not modificateurs and keyval == Gdk.KEY_BackSpace:
        logger.debug("depuis_evenement: désactiver")
        return DESACTIVER, None
    combinaison = Combinaison(modificateurs, nom)
    masque = Gdk.ModifierType(0)
    for m, valeur in _MASQUES:
        if m in modificateurs:
            masque |= valeur
    # GTK refuse Tab comme accélérateur, mais pose bien <Control>Tab (valeur par défaut).
    if not nom or (keyval != Gdk.KEY_Tab and not Gtk.accelerator_valid(keyval, masque)):
        logger.debug("depuis_evenement: accélérateur refusé par GTK")
        return INVALIDE, combinaison
    if not forts and not _sans_modificateur(keyval):
        logger.debug("depuis_evenement: sans Ctrl/Alt/Super")
        return INVALIDE, combinaison
    logger.debug("depuis_evenement: {}", combinaison.gtk3)
    return VALIDE, combinaison


def libelle(combinaison: Combinaison | None) -> str:
    """Combinaison -> texte lisible et traduit (``Ctrl+Page Down``) ; ``None`` : « Disabled »."""
    logger.trace("libelle: {}", combinaison)
    if combinaison is None:
        logger.trace("libelle: désactivé")
        return _("Disabled")
    ok, cle, mods = Gtk.accelerator_parse(combinaison.accelerateur)
    resultat = Gtk.accelerator_get_label(cle, mods) if ok and cle else combinaison.gtk3
    logger.trace("libelle: {!r}", resultat)
    return resultat


#: Rappel du dialogue : ("set", combinaison ou None pour désactiver) ou ("reset", None).
#: Union entre guillemets : jamais évaluée à l'exécution — le Python 3.9 du
#: job « gcm4 sur Rocky Linux 9 » ne connaît pas « X | None » hors annotation
#: (même style que « Resolveur » dans gcm4/core/raccourcis.py).
RappelSaisie = Callable[[str, "Combinaison | None"], None]


class SaisieRaccourci:
    """Dialogue de saisie du raccourci d'une action de la fenêtre principale.

    La touche est lue par un ``Gtk.EventControllerKey`` en phase de capture
    sur le dialogue ; Échap, Entrée, Tab et les flèches sans modificateur
    restent au dialogue (annuler, valider, naviguer). « Set » n'est
    actif que pour une combinaison valide et libre : un conflit avec une autre
    action ou avec un raccourci de l'arbre des hôtes est affiché, jamais
    enregistré.
    """

    def __init__(
        self,
        parent: Gtk.Widget,
        action: str,
        actuels: Mapping[str, tuple[str, ...]],
        personnalise: bool,
        rappel: RappelSaisie,
    ) -> None:
        """Construit et affiche le dialogue.

        Args:
            parent (Gtk.Widget): Fenêtre des préférences.
            action (str): Action modifiée (clé de :data:`gcm4.core.raccourcis.CLES`).
            actuels (Mapping[str, tuple[str, ...]]): Accélérateurs posés par action.
            personnalise (bool): Valeur enregistrée : propose « Reset to default ».
            rappel (RappelSaisie): Appelé une seule fois, sauf annulation.
        """
        logger.debug("SaisieRaccourci.__init__: {}", action)
        self.action = action
        self.actuels = actuels
        self.rappel = rappel
        self.libelles = prefs_core.libelles_actions()
        self.choix: Combinaison | None = None  # None : désactiver
        self.pret = False  # une saisie valide et libre attend « Set »
        self.traite = False  # protège contre une deuxième réponse
        boite = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.touches = Gtk.Label(label=_("Press a key combination"))
        self.touches.add_css_class("title-2")
        self.message = Gtk.Label(wrap=True)
        self.message.add_css_class("error")
        self.message.set_visible(False)
        boite.append(self.touches)
        boite.append(self.message)
        reponses: list[dialogues.Reponse] = [("cancel", _("Cancel"), None)]
        if personnalise:
            reponses.append(("reset", _("Reset to default"), None))
        reponses.append(("set", _("Set"), Adw.ResponseAppearance.SUGGESTED))
        corps = _("Press the new shortcut for “{action}”, or Backspace to disable it.").format(
            action=_(self.libelles[action])
        )
        self.dialogue = dialogues.creer(
            parent, _("Set Shortcut"), corps, reponses, "set", "cancel", self._on_reponse, boite
        )
        self.dialogue.set_response_enabled("set", False)
        clavier = Gtk.EventControllerKey()
        clavier.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        clavier.connect("key-pressed", self._on_touche)
        self.dialogue.add_controller(clavier)
        logger.debug("SaisieRaccourci.__init__: affiché")

    def proposer(self, keyval: int, etat: Gdk.ModifierType) -> bool:
        """Traite une touche (aussi appelé par les tests) ; ``True`` : touche consommée."""
        logger.debug("proposer: keyval={}", keyval)
        verdict, combinaison = depuis_evenement(keyval, etat)
        if verdict == IGNOREE and keyval in _ENTREE and not etat & _FORTS:
            # Le bouton « Set », inactif à l'ouverture, n'a pas le focus : Entrée
            # n'activerait rien. Valide la saisie prête, sinon ne fait rien.
            if self.pret:
                self.repondre("set")
            logger.debug("proposer: Entrée (prêt={})", self.pret)
            return True
        if verdict == IGNOREE:
            logger.debug("proposer: laissée au dialogue")
            return False
        self.pret = False
        if verdict == DESACTIVER:
            self.choix, self.pret = None, True
            self.touches.set_label(libelle(None))
            self._signaler(None)
        elif verdict == INVALIDE:
            self.touches.set_label(libelle(combinaison))
            self._signaler(_("Use Ctrl, Alt or Super with this key."))
        else:
            assert combinaison is not None
            self.touches.set_label(libelle(combinaison))
            proprietaire = raccourcis_core.conflit(
                combinaison, self.action, self.actuels, prefs_core.RACCOURCIS_ARBRE
            )
            if proprietaire is None:
                self.choix, self.pret = combinaison, True
                self._signaler(None)
            else:
                nom = _(self.libelles.get(proprietaire, proprietaire))
                self._signaler(_("Already used by “{action}”.").format(action=nom))
        self.dialogue.set_response_enabled("set", self.pret)
        logger.debug("proposer: {} prêt={}", verdict, self.pret)
        return True

    def repondre(self, reponse: str) -> None:
        """Répond comme un clic sur le bouton ``reponse``, puis ferme le dialogue."""
        logger.debug("repondre: {}", reponse)
        self.dialogue.emit("response", reponse)
        if isinstance(self.dialogue, Adw.MessageDialog):
            self.dialogue.close()
        else:
            self.dialogue.force_close()  # sans close-response : la réponse est déjà donnée
        logger.debug("repondre: fait")

    def _signaler(self, message: str | None) -> None:
        """Affiche (ou masque) la raison du refus."""
        logger.trace("_signaler: {!r}", message)
        self.message.set_label(message or "")
        self.message.set_visible(message is not None)
        logger.trace("_signaler: fait")

    def _on_touche(
        self, _clavier: Gtk.EventControllerKey, keyval: int, _code: int, etat: Gdk.ModifierType
    ) -> bool:
        """Touche pressée dans le dialogue."""
        logger.debug("_on_touche: entrée")
        resultat = self.proposer(keyval, etat)
        logger.debug("_on_touche: consommée={}", resultat)
        return resultat

    def _on_reponse(self, reponse: str) -> None:
        """Réponse du dialogue : une seule est traitée ; « Set » exige une saisie prête."""
        logger.debug("_on_reponse: {}", reponse)
        if self.traite:
            logger.debug("_on_reponse: réponse déjà traitée")
            return
        if reponse == "set" and not self.pret:
            logger.debug("_on_reponse: aucune saisie valide, ignorée")
            return
        self.traite = True
        if reponse in ("set", "reset"):
            self.rappel(reponse, self.choix if reponse == "set" else None)
        logger.debug("_on_reponse: fait")
