"""``QObject``, ``Signal`` and ``Slot``, with or without Qt.

The desktop app needs the real ones: QWebChannel only exposes Qt slots and
signals to the page. The web server does not, and a Linux server should not
need 500 MB of Qt - with its system graphics libraries - to answer HTTP. So the
bridge imports these three names from here instead of from PySide6.

Qt is used when it can be imported, unless ``MTGFISH_NO_QT`` is set. The web
server sets it before importing the bridge, so it behaves the same on a machine
that happens to have PySide6 installed as on one that does not.

The fallback is deliberately tiny. ``Slot`` marks a method so the web server
can tell what the page may call, and ``Signal`` calls its listeners on the
emitting thread - which is what the server wants, since it forwards each emit
to a queue and never touches the bridge from the listener.
"""

from __future__ import annotations

import os
import threading

HAS_QT = False

if not os.environ.get("MTGFISH_NO_QT"):
    try:
        from PySide6.QtCore import QObject, Signal, Slot  # noqa: F401

        HAS_QT = True
    except ImportError:  # pragma: no cover - depends on the machine
        pass

if not HAS_QT:

    class QObject:  # type: ignore[no-redef]
        """Stands in for ``QObject``; the bridge needs nothing from it."""

        def __init__(self, *args, **kwargs) -> None:
            pass

    class _BoundSignal:
        def __init__(self) -> None:
            self._listeners: list = []
            self._lock = threading.Lock()

        def connect(self, listener, *_ignored) -> None:
            with self._lock:
                self._listeners.append(listener)

        def disconnect(self, listener=None) -> None:
            with self._lock:
                if listener is None:
                    self._listeners.clear()
                elif listener in self._listeners:
                    self._listeners.remove(listener)

        def emit(self, *args) -> None:
            with self._lock:
                listeners = list(self._listeners)
            for listener in listeners:
                listener(*args)

    class Signal:  # type: ignore[no-redef]
        """A per-instance signal, created on first access."""

        def __init__(self, *types) -> None:
            self.types = types
            self.name = ""

        def __set_name__(self, owner, name) -> None:
            self.name = name

        def __get__(self, instance, owner=None):
            if instance is None:
                return self
            bound = instance.__dict__.get(self.name)
            if bound is None:
                bound = instance.__dict__.setdefault(self.name, _BoundSignal())
            return bound

    def Slot(*types, result=None):  # type: ignore[no-redef]  # noqa: N802
        """Mark a method as callable from the page."""

        def mark(fn):
            fn._mtgfish_slot = types
            return fn

        return mark


def is_slot(owner: type, name: str) -> bool:
    """Whether ``owner.name`` is a slot the page is allowed to call.

    Answered from Qt's own meta-object when Qt is in use, so this agrees with
    exactly what QWebChannel would expose.
    """
    if not HAS_QT:
        return hasattr(getattr(owner, name, None), "_mtgfish_slot")
    from PySide6.QtCore import QMetaMethod

    meta = owner.staticMetaObject
    for index in range(meta.methodOffset(), meta.methodCount()):
        method = meta.method(index)
        if method.methodType() == QMetaMethod.MethodType.Slot and bytes(
            method.name()
        ).decode() == name:
            return True
    return False


def signal_names(owner: type) -> list[str]:
    """Every signal declared on ``owner``, in declaration order."""
    names = []
    for klass in reversed(owner.__mro__):
        for name, value in vars(klass).items():
            if isinstance(value, Signal) and name not in names:
                names.append(name)
    return names
