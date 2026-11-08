# SPDX-License-Identifier: GPL-3.0-or-later
"""Plugin SSH — dossier pilote de la migration GTK4 (découpage par plugin).

Contient aujourd'hui :

- ``core.py`` — logique métier (parsing ``~/.ssh/config``, migration/import
  gcm.conf, liste des clés de ``~/.ssh/``, formulaire de l'éditeur, gestion des clés,
  ``known_hosts`` et ``authorized_keys``, zéro GTK), fusion de l'ancien
  ``ssh_config_parser.py`` + ``ssh_migrate_gcm.py`` (session-26).
- ``gtk4.py`` — outils SSH GTK4 de gcm4 (#97), portage des fichiers
  historiques de la racine (``key_picker_dialog.py``,
  ``ssh_config_editor.py`` et ``ssh_key_manager_dialog.py``, supprimés
  avec le cœur GTK3 en #182/#180). Sans ``PLUGIN`` tant que la session
  SSH n'est pas portée (#396) : le dossier apparaît en erreur de
  chargement dans gcm4.

Ce fichier n'expose donc volontairement aucun ``get_plugin()``/
``get_batch_plugin()`` pour l'instant : ce sous-dossier n'est pas
(encore) un point d'entrée de plugin, juste l'emplacement du nouveau
``core.py``.
"""

from __future__ import annotations
