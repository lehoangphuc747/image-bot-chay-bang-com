"""Qt binding compatibility shim.

Inside Anki, the standard import path is :mod:`aqt.qt`, which re-exports
the active Qt binding (PyQt6 on modern Anki). Outside Anki - for
example, when running the pure-module test suite on a CI image without
PyQt installed - this module falls back through PyQt6, PyQt5, and
PySide6, and finally to a minimal in-process shim that implements just
enough of :class:`QObject` and :func:`pyqtSignal` for unit tests to
construct and exercise add-on classes that subclass :class:`QObject`.

The shim is *never* used inside Anki: the production runtime always
imports from ``aqt.qt`` because Anki ships PyQt6.

Only the symbols this add-on actually needs are re-exported here:

- :class:`QObject` - base class for :class:`WorkerBus` and other
  signal-emitting hubs.
- :func:`pyqtSignal` - signal descriptor used at class level.

If a future module needs additional Qt symbols (``QDialog``, ``QPixmap``
...), it should import them from ``aqt.qt`` directly. The narrow surface
here keeps the shim small and focused.
"""

from __future__ import annotations

from typing import Any

# --- Resolve the active Qt binding -----------------------------------
# Try Anki's wrapper first, then the raw bindings, then the shim.
QObject: type
pyqtSignal: Any

_BINDING: str

try:  # Anki runtime
    from aqt.qt import QObject as _AqtQObject  # type: ignore[attr-defined]
    from aqt.qt import pyqtSignal as _AqtPyqtSignal  # type: ignore[attr-defined]

    QObject = _AqtQObject
    pyqtSignal = _AqtPyqtSignal
    _BINDING = "aqt.qt"
except Exception:  # pragma: no cover - exercised only outside Anki
    try:
        from PyQt6.QtCore import QObject as _PyQt6QObject  # type: ignore
        from PyQt6.QtCore import pyqtSignal as _PyQt6PyqtSignal  # type: ignore

        QObject = _PyQt6QObject
        pyqtSignal = _PyQt6PyqtSignal
        _BINDING = "PyQt6"
    except Exception:
        try:
            from PyQt5.QtCore import QObject as _PyQt5QObject  # type: ignore
            from PyQt5.QtCore import pyqtSignal as _PyQt5PyqtSignal  # type: ignore

            QObject = _PyQt5QObject
            pyqtSignal = _PyQt5PyqtSignal
            _BINDING = "PyQt5"
        except Exception:
            try:
                from PySide6.QtCore import QObject as _PySide6QObject  # type: ignore
                from PySide6.QtCore import Signal as _PySide6Signal  # type: ignore

                QObject = _PySide6QObject
                pyqtSignal = _PySide6Signal
                _BINDING = "PySide6"
            except Exception:
                # ----------------------------------------------------
                # Minimal in-process shim. Used only when no Qt binding
                # is available (e.g. running pure-module tests on a CI
                # image without Qt). Anki itself always has PyQt6.
                # ----------------------------------------------------
                from threading import RLock as _RLock
                from typing import Callable, List, Tuple

                _BINDING = "shim"

                class _BoundSignal:
                    """Per-instance bound signal: ``connect`` and ``emit``."""

                    __slots__ = ("_slots", "_lock")

                    def __init__(self) -> None:
                        self._slots: List[Callable[..., Any]] = []
                        self._lock = _RLock()

                    def connect(self, slot: Callable[..., Any]) -> None:
                        if not callable(slot):
                            raise TypeError("slot must be callable")
                        with self._lock:
                            self._slots.append(slot)

                    def disconnect(
                        self, slot: Callable[..., Any] | None = None
                    ) -> None:
                        with self._lock:
                            if slot is None:
                                self._slots.clear()
                            else:
                                self._slots = [
                                    s for s in self._slots if s is not slot
                                ]

                    def emit(self, *args: Any) -> None:
                        # Snapshot under the lock so a slot disconnecting
                        # itself during emission cannot mutate the list
                        # we are iterating.
                        with self._lock:
                            slots = list(self._slots)
                        for slot in slots:
                            slot(*args)

                class _SignalDescriptor:
                    """Class-level descriptor returning a per-instance signal."""

                    __slots__ = ("_types", "_attr")

                    def __init__(self, *types: Any) -> None:
                        self._types: Tuple[Any, ...] = types
                        self._attr: str = ""

                    def __set_name__(
                        self, owner: type, name: str
                    ) -> None:
                        self._attr = f"_signal__{name}"

                    def __get__(
                        self,
                        instance: Any,
                        owner: type | None = None,
                    ) -> Any:
                        if instance is None:
                            return self
                        bound = instance.__dict__.get(self._attr)
                        if bound is None:
                            bound = _BoundSignal()
                            instance.__dict__[self._attr] = bound
                        return bound

                def _shim_pyqt_signal(*types: Any) -> _SignalDescriptor:
                    return _SignalDescriptor(*types)

                class _ShimQObject:
                    """Minimal :class:`QObject` stand-in.

                    Accepts an optional ``parent`` argument so subclass
                    constructors can call ``super().__init__(parent)``
                    without changes.
                    """

                    def __init__(self, parent: Any = None) -> None:
                        self._parent = parent

                    def parent(self) -> Any:
                        return self._parent

                QObject = _ShimQObject  # type: ignore[assignment,misc]
                pyqtSignal = _shim_pyqt_signal  # type: ignore[assignment]


__all__ = ["QObject", "pyqtSignal"]
