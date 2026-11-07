# Proposition d'architecture — plugins par dossier, séparation core/GTK

> **Statut : proposition à valider avant tout codage.** Rien n'a été modifié
> dans le dépôt à ce stade — conformément à la méthode de travail actée
> (`claude.md` : « proposer un plan avant de coder, attendre validation avant
> chaque écran »).

## 1. Constat sur l'existant (vérifié dans le code, pas supposé)

- `plugins/` est un dossier **plat**, pas un package Python (pas de
  `__init__.py`) : `gnome_connection_manager.py` fait
  `sys.path.insert(0, .../plugins)` puis `from plugins.plugin_base import ...`,
  et chaque fichier `plugin_*.py` s'importe ensuite entre eux en imports
  plats (`from plugin_base import ConnectionPlugin`, `from widgets import
  ...`), pas en imports relatifs de package.
- La découverte est un simple glob sur le nom de fichier :
  `directory.glob(f"{PLUGIN_MODULE_PREFIX}*.py")` avec
  `PLUGIN_MODULE_PREFIX = "plugin_"`, dans
  `plugin_base.py::_discover_plugin_modules()`. Un seul niveau, pas de
  sous-dossiers.
- **`plugin_base.py` lui-même verrouille GTK3** :
  `gi.require_version("Gtk", "3.0")` en tête de fichier — alors qu'en le
  vérifiant, `Gtk` n'y sert que pour deux annotations de type
  (`-> Gtk.Widget`) et le fichier a déjà `from __future__ import
  annotations` (donc les annotations sont déjà des chaînes non évaluées à
  l'exécution). Ce verrou n'a donc **aucune nécessité d'exécution** —
  c'est une dette facile à lever, détail au §4.
- **Le cœur importe déjà du code spécifique SSH directement**, en violation
  de la neutralité de protocole visée : `gnome_connection_manager.py` ligne
  56 fait `from ssh_migrate_gcm import patch_edit_host_dialog` au niveau
  module. C'est exactement le problème que tu décris.
- Les fichiers SSH sont aujourd'hui éclatés à la racine du dépôt, hors de
  `plugins/`, alors qu'ils sont fonctionnellement 100 % SSH :
  `ssh_config_editor.py`, `ssh_config_parser.py`, `ssh_key_manager_dialog.py`,
  `ssh_migrate_gcm.py`, plus `plugins/plugin_ssh.py` qui les importe (en
  local, à l'intérieur des méthodes, pas en tête de fichier — déjà un
  découplage partiel, mais l'emplacement physique reste incohérent).
- Le mécanisme sain existe déjà ailleurs, à répliquer plutôt qu'inventer :
  `snmp_bulk_core.py`/`netmiko_bulk_core.py` (logique métier, zéro import
  GTK) + `plugin_snmp_push.py`/`plugin_netmiko_push.py` (UI) — cette
  séparation core/UI fonctionne déjà pour les *BatchPlugin*, il ne reste
  qu'à l'étendre au découpage en dossiers et aux *ConnectionPlugin*.
- Le futur plugin Redfish (GTK4, pas encore intégré) a déjà été conçu comme
  brique autonome pour la même raison : le verrou GTK3 empêche aujourd'hui
  de charger un plugin GTK4 dans l'app telle quelle.

## 2. Objectifs (reformulés pour validation)

1. Le cœur (`gnome_connection_manager.py`, `utils.py`, `widgets.py`, `models.py`)
   ne doit plus contenir **aucun** import spécifique à un protocole/plugin
   (SSH, RDP, SNMP...). Zéro exception, y compris les imports « juste pour
   patcher une dialogue ».
2. Le cœur découvre les plugins automatiquement (déjà le cas aujourd'hui,
   à conserver) et n'a besoin d'aucune modification pour ajouter, retirer ou
   faire évoluer un plugin.
3. Séparer, **à l'intérieur de chaque plugin**, la logique métier (« core »,
   zéro GTK) de la couche GTK — et permettre à terme GTK3 **et** GTK4 de
   coexister par plugin le temps de la transition.
4. Tous les fichiers SSH deviennent la propriété du plugin SSH — plus aucun
   fichier `ssh_*.py` à la racine du dépôt.

## 3. Structure de dossiers proposée

Un sous-dossier par plugin sous `plugins/`, chacun devenant son propre
mini-package :

```
plugins/
  plugin_base.py              # contrat ConnectionPlugin/BatchPlugin + registries
                               # (inchangé fonctionnellement, allégé du verrou GTK3 — §4)
  ssh/
    __init__.py                # point d'entrée unique : get_plugin() / get_batch_plugin()
    core.py                    # ssh_config_parser.py + ssh_migrate_gcm.py fusionnés
                                # (logique pure : parsing ~/.ssh/config, migration
                                # d'hôtes, aucun import gi)
    gtk3.py                    # plugin_ssh.py + ssh_config_editor.py +
                                # ssh_key_manager_dialog.py (UI GTK3 actuelle)
    gtk4.py                    # n'existe pas encore — futur écran GTK4,
                                # même contrat que gtk3.py
  rdp/
    __init__.py
    gtk3.py                     # plugin_rdp.py actuel (pas de logique séparable
                                 # identifiée pour l'instant : tout reste UI+pilotage
                                 # de gtk-frdp, pas de "core" pur à extraire)
  vnc/     __init__.py, gtk3.py  # idem, plugin_vnc.py
  spice/   __init__.py, gtk3.py  # idem, plugin_spice.py
  serial/  __init__.py, gtk3.py
  telnet/  __init__.py, gtk3.py
  ipmisol/ __init__.py, gtk3.py
  web/     __init__.py, gtk3.py
  local/   __init__.py, gtk3.py
  snmp_push/
    __init__.py
    core.py                    # snmp_bulk_core.py (décision séparée en cours,
                                # §2.2-bis de features.md, sur snmp_push_core.py)
    gtk3.py                    # plugin_snmp_push.py actuel
  netmiko_push/
    __init__.py
    core.py                    # netmiko_bulk_core.py
    gtk3.py                    # plugin_netmiko_push.py actuel
  import_csv/    __init__.py, gtk3.py   # plugin_import_csv.py (batch, léger)
  import_json/   __init__.py, gtk3.py
  export_csv/    __init__.py, gtk3.py
  export_json/   __init__.py, gtk3.py
  import_libvirt/   __init__.py, core.py, gtk3.py   # hypervisor_import_common.py
  import_ovirt/     __init__.py, core.py, gtk3.py   # partagé -> voir §5.3
  import_proxmox/   __init__.py, core.py, gtk3.py
  import_virtualbox/__init__.py, core.py, gtk3.py
```

Règle simple et uniforme : **si un plugin a de la logique testable sans
GTK, elle va dans `core.py` ; tout ce qui touche `Gtk`/`Vte`/widgets va dans
`gtk3.py` (puis `gtk4.py` le jour venu) ; `__init__.py` fait le lien.**
Un plugin sans logique séparable (RDP, VNC...) n'a simplement pas de
`core.py` — pas de fichier vide à créer pour la forme.

## 4. `plugin_base.py` : devenir agnostique GTK3/GTK4

Constat du §1 : le seul usage de `Gtk` dans `plugin_base.py` est en
annotation de type, déjà en chaîne différée grâce à `from __future__ import
annotations`. Proposition minimale, indépendante du reste (peut être faite
seule, en premier, sans attendre la réorganisation en dossiers) :

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gi.repository import Gtk  # uniquement pour le type-checker
```

Plus de `gi.require_version("Gtk", "3.0")` ni d'import `Gtk` réel dans
`plugin_base.py`. Le contrat (`ConnectionPlugin.build_tab() -> Gtk.Widget`)
ne change pas ; c'est uniquement le *verrou de version* imposé à tous les
plugins, y compris ceux qui n'ont pas encore besoin de charger `Gtk`, qui
disparaît. Chaque `__init__.py` de plugin devient alors seul responsable de
choisir *sa* version GTK.

## 5. Mécanisme de découverte (nouvelle version, principe)

```python
def _discover_plugin_modules(directory: Path, package: str | None, self_module_name: str):
    for plugin_dir in sorted(
        p for p in directory.iterdir() if p.is_dir() and (p / "__init__.py").exists()
    ):
        module_name = plugin_dir.name
        # même convention qu'aujourd'hui pour gnome_connection_manager.py :
        # le dossier du plugin est ajouté à sys.path pour que ses fichiers
        # internes (core.py/gtk3.py/gtk4.py) s'importent en flat
        # (`from core import ...`), sans transformer tout le projet en
        # imports relatifs de package.
        sys.path.insert(0, str(plugin_dir))
        try:
            module = importlib.import_module(module_name)
        except Exception as exc:
            yield module_name, module_name, None, str(exc)
            continue
        yield module_name, module_name, module, None
```

- Le cœur ne connaît toujours **aucun nom de protocole** : il scanne des
  dossiers ayant un `__init__.py`, point.
- `plugin_ssh.py` disparaît en tant que nom : c'est `ssh/__init__.py` qui
  expose `get_plugin()`, en import interne :
  ```python
  # plugins/ssh/__init__.py
  from gtk3 import get_plugin  # (ou gtk4, selon la version GTK active)
  ```
- Reste à trancher ensemble : **comment `__init__.py` sait-il si le process
  tourne en GTK3 ou GTK4 ?** Deux options simples :
  - (a) le cœur expose un flag unique avant l'autoload (ex.
    `plugin_base.GTK_MAJOR = 3` ou `4`, fixé une fois par
    `gnome_connection_manager.py`) ; chaque `__init__.py` lit ce flag.
  - (b) chaque `__init__.py` teste directement `Gtk.get_major_version()`
    (nécessite que `gi` soit déjà importé/initialisé par le cœur avant
    l'autoload, ce qui est déjà le cas aujourd'hui).
  Je penche pour (a), plus explicite et plus facile à forcer en test, mais
  c'est un point à valider avec toi avant d'écrire quoi que ce soit — ça
  touche directement la logique de démarrage de `gnome_connection_manager.py`.

## 6. Cas SSH en détail (le cas cité dans ta demande)

| Fichier actuel (racine du dépôt) | Devient |
|---|---|
| `ssh_config_parser.py` | `plugins/ssh/core.py` (fusionné avec ce qui suit) |
| `ssh_migrate_gcm.py` | fusionné dans `plugins/ssh/core.py` |
| `ssh_config_editor.py` | `plugins/ssh/gtk3.py` (fusionné avec `plugin_ssh.py`) ou fichier séparé `plugins/ssh/config_editor_gtk3.py` importé par `gtk3.py` — **détail à trancher selon la taille réelle une fois regardés ensemble**, les deux options respectent l'objectif |
| `ssh_key_manager_dialog.py` | idem, dans `gtk3.py` ou `plugins/ssh/key_manager_gtk3.py` |
| `plugins/plugin_ssh.py` | `plugins/ssh/gtk3.py` |

Conséquence directe sur le cœur : la ligne
`from ssh_migrate_gcm import patch_edit_host_dialog` dans
`gnome_connection_manager.py` doit disparaître. Il faut donc d'abord
comprendre **ce que `patch_edit_host_dialog` fait au cœur** (patch de
`Whost` ? Sur quel événement ?) pour décider comment cet appel doit être
exposé par le plugin sans que le cœur importe SSH directement — probablement
via une méthode du contrat `ConnectionPlugin` déjà existant (à vérifier s'il
y en a déjà une adaptée, sinon en ajouter une, ex. `on_host_dialog_built()`
optionnelle) plutôt qu'un import statique. **Je n'ai pas encore regardé le
contenu de `patch_edit_host_dialog` ni son point d'appel exact dans le
cœur — étape à faire avant de proposer le mécanisme de remplacement
précis, pour ne pas deviner.**

## 7. Ce qui ne change pas

- Le contrat `ConnectionPlugin`/`BatchPlugin` (méthodes abstraites,
  `build_tab`, `build_edit_page`, etc.) reste identique — seule
  l'organisation des fichiers change.
- `widgets.py`/`utils.py` restent au niveau central, utilisés par les
  plugins comme aujourd'hui (`from widgets import app_logger, vte_run...`)
  — ce sens de dépendance (plugin → utilitaires du cœur) n'est pas le
  problème signalé, seul le sens inverse (cœur → plugin) l'est.
- `SshConfigEditorDialog` reste, comme déjà décidé, un écran du plugin SSH
  pour la migration GTK4 des onglets — cette proposition ne fait que
  préciser **où le fichier vit physiquement**, la décision de fond (§
  `gcm-tab-migration`) ne change pas.

## 8. Ordre de mise en œuvre suggéré (si tu valides le principe)

1. **`plugin_base.py` : lever le verrou GTK3** (§4) — isolé, sans risque,
   testable seul, ne dépend de rien d'autre.
2. **Comprendre et documenter `patch_edit_host_dialog`** (§6) avant de
   toucher au cœur — pas de code tant que ce point n'est pas éclairci.
3. **Un seul plugin pilote pour valider le schéma de dossier** — je
   suggère SSH (le plus concerné par ta demande, et déjà partiellement
   découplé) plutôt que RDP/VNC (pas de `core.py` à extraire, donc moins
   représentatif du schéma complet).
4. Une fois le plugin pilote validé, généraliser aux autres — un plugin par
   session, comme pour la migration d'onglets, pas tout d'un coup.

## 9. Points à valider avec toi avant tout codage

> **Tranché le 2026-09-10** — voir `docs/gtk4-migration.md` §3.0-bis pour le
> détail des réponses. Résumé : dossier par plugin `core.py`+`gtk4.py`
> uniquement (pas de `gtk3.py`) ; pas de mécanisme de sélection GTK3/GTK4 à
> écrire puisqu'il n'y a plus de coexistence ; fichiers SSH fusionnés en 2
> (`core.py`+`gtk4.py`) ; SSH confirmé comme plugin pilote.

- Le principe général (dossier par plugin, `core.py`/`gtk3.py`/`gtk4.py`) —
  ou préfères-tu l'option B envisagée puis écartée (préfixe commun à plat,
  sans sous-dossiers) pour un refactor plus léger malgré un dossier
  `plugins/` plus chargé à terme ?
- Le mécanisme de sélection GTK3/GTK4 côté `__init__.py` (option a ou b,
  §5).
- Le découpage fichier par fichier pour SSH (fusion en 2 fichiers vs
  fichiers séparés conservés, §6).
- Confirmer qu'on démarre bien par SSH comme plugin pilote (§8.3).
