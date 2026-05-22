# ⚡ Image Picker - Fastest Image Search & Insert (by AnkiVN) — Configuration

Edit this configuration through Anki's **Tools → Add-ons → ⚡ Image Picker -
Fastest Image Search & Insert (by AnkiVN) → Config** dialog. Changes apply
the next time you open the picker; no Anki restart is required.

If the configuration file is missing, every key falls back to the default value
listed below and a single warning is written to the Anki debug console. If a
key is missing, holds an empty string, has the wrong type, or holds a value
outside its allowed range, that key alone falls back to its default — the rest
of the configuration is preserved.

## Keys

### `source_field`

- **Type:** non-empty string
- **Default:** `"word"`
- **Purpose:** Name of the note field whose text is read as the image search
  query. HTML tags are stripped and surrounding whitespace is trimmed before
  the query is sent.

### `target_field`

- **Type:** non-empty string
- **Default:** `"image"`
- **Purpose:** Name of the note field that the picker writes the selected
  `<img>` tag into. Existing content in this field is preserved; the new
  `<img>` tag is appended after it.

### `providers`

- **Type:** non-empty list of provider identifier strings
- **Default:** `["unsplash"]`
- **Purpose:** Ordered list of image providers the picker will query in
  parallel. Each identifier must match a registered provider (currently
  `"unsplash"` and `"pixabay"`). Unknown identifiers are dropped; if every
  entry is unknown the default list is used instead.

### `max_results_per_provider`

- **Type:** integer
- **Allowed range:** `1`–`200` (inclusive)
- **Default:** `30`
- **Purpose:** Fallback limit when a per-provider limit (see below) is not set.
  Each provider clamps to its own API hard cap.

### Per-provider limits

These override `max_results_per_provider` when set to a non-zero value.
Set to `0` to use the fallback. Each provider has a hard API cap:

- **`unsplash_max_results`** - Range `0`–`30`, default `30`
- **`pexels_max_results`** - Range `0`–`80`, default `80`
- **`wikimedia_max_results`** - Range `0`–`50`, default `50`
- **`openverse_max_results`** - Range `0`–`20`, default `20`

With all providers at max, you get **180 images per request** (30+80+50+20).
Click "Load More" to fetch the next page.

### `prefetch_notes_ahead`

- **Type:** integer
- **Allowed range:** `0`–`20` (inclusive)
- **Default:** `5`
- **Purpose:** In batch mode (Browser → ⚡ Image Picker), how many
  upcoming notes to start searching for in the background while the user
  is interacting with the current note. Higher values mean less waiting
  but more API requests upfront. Set to `0` to disable prefetching.

### `thumbnail_cache_max_mb`

- **Type:** integer
- **Allowed range:** `1`–`1024` (inclusive)
- **Default:** `64`
- **Purpose:** Maximum size, in megabytes, of the on-disk thumbnail cache
  stored under the add-on's `user_files/thumbnail_cache/` directory. When the
  cache exceeds this limit, the least-recently-used thumbnails are evicted
  until the cache is at or below the limit.

### `unsplash_access_key`

- **Type:** string
- **Default:** `""` (empty — not configured)
- **Purpose:** Your Unsplash API access key. Get one for free at
  https://unsplash.com/developers — create an app and copy the "Access Key".
  The picker cannot search Unsplash without this key.

### `pexels_api_key`

- **Type:** string
- **Default:** `""` (empty — not configured)
- **Purpose:** Your Pexels API key. Get one for free at
  https://www.pexels.com/api/ — sign up and copy your API key.
  Required only if you add `"pexels"` to the `providers` list.

### `google_api_key`

- **Type:** string
- **Default:** `""` (empty — not configured)
- **Purpose:** Your Google API key for Custom Search API. 
  **⚠️ IMPORTANT:** Google Custom Search API requires a **billing account** to be 
  activated, even for the free tier (100 queries/day). See `GOOGLE_API_SETUP.md` 
  for detailed setup instructions.
  
  Get your API key at: https://console.cloud.google.com/apis/credentials
  
  Required only if you add `"google"` to the `providers` list.

### `google_cse_id`

- **Type:** string
- **Default:** `""` (empty — not configured)
- **Purpose:** Your Google Custom Search Engine ID. Create one at
  https://programmablesearchengine.google.com/ and ensure:
  - "Image search" is enabled
  - "Search the entire web" is turned on
  
  Required only if you add `"google"` to the `providers` list.

## Available Providers

- **unsplash** - Free, no billing required (API key provided)
- **pexels** - Free, no billing required (API key provided)
- **wikimedia** - Free, no API key needed
- **openverse** - Free, no API key needed
- **google** - Free tier (100 queries/day) but **requires billing account activation**

## Unknown keys

Any key that is not listed above is ignored, and one warning is logged to the
Anki debug console naming each unknown key.
