# SPDX-License-Identifier: GPL-3.0-or-later
"""Plugin outil « Déploiement Netmiko » GTK4 (#441, portage #179).

L'onglet du plugin est piloté comme l'utilisateur : activation de l'action
du menu Outils, choix des fichiers d'inventaire et de gabarit via les
rappels du sélecteur, chargement, édition de profils, validation à blanc
puis lancement d'un envoi factice (``run_bulk_push`` remplacé) suivi sur
la page Suivi et lu sur la page Résultats.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import gcm4.ui  # noqa: F401  (require_version avant gi.repository)
from gcm4.core import registre
from gcm4.ui.main_window import MainWindow

RACINE = Path(__file__).resolve().parents[2]


def _fenetre(app):
    return next(f for f in app.get_windows() if isinstance(f, MainWindow))


@pytest.fixture
def app_netmiko(tmp_path, monkeypatch, pomper):
    """Application sur la fixture aes avec le seul plugin netmiko_push."""
    import shutil

    from gi.repository import Gio

    from gcm4.core import chemins
    from gcm4.core import config as config_mod
    from gcm4.ui.application import Gcm4Application

    monkeypatch.setenv("USER", "utilisateur")
    monkeypatch.setattr(config_mod, "_sauvegardes", set())
    dossier = tmp_path / "gcm"
    shutil.copytree(RACINE / "tests" / "data" / "config" / "aes", dossier)
    chemins.preparer(str(dossier))
    candidats = [
        c for c in registre.candidats_dossier(RACINE / "plugins") if c.nom == "netmiko_push"
    ]
    app = Gcm4Application(
        _args(tmp_path),
        flags=Gio.ApplicationFlags.NON_UNIQUE,
        candidats=candidats,
    )
    app.register(None)
    app.activate()
    pomper()
    yield app, dossier
    for fenetre in list(app.get_windows()):
        fenetre.destroy()
    pomper()


def _args(tmp_path):
    import argparse

    return argparse.Namespace(config_dir=str(tmp_path / "gcm"), debug=False, hotes=[])


@pytest.fixture
def plugin(pomper, app_netmiko):
    """Module du plugin chargé par le registre, onglet ouvert par l'action."""
    import gcm4_plugin_netmiko_push.gtk4 as module  # noqa: PLC0415

    app, _dossier = app_netmiko
    fenetre = _fenetre(app)
    fenetre.activate_action("win.outil-netmiko-push", None)
    pomper()
    onglet = fenetre.onglets.get_nth_page(fenetre.onglets.get_n_pages() - 1)
    assert isinstance(onglet, module.OngletNetmiko)
    return module, onglet, fenetre


def _fichiers(tmp_path: Path) -> tuple[Path, Path]:
    """Inventaire CSV (deux hôtes, une variable) et gabarit à rendre."""
    csv = tmp_path / "inventaire.csv"
    csv.write_text(
        "ip,type,vlan\n10.0.0.1,comware,10\n10.0.0.2,comware,20\n",
        encoding="utf-8",
    )
    gabarit = tmp_path / "gabarit.txt"
    gabarit.write_text("vlan #vlan#\n", encoding="utf-8")
    return csv, gabarit


def _pomper_jusqua(condition, essais: int = 400):
    """Pompe la boucle GTK jusqu'à ce que ``condition()`` soit vrai.

    Une courte pause entre deux itérations laisse le thread ouvrier
    progresser même sur une machine chargée (l'envoi est asynchrone).
    """
    import time

    from gi.repository import GLib

    contexte = GLib.MainContext.default()
    for _ in range(essais):
        if condition():
            return True
        contexte.iteration(False)
        time.sleep(0.005)
    return condition()


def test_entree_de_menu_et_onglet(plugin, app_netmiko):
    """L'outil est dans le sous-menu Tools et son action ouvre l'onglet."""
    app, _dossier = app_netmiko
    fenetre = _fenetre(app)
    sous_menus = []
    modele = fenetre.bouton_menu.get_menu_model()
    for i in range(modele.get_n_items()):
        sous_menus.append(modele.get_item_attribute_value(i, "label", None).get_string())
    assert "Tools" in sous_menus
    assert fenetre.onglets.get_n_pages() == 2  # accueil + outil
    assert fenetre.titres_onglets[plugin[1]] == "Netmiko"


def test_charger_inventaire_et_apercu(plugin, pomper, tmp_path):
    """Chargement de l'inventaire : lignes cochées, aperçu rendu au clic."""
    module, onglet, fenetre = plugin
    csv, gabarit = _fichiers(tmp_path)
    onglet._sur_csv_choisi(str(csv))
    onglet._sur_gabarit_choisi(str(gabarit))
    onglet._on_charger_inventaire(None)
    pomper()
    assert len(onglet.rows) == 2
    assert len(onglet._vue_inventaire.selectionnees()) == 2
    assert "2" in onglet._etiquette_statut.get_label()

    # Sélection de la première ligne : le gabarit rendu apparaît.
    onglet._vue_inventaire.selection.set_selected(0)
    pomper()
    tampon = onglet._tampon_apercu
    assert tampon.get_text(tampon.get_start_iter(), tampon.get_end_iter(), False) == "vlan 10\n"

    # Décoche de la seconde ligne : une seule reste cochée.
    deuxieme = onglet._vue_inventaire.elements()[1]
    deuxieme.coche = False
    assert len(onglet._lignes_cochees()) == 1


def test_profils_edition_et_ecriture(plugin, pomper, tmp_path):
    """Choix du fichier de profils, édition, écriture sur disque."""
    module, onglet, fenetre = plugin
    chemin = tmp_path / "profiles.ini"
    onglet._sur_profils_choisis(str(chemin))
    pomper()
    assert chemin.exists()  # créé depuis le modèle

    # Nouveau profil : formulaire rempli puis enregistré.
    onglet._ch_nom.set_text("comware")
    onglet._choix_constructeur.set_selected(
        list(module.core.VENDOR_DEVICE_TYPES).index("HPE / Aruba")
    )
    pomper()
    onglet._choix_type.set_selected(0)
    onglet._ch_utilisateur.set_text("admin")
    onglet._ch_mot_de_passe.set_text("secret")
    onglet._on_profil_enregistrer(None)
    pomper()
    assert onglet.profiles.get("comware") is not None
    assert "mis à jour" in onglet._etiquette_statut.get_label()

    # Sélection dans la liste : le formulaire se remplit (cascade incluse).
    index_comware = [p.name for p in onglet.profiles.all()].index("comware")
    onglet._vue_profils.selection.set_selected(index_comware)
    pomper()
    assert onglet._ch_nom.get_text() == "comware"
    assert onglet._choix_type.get_selected() == 0

    # Écriture sur disque puis relecture.
    onglet._on_ecrire_profils(None)
    pomper()
    relu = module.core.ProfileStore(chemin)
    assert relu.get("comware") is not None
    assert relu.get("comware").username == "admin"

    # Suppression dans le magasin (en mémoire).
    onglet._on_profil_supprimer(None)
    pomper()
    assert onglet.profiles.get("comware") is None

    # Re-choix du même fichier existant : relu tel quel depuis le disque.
    onglet._sur_profils_choisis(str(chemin))
    pomper()
    assert onglet.profiles.get("comware") is not None


def test_gardes_et_validation(plugin, pomper, tmp_path):
    """Lancement sans fichiers, sans inventaire, puis validation à blanc."""
    module, onglet, fenetre = plugin

    # Aucun fichier choisi : refus sans exception.
    onglet._on_charger_inventaire(None)
    assert "Choisissez l'inventaire CSV et le gabarit." in onglet._etiquette_statut.get_label()

    # Lancement sans inventaire chargé : refus.
    onglet._on_lancer(None)
    assert "Chargez d'abord un inventaire." in onglet._etiquette_statut.get_label()

    # Validation sans inventaire : même refus.
    onglet._on_valider(None)
    assert "Chargez d'abord un inventaire." in onglet._etiquette_statut.get_label()

    csv, gabarit = _fichiers(tmp_path)
    onglet._sur_csv_choisi(str(csv))
    onglet._sur_gabarit_choisi(str(gabarit))
    onglet._on_charger_inventaire(None)
    pomper()

    # Validation à blanc : profil inconnu pour les deux lignes.
    onglet._on_valider(None)
    assert "0 prêt(s), 2 en erreur." in onglet._etiquette_statut.get_label()

    # Toutes les lignes décochées : lancement refusé.
    for element in onglet._vue_inventaire.elements():
        element.coche = False
    onglet._on_lancer(None)
    assert "Aucune ligne cochée." in onglet._etiquette_statut.get_label()

    # Profil connu mais variable absente du CSV : erreur comptée aussi.
    onglet.profiles.set(module.core.DeviceProfile(name="comware", device_type="hp_comware"))
    gabarit_absent = tmp_path / "absent.txt"
    gabarit_absent.write_text("vlan #absent#\n", encoding="utf-8")
    onglet._chemin_gabarit = gabarit_absent
    onglet._on_charger_inventaire(None)
    pomper()
    onglet._on_valider(None)
    assert "0 prêt(s), 2 en erreur." in onglet._etiquette_statut.get_label()

    # Gabarit complet cette fois : les deux lignes sont prêtes.
    onglet._chemin_gabarit = gabarit
    onglet._on_charger_inventaire(None)
    pomper()
    onglet._on_valider(None)
    assert "2 prêt(s), 0 en erreur." in onglet._etiquette_statut.get_label()


def test_envoi_suivi_et_resultats(plugin, pomper, tmp_path, monkeypatch):
    """Envoi factice : suivi ligne à ligne, compteurs, résultats, journal."""
    module, onglet, fenetre = plugin
    csv, gabarit = _fichiers(tmp_path)
    onglet._sur_csv_choisi(str(csv))
    onglet._sur_gabarit_choisi(str(gabarit))
    onglet._on_charger_inventaire(None)
    pomper()

    def _faux_bulk(
        rows,
        profiles,
        template_text,
        *,
        log_dir,
        save,
        max_workers,
        on_row_start,
        on_row_done,
        cancel_event,
    ):
        """Envoi factice : chaque ligne démarre puis finit, l'une en échec.

        Le journal de la première ligne est écrit au chemin attendu par
        la page Résultats.
        """
        assert len(rows) == 2
        assert save is False
        Path(log_dir).mkdir(parents=True, exist_ok=True)
        resultats = []
        for index, ligne in enumerate(rows):
            on_row_start(ligne)
            journal = Path(log_dir) / f"{ligne.ip}.log"
            journal.write_text(f"vlan {ligne.variables['vlan']}\n", encoding="utf-8")
            resultat = module.core.PushResult(
                row=ligne,
                status=module.core.STATUS_FAILED if index else module.core.STATUS_OK,
                duration_s=1.5,
                log_path=str(journal),
            )
            resultats.append(resultat)
            on_row_done(resultat)
        return resultats

    monkeypatch.setattr(module.core, "run_bulk_push", _faux_bulk)
    monkeypatch.setattr(module, "DOSSIER_JOURNAUX", tmp_path / "journaux")

    onglet._on_lancer(None)
    assert _pomper_jusqua(lambda: not onglet._en_cours)
    pomper()

    # Page Suivi : deux lignes, une OK, une en échec, progression complète.
    assert onglet._stack.get_visible_child_name() == "monitor"
    assert len(onglet._suivi) == 2
    assert onglet._etiquette_ok.get_label() == "OK : 1"
    assert onglet._etiquette_echecs.get_label() == "Échecs : 1"
    assert onglet._etiquette_attente.get_label() == "En attente : 0"
    assert onglet._progression.get_fraction() == 1.0
    assert not onglet._bouton_annuler.get_sensitive()
    assert onglet._bouton_voir_resultats.get_sensitive()

    # Page Résultats : résumé, deux lignes, journal affiché au clic.
    onglet._stack.set_visible_child_name("results")
    resume = onglet._etiquette_resume.get_label()
    assert "Total : 2" in resume and "OK : 1" in resume and "Échecs : 1" in resume
    onglet._vue_resultats.selection.set_selected(0)
    pomper()
    tampon = onglet._tampon_journal
    assert tampon.get_text(tampon.get_start_iter(), tampon.get_end_iter(), False) == "vlan 10\n"


def test_annulation_demandee(plugin, pomper, tmp_path):
    """Le bouton d'annulation arme l'événement lu par le cœur."""
    module, onglet, fenetre = plugin
    assert not onglet._annuler.is_set()
    onglet._bouton_annuler.set_sensitive(True)
    onglet._on_annuler(None)
    assert onglet._annuler.is_set()
    assert not onglet._bouton_annuler.get_sensitive()


def test_gardes_selecteurs_et_profils(plugin, pomper, tmp_path, monkeypatch):
    """Sélecteurs annulés ou sans parent, profils en erreur, formulaire vidé."""
    module, onglet, fenetre = plugin

    # Onglet hors fenêtre : les sélecteurs restent silencieux (garde).
    orphelin = module.OngletNetmiko(onglet.context)
    orphelin._ouvrir_selecteur("titre", lambda _c: None, True)
    orphelin._on_choisir_profils(None)

    # Sélecteurs annulés : aucun chemin ne change.
    onglet._sur_csv_choisi(None)
    onglet._sur_gabarit_choisi(None)
    onglet._sur_profils_choisis(None)
    assert onglet._chemin_csv is None
    assert onglet._chemin_gabarit is None
    assert onglet._chemin_profils is None

    # Ouvertures réelles : fichiers.choisir intercepté, aucun dialogue affiché.
    ouvertures = []
    monkeypatch.setattr(
        module.fichiers, "choisir", lambda *args, **kwargs: ouvertures.append(args)
    )
    onglet._ouvrir_selecteur("Inventaire (CSV) :", onglet._sur_csv_choisi, True)
    onglet._on_choisir_profils(None)
    assert len(ouvertures) == 2

    # Chargement des profils en erreur : notifié, magasin inchangé.
    def _muet(chemin):
        raise OSError("fichier refusé")

    monkeypatch.setattr(module.core.ProfileStore, "write_template", _muet)
    onglet._sur_profils_choisis(str(tmp_path / "profiles.ini"))
    assert "Erreur de chargement" in onglet._etiquette_statut.get_label()

    # Nouveau profil : formulaire vidé, cascade réinitialisée.
    onglet._ch_nom.set_text("ancien")
    onglet._choix_constructeur.set_selected(0)
    onglet._on_profil_nouveau(None)
    assert onglet._ch_nom.get_text() == ""

    # Enregistrement sans nom, puis sans modèle : refus notifiés.
    onglet._on_profil_enregistrer(None)
    assert "doit avoir un nom" in onglet._etiquette_statut.get_label()
    onglet._ch_nom.set_text("sans-modele")
    onglet._on_profil_enregistrer(None)
    assert "constructeur et un modèle" in onglet._etiquette_statut.get_label()

    # Profil au constructeur inconnu : le formulaire se remplit sans casser.
    inconnu = module.core.DeviceProfile(
        name="inconnu", vendor="Nulle part", device_type="driver_inconnu"
    )
    onglet._on_profil_choisi(inconnu)
    assert onglet._ch_nom.get_text() == "inconnu"

    # Sans constructeur, modèle hors liste du constructeur, second modèle :
    # la cascade se comporte dans les trois cas.
    onglet._on_profil_choisi(
        module.core.DeviceProfile(name="sans-vendor", device_type="cisco_ios")
    )
    assert onglet._ch_nom.get_text() == "sans-vendor"
    # Constructeur désélectionné : la cascade se vide sans casser.
    combo_vide = type(
        "ComboVide", (), {"get_selected": staticmethod(lambda: module.Gtk.INVALID_LIST_POSITION)}
    )
    onglet._on_constructeur_change(combo_vide(), None)
    onglet._on_profil_choisi(
        module.core.DeviceProfile(name="hors-liste", vendor="Cisco", device_type="hp_comware")
    )
    onglet._on_profil_choisi(
        module.core.DeviceProfile(name="second-modele", vendor="Cisco", device_type="cisco_xe")
    )

    # Visibilité des mots de passe : la case bascule les deux champs.
    assert not onglet._ch_mot_de_passe.get_visibility()
    onglet._case_voir.set_active(True)
    assert onglet._ch_mot_de_passe.get_visibility()
    assert onglet._ch_secret.get_visibility()

    # Sélection sans rappel, puis sélection vide : gardes silencieuses.
    onglet._vue_suivi._selection_changee(None, None, None)
    onglet._vue_resultats._selection_changee(
        type("SelectionVide", (), {"get_selected_item": lambda self: None})(), None, None
    )

    # Suppression sans sélection dans la liste : garde silencieuse.
    onglet._on_profil_supprimer(None)
    assert onglet.profiles.get("inconnu") is None

    # Écriture sans fichier choisi, puis en erreur : refus notifiés.
    onglet._on_ecrire_profils(None)
    assert "Choisissez d'abord un fichier" in onglet._etiquette_statut.get_label()
    onglet._chemin_profils = tmp_path / "profiles.ini"

    def _echoue(self):
        raise OSError("disque plein")

    monkeypatch.setattr(module.core.ProfileStore, "save", _echoue)
    onglet._on_ecrire_profils(None)
    assert "Erreur d'écriture" in onglet._etiquette_statut.get_label()


def test_erreurs_et_journal(plugin, pomper, tmp_path, monkeypatch):
    """Chargement en erreur, variables absentes, journal illisible, dossier."""
    module, onglet, fenetre = plugin
    csv, gabarit = _fichiers(tmp_path)

    # Lancement pendant un envoi déjà actif : garde silencieuse.
    onglet._en_cours = True
    onglet._on_lancer(None)
    onglet._en_cours = False

    # Gabarit illisible (dossier) : erreur de chargement notifiée.
    onglet._sur_csv_choisi(str(csv))
    onglet._chemin_gabarit = tmp_path  # un dossier : read_text échoue
    onglet._on_charger_inventaire(None)
    assert "Erreur de chargement" in onglet._etiquette_statut.get_label()
    # L'inventaire, lui, a été lu avant l'échec du gabarit.
    assert len(onglet.rows) == 2

    # Inventaire valide puis ligne sans la variable du gabarit : avertissement.
    gabarit_absent = tmp_path / "absent.txt"
    gabarit_absent.write_text("vlan #absent#\n", encoding="utf-8")
    onglet._chemin_gabarit = gabarit_absent
    onglet._on_charger_inventaire(None)
    pomper()
    onglet._on_ligne_inventaire_choisie(onglet.rows[0])
    assert "Variables absentes du CSV" in onglet._etiquette_apercu.get_label()

    # Suivi d'une ligne inconnue : garde silencieuse.
    onglet._statut_suivi(999, "running", "", "")

    # Compteur : un statut non terminal est ignoré, pas d'exception.
    onglet._resultats[999] = module.core.PushResult(row=onglet.rows[0], status="running")
    onglet._maj_compteurs()

    # Résultat sans journal puis journal illisible : tampon vidé ou message.
    resultat = module.core.PushResult(row=onglet.rows[0], status=module.core.STATUS_OK)
    onglet._on_resultat_choisi(resultat)
    tampon = onglet._tampon_journal
    assert tampon.get_text(tampon.get_start_iter(), tampon.get_end_iter(), False) == ""
    resultat.log_path = str(tmp_path / "inexistant.log")
    onglet._on_resultat_choisi(resultat)
    assert "Impossible de lire le journal" in tampon.get_text(
        tampon.get_start_iter(), tampon.get_end_iter(), False
    )

    # Dossier des journaux : sans dossier rien ne se passe, avec dossier oui.
    ouverts = []
    ouvrir_dossier_reel = module._ouvrir_dossier
    monkeypatch.setattr(
        module, "_ouvrir_dossier", lambda fenetre, dossier: ouverts.append(dossier)
    )
    onglet._log_dir = None
    onglet._on_ouvrir_journaux(None)
    assert ouverts == []
    onglet._log_dir = tmp_path / "journaux"
    onglet._on_ouvrir_journaux(None)
    assert ouverts == [str(tmp_path / "journaux")]

    # Lanceur GTK ≥ 4.10 puis repli AppInfo (GTK < 4.10).
    lanceurs = []

    class FauxFileLauncher:
        @staticmethod
        def new(fichier):
            class _Lanceur:
                def launch(self, parent, annul, fin):
                    lanceurs.append(fichier.get_path())

            return _Lanceur()

    monkeypatch.setattr(module.Gtk, "FileLauncher", FauxFileLauncher, raising=False)
    ouvrir_dossier_reel(None, str(tmp_path))
    assert lanceurs == [str(tmp_path)]

    # Rappel « fin » de lancement : échec journalisé puis succès silencieux.
    fins = []

    class FauxFileLauncherRepond:
        @staticmethod
        def new(fichier):
            class _Lanceur:
                def launch(self, parent, annul, fin):
                    fin(self, None)

                def launch_finish(self, resultat):
                    fins.append("fin")
                    if len(fins) == 1:
                        raise module.GLib.Error("échec simulée")

            return _Lanceur()

    monkeypatch.setattr(module.Gtk, "FileLauncher", FauxFileLauncherRepond, raising=False)
    ouvrir_dossier_reel(None, str(tmp_path))
    ouvrir_dossier_reel(None, str(tmp_path))
    assert len(fins) == 2

    # Repli AppInfo (GTK < 4.10) : un « Gtk » de module sans FileLauncher.
    uris = []
    monkeypatch.setattr(module, "Gtk", type("GtkSansLauncher", (), {}))
    monkeypatch.setattr(
        module.Gio.AppInfo,
        "launch_default_for_uri",
        lambda uri, contexte: uris.append(uri),
    )
    ouvrir_dossier_reel(None, str(tmp_path))
    assert uris and uris[0].endswith(str(tmp_path))
