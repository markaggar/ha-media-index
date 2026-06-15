# Media Index Services

This document describes all available services provided by the Media Index integration.

## Multi-Instance Support

All services support multiple integration instances using Home Assistant's target selector:

```yaml
service: media_index.restore_edited_files
target:
  entity_id: sensor.media_index_photos_total_files
```

**Target Options:**
- `entity_id: sensor.media_index_photos_total_files` - Target specific instance
- Omit target - Operates on all configured instances

## User Services

### `media_index.restore_edited_files`

**Most Important for End Users** - Move files from `_Edit` folder back to their original locations.

**Parameters:**
- `folder_filter` (optional, default: `_Edit`): Filter by destination folder
- `file_path` (optional): Restore only this specific file

**How it works:**
- Tracks original file paths when moving to `_Edit`
- Stores move history in database
- Service reads history and restores files to original locations
- Updates database to reflect new locations

**Use cases:**
- Complete editing workflow after making corrections
- Bulk restore after batch editing in external applications
- Undo accidental moves to `_Edit` folder

**Example:**
```yaml
# Restore all edited files
service: media_index.restore_edited_files

# Restore specific file
service: media_index.restore_edited_files
data:
  file_path: /media/photo/Photos/_Edit/vacation.jpg
```

**Recommendation:** Run this service periodically (weekly/monthly) as part of your media management workflow.

### `media_index.check_file_exists`

**NEW in v1.5.6** - Lightweight filesystem validation for Media Card integration.

**Parameters:**
- `file_path` (optional): Filesystem path to check
- `media_source_uri` (optional): Media-source URI to check (converted to path)

**Note:** Must provide either `file_path` OR `media_source_uri` (not both required, but at least one).

**Returns:** 
```json
{
  "exists": true,
  "path": "/media/photo/Photos/2024/IMG_1234.jpg"
}
```

**Security:**
- Path traversal protection: All paths validated against configured `base_folder`
- Rejects attempts to probe filesystem outside media collection scope
- Uses `os.path.realpath()` to resolve symbolic links and prevent symlink attacks
- Normalizes paths to reject `..` traversals and resolve `.` components

**Use case:**
- Media Card v5.6.5+ uses this for instant 404 detection (~1ms vs 100ms+ image preload)
- Eliminates broken image icons by checking filesystem before rendering
- No network request, no image decode - just `os.path.exists()` check

**Example:**
```yaml
# Check by filesystem path
service: media_index.check_file_exists
target:
  entity_id: sensor.media_index_photos_total_files
data:
  file_path: /media/photo/Photos/2024/IMG_1234.jpg

# Check by media-source URI
service: media_index.check_file_exists
target:
  entity_id: sensor.media_index_photos_total_files
data:
  media_source_uri: media-source://media_source/media/photo/Photos/2024/IMG_1234.jpg
```

## Media Query Services

### `media_index.get_random_items`

Get random media files from the index (used by Media Card).

**Parameters:**
- `count` (optional, default: 1): Number of items to return (1-100)
- `folder` (optional): Filter by folder (filesystem path or media-source URI)
- `recursive` (optional, default: true): Include subfolders when filtering by folder
- `file_type` (optional): Filter by `image` or `video`
- `favorites_only` (optional, default: false): Only return files marked as favorites
- `date_from` (optional): ISO date string (YYYY-MM-DD) - uses EXIF date_taken if available, falls back to created_time. Null means "no lower limit"
- `date_to` (optional): ISO date string (YYYY-MM-DD) - uses EXIF date_taken if available, falls back to created_time. Null means "no upper limit"
- `timestamp_from` (optional, v1.5.9+): Unix timestamp (seconds since epoch) - takes precedence over date_from for exact time filtering
- `timestamp_to` (optional, v1.5.9+): Unix timestamp (seconds since epoch) - takes precedence over date_to for exact time filtering
- `anniversary_month` (optional): Month for anniversary matching (`"01"`-`"12"` or `"*"` for any month)
- `anniversary_day` (optional): Day for anniversary matching (`"01"`-`"31"` or `"*"` for any day)
- `anniversary_window_days` (optional, default: 0): Expand ±N days around target date for anniversary matching
- `priority_new_files` (optional, default: false): Prioritize recently scanned files
- `new_files_threshold_seconds` (optional, default: 3600): Seconds threshold for "new" files (1 hour)
- `auto_select_burst_favorite` (optional, default: false): Exclude non-favorite images from burst groups that have an indexed favorite (requires `index_burst_groups` to have been run first)

**Returns:** List of media items with metadata (includes `media_source_uri` in v1.4+)

**Examples:**
```yaml
# Basic random selection
service: media_index.get_random_items
data:
  count: 20
  file_type: image

# Filter by folder (filesystem path)
service: media_index.get_random_items
data:
  count: 20
  folder: /media/Photo/Vacation

# v1.4: Filter by folder (media-source URI)
service: media_index.get_random_items
data:
  count: 20
  folder: media-source://media_source/local/photos/vacation

# Priority for recent files (v1.3 feature)
service: media_index.get_random_items
data:
  count: 50
  priority_new_files: true
  new_files_threshold_seconds: 2592000  # 30 days

# Only favorites (useful for slideshow of best photos)
service: media_index.get_random_items
data:
  count: 20
  favorites_only: true

# Date range: files from 2023 (null date_from/date_to means no limit)
service: media_index.get_random_items
data:
  count: 20
  date_from: "2023-01-01"
  date_to: "2023-12-31"

# Timestamp range: files from specific day (v1.5.9+)
# Example: 2019-12-25 00:00:00 to 23:59:59 PST
service: media_index.get_random_items
data:
  count: 20
  timestamp_from: 1577260800
  timestamp_to: 1577347199

# "This month or earlier" (date_from null = no lower limit)
service: media_index.get_random_items
data:
  count: 20
  date_to: "2024-11-30"

# "This year onwards" (date_to null = no upper limit)
service: media_index.get_random_items
data:
  count: 20
  date_from: "2024-01-01"

# Anniversary mode: Photos from same date across all years (Through the Years)
service: media_index.get_random_items
data:
  count: 100
  anniversary_month: "*"
  anniversary_day: "25"
  anniversary_window_days: 3

# Anniversary mode: Photos from December 25 across all years, ±7 day window
service: media_index.get_random_items
data:
  count: 100
  anniversary_month: "12"
  anniversary_day: "25"
  anniversary_window_days: 7
```

**Anniversary Mode Parameters (v1.5+):**
- `anniversary_month` (optional): Month for anniversary matching (`"01"`-`"12"` or `"*"` for any month)
- `anniversary_day` (optional): Day for anniversary matching (`"01"`-`"31"` or `"*"` for any day)
- `anniversary_window_days` (optional, default: 0): Expand ±N days around target date

**Use cases:**
- Through the Years feature in Media Card - show photos from today's date across all years
- Holiday memories - "All December 25th photos from any year"
- Birthday/anniversary retrospectives with adjustable date tolerance

### `media_index.get_ordered_files`

**New in v1.3** - Get ordered media files with configurable sort field and direction. Supports date range filtering and cursor-based pagination for stable page-by-page traversal.

**Parameters:**
- `count` (optional, default: 50): Maximum number of files to return (1-1000)
- `folder` (optional): Filter by folder (filesystem path or media-source URI)
- `recursive` (optional, default: true): Include subfolders
- `file_type` (optional): Filter by `image` or `video`
- `order_by` (optional, default: `date_taken`): Sort field (`date_taken`, `filename`, `path`, `modified_time`)
- `order_direction` (optional, default: `desc`): Sort direction (`asc` or `desc`)
- `date_from` (optional): Include only files taken on or after this date (`YYYY-MM-DD`)
- `date_to` (optional): Include only files taken on or before this date (`YYYY-MM-DD`)
- `timestamp_from` (optional): Unix timestamp lower bound (takes precedence over `date_from`)
- `timestamp_to` (optional): Unix timestamp upper bound (takes precedence over `date_to`)
- `after_value` (optional): Compound cursor — pass `next_cursor.after_value` from the previous response to fetch the next page
- `after_id` (optional): Cursor tie-breaker — pass `next_cursor.after_id` from the previous response (must accompany `after_value`)

**Cursor pagination:** The response includes a `next_cursor` object (`{after_value, after_id}`) when more results exist. Pass these back on the next call to get the next page without duplicates or gaps, even if files are added/removed between calls.

**Returns:** List of ordered media items with metadata (includes `media_source_uri` in v1.4+) and a `next_cursor` field for pagination.

**Examples:**
```yaml
# Get newest photos by EXIF date
service: media_index.get_ordered_files
data:
  count: 100
  order_by: date_taken
  order_direction: desc

# Get alphabetically sorted folder
service: media_index.get_ordered_files
data:
  folder: /media/photo/Photos/2023
  order_by: filename
  order_direction: asc
  recursive: false

# Date-filtered — photos from a specific year only
service: media_index.get_ordered_files
data:
  count: 100
  order_by: date_taken
  order_direction: asc
  date_from: "2023-01-01"
  date_to: "2023-12-31"

# Next page using cursor from previous response
service: media_index.get_ordered_files
data:
  count: 50
  order_by: date_taken
  order_direction: desc
  after_value: "2024-06-15T10:30:00"
  after_id: 1234
```

### `media_index.get_file_metadata`

Get detailed metadata for a specific file.

**Parameters:**
- `file_path` (optional): Full filesystem path to media file
- `media_source_uri` (optional, v1.4+): Media-source URI (alternative to file_path)

**Note:** Provide either `file_path` OR `media_source_uri`

**Returns:** Complete metadata including EXIF, location, GPS, and ratings

**Examples:**
```yaml
# Using filesystem path
service: media_index.get_file_metadata
data:
  file_path: /media/Photo/PhotoLibrary/sunset.jpg

# v1.4: Using media-source URI
service: media_index.get_file_metadata
data:
  media_source_uri: media-source://media_source/media/Photo/PhotoLibrary/sunset.jpg
```

### `media_index.get_related_files`

**New in v1.5, updated in v1.6.0** - Find related photos for a reference file.

**Parameters:**
- `mode` (required): `"burst"` for burst detection or `"anniversary"` for same-day photos across years
- `reference_path` (optional): Filesystem path to reference photo
- `media_source_uri` (optional): Media-source URI (alternative to reference_path)
- `sort_order` (optional, default: `"time_asc"`): Result ordering (`time_asc` or `time_desc`)

**Anniversary Mode Parameters:**
- `window_days` (optional, default: 3): Days before/after reference date to include (±N days)
- `years_back` (optional, default: 15): How many years back to search

**Note:** Provide either `reference_path` OR `media_source_uri`.

**Burst Mode Behavior (v1.6.0+):**

Grouping thresholds are determined by the integration, not the caller — no time or location parameters are accepted for burst mode.

1. **Fast path**: If the reference photo has a `burst_id` (assigned by `index_burst_groups`), all members of that pre-computed group are returned via a single indexed join. This is the normal case after `index_burst_groups` has run.
2. **Fallback**: If the photo has no `burst_id`, at-query-time proximity detection runs using the `burst_time_window_seconds` and `burst_location_tolerance_meters` values configured in the integration options (Settings → Devices & Services → Media Index → Configure). Safe hardcoded defaults are used when connecting to a media_index version older than v1.6.0.

**Returns:** List of related photos with `seconds_offset`, `distance_meters` (fallback path only), `is_favorited`, `rating`, and `media_source_uri`.

**Use cases:**
- Burst Review panel in Media Card — compare rapid-fire shots to select the best photo
- Finding all members of a burst group for batch-favorite or batch-delete workflows

**Example:**
```yaml
# Burst mode — no time/location params needed; integration handles grouping
service: media_index.get_related_files
data:
  mode: burst
  media_source_uri: media-source://media_source/media/Photo/PhotoLibrary/IMG_1234.jpg
```

### `media_index.index_burst_groups`

**New in v1.6.0** - Scan the entire library and write burst group membership to every file. Run this once (or after bulk imports) to enable database-level burst filtering in `get_random_items`.

**Parameters:**
- `folder` (optional): Limit the scan to files under this folder prefix. Omit to scan the entire library.
- `time_window_seconds` (optional, default: 10): Maximum gap in seconds between consecutive photos in the same burst group.
- `location_tolerance_meters` (optional, default: 50): GPS radius in metres for grouping photos at the same location. Use 0 to disable GPS sub-clustering.
- `min_group_size` (optional, default: 2): Minimum number of photos required to be considered a burst group.
- `overwrite_existing` (optional, default: true): If true, recalculate burst data for all matching files. If false, skip files that already have `burst_count` set.

**Returns:**
- `status`: `"success"` or `"error"`
- `groups_found`: Number of distinct burst groups identified
- `files_updated`: Number of files written with burst metadata
- `files_skipped`: Files already up-to-date (no change needed)
- `errors`: Number of files that could not be written

**How it works:**
- Streams all indexed files sorted by `date_taken` using 1000-row fetch batches — memory footprint is O(burst_size), not O(library_size)
- Groups consecutive photos within the configured time window; each group is processed and written immediately when complete
- Sub-clusters by GPS proximity when coordinates are available for group members
- Assigns a stable `burst_id` to every member of each group (the lowest `file_id` in the group); used by `get_related_files` for O(1) fast-path lookups
- Writes `burst_count`, `burst_favorites`, and `burst_id` to `exif_data` in 500-row commit batches
- Idempotent: safe to run multiple times; already-correct rows are skipped when `overwrite_existing: false`

**Per-folder burst customization:**

You can call `index_burst_groups` separately per folder with different thresholds — for example, a burst-heavy sports folder might use a 3-second window while a general library uses 10 seconds. Each folder’s `burst_id` assignments are stored independently, and `get_related_files` always honors the pre-computed group for each file regardless of what the global integration defaults are.

```yaml
# Tighter grouping for action/sports shots
service: media_index.index_burst_groups
data:
  folder: /media/Photo/Sports
  time_window_seconds: 3
  location_tolerance_meters: 20

# Looser grouping for general library (run without folder to cover everything else)
service: media_index.index_burst_groups
data:
  time_window_seconds: 10
  location_tolerance_meters: 50
  overwrite_existing: false  # don't overwrite the Sports folder already processed above
```

**Example:**
```yaml
service: media_index.index_burst_groups
target:
  entity_id: sensor.media_index_photos_total_files
data:
  time_window_seconds: 10
  location_tolerance_meters: 50
  min_group_size: 2
```

**Response example:**
```json
{
  "status": "success",
  "groups_found": 842,
  "files_updated": 3107,
  "files_skipped": 41823,
  "errors": 0
}
```

**Tip:** After running `index_burst_groups`, enable `auto_select_burst_favorite: true` in Media Card and the card will automatically receive only favorited images from burst groups — no 2-second timers, no client-side splicing.

### `media_index.update_burst_metadata`

**New in v1.5** - Save burst review session data to file metadata for historical tracking.

**Parameters:**
- `burst_files` (required): List of all file URIs in the burst group
- `favorited_files` (required): List of file URIs marked as favorites during review

**Returns:**
- `files_updated`: Number of files updated
- `burst_count`: Total files in burst
- `favorites_count`: Number of favorited files

**How it works:**
- Writes `burst_favorites` (JSON array of filenames) to all files in the burst
- Writes `burst_count` (integer) to record total files at review time
- Metadata persists even if files are deleted or parameters change
- Enables historical tracking and future features

**Example:**
```yaml
service: media_index.update_burst_metadata
data:
  burst_files:
    - media-source://media_source/media/Photo/PhotoLibrary/IMG_1234.jpg
    - media-source://media_source/media/Photo/PhotoLibrary/IMG_1235.jpg
    - media-source://media_source/media/Photo/PhotoLibrary/IMG_1236.jpg
    - media-source://media_source/media/Photo/PhotoLibrary/IMG_1237.jpg
  favorited_files:
    - media-source://media_source/media/Photo/PhotoLibrary/IMG_1235.jpg
    - media-source://media_source/media/Photo/PhotoLibrary/IMG_1236.jpg
```

### `media_index.find_duplicate_files`

**New in v1.7.0** - Find filesystem-level duplicate files within burst groups and optionally move non-keepers to `_Junk`.

Burst indexing is run automatically at the start of every call so duplicate detection always works from fresh data — no separate `index_burst_groups` call is needed beforehand.

**Duplicate detection uses two passes:**
1. **Exact match** — files in the same burst group sharing identical `file_size + date_taken + width + height`
2. **Filename match** — files in the same burst group with the same `filename + date_taken + width + height` whose file sizes are within 1% of each other (catches the same photo uploaded twice with minor EXIF padding differences)

**Keeper selection is folder-pair aware**: rather than picking a keeper per file, the service tallies which folder contributes more duplicate files across all matching pairs and designates that folder as the keeper globally. This ensures all keepers come from one folder and all non-keepers from the other, rather than being scattered randomly. Within the keeper folder the file that is favorited / most recently modified / first alphabetically is kept.

**Parameters:**
- `folder` (optional): Limit the search to files under this folder prefix. Omit to search the entire library.
- `prefer_folders` (optional): Comma-delimited list of folder path entries that override the automatic majority-vote. Any folder matching an entry is always chosen as the keeper. Entries earlier in the list take precedence over later ones.
  - Each entry can be a **full absolute path** (`/media/homes/jdaggar/Photos/Camera Roll`) or a **partial suffix** (`/Camera Roll`) — suffix matching means you don't need to know the full base path.
  - Example: `prefer_folders: "/Samsung Gallery/DCIM/Camera,/Camera Roll"` — the first entry wins if both match.
- `dry_run` (optional, default: `true`): Return duplicate groups without moving any files.
- `auto_delete` (optional, default: `false`): When `dry_run: false`, move all non-keeper duplicates to `_Junk`. Has no effect when `dry_run: true`.

**Safety behaviour when `auto_delete: true`:**
- The keeper file is verified to exist on disk before any duplicate in its group is moved. Groups with a missing keeper are skipped entirely (logged as a warning) so no file is ever orphaned.
- If any duplicate being moved is favorited but the keeper is not, the keeper is automatically marked as favorited before the duplicate is deleted, so the favorite status is never silently lost.

**Returns:**
- `status`: `"success"` or `"error"`
- `dry_run`: Whether this was a preview run
- `duplicate_sets`: Number of distinct duplicate groups found
- `total_duplicates`: Total non-keeper files identified
- `total_duplicate_size_gb`: Estimated reclaimable disk space in GB (sum of `file_size × duplicate_count` per group)
- `deleted`: Files moved to `_Junk` (0 when `dry_run: true`)
- `delete_errors`: Files that could not be moved
- `folder_pairs`: High-level summary per folder pair — each entry has `keeper_folder`, `duplicate_folder`, `duplicate_sets`, `total_duplicates`
- `groups`: Full list of duplicate sets — each has `keeper` and `duplicates` entries with `path`, `folder`, `file_id`, `is_favorited`, `modified_time`

**Recommended workflow:**
```yaml
# Step 1: Preview — check folder_pairs summary and total_duplicate_size_gb before committing
service: media_index.find_duplicate_files
target:
  entity_id: sensor.media_index_media_photo_photolibrary_total_files
data:
  dry_run: true

# Step 2: Preview with preferred keeper folders (partial suffix paths work)
service: media_index.find_duplicate_files
target:
  entity_id: sensor.media_index_media_photo_photolibrary_total_files
data:
  dry_run: true
  prefer_folders: "/Samsung Gallery/DCIM/Camera,/Camera Roll"

# Step 3: Delete — once satisfied with the preview
service: media_index.find_duplicate_files
target:
  entity_id: sensor.media_index_media_photo_photolibrary_total_files
data:
  dry_run: false
  auto_delete: true
  prefer_folders: "/Samsung Gallery/DCIM/Camera,/Camera Roll"
```

## File Management Services

### `media_index.mark_favorite`

Mark a file as favorite (writes to database and EXIF).

**Parameters:**
- `file_path` (optional): Full filesystem path to media file
- `media_source_uri` (optional, v1.4+): Media-source URI (alternative to file_path)
- `is_favorite` (optional, default: true): Favorite status

**Note:** Provide either `file_path` OR `media_source_uri`

**Examples:**
```yaml
# Using filesystem path
service: media_index.mark_favorite
data:
  file_path: /media/photo/PhotoLibrary/sunset.jpg
  is_favorite: true

# v1.4: Using media-source URI
service: media_index.mark_favorite
data:
  media_source_uri: media-source://media_source/media/Photo/PhotoLibrary/sunset.jpg
  is_favorite: true
```

### `media_index.delete_media`

Delete a media file (moves to `_Junk` folder).

**Parameters:**
- `file_path` (optional): Full filesystem path to media file
- `media_source_uri` (optional, v1.4+): Media-source URI (alternative to file_path)

**Note:** Provide either `file_path` OR `media_source_uri`

**Examples:**
```yaml
# Using filesystem path
service: media_index.delete_media
data:
  file_path: /media/photo/PhotoLibrary/blurry.jpg

# v1.4: Using media-source URI
service: media_index.delete_media
data:
  media_source_uri: media-source://media_source/media/Photo/PhotoLibrary/blurry.jpg
```

### `media_index.mark_for_edit`

Mark a file for editing (moves to `_Edit` folder).

**Parameters:**
- `file_path` (optional): Full filesystem path to media file
- `media_source_uri` (optional, v1.4+): Media-source URI (alternative to file_path)

**Note:** Provide either `file_path` OR `media_source_uri`

**Examples:**
```yaml
# Using filesystem path
service: media_index.mark_for_edit
data:
  file_path: /media/photo/PhotoLibrary/needs_crop.jpg

# v1.4: Using media-source URI
service: media_index.mark_for_edit
data:
  media_source_uri: media-source://media_source/media/Photo/PhotoLibrary/needs_crop.jpg
```

## Maintenance Services

### `media_index.scan_folder`

Trigger a manual scan of media folders.

**Parameters:**
- `folder_path` (optional): Specific folder to scan (defaults to base folder if not specified)
- `force_rescan` (optional, default: false): Re-extract metadata for existing files

**Example:**
```yaml
# Scan all folders
service: media_index.scan_folder

# Scan specific folder
service: media_index.scan_folder
data:
  folder_path: /media/photo/Photos/2023
  force_rescan: true
```

### `media_index.geocode_file`

Force geocoding of a file's GPS coordinates (cache-first, on-demand).

**Parameters:**
- `file_id` (optional): Database ID of the file to geocode
- `file_path` (optional): Full filesystem path to media file
- `media_source_uri` (optional, v1.4+): Media-source URI (alternative to file_path)
- `latitude` (optional): GPS latitude (v1.3+, alternative to file identification)
- `longitude` (optional): GPS longitude (v1.3+, requires latitude)

**Note:** Provide one of: `file_id`, `file_path`, `media_source_uri`, or `latitude`+`longitude`

**Examples:**
```yaml
# Geocode by file ID
service: media_index.geocode_file
data:
  file_id: 12345

# Geocode by filesystem path
service: media_index.geocode_file
data:
  file_path: /media/Photo/PhotoLibrary/sunset.jpg

# v1.4: Geocode by media-source URI
service: media_index.geocode_file
data:
  media_source_uri: media-source://media_source/media/Photo/PhotoLibrary/sunset.jpg

# v1.3: Geocode by coordinates
service: media_index.geocode_file
data:
  latitude: 37.7749
  longitude: -122.4194
```

### `media_index.cleanup_database`

Remove database entries for files that no longer exist on the filesystem (v1.5+).

**When to use:**
- After moving or deleting files outside Home Assistant
- To fix 404 errors in Media Card from stale database entries
- As periodic maintenance to keep database in sync with filesystem

**Parameters:**
- `dry_run` (optional, default: true): If true, only reports stale files without removing them

**Returns:**
- `files_checked`: Total number of files validated
- `stale_files`: List of files no longer on filesystem
- `files_removed`: Number of database entries deleted (0 if dry_run=true)

**Examples:**
```yaml
# Safe preview - see what would be removed
service: media_index.cleanup_database
data:
  dry_run: true

# Actually remove stale entries
service: media_index.cleanup_database
data:
  dry_run: false
```

**Response example:**
```json
{
  "files_checked": 15234,
  "files_removed": 42,
  "stale_files": [
    {"id": 123, "path": "/media/Photo/deleted_file.jpg"},
    {"id": 456, "path": "/media/Photo/moved_file.jpg"}
  ]
}
```

## Cast to TV Services

These services power the [Media Card](https://github.com/markaggar/ha-media-card) cast-to-TV feature and can also be called directly from automations or scripts.

### `media_index.roku_ecp_cast`

Cast an image or video directly to a Roku TV via the [xcast](https://channelstore.roku.com/details/687485) ECP app (app ID 687485).

**How it works**

The service generates a short HMAC-signed stream URL pointing to HA's built-in streaming endpoint, then POSTs it to the Roku ECP input API (`http://{roku_host}:8060/input/687485?...`) server-side — bypassing browser CORS restrictions. The Roku fetches the image directly from HA.

Before serving, images are transcoded through Pillow:
- Re-encoded with standard JPEG Huffman/quantization tables (fixes grey screen on cameras like Nikon D5100 with non-standard encoding)
- Downscaled to 3840×2160 max for Roku hardware JPEG decoder compatibility
- EXIF orientation applied (`exif_transpose`) so the pixels are always in their correct display orientation — prevents horizontal stretch on rotated phone photos

**Requirements**

1. **Roku HA integration** configured for the target device (Settings → Devices & Services → Roku)
2. **xcast channel** installed on the Roku from the [Roku Channel Store](https://channelstore.roku.com/details/687485)
   - xcast acts as a Digital Media Renderer (DMR) that receives content pushed from HA
   - On first activation (cold start), xcast launches automatically when the ECP command arrives; the card retries ~2.5 s later to ensure receipt once the channel is fully loaded

**Parameters**

| Parameter | Required | Description |
|-----------|----------|-------------|
| `roku_entity_id` | ✅ | The `media_player.*` entity ID of the Roku device |
| `file_id` | one of these | Media Index file ID |
| `file_path` | one of these | Filesystem path to the file |
| `media_source_uri` | one of these | HA media-source URI |
| `path_contains` | one of these | Partial path substring to look up |
| `ttl` | optional | Stream URL expiry in seconds (default: 3600) |

**Returns**

```json
{
  "url": "http://10.0.0.62:8123/api/media_index/stream/1234/photo.jpg?t=abc1&exp=...",
  "file_id": 1234,
  "roku_host": "10.0.0.15",
  "ecp_status": 200,
  "ecp_url_sent": "http://10.0.0.15:8060/input/687485?...",
  "media_type": "image"
}
```

**Example**

```yaml
service: media_index.roku_ecp_cast
data:
  roku_entity_id: media_player.living_room_tv
  media_source_uri: media-source://media_source/media/photo/Photos/IMG_1234.jpg
```

---

### `media_index.stop_cast`

Send an ECP `keypress/Home` to a Roku device, clearing the cast image and returning the TV to its home screen.

Use this when stopping a cast session to prevent the last image remaining frozen on the TV. The [Media Card](https://github.com/markaggar/ha-media-card) calls this automatically when the user taps the cast button to stop or navigates away from the card.

Note: This service uses the ECP protocol directly because the Roku's `media_player` entity in HA does not support the generic `media_player.media_stop` action when xcast is active.

**Parameters**

| Parameter | Required | Description |
|-----------|----------|-------------|
| `roku_entity_id` | ✅ | The `media_player.*` entity ID of the Roku device |

**Returns**

```json
{
  "roku_host": "10.0.0.15",
  "ecp_status": 200
}
```

**Example**

```yaml
service: media_index.stop_cast
data:
  roku_entity_id: media_player.living_room_tv
```

---

### `media_index.roku_ecp_query`

Query the current playback state of a Roku device directly via ECP, bypassing the HA `media_player` entity which has up to an 8-second polling lag. Used by Media Card to keep its local video clock in sync with what the Roku is actually playing.

**Parameters**

| Parameter | Required | Description |
|-----------|----------|-------------|
| `roku_entity_id` | ✅ | The `media_player.*` entity ID of the Roku device |

**Returns**

```json
{
  "state": "play",
  "position": 42.3,
  "duration": 120.0
}
```

---

### `media_index.roku_ecp_keypress`

Send an ECP keypress directly to a Roku device, bypassing the HA `media_player` entity. Use for timing-sensitive actions such as pausing video a few seconds before it ends.

**Common key names:** `Play` (toggle play/pause), `Pause`, `Home`, `Back`, `Fwd`, `Rev`

**Parameters**

| Parameter | Required | Description |
|-----------|----------|-------------|
| `roku_entity_id` | ✅ | The `media_player.*` entity ID of the Roku device |
| `keyname` | ✅ | ECP key name (letters, digits, hyphens, underscores only) |

**Example**

```yaml
service: media_index.roku_ecp_keypress
data:
  roku_entity_id: media_player.living_room_tv
  keyname: Pause
```

---

## Cast Slideshow Services (v1.8+)

> **🎬 No Media Card required** — these services run a fully autonomous slideshow directly on a Roku TV from Home Assistant automations or scripts. No browser, no Lovelace dashboard, no Media Card needed.

### `media_index.start_cast_slideshow`

Start an unattended random-batch slideshow cast to a Roku TV (or any HA `media_player`). The integration fetches random files from its database in batches of 100 and pushes each one to the TV in turn, advancing automatically on a configurable interval. The slideshow continues indefinitely until stopped with `stop_cast_slideshow`.

For Roku devices, ECP is used automatically (better orientation, native format support). For other `media_player` entities, `media_player.play_media` is used.

**xcast cold-start handling**: On first use (or after the Roku has left xcast), the initial push launches the xcast app. The integration detects when xcast is fully initialised by watching the HA entity's `app_name` attribute and automatically resends the first item once xcast is ready — so the first photo/video always plays correctly.

**Parameters**

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `media_player_entity_id` | ✅ | — | Target `media_player` entity |
| `interval` | optional | `10` | Seconds to display each image. For videos, the actual file duration is used automatically. |
| `video_overlap` | optional | `0` | Push the next item this many seconds before the current video ends. Set to `1`–`2` if you see a black screen between videos. |
| `sync_group` | optional | — | Sync group ID. When set, each advance is written to the sync group so paired Media Cards follow along. |
| `also_write_sync` | optional | `false` | Enable writing to `sync_group` on each advance. |
| `folder` | optional | — | Limit files to this folder path or `media-source://` URI. |
| `recursive` | optional | `true` | Include files in sub-folders. |
| `file_type` | optional | — | Filter by `image` or `video`. |
| `date_from` | optional | — | Include files taken on or after this date (`YYYY-MM-DD`). |
| `date_to` | optional | — | Include files taken on or before this date (`YYYY-MM-DD`). |
| `favorites_only` | optional | `false` | Include only favorited files. |
| `anniversary_month` | optional | — | Month (1–12) for anniversary filtering. |
| `anniversary_day` | optional | — | Day of month for anniversary filtering. |
| `anniversary_window_days` | optional | `0` | Days either side of the anniversary date to include. |
| `priority_new_files` | optional | `false` | Prioritise files not yet shown in this session. |

**Target selector**

Uses a `target:` entity picker set to `integration: media_index` to select which Media Index instance provides the media. Required for multi-instance setups.

**Example — basic**

```yaml
service: media_index.start_cast_slideshow
target:
  entity_id: sensor.media_index_photos_total_files
data:
  media_player_entity_id: media_player.living_room_tv
  interval: 15
```

**Example — anniversary slideshow, shared with a card**

```yaml
service: media_index.start_cast_slideshow
target:
  entity_id: sensor.media_index_photos_total_files
data:
  media_player_entity_id: media_player.living_room_tv
  interval: 20
  anniversary_month: 5
  anniversary_day: 22
  anniversary_window_days: 3
  sync_group: living_room_queue
  also_write_sync: true
```

---

### `media_index.stop_cast_slideshow`

Stop a running `start_cast_slideshow` or `mirror_to_cast` session. For Roku targets, also sends a `keypress/Home` ECP command to dismiss the xcast app from the TV screen — preventing the last photo/video from remaining frozen on screen.

**Parameters**

| Parameter | Required | Description |
|-----------|----------|-------------|
| `media_player_entity_id` | optional | Stop the session for this specific entity. Omit to stop **all** active cast sessions. |

**Example — stop a specific session**

```yaml
service: media_index.stop_cast_slideshow
data:
  media_player_entity_id: media_player.living_room_tv
```

**Example — stop all sessions**

```yaml
service: media_index.stop_cast_slideshow
```

---

### `media_index.mirror_to_cast`

Mirror a Media Card's shared queue group to a Roku TV in real-time. Every time the card navigates to a new item (or any source writes to the sync group), the same media is pushed to the TV immediately. The TV follows the card rather than advancing on its own timer.

This is the complement to `start_cast_slideshow`: instead of the TV leading and the card following, the card leads and the TV mirrors.

Stop the session with `stop_cast_slideshow`.

**Parameters**

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `media_player_entity_id` | ✅ | — | Target `media_player` entity |
| `sync_group` | ✅ | — | Shared queue group ID to listen to. Must match the `sync_group` set on the Media Card. |
| `pre_end_pause` | optional | `true` | Issue a `Pause` keypress a few seconds before a video ends, preventing xcast from exiting to the home screen when the video finishes. |
| `video_overlap` | optional | `2` | How many seconds before the video end to issue the pre-end pause. |

**Target selector**

Uses a `target:` entity picker set to `integration: media_index`.

**Example**

```yaml
service: media_index.mirror_to_cast
target:
  entity_id: sensor.media_index_photos_total_files
data:
  media_player_entity_id: media_player.living_room_tv
  sync_group: living_room_queue
  pre_end_pause: true
```

---

## Service Usage with Media Card

The Media Index services integrate seamlessly with the [Home Assistant Media Card](https://github.com/markaggar/ha-media-card):

- **`get_random_items`** - Used automatically by Media Card for random slideshow mode
  - Anniversary mode (v1.5+) powers "Through the Years" feature
- **`get_ordered_files`** - Used automatically by Media Card for sequential slideshow mode (v1.3)
- **`get_related_files`** (v1.5+) - Powers "Burst Review" feature for reviewing rapid-fire photos
- **`update_burst_metadata`** (v1.5+) - Saves burst review favorites to file metadata
- **`index_burst_groups`** (v1.6.0+) - One-shot library scan that enables backend-level burst filtering in `get_random_items`
- **`mark_favorite`** - Called when clicking favorite button on Media Card
- **`delete_media`** - Called when clicking delete button on Media Card
- **`mark_for_edit`** - Called when clicking edit button on Media Card
- **`restore_edited_files`** - Run periodically to restore edited files
- **`roku_ecp_cast`** - Called by Media Card cast button to push current item to Roku
- **`stop_cast`** - Called by Media Card when stopping a cast session
- **`roku_ecp_query`** - Polls Roku playback position for video sync (bypasses 8s HA entity lag)
- **`roku_ecp_keypress`** - Issues timing-sensitive keypresses (e.g. Pause before video end)

## v1.8 Enhancements Summary

### New Services
- ✨ **`start_cast_slideshow`** — Autonomous random slideshow directly to a Roku TV; no Media Card required. Supports all `get_random_items` filters, configurable interval, sync group write-back so paired cards can follow along. Auto-detects Roku and uses ECP transport.
- ✨ **`stop_cast_slideshow`** — Stops a running slideshow or mirror session and sends `keypress/Home` to dismiss xcast from the TV screen.
- ✨ **`mirror_to_cast`** — Real-time mirror: TV follows a Media Card's sync group navigation instead of advancing on its own timer.

### Enhanced Services
- 🔄 **`start_cast_slideshow`** — xcast cold-start handling: detects when xcast is fully initialised (via `app_name` entity attribute) and resends the first item, fixing the common "first item doesn't display" issue.
- 🐛 **`mirror_to_cast` / `roku_ecp_cast`** — File type is now resolved from the database record rather than trusting card-supplied metadata, fixing videos displaying as grey screens on Roku when cast from the Media Card.

## v1.6.0 Enhancements Summary

### New Services
- ✨ **`index_burst_groups`** - Full-library burst indexer; enables SQL-level filtering of non-favorite burst members
- ✨ **`find_duplicate_files`** - Folder-pair-aware duplicate detection within burst groups; dry-run preview + auto-delete to `_Junk`; auto-runs burst indexing first; `prefer_folders` with full-path/suffix matching; keeper disk-existence check; favorite propagation; `total_duplicate_size_gb` in response

### Enhanced Services
- 🌟 **`get_random_items`** - Added `auto_select_burst_favorite` parameter; non-favorite burst members are excluded in the database query before results reach the card

### Bug Fixes
- 🐛 **`get_burst_photos`** - Iterative flood-fill for consistent group membership regardless of reference photo
- 🐛 **EXIF/XMP Rating** - XMP:Rating (written by exiftool, Windows Explorer, Lightroom) now read as fallback when EXIF tag 0x4746 is absent; rating tag type coercion handles `bytes`/`float`/`tuple` returns from PIL
- 🐛 **Non-admin sync subscriptions** - Custom `media_index/subscribe_sync` WebSocket command replaces generic `subscribe_events` (no admin required)

## v1.5 Enhancements Summary

### New Services
- ✨ **`get_related_files`** - Burst detection with time-based and GPS-based filtering
- ✨ **`update_burst_metadata`** - Persist burst review session data to file metadata

### Enhanced Services
- 📅 **`get_random_items`** - Added anniversary mode for "Through the Years" features
  - New parameters: `anniversary_month`, `anniversary_day`, `anniversary_window_days`
  - Cross-year date matching with adjustable window

### Use Cases
- Burst Review - Compare rapid-fire shots taken at same moment
- Through the Years - View photos from same date across all years
- Historical tracking - Burst favorites persist in file metadata

## v1.3 Enhancements Summary

### New Services
- ✨ **`get_ordered_files`** - Sequential file retrieval with configurable ordering

### Enhanced Services
- 📈 **`get_random_items`** - Added `priority_new_files` mode for recent file sampling
- 🔄 **`restore_edited_files`** - Added `file_path` parameter for single-file restore
- 🌍 **`geocode_file`** - Now supports direct lat/lon lookup (not just file_id)

### Performance Improvements
- All blocking I/O operations wrapped in executor jobs (HA 2025.x compatibility)
- Reduced service call logging (changed from WARNING to DEBUG level)
- Optimized EXIF parsing with caching

## v1.4 URI Support

### Complete Media-Source URI Integration

**Automatic URI Construction (v1.4+)**: Media Index automatically constructs `media_source_uri` from your `base_folder` if not explicitly configured. This provides seamless v1.4 upgrade without requiring configuration changes.

**Example automatic construction:**
- Base folder: `/media/Photo/PhotoLibrary`
- Auto-constructed URI: `media-source://media_source/media/Photo/PhotoLibrary`

**Explicit configuration (optional):**
```yaml
sensor:
  - platform: media_index
    name: "PhotoLibrary"
    base_folder: "/media/Photo/PhotoLibrary"
    media_source_uri: "media-source://media_source/media/Photo/PhotoLibrary"  # Optional
```

### Features

- 🔗 **All file-based services** now accept `media_source_uri` parameter as alternative to `file_path`
  - `get_file_metadata`
  - `mark_favorite`
  - `delete_media`
  - `mark_for_edit`
  - `geocode_file`
- 📁 **Folder filtering services** accept media-source URIs in `folder` parameter
  - `get_random_items`
  - `get_ordered_files`
- 📤 **Response items** include `media_source_uri` field alongside `path`
- 🔄 **Automatic conversion** - Backend handles URI ↔ path translation transparently
- ✅ **Full backward compatibility** - All existing `file_path` usage continues to work
