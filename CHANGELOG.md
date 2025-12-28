# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.5.6] - 2025-12-25

## Added

- **File Existence Check Service**: New `check_file_exists` service for lightweight filesystem validation
  - Accepts `file_path` or `media_source_uri` parameter
  - Returns `{"exists": bool, "path": string}` without loading metadata
  - Designed for Media Card v5.6.5+ to validate files before display
  - Eliminates 404 broken image icons by checking filesystem first
  - ~1ms response time vs ~100ms+ for image preload
  - No network request, no image decode - just `os.path.exists()` check
  - **Security**: Path traversal protection - validates all paths are within configured `base_folder`
  - Rejects attempts to probe filesystem outside media collection scope

- **File Watcher Event Throttling**: Prevents resource exhaustion during large sync operations
  - Events now queued and processed in batches instead of immediately
  - Batch delay: 2 seconds to collect events before processing
  - Max batch size: 50 files processed at once
  - Rate limiting: 0.5 second delay between batches to yield control to HA event loop
  - Processes deletions first (fast), then new files, then modifications
  - Background processor auto-stops when no pending events
  - Prevents frontend freezing when database/filesystem are out of sync
  - Reduces log spam (one log line per batch instead of per file)

### Changed

- **File Watcher Behavior**: Disabled when no watched_folders specified
  - Watcher only starts if `watched_folders` list is non-empty
  - Prevents monitoring entire base folder (resource-intensive for large collections)
  - Clear log message explains watcher disabled and recommends scheduled scans
  - For large collections (100K+ files), use watched_folders for specific subfolders or rely on scheduled scans
  - Improves startup performance and reduces resource usage for users without watched folders configured

### Fixed

- **File Watcher Thread Safety**: Fixed race conditions in event queue access (GitHub review feedback)
  - All queue modifications now use `call_soon_threadsafe` from watchdog thread
  - Prevents data corruption when watchdog and asyncio threads access queues concurrently
  - Event deduplication: removes path from other queues when adding to new queue
  - Ensures same file path doesn't get processed multiple times from different queues

- **File Watcher Robustness**: Improved batch processor error handling
  - Added `finally` block to ensure `_is_processing` flag is always reset
  - Consistent rate limiting: always yields to event loop after each iteration
  - Prevents flag stuck at True if exception occurs outside inner try-except

### Technical

- Added event queues: `_pending_new`, `_pending_modified`, `_pending_deleted`
- Implemented `_process_event_batches()` background task with configurable throttling
- Enhanced `stop_watching()` to cancel processor task and clear pending events
- Thread-safe queue access via `call_soon_threadsafe` with lambda functions
- Cross-queue deduplication prevents redundant processing of same file
- See `dev-docs/WATCHER_THROTTLING.md` for implementation details and performance benchmarks

## [1.5.5] - 2025-12-22

### Fixed

- **Video Parser Logging Optimization**: Reduced excessive logging to prevent log spam
  - Removed warning for non-ASCII characters in file paths (normal operation with UTF-8/Unicode)
  - Removed redundant debug logs for GPS field detection and coordinate extraction
  - Removed verbose debug logs for datetime field detection and parsing
  - Removed dimension/duration logging on every video parse
  - Keeps UTF-8/Unicode-safe path handling while removing noisy warnings
  - Resolves "Module is logging too frequently" warnings in Home Assistant logs
  - Prevents hundreds of debug messages during bulk video scanning

## [1.5.4] - 2025-12-21

### Fixed

- **iPhone Video Metadata Extraction**: Fixed date extraction from iPhone .mov files
  - Added support for Apple QuickTime `comapplequicktimecreationdate` field (highest priority for iPhone videos)
  - Parser now handles multiple datetime values separated by " / " (takes first occurrence)
  - Fixed ISO 8601 timezone parsing for formats like "2021-07-10T12:37:11+0200"
  - GPS extraction already working via `comapplequicktimelocationiso6709` field
  - Resolves issue where iPhone videos showed file modification date instead of actual capture date

## [1.5.3] - 2025-12-20

### Changed

- **Streamlined libmediainfo Auto-Install**: Even better - no reload needed!
  - Now installs libmediainfo **during** integration setup if missing and auto-install enabled
  - Integration loads normally with video metadata extraction immediately available
  - No integration reload, no delay, no complexity - it just works
  - Manual `install_libmediainfo` service still auto-reloads for existing installations
  - Quick network check (5s) fails fast when internet is down
  - Reduced timeouts: APK 30s, APT 60s (was 60s/120s)

### Fixed

- **Graceful Scan Abort on Database Closure**: Prevents log spam when integration reloads during scan
  - Detects "no active connection" errors and aborts scan immediately
  - Prevents 1000+ error log entries when database is closed during active scan
  - Rate limits other scan errors to max 10 log entries per scan
  - Clear warning message when scan is aborted due to database closure
  - Resolves "Module is logging too frequently" warnings

- **Blocking I/O Warning**: Fixed blocking call to `Image.open()` in file watcher/manual scan context
  - EXIF extraction now runs in executor thread when scanning individual files
  - Prevents "Detected blocking call to open" warnings in Home Assistant logs
  - Both image (EXIF) and video (pymediainfo) metadata extraction now properly async

- **Timestamp Handling on Linux**: Fixed `created_time` and `modified_time` swap on Linux/Unix systems
  - Now uses `st_birthtime` when available (macOS, BSD)
  - Falls back to `st_ctime` on Linux (which is inode change time, not creation time)
  - `modified_time` always uses `st_mtime` (modification time)
  - Note: On Linux, true file creation time is not available from filesystem
  - Service responses now show correct modification timestamps

### Documentation

- **Geocoding Language Persistence**: Clarified that location names are cached permanently
  - Existing geocoded files retain their original language
  - Only newly scanned files or manual `geocode_file` service calls get new language setting
  - To update all files: Use `geocode_file` service individually or clear database and re-scan
  - This is by design for performance (avoids re-geocoding 1000s of files on every scan)

## [1.5.1] - 2025-12-18

### Added

- **Automatic libmediainfo Installation**: New `auto_install_libmediainfo` configuration option (default: false)
  - Automatically installs libmediainfo system library when enabled via configuration options (no restart required to trigger)
  - Simplifies video metadata extraction setup for Home Assistant OS/Supervised users
  - ~~Creates persistent notification prompting for manual Home Assistant restart after successful installation~~ (Changed in next release: Now automatically reloads integration)
  - ⚠️ **Note**: After each Home Assistant core upgrade, the system library will be automatically reinstalled on next restart (option stays enabled). ~~A new persistent notification will prompt for the additional restart to complete setup.~~ (Changed in next release: Reloads automatically)
  - Manual installation also available via `media_index.install_libmediainfo` service

- **Sensor Attribute**: New `libmediainfo_available` boolean attribute on scan status sensor
  - Shows True/False to indicate if libmediainfo system library is installed and working
  - Useful for monitoring, troubleshooting, and automations
  - Updates with every sensor state change
  - Easy way to verify video metadata extraction capability without checking logs

- **Geocoding Language Support**: Geocoding now respects Home Assistant's configured language
  - When `use_native_language` is disabled, location names are returned in your HA instance's language setting
  - Falls back to English if language not configured
  - Benefits international users who want location names in their preferred language (e.g., German HA → German location names)

### Fixed

- **Performance Optimization**: Geocoding cache statistics now use batched updates
  - Cache hit/miss counters accumulated in-memory and flushed every 100 lookups
  - Additional flush on scan completion to ensure accurate final statistics
  - Reduces database I/O overhead during bulk scanning operations
  - New constant: `GEOCODE_STATS_BATCH_SIZE = 100`

### Technical Details

- Persistent notification ID: `media_index_libmediainfo_restart` (automatically dismisses on restart)
- Installation helper now returns structured status dictionary for service responses
- Geocoding stats batching implemented with in-memory counters: `_geocode_stats_cache_hits`, `_geocode_stats_cache_misses`, `_geocode_stats_counter`

## [1.5.0] - 2025-12-14

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

- **Foreign Key Constraints** 🚨 **CRITICAL DATA INTEGRITY FIX**
  - SQLite foreign key constraints were disabled by default, causing `ON DELETE CASCADE` to never work
  - Added `PRAGMA foreign_keys = ON` during database initialization
  - **Impact**: Prevents orphaned EXIF data accumulation on all new and existing installations
  - **User Action Required**: Existing installations should run `cleanup_database` service to remove accumulated orphans
  - This was a silent data integrity issue affecting all installations since v1.0

- **Orphaned EXIF Data Cleanup**
  - Enhanced `cleanup_database` service to detect and remove orphaned exif_data rows
  - Orphan detection now runs in both dry_run and live modes for visibility
  - Added `orphaned_exif_removed` count in service response
  - Uses optimized single-query deletion instead of separate count + delete
  - **Result**: Test cleanup removed 1,432,402 orphaned rows, reclaimed 232.68 MB from one database
  - Provides public `cleanup_orphaned_exif()` method for proper encapsulation

- **Database Bloat Prevention**: Added automatic SQLite VACUUM operations
  - VACUUM now runs automatically after `cleanup_database` service completes (when `dry_run=false`)
  - Weekly automatic VACUUM scheduled to reclaim space from deleted/updated rows
  - Fixes database file growing indefinitely due to SQLite's copy-on-write behavior
  - Returns `db_size_before_mb`, `db_size_after_mb`, and `space_reclaimed_mb` in cleanup response
  - Added error handling for file size checks (FileNotFoundError on fresh installs)
  - Provides public `vacuum_database()` method for proper encapsulation
  - Resolves issue where 22MB database held only 172 files due to accumulated ghost data

- **Geocode Cache Statistics Tracking**
  - Fixed cache miss tracking to occur at lookup time, not after API call
  - Cache misses now counted even when API calls fail (network errors, rate limits)
  - Provides accurate cache effectiveness statistics regardless of API availability
  - Added singleton pattern comment explaining `CHECK (id = 1)` constraint

- **Cleanup Database Service Schema**: Fixed schema to allow `entity_id` parameter
  - Added `extra=vol.ALLOW_EXTRA` to service schema
  - Resolves "extra keys not allowed @ data['entity_id']" error when using target selector
  - Service now works correctly with both target selector and direct service calls

- **Video Metadata Extraction**
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
