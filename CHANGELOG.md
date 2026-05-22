# Changelog

All notable changes to **⚡ Image Picker - Fastest Image Search & Insert (by AnkiVN)** are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

Nothing yet — see [`ROADMAP.md`](ROADMAP.md) for what's next.

---

## [0.3.0] — 2026-05-22

UX polish + branding pass. The picker now lives under a shared "AnkiVN"
menu and the settings dialog has been rebuilt from scratch.

### Added
- Left-side notes panel in batch mode, with `▶`/`✅`/`⏭` markers, image-presence
  badges, and click-to-jump navigation. Optional "Click note to search"
  toggle so users can change context without firing a fresh search.
- "Skip notes with images ⏩" button that fast-forwards over every upcoming
  note whose target field already contains an `<img>` tag.
- Shared "AnkiVN" parent menu (object name `sf_ankivn_menu`) so co-installed
  AnkiVN add-ons share a single entry on the menu bar. Image Picker installs
  itself as `⚡ Image Picker Settings` under that menu.
- Polished three-tab settings dialog (General / Providers / Advanced) with
  password-masked API keys, reveal toggle, "Get a free key" links, live
  status badges, total-results estimate, and reset-to-defaults action.
- `ROADMAP.md` documenting prioritized bugs, design issues, and missing
  features.

### Changed
- Add-on name is now `⚡ Image Picker - Fastest Image Search & Insert
  (by AnkiVN)` in `manifest.json` and across user-facing strings.
- Splitter receives a stretch factor of 1 so the notes panel + grid absorb
  all spare vertical space; previously empty padding accumulated above and
  below.
- Batch dialog auto-grows to at least 1100×750 the first time it opens so
  the grid isn't cramped after restoring older single-note geometry.
- Splitter sizes are remembered across sessions; the default split is
  180/820 instead of 220/700.
- Notes panel and grid use `ScrollPerPixel` with custom step sizes for
  smooth pixel-based scrolling instead of jumping a full row per wheel
  notch.
- Default `prefetch_notes_ahead` bumped from 5 to 8.

### Fixed
- Settings dialog form was a single dense `QFormLayout` with plain-text API
  keys and no validation. Replaced with a structured tabbed UI.

---

## [0.2.0] — 2026-05-22

Performance + batch-mode rebuild. The picker can now drive an entire
Browser selection through a single reused dialog, with full-image
downloads running in the background.

### Added
- Single reused `PickerDialog` across batch notes via new `start_batch`,
  `swap_to_query`, and `_advance_batch` helpers. Eliminates the 1–2 second
  flicker that occurred between notes when each one opened a fresh dialog.
- Fire-and-forget full-image download pool (4 workers) so double-clicking
  an image immediately swaps to the next note instead of blocking until
  the download completes.
- Prefetch progress indicator in the batch status bar
  (`📦 Prefetched X/Y notes · Z in flight`), polled every 250 ms.
- "Skip" path that records `skipped` in batch outcomes without aborting.

### Changed
- `HttpClient` now uses a persistent `requests.Session` with an HTTP
  adapter sized at `pool_maxsize=32` per host. Subsequent requests to the
  same provider host complete in roughly one RTT instead of paying for a
  fresh TCP+TLS handshake.
- Thumbnail download pool grew from 4 to 8 workers; orchestrator pool cap
  raised from 8 to `max(len(providers) * 2, 16)`.

### Fixed
- Pool of background download tasks now lives until the batch ends so the
  user's chosen images still land in their notes if the dialog is closed
  before all downloads complete.
- Timeout-budget tests updated to patch `requests.Session.get` after the
  client switched away from module-level `requests.get`.

---

## [0.1.0] — 2026-05-22

Initial release.

### Added
- Toolbar button injected into every Editor instance via
  `editor_did_init_buttons`.
- "Tools" menu entry plus a "Notes" menu entry on the card Browser that
  walks the selection sequentially through the picker.
- Provider plugin system with concrete implementations for Unsplash,
  Pexels, Wikimedia Commons, and Openverse. Provider registry, hard-cap
  metadata, and per-provider rate-limit notes are all module-level
  constants in `provider_info.py`.
- HTTP foundation (`HttpClient`) enforcing a 15-second `(connect, read)`
  timeout budget and per-chunk cancellation polling.
- On-disk LRU thumbnail cache backed by `user_files/thumbnail_cache/`.
- Round-robin streaming grid that interleaves results from multiple
  providers as their thumbnails arrive.
- Modal `PickerDialog` with: search bar, translate-to-English toggle,
  re-query, load-more pagination, and provider-status footer.
- Batch picker for the Browser with prefetch-ahead (default 5 notes),
  per-note skip tagging, and a sequential `for note in selection: dialog.exec()`
  loop.
- Anki JSON config (`config.json`, `config.md`) with field mappings, API
  keys, per-provider result caps, and the `prefetch_notes_ahead` knob.
- Pytest suite (unit, smoke, integration, property) plus shared Hypothesis
  strategies.

---

[Unreleased]: https://github.com/lehoangphuc747/image-bot-chay-bang-com/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/lehoangphuc747/image-bot-chay-bang-com/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/lehoangphuc747/image-bot-chay-bang-com/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/lehoangphuc747/image-bot-chay-bang-com/releases/tag/v0.1.0
