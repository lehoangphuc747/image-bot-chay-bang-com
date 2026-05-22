"""Unit tests for :mod:`ankivn_image_picker.ui.worker_bus`.

These tests verify the bus exposes every documented signal and that
each signal forwards its declared payload to connected slots. The
tests run without a live Qt event loop: they exercise the in-process
shim from :mod:`ankivn_image_picker._qt_compat` when no Qt binding is
installed, and they pass with PyQt6 / PyQt5 / PySide6 alike because
direct (in-thread) signal emissions deliver synchronously on every
binding.
"""

from __future__ import annotations

from ankivn_image_picker.ui.worker_bus import WorkerBus

# Names of every signal the design promises the bus exposes.
EXPECTED_SIGNALS = (
    "result_ready",
    "provider_failed",
    "thumbnail_ready",
    "thumbnail_failed",
    "download_progress",
    "download_complete",
    "download_failed",
    "unhandled_error",
)


def _capture_emissions(bus: WorkerBus, signal_name: str) -> list:
    """Connect a recording slot to ``signal_name`` and return the buffer."""

    received: list = []
    signal = getattr(bus, signal_name)
    signal.connect(lambda *args: received.append(args))
    return received


def test_bus_construction_does_not_raise() -> None:
    """A fresh bus can be constructed without any arguments."""

    bus = WorkerBus()
    assert bus is not None


def test_bus_exposes_every_documented_signal() -> None:
    """Every signal listed in the design is reachable as an attribute."""

    bus = WorkerBus()
    for name in EXPECTED_SIGNALS:
        assert hasattr(bus, name), f"WorkerBus missing signal: {name}"


def test_result_ready_forwards_object_payload() -> None:
    bus = WorkerBus()
    received = _capture_emissions(bus, "result_ready")

    payload = {"provider_id": "unsplash", "thumbnail_url": "https://x/y.jpg"}
    bus.result_ready.emit(payload)

    assert received == [(payload,)]


def test_provider_failed_forwards_two_strings() -> None:
    bus = WorkerBus()
    received = _capture_emissions(bus, "provider_failed")

    bus.provider_failed.emit("unsplash", "HTTP 503")

    assert received == [("unsplash", "HTTP 503")]


def test_thumbnail_ready_forwards_url_and_bytes() -> None:
    bus = WorkerBus()
    received = _capture_emissions(bus, "thumbnail_ready")

    data = b"\x89PNG\r\n\x1a\nfake"
    bus.thumbnail_ready.emit("https://x/y.png", data)

    assert received == [("https://x/y.png", data)]


def test_thumbnail_failed_forwards_url_and_message() -> None:
    bus = WorkerBus()
    received = _capture_emissions(bus, "thumbnail_failed")

    bus.thumbnail_failed.emit("https://x/y.png", "timeout")

    assert received == [("https://x/y.png", "timeout")]


def test_download_progress_forwards_url_and_fraction() -> None:
    bus = WorkerBus()
    received = _capture_emissions(bus, "download_progress")

    bus.download_progress.emit("https://x/y.jpg", 0.0)
    bus.download_progress.emit("https://x/y.jpg", 0.5)
    bus.download_progress.emit("https://x/y.jpg", 1.0)

    assert received == [
        ("https://x/y.jpg", 0.0),
        ("https://x/y.jpg", 0.5),
        ("https://x/y.jpg", 1.0),
    ]


def test_download_complete_forwards_url_bytes_and_extension() -> None:
    bus = WorkerBus()
    received = _capture_emissions(bus, "download_complete")

    data = b"\xff\xd8\xff\xe0fake-jpeg"
    bus.download_complete.emit("https://x/y.jpg", data, "jpg")

    assert received == [("https://x/y.jpg", data, "jpg")]


def test_download_failed_forwards_url_and_message() -> None:
    bus = WorkerBus()
    received = _capture_emissions(bus, "download_failed")

    bus.download_failed.emit("https://x/y.jpg", "HTTP 404")

    assert received == [("https://x/y.jpg", "HTTP 404")]


def test_unhandled_error_forwards_message() -> None:
    bus = WorkerBus()
    received = _capture_emissions(bus, "unhandled_error")

    bus.unhandled_error.emit("ZeroDivisionError: division by zero")

    assert received == [("ZeroDivisionError: division by zero",)]


def test_signals_are_per_instance_not_shared() -> None:
    """Connecting to one bus must not deliver emissions on another bus."""

    bus_a = WorkerBus()
    bus_b = WorkerBus()
    received_a = _capture_emissions(bus_a, "thumbnail_ready")
    received_b = _capture_emissions(bus_b, "thumbnail_ready")

    bus_a.thumbnail_ready.emit("https://x/y.png", b"a")

    assert received_a == [("https://x/y.png", b"a")]
    assert received_b == []


def test_signal_supports_multiple_slots() -> None:
    bus = WorkerBus()
    received_one = _capture_emissions(bus, "provider_failed")
    received_two = _capture_emissions(bus, "provider_failed")

    bus.provider_failed.emit("pixabay", "boom")

    assert received_one == [("pixabay", "boom")]
    assert received_two == [("pixabay", "boom")]
