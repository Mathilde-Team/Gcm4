"""Partie métier du plugin serial (sans GTK) — portage #179.

Comportement repris de ``plugins/plugin_serial.py`` (GTK3) : détection de
l'outil série disponible (picocom > minicom > screen, l'outil choisi par
l'hôte prioritaire), construction de la commande selon l'outil et les
paramètres série (débit, bits de données, parité, bits de stop, contrôle
de flux), templates constructeur. Les paramètres sont lus depuis la zone
d'options du protocole (``protocol_options["serial"]``), avec repli sur
``host.port`` pour le débit (héritage GTK3 : le champ Port servait de
débit).
"""

from __future__ import annotations

import shlex
import shutil
from collections.abc import Mapping

from loguru import logger

#: Outils série détectés dans cet ordre (picocom prioritaire, comme GTK3).
OUTILS = ("picocom", "minicom", "screen")

#: Templates constructeur : nom → (débit, flux, parité, bits de données,
#: bits de stop), comme le dict GTK3 (le pré-remplissage cassé du combo
#: glade — ids incohérents — n'est pas repris, voir la docstring GTK3).
TEMPLATES: dict[str, tuple[str, str, str, str, str]] = {
    "Cisco IOS / IOS-XE / NX-OS": ("9600", "n", "n", "8", "1"),
    "HP Comware (H3C)": ("9600", "n", "n", "8", "1"),
    "Aruba AOS-S / AOS-CX": ("9600", "n", "n", "8", "1"),
    "Juniper JunOS": ("9600", "n", "n", "8", "1"),
    "Fortinet FortiOS": ("9600", "n", "n", "8", "1"),
    "Palo Alto PAN-OS": ("9600", "n", "n", "8", "1"),
    "F5 TMOS": ("19200", "n", "n", "8", "1"),
    "Linux / Raspberry Pi": ("115200", "n", "n", "8", "1"),
    "Arduino / ESP32": ("115200", "n", "n", "8", "1"),
    "RS-485 Modbus RTU": ("9600", "n", "n", "8", "1"),
    "Libre (manuel)": ("9600", "n", "n", "8", "1"),
}

#: Valeurs proposées par la zone d'options (libellés traduits dans gtk4).
DEBITS = (
    "300",
    "1200",
    "2400",
    "4800",
    "9600",
    "19200",
    "38400",
    "57600",
    "115200",
    "230400",
    "460800",
    "921600",
)
BITS_DONNEES = ("5", "6", "7", "8")
PARITES = ("n", "e", "o")
BITS_STOP = ("1", "2")
FLUX = ("n", "x", "h", "d")

#: Clés de la zone d'options et défauts (9600 8N1, comme GTK3).
DEFAUTS = {
    "template": "",
    "baud": "9600",
    "databits": "8",
    "parity": "n",
    "stopbits": "1",
    "flow": "n",
    "tool": "",
}


def outil(environ: Mapping[str, str], prefere: str = "") -> str | None:
    """Chemin de l'outil série : celui choisi s'il est installé, sinon le premier trouvé.

    Args:
        environ: environnement (``os.environ``) pour le ``PATH``.
        prefere: outil choisi pour l'hôte (clé ``tool``, vide : aucun).

    Returns:
        Chemin de l'exécutable, ou ``None`` si aucun des trois n'est
        installé.
    """
    logger.debug("outil: entrée (préféré {!r})", prefere)
    if prefere in OUTILS:
        chemin = shutil.which(prefere, path=environ.get("PATH"))
        if chemin is not None:
            logger.debug("outil: {} préféré", chemin)
            return chemin
    for nom in OUTILS:
        chemin = shutil.which(nom, path=environ.get("PATH"))
        if chemin is not None:
            logger.debug("outil: {}", chemin)
            return chemin
    logger.debug("outil: aucun")
    return None


def parametres(options: Mapping[str, str], port_hote: int | None = None) -> dict[str, str]:
    """Options série normalisées : défauts, repli du débit sur le port de l'hôte.

    Args:
        options: options du protocole (``protocol_options["serial"]``).
        port_hote: champ Port de l'hôte — en GTK3 il servait de débit
            (héritage), il reste lu si ``baud`` n'est pas dans les options.
    """
    logger.debug("parametres: entrée")
    resultat = dict(DEFAUTS)
    resultat.update({cle: valeur for cle, valeur in options.items() if cle in DEFAUTS})
    if not options.get("baud") and port_hote:
        resultat["baud"] = str(port_hote)
    if resultat["baud"] not in DEBITS:
        resultat["baud"] = DEFAUTS["baud"]
    if resultat["databits"] not in BITS_DONNEES:
        resultat["databits"] = DEFAUTS["databits"]
    if resultat["parity"] not in PARITES:
        resultat["parity"] = DEFAUTS["parity"]
    if resultat["stopbits"] not in BITS_STOP:
        resultat["stopbits"] = DEFAUTS["stopbits"]
    if resultat["flow"] not in FLUX:
        resultat["flow"] = DEFAUTS["flow"]
    if resultat["template"] not in TEMPLATES and resultat["template"]:
        resultat["template"] = ""
    logger.debug("parametres: fait")
    return resultat


def parametres_template(nom: str) -> tuple[str, str, str, str, str] | None:
    """Paramètres du template constructeur, ou ``None`` s'il est inconnu."""
    logger.debug("parametres_template: {!r}", nom)
    return TEMPLATES.get(nom)


def commande(
    chemin: str,
    appareil: str,
    parametres: Mapping[str, str],
    extra: str = "",
) -> list[str]:
    """Arguments de l'outil série selon ses conventions (comme ``_build_cmd`` GTK3).

    Args:
        chemin: chemin de l'exécutable (picocom, minicom ou screen — le
            nom de fichier décide, comme GTK3).
        appareil: chemin du port série (ex. ``/dev/ttyUSB0``).
        parametres: options normalisées (:func:`parametres`).
        extra: options libres supplémentaires, découpées par ``shlex``.

    Raises:
        ValueError: ``extra`` mal formé (``shlex.split``).
    """
    logger.debug("commande: entrée")
    debit = parametres["baud"]
    bits_donnees = parametres["databits"]
    parite = parametres["parity"]
    bits_stop = parametres["stopbits"]
    flux = parametres["flow"]
    nom = chemin.rsplit("/", 1)[-1]
    if nom == "picocom":
        argv = [
            chemin,
            "--baud",
            debit,
            "--flow",
            flux,
            "--parity",
            parite,
            "--databits",
            bits_donnees,
            "--stopbits",
            bits_stop,
        ]
        argv += shlex.split(extra)
        argv.append(appareil or "/dev/ttyUSB0")
    elif nom == "minicom":
        argv = [chemin, "-b", debit, "-D", appareil or "/dev/ttyUSB0"]
        argv += ["--databits", bits_donnees, "--stopbits", bits_stop]
        if flux == "x":
            argv += ["-f", "on"]
        elif flux == "h":
            argv += ["--rtscts"]
        argv += shlex.split(extra)
    else:  # screen : appareil débit 8n1 (bits, parité, stop collés)
        argv = [chemin, appareil or "/dev/ttyUSB0", debit, f"{bits_donnees}{parite}{bits_stop}"]
        argv += shlex.split(extra)
    logger.debug("commande: {} argument(s)", len(argv))
    return argv
