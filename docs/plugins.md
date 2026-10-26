# Écrire un plugin de connexion gcm4 (contrat v1)

La base gcm4 ne connaît aucun protocole. Ouvrir une connexion SSH, RDP, VNC…
est le travail d'un **plugin**. Ce document suffit pour en écrire un. La
version typée du contrat est `src/gcm4/core/plugins.py`.

- Version du contrat : **`API_VERSION = 1`**.
- Sans aucun plugin, gcm4 démarre, affiche et modifie les hôtes. Les options
  des protocoles inconnus sont conservées telles quelles.

## Exemple complet

```python
# mon_paquet/gcm4_demo.py
import gi

gi.require_version("Gtk", "4.0")  # déjà fixé par gcm4, sans effet ; documente la version
from gi.repository import Gtk

from gcm4.core.plugins import ConnectionPlugin
from gcm4.core.validation import FieldError


class DemoSession:
    def __init__(self, host):
        self.widget = Gtk.Label(label=f"Connecté à {host.host}")

    def close(self):
        pass  # fermer la socket, tuer le processus…


class DemoPlugin(ConnectionPlugin):
    name = "demo"  # identifiant stable, minuscules
    version = "1.0.0"
    protocols = ("demo",)  # valeurs de Host.protocol gérées

    def default_port(self, protocol):
        return 4242

    def default_options(self, protocol):
        return {"mode": "rapide"}

    def validate_options(self, protocol, options):
        if options.get("mode") not in ("rapide", "lent"):
            return [FieldError("mode", "Mode inconnu")]
        return []

    def build_host_options_widget(self, host, options, changed):
        entree = Gtk.Entry(text=options.get("mode", ""))

        def on_changed(e):
            options["mode"] = e.get_text()
            changed()

        entree.connect("changed", on_changed)
        return entree

    def open(self, host):
        self.context.log.info("ouverture de {}", host.path)
        return DemoSession(host)
```

## Installation et découverte

gcm4 cherche les plugins à deux endroits, dans cet ordre, puis les trie
par `name` :

1. **paquet Python installé** : point d'entrée du groupe `gcm4.plugins`,
   qui désigne la classe du plugin.

   ```toml
   # pyproject.toml du plugin
   [project.entry-points."gcm4.plugins"]
   demo = "mon_paquet.gcm4_demo:DemoPlugin"
   ```

2. **mode source** : dossier `plugins/<nom>/` du dépôt gcm4, avec un
   `gtk4.py` qui expose la classe dans une variable `PLUGIN`. Le dossier est
   importé comme un paquet (`gcm4_plugin_<nom>`, son `__init__.py` est
   exécuté s'il existe) : `gtk4.py` peut importer ses voisins, par exemple
   la logique sans GTK de `core.py` avec `from . import core`. Si l'import
   échoue, rien du dossier ne reste chargé.

Deux plugins de même `name` : le premier trouvé est gardé, l'autre est
signalé en erreur.

## Chargement et erreurs

Un plugin est **refusé**, avec une raison affichée dans Préférences >
Extensions et un `warning` dans le journal, si :

- sa classe n'hérite pas de `ConnectionPlugin` ;
- `api_version` ne vaut pas `API_VERSION` (1) ;
- `name` est vide, contient une majuscule ou une espace ;
- `protocols` est vide ;
- `build_host_options_widget` ou `open` n'est pas implémentée ;
- son import ou son `__init__` lève une exception.

Un plugin refusé n'empêche ni le démarrage ni les autres plugins. Les hôtes
de ses protocoles restent intacts.

## Le contexte

Le plugin ne lit **aucun global** de l'application. Tout passe par
`self.context`, reçu à la construction (`__init__(self, context)` : si vous
surchargez `__init__`, appelez `super().__init__(context)`).

| Membre | Rôle |
|---|---|
| `context.log` | journal loguru lié au plugin (`log.debug(...)`, `log.warning(...)`) |
| `context.config()` | configuration du plugin en lecture seule : clés de la section `[plugin <name>]` de `gcm4.conf` |
| `context.ask_password(host, done)` | demande un mot de passe ; `done(texte)` ou `done(None)` si l'utilisateur annule ; asynchrone |
| `context.notify(message)` | message bref à l'utilisateur (toast) |
| `context.open_tab(title, widget)` | onglet supplémentaire (la connexion elle-même passe par `open`) |

## Méthodes

### Identité

| Membre | Obligatoire | Description |
|---|---|---|
| `name` | oui | identifiant stable, minuscules, sans espace |
| `version` | non | affichée dans Préférences > Extensions |
| `protocols` | oui | tuple des protocoles gérés (`Host.protocol`) |
| `default_port(protocol)` | non | port proposé quand le champ est vide, ou quand il contient encore le port par défaut de l'ancien protocole ; `None` : aucun |

### Hôte

Les champs **communs** (nom, groupe, adresse, port, utilisateur, mot de
passe, description, couleurs, protocole, espace de travail, fermeture de la
console, commandes avant et après, paramètres supplémentaires) sont édités
par la base. Inventaire : `docs/architecture.md` §2.10.

Tout le reste est une **option** du protocole. C'est un dictionnaire
`dict[str, str]` : clés INI, valeurs texte, rangé dans
`host.protocol_options[protocole]` et écrit tel quel dans la section
`[host N]`.

| Méthode | Obligatoire | Description |
|---|---|---|
| `default_options(protocol)` | non | options d'un nouvel hôte |
| `validate_options(protocol, options)` | non | liste de `FieldError(champ=clé, message=texte traduit)` ; « Enregistrer » reste inactif tant qu'elle n'est pas vide |
| `build_host_options_widget(host, options, changed)` | **oui** | zone d'options du dialogue d'hôte (voir ci-dessous) |

`build_host_options_widget` reçoit une **copie** des options. Le plugin la
modifie sur place et appelle `changed()` après chaque modification. La base
revalide, met à jour le bouton Enregistrer et la confirmation de fermeture.
Elle n'écrit la copie dans l'hôte que si l'utilisateur enregistre. `host`
est en lecture seule.

### Connexion

| Méthode | Obligatoire | Description |
|---|---|---|
| `open(host)` | **oui** | ouvre la connexion et rend une **session** : objet avec `widget` (`Gtk.Widget`, contenu de l'onglet) et `close()` |

- Une session par onglet. `close()` est appelée une fois, quand l'onglet se
  ferme.
- Lever une exception dans `open` signale un échec. La base l'affiche et le
  journalise, sans ouvrir d'onglet.
- Les commandes avant la connexion et après la déconnexion
  (`host.pre_connect_command`, `host.post_connect_command`) sont exécutées
  par le plugin, s'il les prend en charge.

### Facultatif

| Méthode | Description |
|---|---|
| `build_preferences_page()` | page du plugin dans les préférences, après la page Extensions : un `Adw.PreferencesPage` est ajouté tel quel (titre par défaut : `name`), tout autre widget est placé dans une page au nom du plugin ; `None` : aucune. Une exception est journalisée et affichée dans la ligne du plugin (page Extensions), sans fermer les préférences |
| `context_menu_items(host)` | liste de `MenuItem(label, activate)` ajoutée au menu contextuel d'un hôte du plugin |

## Plugins terminal : modules communs

Les plugins qui ouvrent un terminal (telnet, local, puis serial, ipmisol et
ssh) partagent deux modules de la base (#295), pour ne pas recopier la même
session dans chacun :

| Module | GTK | Contenu |
|---|---|---|
| `gcm4.core.terminal` | non | clés d'options `commands`, `backspace-key`, `delete-key`, `term` (mêmes clés INI que GTK3) et `encoding` (#85, `ENCODAGES`, `encodage`, `encoder`) ; `Lancement(argv, mot_de_passe)` ; `environnement`, `liaison`, `lire_commandes`, `ecrire_commandes`, `etapes(commandes, debut_ms)` |
| `gcm4.ui.terminal` | VTE 3.91 | `SessionTerminal(host, protocole, lancement, log, debut_commandes_ms)` : `Vte.Terminal` dans un défilement, lancement asynchrone, mot de passe éventuel sur le descripteur 3, commandes programmées, « Session ended » en fin de programme, `close()` qui annule les envois et envoie SIGHUP ; `zone_options(options, changed)` : zone d'options de l'hôte ; `PALETTE`, `rgba` |

Exemple, le plugin local (`plugins/local/gtk4.py`) :

```python
class LocalSession(SessionTerminal):
    def __init__(self, host, lancement, log):
        super().__init__(host, "local", lancement, log, 700)
```

`gcm4.ui.terminal` exige VTE GTK4 (`gi.require_version("Vte", "3.91")`).
Seuls les plugins l'importent, jamais la base (vérifié par
`tests/test_gcm4_paquet.py`) : là où VTE GTK4 manque (Rocky Linux 9 et 10,
#223), gcm4 démarre et le plugin est signalé en erreur dans Préférences >
Extensions.

## Règles

- **Mot de passe** : ne jamais écrire `host.password` dans le journal. Il est
  exclu de `repr(host)`.
- **Bloquant** : rien de long dans la boucle GTK. Connexion réseau dans un fil
  ou en asynchrone, résultat rendu via `GLib.idle_add`.
- **Traductions** : textes affichés avec `gettext`, dans le domaine du plugin.
- **Compatibilité** : GTK ≥ 4.8 et libadwaita ≥ 1.2, comme la base.
