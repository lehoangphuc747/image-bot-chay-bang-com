"""Unit tests for :mod:`ankivn_image_picker.cancellation`.

These tests verify the contract of :class:`CancellationToken`:

* :meth:`~CancellationToken.cancel` flips :attr:`is_cancelled` from
  ``False`` to ``True`` and is idempotent.
* :meth:`~CancellationToken.raise_if_cancelled` is a no-op while the
  token is uncancelled and raises
  :class:`~ankivn_image_picker.errors.CancelledError` once cancelled.
* The token is safe to share across threads: a worker thread polling
  the token observes the cancellation made on the main thread, and
  many threads cancelling the same token concurrently never raise
  spuriously.

The token wraps :class:`threading.Event` so the "thread-safe" property
is inherited from the standard library, but we still exercise it
end-to-end so a future refactor that swaps the implementation cannot
silently regress this guarantee (Req 10.4).
"""

from __future__ import annotations

import threading
import time

import pytest

from ankivn_image_picker.cancellation import CancellationToken
from ankivn_image_picker.errors import AnkivnImagePickerError, CancelledError


# ---------------------------------------------------------------------------
# cancel() flips the flag
# ---------------------------------------------------------------------------


def test_fresh_token_is_not_cancelled() -> None:
    """A newly-constructed token starts out uncancelled."""

    token = CancellationToken()

    assert token.is_cancelled is False


def test_cancel_flips_is_cancelled_to_true() -> None:
    """One call to ``cancel()`` is enough to flip the flag."""

    token = CancellationToken()

    token.cancel()

    assert token.is_cancelled is True


def test_cancel_is_idempotent() -> None:
    """Calling ``cancel()`` repeatedly leaves the flag set without error."""

    token = CancellationToken()

    token.cancel()
    token.cancel()
    token.cancel()

    assert token.is_cancelled is True


# ---------------------------------------------------------------------------
# raise_if_cancelled() semantics
# ---------------------------------------------------------------------------


def test_raise_if_cancelled_is_noop_before_cancel() -> None:
    """Polling an uncancelled token never raises."""

    token = CancellationToken()

    # Calling many times must not raise either; this matches the
    # design's "safe to sprinkle through hot loops" contract.
    for _ in range(100):
        token.raise_if_cancelled()


def test_raise_if_cancelled_raises_cancelled_error_once_cancelled() -> None:
    """After ``cancel()`` the token raises :class:`CancelledError`."""

    token = CancellationToken()
    token.cancel()

    with pytest.raises(CancelledError):
        token.raise_if_cancelled()


def test_raise_if_cancelled_keeps_raising_on_repeated_polls() -> None:
    """The token is single-shot; once cancelled it stays cancelled."""

    token = CancellationToken()
    token.cancel()

    with pytest.raises(CancelledError):
        token.raise_if_cancelled()
    with pytest.raises(CancelledError):
        token.raise_if_cancelled()


def test_cancelled_error_is_an_addon_error() -> None:
    """Every error raised by the add-on must subclass the package base.

    The orchestrator's per-worker ``except`` blocks rely on this so
    they can match :class:`CancelledError` alongside other typed
    add-on errors (Req 10.4).
    """

    token = CancellationToken()
    token.cancel()

    with pytest.raises(AnkivnImagePickerError):
        token.raise_if_cancelled()


# ---------------------------------------------------------------------------
# Cross-thread safety
# ---------------------------------------------------------------------------


def test_worker_thread_observes_cancel_from_main_thread() -> None:
    """A polling worker exits once the main thread cancels the token."""

    token = CancellationToken()
    worker_started = threading.Event()
    observed_cancellation: list[bool] = []

    def worker() -> None:
        worker_started.set()
        # Tight poll loop, like the orchestrator workers do between
        # streamed chunks and before signal emissions.
        while True:
            try:
                token.raise_if_cancelled()
            except CancelledError:
                observed_cancellation.append(True)
                return
            time.sleep(0.001)

    thread = threading.Thread(target=worker)
    thread.start()
    try:
        assert worker_started.wait(timeout=2.0), "worker thread never started"
        token.cancel()
        thread.join(timeout=2.0)
        assert not thread.is_alive(), "worker did not exit after cancel"
    finally:
        # Ensure we never leak the worker if the assertions above fail.
        token.cancel()
        thread.join(timeout=2.0)

    assert observed_cancellation == [True]


def test_concurrent_cancels_from_many_threads_are_safe() -> None:
    """Many threads cancelling the same token leave it cancelled exactly once.

    No spurious exception is raised, and the final state is consistent.
    """

    token = CancellationToken()
    errors: list[BaseException] = []
    barrier = threading.Barrier(parties=8)

    def canceller() -> None:
        try:
            # Synchronize the start so the cancels really do race.
            barrier.wait(timeout=2.0)
            token.cancel()
        except BaseException as exc:  # pragma: no cover - sanity guard
            errors.append(exc)

    threads = [threading.Thread(target=canceller) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=2.0)
        assert not t.is_alive(), "canceller thread hung"

    assert errors == []
    assert token.is_cancelled is True


def test_token_shared_between_reader_and_writer_threads() -> None:
    """A reader thread keeps polling until a writer thread cancels.

    Mirrors the real layout: orchestrator worker (reader) and dialog
    ``closeEvent`` (writer) on different threads.
    """

    token = CancellationToken()
    poll_count = 0
    poll_lock = threading.Lock()
    stop_for_safety = threading.Event()

    def reader() -> None:
        nonlocal poll_count
        while not stop_for_safety.is_set():
            if token.is_cancelled:
                return
            with poll_lock:
                poll_count += 1
            time.sleep(0.001)

    reader_thread = threading.Thread(target=reader)
    reader_thread.start()
    try:
        # Let the reader spin a few times so we know it really is
        # observing the uncancelled state.
        time.sleep(0.02)
        with poll_lock:
            polls_before_cancel = poll_count
        assert polls_before_cancel > 0, "reader thread did not poll"

        token.cancel()
        reader_thread.join(timeout=2.0)
        assert not reader_thread.is_alive(), "reader did not exit after cancel"
    finally:
        stop_for_safety.set()
        reader_thread.join(timeout=2.0)

    assert token.is_cancelled is True
