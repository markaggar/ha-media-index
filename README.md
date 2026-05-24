# Home Assistant Media Index

[![GitHub release (latest by date)](https://img.shields.io/github/v/release/markaggar/ha-media-index)](https://github.com/markaggar/ha-media-index/releases)
[![GitHub](https://img.shields.io/github/license/markaggar/ha-media-index)](LICENSE)
[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)

[![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20A%20Coffee-Support-orange?logo=buy-me-a-coffee)](https://buymeacoffee.com/markaggar)

A custom Home Assistant integration that indexes media files (images and videos) from local folders, extracts EXIF/metadata, provides geocoding, and offers services for random media selection, favorites management, and file operations. Specifically designed for [Media Card](https://github.com/markaggar/ha-media-card), but can easily be used by any other card, integration or automation/script through Home Assistant [Actions (Services)](docs/SERVICES.md) or [WebSocket APIs](docs/DEVELOPER_API.md).

## Features

### 📸 Media Indexing
- **Automatic scanning** of configured media folders
- **File type detection** for images (JPG, PNG, HEIC, etc.) and videos (MP4, MOV, AVI, etc.)
- **Real-time monitoring** with file system watcher for instant updates
- **EXIF metadata extraction** for images (GPS, date taken, camera info)
- **Video metadata extraction** for MP4/MOV files (creation date, GPS coordinates - see installation prerequisites below)

### 🌍 Geocoding
- **Reverse geocoding** of GPS coordinates to location names
- **Smart caching** to minimize API calls (Nominatim)
- **Progressive geocoding** during scans
- **Location hierarchy**: name → city → state → country

### ⭐ Favorites & Ratings
- **Star ratings** (0-5 stars) extracted from EXIF/XMP metadata
- **Automatic favoriting** of 5-star rated files
- **Rating persistence**:
  - ✅ **Images**: Writes back to EXIF/XMP metadata
  - ⚠️ **Videos**: Database-only (file writes disabled - see limitations below)
- **Database tracking** of favorite status

### 🎲 Random & Sequential Media Selection
- **Smart random selection** with exclusion tracking
- **Ordered selection** by date, path, filename ascending or descending
- **Filter by** folder, file type, date range, favorites
- **Anniversary mode** for "Through the Years" features - find photos from the same date across all years
- **Session management** to avoid repetition within slideshow sessions
- **Efficient querying** from SQLite database

### 📸 Burst Detection & Review
- **Burst Indexing** - Automatically or manually scan the library and mark burst photos using customizable time and distance settings
- **Time-based burst detection** - find photos taken within ±N seconds of a reference photo (default ±10 seconds)
- **GPS-based filtering** - match photos by location proximity using Haversine distance (default 50 meters)
- **Automatic fallback** to time-only matching when GPS data unavailable
- **Burst metadata persistence** - save favorite selections and burst counts to file metadata
- **Historical tracking** - burst review data persists even if files are deleted

### 🔍 Duplicate Detection
- **`find_duplicate_files` service** - detects filesystem-level duplicates within burst groups (same `file_size`, `date_taken`, and dimensions)
- **Folder-pair aware** - designates one entire folder as the keeper rather than scattering keepers across both folders
- **`prefer_folder` override** - force a specific folder to always be kept
- **Safe dry-run mode** - previews duplicate groups and a folder-pair summary before deleting anything
- **Auto-delete** - moves all non-keeper duplicates to `_Junk` when confirmed

### 📺 Cast to TV

> **🎬 Works without Media Card** — the cast slideshow services (`start_cast_slideshow` / `stop_cast_slideshow`) are the first Media Index services that run a fully autonomous slideshow without needing any Lovelace card or browser session.

The slideshow and mirror services work with **any HA `media_player` entity** (Chromecast, smart TVs, Fire TV, Roku, etc.):

- **`start_cast_slideshow` service** — start an unattended random slideshow on any HA media player. Supports all the same filters as `get_random_items` (folder, date range, favorites, anniversary mode). For Roku targets, ECP is used automatically for correct orientation and native format support; for other devices, standard `media_player.play_media` is used.
- **`stop_cast_slideshow` service** — stops the slideshow. For Roku targets, also sends `keypress/Home` to dismiss xcast from the screen; for other devices, the cast session is simply stopped.
- **`mirror_to_cast` service** — mirrors a Media Card's navigation to any media player in real-time (card leads, TV follows)
- **`cast_active` / `cast_targets` sensor attributes** — update immediately when any session starts or stops; works regardless of device type. See [Sensors](#sensors).

**Roku-specific services** (ECP-based, require the [xcast channel](https://channelstore.roku.com/details/687485) and Roku HA integration):

- **`roku_ecp_cast` service** — cast a single image or video directly to a Roku TV via xcast ECP (used by [Media Card](https://github.com/markaggar/ha-media-card)'s cast button)
- **`stop_cast` service** — sends an ECP `keypress/Home` to clear the image from the TV after a single direct cast
- **JPEG transcoding** — images re-encoded with standard Huffman tables via Pillow, fixing grey-screen failures on cameras with non-standard JPEG encoding (e.g. Nikon D5100)
- **EXIF orientation correction** — rotated phone photos are physically transposed before serving so the Roku always receives correctly-oriented pixels at the right aspect ratio
- **Short signed stream URLs** — HMAC-signed URL generated per cast; the Roku fetches the image directly from HA, bypassing the 255-character URL length limit of standard HA auth tokens

### �🗑️ File Management
- **Delete media** - moves files to `_Junk` folder
- **Mark for editing** - moves files to `_Edit` folder
- **Database cleanup** - automatically removes deleted files from index

## Using with Media Cards

Media Index is designed to work seamlessly with [Media Card](https://github.com/markaggar/ha-media-card):

- **Instant slideshow loading** - No waiting for folder scans
- **Smart random selection** - Avoids repetition within slideshow sessions  
- **Rich metadata display** - Shows location names, ratings, and EXIF data
- **Interactive controls** - Favorite, delete, and mark for editing directly from the card
- **Background updates** - New photos appear automatically without restart

## Using Without Media Card

The cast slideshow services work entirely standalone — no Lovelace card or browser needed:

- **`start_cast_slideshow`** — start a random photo/video slideshow on any HA media player from an automation or script
- **`stop_cast_slideshow`** — stop the slideshow and clear the screen
- **`mirror_to_cast`** — mirror whatever a Media Card is currently showing to any media player in real-time

These can be triggered from HA automations (e.g. turn on the TV slideshow at a set time), scripts, or the Developer Tools Actions panel directly.

### Blueprint: Auto-start Cast Slideshow on TV

A ready-to-use HA automation blueprint (`blueprints/automation/auto_cast_slideshow.yaml`) starts the cast slideshow automatically when a Roku TV returns to the Home screen and stops it when another app launches or the TV turns off.

- Import via **Settings → Automations → Blueprints → Import Blueprint** using the raw GitHub URL, or copy the file into your HA `config/blueprints/automation/` folder
- All `start_cast_slideshow` parameters are exposed in the blueprint UI — no manual YAML editing required
- Sync group is auto-generated from the media player entity ID (e.g. `media_player.office_tv` → `autocast_office_tv`); the blueprint UI displays the value so you can copy it into a paired Media Card
- Create one automation instance per TV for independent control of multiple TVs

## NAS Utilities

The `NAS_utils/` folder contains standalone Bash scripts for Synology NAS (or any Linux host with Docker and exiftool) to prepare your media library before or alongside indexing.

### `fix_videos.sh`

Normalises video files in a single Docker QSV hardware-encode pass:
- **Rotation fix** — re-encodes misoriented portrait videos (EXIF rotation 90°/270°) so they display correctly in every player
- **Format conversion** — converts WMV, AVI, MTS, and MOV files to MP4
- **Codec normalisation** — re-encodes non-browser-safe video codecs to HEVC (default) or H.264; transcodes non-AAC audio to AAC
- Incremental scanning with per-folder state files; originals preserved via backup-root or `_hp` rename
- See [`NAS_utils/FIX_VIDEOS.md`](NAS_utils/FIX_VIDEOS.md) for full documentation

### `gps_tag.sh`

Backfills GPS coordinates into photos and videos that lack them by matching timestamps against nearby GPS-enabled files (e.g. phone photos taken at the same time):
- Supports auto mode, explicit donor folders, DSLR recursive backfills, and merged multi-phone donor caches
- Writes GPS to EXIF (JPG) and QuickTime user-data atoms (MP4/MOV)
- See [`NAS_utils/GPS_TAG.md`](NAS_utils/GPS_TAG.md) for full documentation

## Installation

### HACS (Recommended)

[![Open in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=markaggar&repository=ha-media-index&category=integration)

or

1. Open HACS in Home Assistant
2. Click "Integrations"
3. Search for 'Media Index'
4. Click "Install"
5. Restart Home Assistant

### Manual
1. Copy the `custom_components/media_index` folder to your Home Assistant `custom_components` directory
2. Restart Home Assistant

## How It Works

The Media Index integration runs in the background to keep your media organized and accessible:

### Initial Indexing
When first set up, the integration scans your configured folders and:
- **Discovers all media files** (images and videos) in your folders
- **Extracts metadata** like date taken, GPS coordinates, camera settings, and star ratings
- **Stores everything in a local database** for fast access during slideshows

### Real-Time Monitoring  
After initial setup, the integration watches for changes:
- **New files added** → Automatically indexed and added to database
- **Files moved/deleted** → Database updated to reflect changes
- **Files edited** → Re-scanned to pick up metadata changes

### Geocoding (Location Names)
For photos with GPS coordinates, the integration gradually adds location names:
- **Rate limited to 1 request per second** to respect Nominatim API limits
- **Works progressively** during scans - doesn't slow down initial indexing
- **Caches results** to avoid repeated API calls for the same coordinates
- **Provides location hierarchy** from specific place names to country level
- **Language support**: Uses Home Assistant's configured language by default
  - Location names are cached permanently once geocoded
  - Existing files keep their original language
  - Only new files or manual `geocode_file` service calls get the current language setting
  - To update all files to a new language: Use `geocode_file` service individually or clear database and re-scan

### Video Metadata Extraction (GPS, Date):
The integration uses `pymediainfo` (Python package) which requires the `libmediainfo` system library. Simply enable the `auto_install_libmediainfo` option in integration configuration. The integration will automatically install the library during setup if it's missing - no restart or reload needed! Video metadata extraction is available immediately when the integration finishes loading. ⚠️ **Note**: After each Home Assistant core upgrade, the library will be automatically reinstalled during the next restart (the option stays enabled).

### Database Performance
The integration uses an optimized SQLite database that:
- **Responds instantly** to random media requests from your slideshow cards
- **Tracks exclusions** to avoid showing the same photos repeatedly
- **Maintains favorites and ratings** with both database and file metadata
- **Grows efficiently** as your media collection expands

This background processing means your Media Cards can display random photos instantly without scanning folders every time!

## Configuration

1. Go to **Settings** → **Devices & Services**
2. Click **Add Integration**
3. Search for "Media Index"
4. Enter your base media folder path (e.g., `/media/Photos`)
5. If your Media folders in the front end media browse dialogs are not prefixed by 'media-source://media_source/media', then copy and paste the full media-source:// URI (from the HA Media Card config) that points to the same place as the base media folder you specified in the previous step. It is critical that these both point to the same folder structure, e.g.: 
   - base media folder: /config/www/local
   - media source URI: media-source://media_source/local  
6. Configure optional settings (watched folders, scan frequency, EXIF extraction, geocoding, libmediainfo install, burst indexing)

💡 **Multi-Instance Support:** You can add multiple instances with different base folders (e.g., one for Photos, one for Videos) by repeating this process with different folder paths.

### Watched Folders Configuration

**⚠️ IMPORTANT: Watched folders must be specified as RELATIVE paths from your base folder.**

Watched folders allow you to limit file system monitoring to specific subfolders within your base folder. If left empty, the entire base folder is watched.

**Format:** Comma-separated list of relative folder paths

**Example:**

If your base folder is: `/media/Photo/OneDrive`

And you want to watch:
- `/media/Photo/OneDrive/Mark-Pictures/Samsung Gallery/DCIM/Camera`
- `/media/Photo/OneDrive/Tanya-Pictures/Samsung Gallery/DCIM/Camera`

**✅ CORRECT Configuration:**
```
Mark-Pictures/Samsung Gallery/DCIM/Camera, Tanya-Pictures/Samsung Gallery/DCIM/Camera
```

**❌ INCORRECT - Do NOT use absolute paths:**
```
/media/Photo/OneDrive/Mark-Pictures/Samsung Gallery/DCIM/Camera
```
*This will fail because `os.path.join()` treats paths starting with `/` as absolute and ignores the base folder.*

**❌ INCORRECT - Do NOT include base folder in path:**
```
media/Photo/OneDrive/Mark-Pictures/Samsung Gallery/DCIM/Camera
```
*This will create an invalid path like `/media/Photo/OneDrive/media/Photo/OneDrive/...`*

**Notes:**
- Paths are relative to your base folder
- Spaces in folder names are supported
- Leading/trailing whitespace is automatically trimmed
- Quotes are not needed (they're stripped automatically)
- The integration scans the entire base folder regardless, but watches only specified folders for real-time updates

### Scan Schedule & Burst Group Indexing Options

The Media Index integration configuration provides options for controlling how often your media library is scanned for changes and when and how burst groups are indexed. 

#### Scan Schedule
- **Scan schedule** determines when/how often the integration performs a full scan of your media folders. More frequent scans keep your library up to date but can increase CPU and disk usage, especially with large collections.

#### Automatic Burst Group Indexing
These options keep burst group (multiple images created at the same time and place) data current without manual service calls:
- **auto_burst_index** (bool, default `false`): Master enable. When `false`, all automatic burst indexing is disabled.
- **burst_auto_index_interval_hours** (int, default `24`): Minimum hours between automatic burst re-indexing of watched folder. Prevents excessive re-indexing when many files arrive in quick succession. If no new files are detected in the watch folders when the interval is ended, burst re-indexing will not occur.
- **burst_index_after_scan** (bool, default `false`): If enabled, a full-library burst re-index is performed after each scheduled scan completes. Useful for libraries managed by scheduled import scripts.

**Best practice:** For most users, enable `auto_burst_index` with a daily or weekly scan schedule and leave `burst_index_after_scan` disabled unless you need full-library reindexing after every scan.

See the integration options panel in Home Assistant for descriptions and recommended defaults for each setting.

### Reconfiguration

After setup, you can reconfigure options via:
**Settings** → **Devices & Services** → **Media Index** → **Configure**

## Key Service for End Users

### `media_index.restore_edited_files`

**⭐ Most Important Service** - Move files from `_Edit` folder back to their original locations after editing.

When you use the Media Card's "Mark for Edit" button, files are moved to an `_Edit` folder for correction. After making your edits, run this service to restore them to their original locations.

**Usage:**
```yaml
service: media_index.restore_edited_files
```

**Recommendation:** Run this service periodically (weekly/monthly) as part of your media management workflow.

## All Available Services

The integration provides additional services for advanced use cases and Media Card integration. See [SERVICES.md](docs/SERVICES.md) for complete documentation of all available services including:

## Sensors

The integration creates one sensor per configured entry. Most people never look at the attributes, but they are useful for automations and dashboards.

### `sensor.media_index_{entry_name}_total_files`

**State:** Total number of indexed files (images + videos)

**Attributes:**

| Attribute | Description |
|---|---|
| `scan_status` | Current scan state: `idle`, `scanning`, or `error` |
| `last_scan_time` | ISO timestamp of the last completed scan |
| `total_folders` | Number of distinct folders in the index |
| `total_images` | Number of indexed image files |
| `total_videos` | Number of indexed video files |
| `watched_folders` | List of folders being monitored for changes |
| `media_path` | Base media path for this integration instance |
| `media_source_uri` | `media-source://` URI for the base path |
| `libmediainfo_available` | `true` if libmediainfo is installed (required for video GPS/date extraction) |
| `cache_size_mb` | SQLite database file size in MB |
| `geocode_enabled` | `true` if reverse geocoding is configured |
| `geocode_cache_entries` | Number of cached geocode results |
| `geocode_cache_hit_rate` | Cache hit percentage (reduces Nominatim API calls) |
| `files_with_location` | Number of files with GPS coordinates |
| `geocode_attribution` | Required OpenStreetMap attribution text |
| `cast_active` | `true` if any cast slideshow session is currently running |
| `cast_targets` | List of `media_player` entity IDs with active cast sessions |

The `cast_active` and `cast_targets` attributes update immediately (no polling) when a session starts or stops, making them suitable for automation triggers — for example, dimming lights when a slideshow starts.

## Performance

- **SQLite database** for fast queries
- **Geocoding cache** reduces API calls by 90%+
- **File watcher** provides instant updates without full rescans
- **Indexed queries** for sub-millisecond random selection
- **Exclusion tracking** prevents repetition in 100+ item slideshows

## Troubleshooting

### Logs

Check Home Assistant logs for media_index errors:
```
Settings → System → Logs
```

Filter for `media_index` component.

### Common Issues

**No files found after scan:**
- Verify folder paths are accessible from HA container
- Check file permissions
- Ensure folder paths don't have trailing slashes

**Geocoding not working:**
- Check internet connectivity
- Verify Nominatim API is accessible
- Review rate limiting (max 1 request/second)

**File watcher not detecting changes:**
- Ensure `enable_watcher` is enabled in config
- Check that watchdog library is installed
- Network shares may not support file system events

### Database Location

Database file: `config/.storage/media_index_{entry_id}.db`

To reset the database, delete this file and trigger a rescan.

## Limitations & Known Issues

### Video Rating Persistence

**⚠️ Video ratings are stored in the database only, not written to MP4 files.**

**Why?**
- `exiftool` (required for Windows-compatible rating tags) is not accessible in Home Assistant's executor thread context
- `mutagen` library can corrupt MP4 files when writing custom metadata tags
- Safe MP4 metadata writes with `exiftool` require re-encoding the entire video (too resource-intensive)

**Workaround:**
- Video favorites/ratings persist in the SQLite database
- Use the planned export/import services (see Future Enhancements) to backup ratings

### Image Rating Persistence

✅ **Image ratings ARE written to EXIF/XMP metadata** using `piexif` and work reliably.

## Future Enhancements

### Planned Services

#### `media_index.export_ratings`
Export all ratings to a portable JSON/CSV file for backup or migration.

**Use cases:**
- Backup before database reset
- Migrate to another Home Assistant instance
- Share rating data with external applications

#### `media_index.import_ratings`
Import ratings from an exported file and apply to matching files.

**Features:**
- Match files by path or hash
- Merge with existing ratings
- Update database and EXIF (for images)

## Developer Integration

For developers creating cards or integrations that consume Media Index services:

- **Service Documentation**: See [SERVICES.md](docs/SERVICES.md) for complete API reference
- **WebSocket API**: See [docs/DEVELOPER_API.md](docs/DEVELOPER_API.md) for WebSocket usage
- **Multi-Instance Support**: All services support target selectors for multiple instances

## Contributing

Contributions welcome! Please:
1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test thoroughly
5. Submit a pull request

## License

MIT License - see LICENSE file

## Credits

- **Geocoding**: [Nominatim OpenStreetMap](https://nominatim.org/)
- **EXIF Extraction**: [piexif](https://github.com/hMatoba/Piexif)
- **Video Metadata**: [mutagen](https://github.com/quodlibet/mutagen)
- **File Watching**: [watchdog](https://github.com/gorakhargosh/watchdog)

## Support

- **Issues**: [GitHub Issues](https://github.com/markaggar/ha-media-index/issues)
- **Discussions**: [GitHub Discussions](https://github.com/markaggar/ha-media-index/discussions)


