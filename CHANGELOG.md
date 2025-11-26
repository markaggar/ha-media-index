# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
