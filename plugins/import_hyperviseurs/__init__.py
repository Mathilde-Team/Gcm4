# SPDX-License-Identifier: GPL-3.0-or-later
"""Aides partagées des imports d'hyperviseurs (libvirt, Proxmox, VirtualBox, oVirt).

Ce dossier n'est PAS un plugin : sans ``gtk4.py`` de sa propre main, la
découverte (``gcm4.core.registre.candidats_dossier``) ne le charge jamais.
Il fournit aux plugins d'import d'hyperviseurs :

- :mod:`core` : le socle bas niveau (SSH par clé, commandes distantes,
  scan nmap, sonde de port), déjà utilisé par les versions GTK3 ;
- :mod:`ui` : l'interface GTK4 commune (suivi de scan, prévisualisation à
  cases à cocher, stratégie de conflit, import via le contexte).

Chaque plugin le charge comme le paquet d'identité stable ``gcm4_hv``
(même mécanisme que ``gcm4.core.registre._importer_fichier`` pour les
``gcm4_plugin_<nom>`` : identité unique dans ``sys.modules``, donc
``isinstance`` et comparaisons de modules restent fiables).
"""
