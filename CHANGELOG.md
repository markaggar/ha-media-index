# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.5.0] - 2025-12-06

### Added

- **Burst Detection Mode**: New `mode: burst` parameter for `get_related_files` service
  - Time-based filtering: ±N seconds around reference photo's timestamp (default ±2 minutes)
  - GPS-based filtering: Haversine distance calculation for location matching (default 50 meters)
  - Automatic fallback to time-only matching when GPS data unavailable
  - Configurable sort order: chronological (`time_asc`) or reverse (`time_desc`)
  - Returns `seconds_offset` and `distance_meters` for each matching photo
  - Same-year restriction prevents cross-year matches for burst detection
  - Designed for Media Card v5.5+ "Burst Review" feature
  - Example use case: Compare rapid-fire shots to select the best photo for keeping

- **Burst Metadata Persistence**: New `update_burst_metadata` service for tracking burst review sessions
  - Writes `burst_favorites` (JSON array of favorited filenames) to all files in a burst group
  - Writes `burst_count` (integer) to record total files in burst at review time
  - Enables historical tracking even if files are later deleted or parameters change
  - New database columns: `exif_data.burst_favorites` (TEXT), `exif_data.burst_count` (INTEGER)
  - Database migration automatically adds columns to existing installations

- **Anniversary Mode Support**: Enhanced `get_random_items` service for "On This Day" features
  - New parameters: `anniversary_month`, `anniversary_day`, `anniversary_window_days`
  - Wildcards supported: Use `"*"` for any month/day to find all photos matching pattern
  - Window expansion: `anniversary_window_days` adds ±N days tolerance around target date
  - SQL date component matching: `strftime('%m', ...)` and `strftime('%d', ...)` for cross-year queries
  - Designed for Media Card v5.5+ "Through the Years" feature showing photos from same date across all years

- **Database Cleanup Service**: New `cleanup_database` service for maintenance
  - Validates all database entries against filesystem
  - Removes entries for files that no longer exist
  - Dry-run mode by default for safe testing (`dry_run: true`)
  - Returns list of stale files found/removed with counts
  - Solves 404 errors from moved/deleted files
  - Useful after bulk file operations outside Home Assistant

- **Favorites Index**: Added database index on `is_favorited` column
  - Improves query performance when using `favorites_only` filter
  - Optimizes Media Card v5.5+ filtering features

### Fixed

- **Database Bloat Prevention**: Added automatic SQLite VACUUM operations
  - VACUUM now runs automatically after `cleanup_database` service completes (when `dry_run=false`)
  - Weekly automatic VACUUM scheduled to reclaim space from deleted/updated rows
  - Fixes database file growing indefinitely due to SQLite's copy-on-write behavior
  - Returns `db_size_before_mb`, `db_size_after_mb`, and `space_reclaimed_mb` in cleanup response
  - Resolves issue where 22MB database held only 172 files due to accumulated ghost data

- **Cleanup Database Service Schema**: Fixed schema to allow `entity_id` parameter
  - Added `extra=vol.ALLOW_EXTRA` to service schema
  - Resolves "extra keys not allowed @ data['entity_id']" error when using target selector
  - Service now works correctly with both target selector and direct service calls

- **Video Metadata Extraction** (Enhanced in v1.5.0)
  - **NEW**: Integrated `pymediainfo` library for comprehensive video metadata extraction
  - **Extracts from pymediainfo**:
    - DateTime: `encoded_date`, `tagged_date`, `recorded_date`, `mastered_date` fields
    - GPS: `recorded_location` field (Android/Samsung) or `xyz` field (other formats)
    - Dimensions: `width` and `height` from Video track (now properly saved to database)
    - Duration: Converted from milliseconds to seconds (now properly saved to database)
    - Rating: 0-5 star rating from General track
  - **Fallback methods**: 
    - Rating: mutagen MP4 tags (iTunes-style rating)
    - DateTime: Filename patterns → filesystem timestamps
  - **System Requirements**: Requires `libmediainfo` system library (see README Prerequisites)
  - **Fixed**: Video dimensions and duration now saved to `media_files` table (previously only in exif_data)
  - Successfully tested on Android, Samsung, and iPhone videos
  - Tested datetime formats: "2020-05-16 03:37:57 UTC", "2025-07-06 01:28:44"
  - All logging changed from info/warning to debug level to reduce system log noise
  - Prevents null `date_taken` values that caused incorrect sort order in sequential mode
  - Fixes infinite video replay loop when videos with null dates appeared at position 1

### Service Parameters

**get_related_files** (burst mode):
- `mode` (required): Set to `"burst"` for burst detection
- `reference_path` or `media_source_uri` (required): Reference photo path or URI
- `time_window_seconds` (optional, default 120): Time window in seconds (±)
- `prefer_same_location` (optional, default true): Enable GPS proximity filtering
- `location_tolerance_meters` (optional, default 50): Maximum GPS distance for matching
- `sort_order` (optional, default "time_asc"): Result ordering

**update_burst_metadata**:
- `burst_files` (required): List of all file URIs in the burst group
- `favorited_files` (required): List of file URIs marked as favorites
- Returns: `files_updated`, `burst_count`, `favorites_count`

### Changed

- **get_related_files Service**: Now includes `is_favorited` and `rating` fields in burst results
- **get_related_files Service**: Now includes `media_source_uri` for all returned items
- **Service Schema**: Added `extra=vol.ALLOW_EXTRA` to allow `entity_id` from target parameter

### Technical Details

- URI to path conversion uses configured `base_folder` and `media_source_uri` settings
- Favorited filenames stored (not full paths) for portability
- Empty favorites stored as `NULL` rather than empty JSON array
- All files in burst receive same metadata regardless of individual favorite status

## [1.4.0] - 2025-11-25

### Added

- **Complete Media-Source URI Support**: All services now support `media-source://` URIs throughout
  - `get_random_items` and `get_ordered_files` accept URIs for `folder` parameter
  - All file operation services (`get_file_metadata`, `geocode_file`, `mark_favorite`, `delete_media`, `mark_for_edit`) accept `media_source_uri` parameter as alternative to `file_path`
  - Services return both `path` and `media_source_uri` for each item
  - Full backward compatibility maintained - all services still work with `file_path` only

- **Automatic URI Construction**: Integration automatically constructs `media_source_uri` from `base_folder` if not explicitly configured
  - Format: `media-source://media_source` + `base_folder`
  - Example: `/media/Photo/PhotoLibrary` → `media-source://media_source/media/Photo/PhotoLibrary`
  - Simplifies configuration for standard paths under `/media`

- **Sensor Attribute Exposure**: `media_source_uri` configuration exposed as sensor state attribute for verification and debugging

- **Enhanced Filter Support**: Advanced filtering capabilities for Media Card v5.3.0+ integration
  - `favorites_only` filter parameter in `get_random_items` service
  - Date range filtering using EXIF `date_taken` with `created_time` fallback
  - Proper null handling for date range filters

### Fixed

- **Startup Scan Configuration**: Startup scan now properly respects `scan_on_startup` configuration setting
  - Previously defaulted to `True` even when explicitly set to `False`
  - Added logging when startup scan is disabled by configuration

- **Config Flow**: Fixed deprecated `config_entry` assignment in OptionsFlow
  - Removed explicit `__init__` method - parent class provides `self.config_entry` automatically
  - Eliminates Home Assistant 2025.12+ deprecation warning

- **Home Assistant Startup Blocking**: Scan no longer blocks HA startup process
  - Initial scan triggered after `EVENT_HOMEASSISTANT_STARTED` instead of during setup phase
  - Eliminates "Setup timed out for stage 2" errors
  - HA starts immediately, scan runs in background after startup complete

- **Logging Optimization**: Dramatically reduced log noise in production
  - Removed INFO-level logging from service handlers (called on every slideshow advance)
  - Removed debug logs from cache manager hot paths
  - Removed duplicate log messages
  - Cleaner logs focused on important events only

### Changed

- **URI-Based Workflow**: Backend now handles all URI ↔ filesystem path conversions
  - Frontend (Media Card) uses URIs exclusively
  - Cleaner separation of concerns aligned with Home Assistant's media-source system

### Documentation

- Added comprehensive `media_source_uri` parameter documentation for all services
- Documented when explicit URI configuration is required (custom `media_dirs` mappings)
- Added configuration examples for standard and custom media directory setups

### Migration Notes

**Automatic Migration**: No configuration changes required for existing users. The integration automatically constructs URIs from your existing `base_folder` paths.

**When to Configure `media_source_uri` Explicitly**: Only required when using custom `media_dirs` mappings where filesystem path differs from media-source URI path.

Example custom mapping requiring explicit configuration:
```yaml
# configuration.yaml
homeassistant:
  media_dirs:
    local: /config/www/local  # Maps media-source://media_source/local to /config/www/local

# Media Index configuration
sensor:
  - platform: media_index
    name: "LocalPhotos"
    base_folder: "/config/www/local/photos"  # Filesystem path
    media_source_uri: "media-source://media_source/local/photos"  # Must specify - URI differs!
```

For standard paths under `/media`, automatic construction works perfectly:
```yaml
sensor:
  - platform: media_index
    name: "PhotoLibrary"
    base_folder: "/media/Photo/PhotoLibrary"
    # media_source_uri auto-constructed: media-source://media_source/media/Photo/PhotoLibrary
```

## [1.3.1] - 2025-11-XX

### Initial Release

- Database-backed media indexing with EXIF metadata extraction
- GPS location geocoding with native language support
- File system watching with automatic cache updates
- Scheduled scanning (startup, hourly, daily, weekly)
- Services for random/ordered file selection, metadata retrieval, and file management
- SQLite cache with efficient querying
- Home Assistant sensor integration
