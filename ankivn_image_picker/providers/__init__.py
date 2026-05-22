"""Provider package and registry for the AnkiVN Smart Image Picker.

The registry is the only mutable global in the add-on; everything else
threads providers through arguments. Concrete provider modules
(:mod:`ankivn_image_picker.providers.unsplash`,
:mod:`ankivn_image_picker.providers.pixabay`, ...) call
:meth:`ProviderRegistry.register` at import time so that
:func:`ProviderRegistry.create` can resolve a configured provider id
into an :class:`ImageProvider` instance when the picker opens.

Two registry surfaces are exposed:

* The class :class:`ProviderRegistry` provides ``register`` and
  ``create`` as classmethods backed by a single module-level mapping.
  This matches the design document's interface
  (``ProviderRegistry.register(id, factory)`` and
  ``ProviderRegistry.create(id)``).
* :func:`available_ids` returns the sorted list of registered ids,
  which the config validator uses to drop unknown provider entries
  (Req 1.4 + 1.11).

The registry deliberately does **not** import any concrete provider
module. Concrete modules opt in by being imported elsewhere (typically
by the add-on's :mod:`__init__` entry point), so importing the
registry module does not fan out into network-aware code paths and the
test suite can register fakes without side-effects.
"""

from __future__ import annotations

from threading import RLock
from typing import Callable, Dict, List

from .base import ImageProvider, ImageResult

# A factory is a zero-argument callable that returns a fresh
# ``ImageProvider``. We store factories rather than instances so a
# provider can hold per-search state (e.g. cached HTTP session adapters)
# without that state leaking between picker opens.
ProviderFactory = Callable[[], ImageProvider]


class ProviderRegistry:
    """Process-wide registry of provider id -> factory.

    The class exposes ``register`` and ``create`` as classmethods
    operating on the shared private mapping ``_factories``. A
    re-entrant lock guards the mapping because providers are typically
    registered at import time on the main thread, but ``create`` may
    be called on a Qt worker thread when the orchestrator builds the
    provider list for a search.
    """

    _factories: Dict[str, ProviderFactory] = {}
    _lock: RLock = RLock()

    @classmethod
    def register(cls, id: str, factory: ProviderFactory) -> None:
        """Register a factory under a provider id.

        Parameters
        ----------
        id:
            The lowercase identifier the user puts in
            ``config["providers"]``. Must be a non-empty string.
        factory:
            A zero-argument callable returning a new
            :class:`ImageProvider`. The callable is invoked each time
            :meth:`create` is called, so it must be cheap.

        Raises
        ------
        TypeError
            If ``id`` is not a non-empty string or ``factory`` is not
            callable.
        ValueError
            If a different factory is already registered under ``id``.
            Re-registering the *same* factory under the same id is a
            no-op so reloading a provider module during development
            does not raise.
        """

        if not isinstance(id, str) or not id:
            raise TypeError("provider id must be a non-empty string")
        if not callable(factory):
            raise TypeError("provider factory must be callable")

        with cls._lock:
            existing = cls._factories.get(id)
            if existing is not None and existing is not factory:
                raise ValueError(
                    f"provider id {id!r} is already registered to a "
                    "different factory"
                )
            cls._factories[id] = factory

    @classmethod
    def unregister(cls, id: str) -> None:
        """Remove a provider id from the registry.

        Tests use this to roll back fakes between cases. Quietly
        succeeds if ``id`` is not registered so cleanup hooks can be
        unconditional.
        """

        with cls._lock:
            cls._factories.pop(id, None)

    @classmethod
    def create(cls, id: str) -> ImageProvider:
        """Look up a provider id and return a fresh provider instance.

        Parameters
        ----------
        id:
            The provider id to resolve.

        Returns
        -------
        ImageProvider
            The instance returned by the registered factory.

        Raises
        ------
        KeyError
            If ``id`` is not registered. Callers in the config-loading
            path (Req 1.11) catch this to fall back to defaults; the
            orchestrator never sees an unregistered id because the
            config validator drops them first.
        """

        with cls._lock:
            try:
                factory = cls._factories[id]
            except KeyError as exc:
                raise KeyError(
                    f"no provider registered under id {id!r}"
                ) from exc
        return factory()

    @classmethod
    def is_registered(cls, id: str) -> bool:
        """Return ``True`` iff ``id`` has a registered factory."""

        with cls._lock:
            return id in cls._factories

    @classmethod
    def available_ids(cls) -> List[str]:
        """Return the sorted list of currently-registered provider ids."""

        with cls._lock:
            return sorted(cls._factories.keys())


__all__ = [
    "ImageProvider",
    "ImageResult",
    "ProviderFactory",
    "ProviderRegistry",
]
