# Mot de passe maître (gcm4)

Les mots de passe des hôtes sont chiffrés dans `gcm4.conf` avec la clé du
fichier `.gcm4.key` (droits `0600`). Sans mot de passe maître, cette clé est
écrite en clair : quiconque copie le dossier de configuration (sauvegarde,
accès au compte) peut déchiffrer les mots de passe. Le mot de passe maître
(issue #98) chiffre cette clé ; il est demandé à chaque démarrage.

La protection est **facultative** et désactivée par défaut. Une configuration
existante continue de fonctionner sans changement.

## Activer

**Paramètres > Sécurité > Mot de passe maître > Activer…** : saisir deux fois
le nouveau mot de passe. gcm4 affiche alors un **code de recouvrement**
(24 caractères, groupés par 4) **une seule fois** : le noter ou l'imprimer et
le garder en lieu sûr. Il n'est ni stocké ni journalisé.

## Démarrage

Quand la clé est protégée, gcm4 affiche la page « Mots de passe verrouillés »
à la place de la liste des hôtes. La configuration n'est chargée qu'après le
déverrouillage. Un mauvais mot de passe est signalé et la saisie reste
possible.

## Mot de passe oublié

Sur la page de verrouillage, **Mot de passe oublié ?** demande le code de
recouvrement puis un nouveau mot de passe maître (deux fois). Le code est
accepté sans tenir compte des majuscules, espaces et tirets. Après la
récupération, un **nouveau code** est affiché : l'ancien ne fonctionne plus.

Sans mot de passe ni code, les mots de passe enregistrés sont perdus : il n'y
a pas d'autre moyen de déchiffrer la clé.

## Gérer

Dans **Paramètres > Sécurité** :

| Bouton | Effet |
|---|---|
| Changer… | demande le mot de passe actuel et le nouveau ; le code de recouvrement reste valable |
| Nouveau code de recouvrement… | demande le mot de passe actuel, affiche un nouveau code ; l'ancien ne fonctionne plus. Ajoute aussi un code à une clé protégée sans code (format `GCM4MPW1`) |
| Désactiver… | demande le mot de passe actuel et réécrit la clé en clair |

## Détails techniques

- Format `GCM4MPW2:<enveloppe mot de passe>:<enveloppe code>` : la même clé
  est chiffrée deux fois, par une clé dérivée du mot de passe et par une clé
  dérivée du code (PBKDF2-HMAC-SHA256, `master_password_core.py`). Le format
  `GCM4MPW1` (mot de passe seul) reste lu.
- L'écriture de `.gcm4.key` est atomique (fichier temporaire `0600` puis
  remplacement) : une coupure ne laisse pas une clé à moitié écrite.
- L'application GTK3 (`gcm`) utilise un autre fichier (`.gcm.key`) et n'a pas
  de mot de passe maître ; si elle trouve une clé protégée, elle refuse de la
  lire au lieu de l'utiliser comme une clé en clair.
