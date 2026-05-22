"""Thread-safe cooperative cancellation primitive.

Worker tasks (provider searches, thumbnail and full-image downloads)
poll a :class:`CancellationToken` to find out whether the dialog that
queued them has since been closed by the user. When the dialog flips
the token, in-flight workers exit at their next poll and their outer
``try``/``except`` block silently swallows the resulting
:class:`~ankivn_image_picker.errors.CancelledError`, so no signal is
emitted on the worker bus after cancellation (Req 10.4, Property 16).

The token is a thin wrapper over :class:`threading.Event`. Using
``threading.Event`` directly gives us:

* atomic set/get semantics across threads, so no extra lock is needed,
* a single, monotonic transition from "not cancelled" to "cancelled"
  (the token is single-shot; resetting is intentionally not supported),
* compatibility with future code that may want to ``wait()`` on the
  token alongside other events.
"""

from __future__ import annotations

import threading

from .errors import CancelledError


class CancellationToken:
    """Cooperative, thread-safe, single-shot cancellation flag.

    Instances are cheap to create and safe to share across threads.
    Workers poll :attr:`is_cancelled` or call
    :meth:`raise_if_cancelled` at well-defined yield points (before
    issuing an HTTP request, between streamed chunks, and immediately
    before emitting a signal). The owning dialog calls :meth:`cancel`
    in its ``closeEvent`` and whenever a fresh search supersedes the
    in-flight one.
    """

    __slots__ = ("_event",)

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        """Mark this token as cancelled.

        Idempotent and thread-safe. Once called, :attr:`is_cancelled`
        returns ``True`` for the remaining lifetime of the token.
        """
        self._event.set()

    @property
    def is_cancelled(self) -> bool:
        """Whether :meth:`cancel` has been invoked on this token."""
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        """Raise :class:`CancelledError` iff the token is cancelled.

        A no-op while the token is uncancelled, so it is safe to sprinkle
        through hot loops without branching at the call site.
        """
        if self._event.is_set():
            raise CancelledError()


__all__ = ["CancellationToken"]
