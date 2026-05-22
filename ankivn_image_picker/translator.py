"""Auto-translate non-English search queries to English.

Many image providers (Unsplash, Pexels, Openverse) have most of their
content tagged in English, so searching in Vietnamese, Korean, or other
languages returns few or no results. This module wraps a free Google
Translate endpoint to auto-translate queries before they are sent to
the providers.

Endpoint: ``translate.googleapis.com/translate_a/single``
- No API key required
- No documented rate limit but should be used responsibly
- Returns the raw translated string when successful

If translation fails (network issue, unexpected response shape), the
original query is returned unchanged so the search can still proceed.
"""

from __future__ import annotations

import json
import re
from typing import Optional
from urllib.parse import urlencode

from .logging import get_logger

_log = get_logger("translator")

_TRANSLATE_ENDPOINT = "https://translate.googleapis.com/translate_a/single"

# Heuristic: if the query is mostly ASCII letters, assume it is already
# English-ish and skip translation. This avoids the round-trip cost
# for the common case of an English source field.
_ASCII_LETTERS_RE = re.compile(r"^[\x20-\x7e]+$")


def looks_like_english(text: str) -> bool:
    """Cheap heuristic: returns True if the text is plain ASCII.

    This catches the common case where the source field already
    contains an English word or short phrase, so we can skip the
    translation API call entirely.
    """
    if not isinstance(text, str) or not text:
        return False
    return bool(_ASCII_LETTERS_RE.match(text))


def translate_to_english(text: str, *, http=None, cancel=None) -> str:
    """Translate ``text`` to English. Return original on failure.

    Parameters
    ----------
    text:
        The text to translate. Returned unchanged if it already looks
        like English (ASCII-only) or if translation fails.
    http:
        Optional :class:`HttpClient`. If provided, used for the GET
        request so the cancellation token and timeout are respected.
        If omitted, falls back to a plain ``requests.get`` (used in
        contexts without an HttpClient).
    cancel:
        Optional :class:`CancellationToken`. Required if ``http`` is
        provided.

    Returns
    -------
    str
        The translated text, or the original text if translation
        could not be performed.
    """
    if not isinstance(text, str) or not text.strip():
        return text

    # Skip the round-trip if it already looks like English
    if looks_like_english(text):
        return text

    params = {
        "client": "gtx",
        "sl": "auto",  # source language: auto-detect
        "tl": "en",    # target: English
        "dt": "t",     # data type: translation
        "q": text,
    }
    url = f"{_TRANSLATE_ENDPOINT}?{urlencode(params)}"

    try:
        if http is not None and cancel is not None:
            response = http.get(url, cancel=cancel)
            body = response.body
        else:
            import requests as _requests
            resp = _requests.get(url, timeout=(5, 10))
            if resp.status_code >= 400:
                _log.warning(
                    "Translate API returned HTTP %d", resp.status_code
                )
                return text
            body = resp.content
    except Exception as exc:
        _log.warning("Translate request failed: %s", exc)
        return text

    try:
        payload = json.loads(body)
    except (ValueError, TypeError) as exc:
        _log.warning("Translate response not valid JSON: %s", exc)
        return text

    # Response shape: [[["translated", "original", null, null, ...]], ...]
    try:
        if not isinstance(payload, list) or not payload:
            return text
        sentences = payload[0]
        if not isinstance(sentences, list):
            return text
        parts: list[str] = []
        for sent in sentences:
            if isinstance(sent, list) and sent:
                first = sent[0]
                if isinstance(first, str):
                    parts.append(first)
        translated = "".join(parts).strip()
        if translated:
            return translated
    except (KeyError, IndexError, TypeError) as exc:
        _log.warning("Translate response unexpected shape: %s", exc)

    return text


__all__ = ["translate_to_english", "looks_like_english"]
