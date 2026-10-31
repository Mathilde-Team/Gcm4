#!/usr/bin/python3
# -*- coding: UTF-8 -*-
# SPDX-License-Identifier: GPL-3.0-only
"""Utilitaires généraux et classe de base GTK pour l'application GCM."""

import weakref as _weakref

from gi.repository import Gio, GLib, Gtk
from loguru import logger


def run_dialog_sync(dialog: Gtk.Dialog) -> Gtk.ResponseType:
    """Exécute un ``Gtk.Dialog`` de façon synchrone SANS ``Gtk.Dialog.run()``.

    ``Gtk.Dialog.run()`` est purement et simplement supprimé en GTK4 (seul
    le signal ``response``, émis de façon asynchrone, subsiste). Réécrire en
    callbacks tous les appelants de GCM qui dépendent aujourd'hui d'un
    retour synchrone (`msgconfirm`, `inputbox`, `show_open_dialog`, tous les
    dialogues d'édition...) toucherait des dizaines de fonctions à travers
    tout le projet — un chantier à part entière, bien plus risqué que le
    simple remplacement de l'appel `.run()`.

    Cette fonction reproduit exactement le comportement de `.run()` —
    afficher le dialogue, bloquer jusqu'à une réponse, renvoyer le
    `Gtk.ResponseType` obtenu — via une boucle `GLib.MainLoop` locale et le
    signal `response`, qui lui reste disponible en GTK4. C'est le mécanisme
    documenté par la doc de migration GTK elle-même pour ce cas précis :
    remplacer uniquement le point d'implémentation de `.run()`, sans
    imposer un modèle asynchrone à tous les appelants existants.

    Args:
        dialog: Dialogue déjà entièrement configuré (boutons, contenu).
            N'est PAS détruit par cette fonction : l'appelant garde la
            responsabilité de `dialog.destroy()`, exactement comme avant.

    Returns:
        Gtk.ResponseType: La réponse obtenue, ou `Gtk.ResponseType.NONE` si
        le dialogue a été détruit sans réponse explicite (ex. fermeture via
        le gestionnaire de fenêtres, ou un handler interne du dialogue qui
        appelle lui-même `self.destroy()` — cf. `KeyPickerDialog`).
    """
    logger.debug("run_dialog_sync: entrée")
    loop = GLib.MainLoop()
    result = {"response": Gtk.ResponseType.NONE}
    state = {"destroyed": False}

    def _on_response(_dlg, response_id):
        logger.debug("run_dialog_sync._on_response: entrée")
        result["response"] = response_id
        if loop.is_running():
            loop.quit()
        logger.debug("run_dialog_sync._on_response: fin")

    def _on_destroy(_dlg):
        # Filet de sécurité : si le dialogue est détruit sans passer par
        # notre handler "response" (fermeture fenêtre, ou un handler interne
        # du dialogue qui a lui-même appelé destroy()), on ne bloque jamais
        # indéfiniment.
        logger.debug("run_dialog_sync._on_destroy: entrée")
        state["destroyed"] = True
        if loop.is_running():
            loop.quit()
        logger.debug("run_dialog_sync._on_destroy: fin")

    response_handler = dialog.connect("response", _on_response)
    destroy_handler = dialog.connect("destroy", _on_destroy)
    dialog.show()
    loop.run()

    # Si le dialogue a déjà été détruit (par son propre callback de réponse
    # interne, ex. KeyPickerDialog, ou par une fermeture fenêtre), tenter de
    # déconnecter nos handlers émettrait un G_WARNING bruyant ("instance has
    # no handler with id ...") : on saute simplement cette étape, devenue
    # inutile puisque l'objet GObject a déjà libéré ses handlers avec lui.
    if not state["destroyed"]:
        dialog.disconnect(response_handler)
        dialog.disconnect(destroy_handler)
    logger.debug("run_dialog_sync: sortie")
    return result["response"]


def show_open_dialog(parent, title, action, last_path_holder=None):
    """Affiche un dialogue de sélection de fichier (ouverture ou sauvegarde).

    Utilitaire générique partagé par le cœur (``gnome_connection_manager.py``)
    et tout plugin ayant besoin de choisir un fichier (ex. les imports/
    exports CSV/JSON). Volontairement sans dépendance vers quoi que ce soit
    de spécifique à GCM (pas de ``USERHOME_DIR`` global, pas d'icône
    d'application) pour rester importable depuis n'importe quel module
    plugin sans risque de cycle d'import.

    Args:
        parent (Gtk.Widget): Widget parent pour le dialogue.
        title (str): Titre du dialogue.
        action (Gtk.FileChooserAction): OPEN ou SAVE.
        last_path_holder (object, optional): Objet sur lequel lire/écrire
            l'attribut ``lastPath`` (dernier dossier utilisé), pour retenir
            l'emplacement entre deux appels — typiquement ``parent`` lui-même
            si non fourni. Défaut : ``$HOME`` (ou ``""`` si indisponible).

    Returns:
        str or None: Chemin du fichier sélectionné, ou None si annulé.
    """
    logger.debug("show_open_dialog: entrée")
    import os

    holder = last_path_holder if last_path_holder is not None else parent
    dlg = Gtk.FileChooserDialog(title=title, parent=parent, action=action)
    dlg.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
    btn_ok = dlg.add_button(
        _("Save") if action == Gtk.FileChooserAction.SAVE else _("Open"),
        Gtk.ResponseType.OK,
    )
    btn_ok.get_style_context().add_class("suggested-action")
    # GTK4 a retiré la confirmation d'écrasement configurable de
    # GtkFileChooser (le sélecteur la gère lui-même) ; la base GTK3
    # encore debout la demande toujours.
    if hasattr(dlg, "set_do_overwrite_confirmation"):
        dlg.set_do_overwrite_confirmation(True)
    if holder is not None and not hasattr(holder, "lastPath"):
        holder.lastPath = os.path.expanduser("~") or ""
    # GTK4 : set_current_folder prend un Gio.File (la version str a
    # disparu avec GtkFileChooser en GTK4).
    dossier = getattr(holder, "lastPath", os.path.expanduser("~") or "")
    dlg.set_current_folder(Gio.File.new_for_path(dossier))

    if run_dialog_sync(dlg) == Gtk.ResponseType.OK:
        # GTK4 : get_filename() a disparu, get_file() renvoie un Gio.File
        # (None si rien n'est encore sélectionné).
        fichier = dlg.get_file()
        filename = fichier.get_path() if fichier is not None else None
        if holder is not None and filename is not None:
            holder.lastPath = os.path.dirname(filename)
    else:
        filename = None
    dlg.destroy()
    logger.debug("show_open_dialog: sortie")
    return filename


def msgbox(text, parent=None, icon_path=None):
    """Affiche une boîte de dialogue d'erreur/information modale.

    Version générique partagée par le cœur et les plugins. L'icône
    d'application (spécifique à GCM, cf. ``ICON_PATH`` dans
    ``gnome_connection_manager.py``) est optionnelle : les appels depuis un
    plugin l'omettent simplement plutôt que de dupliquer sa résolution.

    Args:
        text (str): Message à afficher.
        parent (Gtk.Window, optional): Fenêtre parente. Defaults to None.
        icon_path (str, optional): Chemin d'une icône à afficher sur la
            fenêtre de dialogue. Ignoré si absent ou introuvable.
    """
    logger.debug("msgbox: entrée")
    msg_box = Gtk.MessageDialog(
        parent=parent,
        modal=True,
        message_type=Gtk.MessageType.ERROR,
        buttons=Gtk.ButtonsType.OK,
        text=text,
    )
    if icon_path and hasattr(msg_box, "set_icon_from_file"):
        # GTK4 a retiré les icônes de fenêtre (l'application les fournit
        # au bureau) : l'appel ne concerne que la base GTK3, avec son
        # repli silencieux si l'icône est introuvable.
        try:
            msg_box.set_icon_from_file(icon_path)
        except GLib.Error as exc:
            logger.debug("msgbox: {} : {}", type(exc).__name__, exc)
    ok_btn = msg_box.get_widget_for_response(Gtk.ResponseType.OK)
    if ok_btn is not None:
        ok_btn.get_style_context().add_class("suggested-action")
    run_dialog_sync(msg_box)
    msg_box.destroy()
    logger.debug("msgbox: fin")


# ``_()`` est injecté globalement par bindtextdomain() au démarrage de
# l'application (cf. gnome_connection_manager.py). Repli no-op pour rester
# important-safe si ce module est chargé isolément (tests, outillage).
try:
    _  # type: ignore[name-defined]  # noqa: B018 -- sonde volontaire de NameError, pas une expression morte
except NameError as exc:
    logger.debug("<module>: {} : {}", type(exc).__name__, exc)

    def _(s: str) -> str:  # noqa: D401
        logger.debug("_: entrée")
        logger.debug("_: sortie")
        return s


class GCMBase:
    """Base class GTK3 natif remplacant SimpleGladeApp.

    Charge un fichier .glade via Gtk.Builder, expose tous les widgets
    comme attributs d'instance, connecte les signaux, puis appelle new().
    """

    def __init__(self, path=None, root=None, domain=None, parent=None, show=True, **kwargs):
        """Initialise l'instance en mode "100% Python" (sans Glade).

        Reserve aux ecrans qui heritent directement d'un widget GTK deja
        construit en Python (ex. ``class SshConfigEditorDialog(GCMBase,
        Gtk.Dialog)``). L'appelant est responsable d'avoir deja construit
        son arbre de widgets (typiquement via ``Gtk.Dialog.__init__`` avant
        cet appel) et de positionner ``self.main_widget`` lui-meme (souvent
        ``self.main_widget = self``, puisque l'instance EST deja le widget
        GTK).

        Args:
            path: Non utilise (conserve pour compatibilite de signature).
                Doit toujours valoir ``None`` — plus aucun appelant du
                projet ne charge de fichier ``.glade``.
            root: Non utilise (idem).
            domain: Non utilise (idem).
            parent (Gtk.Window, optional): Fenetre parente (pour les dialogues).
            show: Non utilise (idem).
            **kwargs: Attributs supplementaires a definir sur l'instance.
        """
        logger.debug("GCMBase.__init__: entrée")
        for key, value in kwargs.items():
            try:
                setattr(self, key, _weakref.proxy(value))
            except TypeError as exc:
                logger.debug("GCMBase.__init__: {} : {}", type(exc).__name__, exc)
                setattr(self, key, value)

        # Ecran ouvert comme onglet epingle (cf. Wmain.open_management_tab) au
        # lieu d'une fenetre autonome. Positionne par l'appelant apres
        # construction ; request_close() s'appuie dessus pour savoir comment
        # se fermer.
        self.is_tab = False
        self.tab_key = None
        self.builder = None
        logger.debug("GCMBase.__init__: fin")

    def request_close(self):
        """Ferme cet ecran.

        Si l'ecran a ete ouvert comme onglet epingle (``self.is_tab``),
        delegue la fermeture a ``Wmain.close_management_tab`` (retire
        l'onglet du notebook). Sinon, conserve le comportement historique :
        detruit la fenetre autonome.
        """
        logger.debug("GCMBase.request_close: entrée")
        if self.is_tab:
            from gnome_connection_manager import wMain

            wMain.close_management_tab(self.tab_key)
        elif self.main_widget is not None:
            self.main_widget.destroy()
        logger.debug("GCMBase.request_close: fin")

    def on_tab_will_close(self):
        """Point d'extension appele juste avant que ``Wmain.close_management_tab``
        ne retire cet ecran du notebook.

        Appele de facon uniforme quelle que soit la voie de fermeture : bouton
        OK/Annuler interne a l'ecran (-> ``request_close`` -> ``close_management_tab``)
        ou bouton "x" de l'onglet (qui appelle ``close_management_tab``
        directement, sans passer par ``request_close``). A surcharger dans
        les sous-classes ayant un nettoyage a faire specifiquement a la
        fermeture (ex. ``Wcluster``, qui doit deselectionner les onglets mis
        en surbrillance). Ne fait rien par defaut.
        """
        pass

    def new(self):
        """Appelee apres construction. A surcharger dans les sous-classes."""
        pass

    def run(self):
        """Demarre la boucle evenementielle GTK principale."""
        logger.debug("GCMBase.run: entrée")
        try:
            Gtk.main()
        except KeyboardInterrupt as exc:
            logger.debug("GCMBase.run: {} : {}", type(exc).__name__, exc)
            pass
        logger.debug("GCMBase.run: fin")


def embed_dialog_content(gcm_instance):
    """Detache la zone de contenu d'un ``GCMBase`` dont la racine Glade est
    un ``Gtk.Dialog`` (ou, depuis ``SSHKeyManagerDialog``, un ``Gtk.Window``
    100% Python), pour l'integrer ailleurs (typiquement comme page d'un
    ``Gtk.Notebook``) au lieu de l'afficher comme fenetre autonome.

    La coquille (vide une fois son contenu detache) est detruite : seule la
    zone de contenu survit et est reassignee a ``gcm_instance.main_widget``.
    Pour un ``Gtk.Dialog``, c'est ``get_content_area()`` (qui inclut la
    barre de boutons OK/Annuler d'origine). Pour un ``Gtk.Window`` simple
    (ex. ``SSHKeyManagerDialog``, qui fait juste ``self.add(vbox)``), c'est
    l'unique enfant renvoye par ``get_child()``.

    Cas particulier (ecrans 100% Python, ex. ``SshConfigEditorDialog``,
    ``SSHKeyManagerDialog``) : quand ``gcm_instance`` EST elle-meme la
    racine GTK (heritage direct, pas de Glade separe), on NE detruit PAS la
    coquille : ``gcm_instance`` reste la reference "proprietaire" gardee
    vivante par l'onglet (``_gcm_owner_instance``) et continue d'etre
    utilisee ailleurs dans son propre code (ex. comme fenetre parente de
    sous-dialogues, via ``_top_window()``). La detruire reviendrait a
    detruire l'objet dont on a justement besoin de conserver l'etat. La
    coquille - videe de son contenu, jamais affichee - reste simplement
    inerte en memoire, sans impact visible.

    Args:
        gcm_instance (GCMBase): Instance dont ``main_widget`` est un
            ``Gtk.Dialog`` ou ``Gtk.Window`` deja construit (typiquement
            avec ``show=False``).

    Returns:
        Gtk.Widget: Le widget de contenu detache, pret a etre insere dans un
        nouveau parent.
    """
    logger.debug("embed_dialog_content: entrée")
    dialog = gcm_instance.main_widget
    if isinstance(dialog, Gtk.Dialog):
        content = dialog.get_content_area()
    elif isinstance(dialog, Gtk.Window):
        content = dialog.get_child()
    else:
        raise TypeError(
            "embed_dialog_content requiert une racine Gtk.Dialog ou Gtk.Window, recu : %r"
            % (dialog,)
        )
    if content is not None:
        # GTK4 : GtkContainer.remove() a disparu. Un enfant direct d'une
        # Window se détache via set_child(None) ; la zone de contenu d'un
        # Dialog a pour parent une Box interne et se détache elle-même.
        if content.get_parent() is dialog:
            dialog.set_child(None)
        else:
            content.unparent()
    if dialog is not gcm_instance:
        dialog.destroy()
    gcm_instance.main_widget = content
    logger.debug("embed_dialog_content: sortie")
    return content


class conf:
    """Classe de configuration globale de l'application."""

    WORD_SEPARATORS = "-A-Za-z0-9,./?%&#:_=+@~"
    BUFFER_LINES = 2000
    STARTUP_LOCAL = True
    LOG_LOCAL = False
    CONFIRM_ON_EXIT = True
    FONT_COLOR = ""
    BACK_COLOR = ""
    TRANSPARENCY = 0
    TERM = ""
    PASTE_ON_RIGHT_CLICK = 1
    CONFIRM_ON_CLOSE_TAB = 1
    CONFIRM_ON_CLOSE_TAB_MIDDLE = 1
    AUTO_CLOSE_TAB = 0
    CYCLE_TABS = True
    DISABLE_SHORTCUTS = False
    # Notifications desktop (deconnexion terminal) — backlog features.md
    # §4.2 #109. Defaut False = notifications activees (comportement opt-out,
    # coherent avec DISABLE_SHORTCUTS ci-dessus) ; envoyees par un appel
    # D-Bus direct a org.freedesktop.Notifications (Gio, deja une dependance
    # dure du projet — pas de nouveau paquet requis), voir
    # gnome_connection_manager.py::send_desktop_notification(). Sans effet
    # si aucun bus de session/demon de notifications n'est disponible
    # (echec silencieux, journalise en debug).
    DISABLE_NOTIFICATIONS = False
    # Sauvegarde/restauration des onglets ouverts — backlog features.md
    # §4.2. Defaut False = opt-in : rouvrir automatiquement des sessions
    # (SSH/RDP/...) au demarrage peut surprendre (mots de passe redemandes,
    # connexions vers des hotes injoignables) — contrairement a
    # DISABLE_SHORTCUTS/DISABLE_NOTIFICATIONS ci-dessus, ce n'est pas un
    # comportement opt-out. OPEN_TABS est la liste "groupe/nom" persistee
    # (section [window], cle open-tabs), recalculee a chaque writeConfig()
    # que l'option soit active ou non, pour que l'activer plus tard restaure
    # bien les onglets de la derniere session plutot qu'une liste vide.
    RESTORE_OPEN_TABS = False
    OPEN_TABS = ""
    # Commandes favorites du dialogue Cluster (#84) : section INI dediee
    # [cluster_favorites], lue par gcm4_core.load_cluster_favorites() (qui
    # fournit les favoris par defaut df / apt update / updatedb).
    CLUSTER_FAVORITES: list = []
    # Couleurs de groupe (dossier) — backlog features.md §4.2, "Couleurs par
    # groupe" (comme PuTTY). Chaine "groupe=couleur,groupe2=couleur2"
    # (section [window], cle group-colors) : pas de section INI dediee car
    # configparser normalise la casse des cles d'option, ce qui casserait un
    # nom de groupe contenant des majuscules. Voir
    # gcm4_core.serialize_group_colors()/parse_group_colors()/
    # resolve_group_color() pour la logique pure (avec heritage vers les
    # sous-dossiers/hotes sans couleur propre).
    GROUP_COLORS = ""
    COLLAPSED_FOLDERS = ""
    LEFT_PANEL_WIDTH = 100
    CHECK_UPDATES = True
    WINDOW_WIDTH = -1
    WINDOW_HEIGHT = -1
    FONT = ""
    DISABLE_HOSTS_STRIPES = False
    AUTO_COPY_SELECTION = 0
    LOG_PATH = None  # Sera défini par le module principal
    CONTINUOUS_TAB_LOG = False
    CONTINUOUS_LOG_PATH = None  # Sera défini par le module principal
    SHOW_PANEL = True
    DARK_MODE = False
    THEME_MODE = "system"  # "system" | "light" | "dark"
    VERSION = 0
    UPDATE_TITLE = 0
    APP_TITLE = ""  # Sera défini par le module principal
    LIBVIRT_DEFAULT_USER = "root"
    PROXMOX_DEFAULT_USER = "root"
    VIRTUALBOX_DEFAULT_USER = "root"
