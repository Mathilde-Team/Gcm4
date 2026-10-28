# SPDX-License-Identifier: GPL-3.0-or-later
import sys
import types


class _NoOp:
    """Instance qui accepte n'importe quel appel/attribut sans erreur."""

    def __call__(self, *args, **kwargs):
        return _NoOp()

    def __getattr__(self, name):
        return _NoOp()

    def __bool__(self):
        return True

    def __eq__(self, other):
        return isinstance(other, _NoOp)

    def __hash__(self):
        return id(self)


class _ClassConstant(str):
    """Sentinelle renvoyée pour un attribut de CLASSE GTK non défini par
    le stub. Deux usages coexistent dans du vrai code GTK et doivent tous
    les deux "juste marcher" sans lever d'exception :

    - constante d'énumération, ex. `Gtk.ContentFit.CONTAIN` -- comparée
      par valeur (`==`), jamais appelée. Une `str` convient : stable,
      hashable, comparable.
    - "constructeur" style GObject-introspection, ex.
      `Gtk.EventControllerScroll.new(flags)` -- appelé comme une
      fonction. D'où `__call__` ci-dessous, qui renvoie un `_NoOp()`
      (même comportement qu'instancier directement la classe widget)."""

    def __call__(self, *args, **kwargs):
        return _NoOp()


class _WidgetMeta(type):
    """Métaclasse pour les classes produites par `_make_widget_class` :
    intercepte l'accès à un attribut de CLASSE non défini (ex.
    `Gtk.ContentFit.CONTAIN`, `Gtk.EventControllerScroll.new`) et renvoie
    une `_ClassConstant`, comme `_Namespace` le fait déjà pour
    `GObject.SignalFlags`.

    Sans ça, un type GTK utilisé à la fois comme classe de base (ex.
    `class VncDisplay(Gtk.Picture)`) ET comme espace de constantes/
    "constructeur" GI plante sur le second usage : `__getattr__` défini
    seulement sur les INSTANCES (cf. `_getattr` ci-dessous) n'intercepte
    pas l'accès `Classe.ATTRIBUT` -- il faut un `__getattr__` côté
    métaclasse pour ça. Repéré en enrichissant ce stub pour instancier un
    vrai `VncDisplay` dans les tests (voir `conftest.make_test_display`
    et `tests/test_display_transform.py`)."""

    def __getattr__(cls, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return _ClassConstant(name)


def _make_widget_class(name):
    """Une vraie classe (utilisable comme base dans class X(Base)) dont
    les instances acceptent n'importe quel appel de méthode GTK, et dont
    les attributs de classe non définis renvoient une sentinelle stable
    (cf. `_WidgetMeta`)."""

    def _getattr(self, attr):
        return _NoOp()

    def _init(self, *args, **kwargs):
        pass

    return _WidgetMeta(name, (object,), {"__getattr__": _getattr, "__init__": _init})


class _Namespace:
    """Espace de constantes façon Gtk.Orientation.VERTICAL : toute
    attribut accédé renvoie une valeur sentinelle stable."""

    def __getattr__(self, name):
        return name  # une string suffit comme sentinelle stable/comparable


class _FakeModule(types.ModuleType):
    def __init__(self, name):
        super().__init__(name)
        self._classes = {}

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        if name not in self._classes:
            self._classes[name] = _make_widget_class(name)
        return self._classes[name]


Gtk = _FakeModule("gi.repository.Gtk")
Gdk = _FakeModule("gi.repository.Gdk")
GLib = _FakeModule("gi.repository.GLib")
Pango = _FakeModule("gi.repository.Pango")


class _GObjectModule(types.ModuleType):
    def __getattr__(self, name):
        if name == "SignalFlags":
            return _Namespace()
        if name == "Object":
            return _make_widget_class("Object")
        if name == "Value":
            return _NoOp
        return _NoOp()


GObject = _GObjectModule("gi.repository.GObject")

sys.modules["gi.repository.Gtk"] = Gtk
sys.modules["gi.repository.Gdk"] = Gdk
sys.modules["gi.repository.GLib"] = GLib
sys.modules["gi.repository.Pango"] = Pango
sys.modules["gi.repository.GObject"] = GObject


class _EventsModule(types.ModuleType):
    def __getattr__(self, name):
        return _NoOp


Events = _EventsModule("gi.events")
sys.modules["gi.events"] = Events
