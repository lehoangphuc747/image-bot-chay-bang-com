"""Typed exception hierarchy for the AnkiVN Smart Image Picker add-on.

Every error raised inside the add-on is a subclass of
:class:`AnkivnImagePickerError`. Worker threads convert third-party
exceptions (``requests.RequestException``, ``json.JSONDecodeError``,
``OSError``, ...) into one of the typed errors below before emitting any
failure signal on the worker bus, so the rest of the system never has to
inspect a foreign exception class.
"""

from __future__ import annotations


class AnkivnImagePickerError(Exception):
    """Base class for every error raised by the add-on."""


class ConfigError(AnkivnImagePickerError):
    """Raised for an unrecoverable configuration problem.

    The add-on's :mod:`~ankivn_image_picker.config` loader is designed to
    degrade gracefully (Req 1.8-1.11), so this error is reserved for
    truly unrecoverable cases such as the configuration backing store
    being unreadable.
    """


class ProviderError(AnkivnImagePickerError):
    """Raised when an :class:`ImageProvider` cannot return results.

    Covers HTTP errors, network errors, and malformed provider
    responses. The orchestrator converts this into a
    ``provider_failed`` signal on the worker bus (Req 4.5).
    """


class DownloadError(AnkivnImagePickerError):
    """Raised when a full-image download fails.

    Covers HTTP errors, network errors, timeouts, and any other
    failure that prevents the bytes from reaching the editor bridge
    (Req 7.4).
    """


class InvalidImageError(DownloadError):
    """Raised when a download succeeds but the response is not an image.

    Triggered by an empty response body or by a ``Content-Type`` whose
    media type is not in the allowed image set (Req 7.5).
    """


class FieldNotFoundError(AnkivnImagePickerError):
    """Raised when a configured source or target field is not on the note type.

    Used to abort the picker open (Req 3.4) or to abort an image
    insertion (Req 9.4) without modifying the current note.
    """


class CancelledError(AnkivnImagePickerError):
    """Raised by a worker that has observed ``CancellationToken.cancel()``.

    The orchestrator's per-worker ``try``/``except`` block silently
    swallows this exception so that no failure signal is emitted after
    the user closes the picker (Req 10.4, Property 16).
    """


__all__ = [
    "AnkivnImagePickerError",
    "ConfigError",
    "ProviderError",
    "DownloadError",
    "InvalidImageError",
    "FieldNotFoundError",
    "CancelledError",
]
