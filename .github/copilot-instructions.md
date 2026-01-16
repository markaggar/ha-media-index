# Media Index Integration

A Home Assistant custom integration for indexing and querying media files with EXIF metadata extraction, geocoding, and database caching.

## Project Overview

**Current Version**: v1.5.10
**Status**: Production ready
**Type**: Home Assistant Custom Integration (Python)

### Critical Rules

**🚨 DO NOT UPDATE VERSION NUMBERS IN THE MANIFEST.JSON OR SOURCE FILES DIRECTLY! YOU MUST ASK FOR PERMISSION FIRST! 🚨**

Version numbers are only updated when creating official releases. Changes accumulate in the current version until release time.

## Project Structure

- `custom_components/media_index/` - Main integration code
  - `__init__.py` - Integration setup, service registration, config flow
  - `cache_manager.py` - SQLite database operations, queries
  - `sensor.py` - Sensor entity for scan status
  - `scanner.py` - File scanning and EXIF extraction
  - `watcher.py` - File system watcher for automatic updates
  - `geocode.py` - Reverse geocoding for GPS coordinates
  - `const.py` - Constants and service names
  - `manifest.json` - Integration metadata and dependencies
- `scripts/` - Deployment and testing scripts
  - `deploy-media-index.ps1` - **CRITICAL**: Use this for all deployments
- `docs/` - User documentation
- `dev-docs/` - Architecture and implementation notes

## Architecture

### Core Components

1. **CacheManager** (`cache_manager.py`)
   - SQLite database operations
   - Two tables: `media_files` (file metadata) and `exif_data` (EXIF details)
   - Compound cursor pagination for stable ordered queries
   - Foreign keys enabled with `PRAGMA foreign_keys = ON`

2. **Scanner** (`scanner.py`)
   - EXIF extraction from images (piexif)
   - Video metadata extraction (pymediainfo - requires libmediainfo system library)
   - Batch scanning with progress reporting
   - Async file operations using executor threads

3. **MediaWatcher** (`watcher.py`)
   - File system monitoring using watchdog
   - Event batching and throttling (2s delay, 50 file batches)
   - Thread-safe queue operations with `call_soon_threadsafe`
   - Processes deletions first, then new files, then modifications

4. **Geocoding** (`geocode.py`)
   - Reverse geocoding using Nominatim
   - SQLite cache for API results
   - Batch statistics tracking (100 lookup batches)
   - Language support (native or HA instance language)

### Database Schema

**NEVER INVENT COLUMN NAMES** - Always check the CREATE TABLE statements in cache_manager.py first!

**media_files table:**
- `id` (INTEGER PRIMARY KEY)
- `path` (TEXT UNIQUE)
- `filename` (TEXT)
- `folder` (TEXT)
- `file_size` (INTEGER)
- `width`, `height` (INTEGER)
- `duration` (REAL) - video duration in seconds
- `file_type` (TEXT) - image/video file type
- `created_time` (INTEGER) - Unix timestamp when file was created
- `modified_time` (INTEGER) - Unix timestamp when file was last modified
- `orientation` (INTEGER) - EXIF orientation value or normalized rotation
- `last_scanned` (INTEGER) - Unix timestamp when this file was last scanned
- `rating` (INTEGER) - 0-5 stars
- `rated_at` (INTEGER) - Unix timestamp when rating was last updated

**exif_data table:**
- `id` (INTEGER PRIMARY KEY)
- `file_id` (INTEGER FOREIGN KEY → media_files.id ON DELETE CASCADE)
- `date_taken` (TEXT) - ISO 8601 timestamp when the media was captured
- `is_favorited` (INTEGER) - 0/1 boolean (default 0)
- `camera_make`, `camera_model` (TEXT)
- `iso`, `aperture`, `focal_length`, `shutter_speed` (TEXT/REAL)
- `latitude`, `longitude`, `altitude` (REAL)
- `location_name`, `location_city`, `location_state`, `location_country` (TEXT) - geocoded location
- `focal_length_35mm`, `exposure_compensation`, `metering_mode`, `white_balance`, `flash` (TEXT)
- `rating` (INTEGER) - 0-5 stars

## Development Guidelines

### CRITICAL: Code Verification Before Writing

**Database Queries:**
- **NEVER invent column names** - grep for CREATE TABLE statements
- **NEVER assume method exists** - search for actual method definition
- **ALWAYS check existing queries** - find similar patterns before writing new code

**Example Mistake:**
```python
# ❌ WRONG - invented column names
SELECT m.extension, m.size, m.date_modified FROM media_files m

# ✅ CORRECT - actual column names
SELECT m.filename, m.file_size, m.modified_time FROM media_files m
```

**Service Names:**
- Check `const.py` for all SERVICE_* constants
- Check `services.yaml` for actual service definitions
- Never invent service names or parameters

**Before Writing ANY Code:**
1. Read the actual implementation you're modifying
2. Search for similar patterns (grep_search)
3. Verify schema/structure by reading relevant files
4. Check existing tests or usage examples
5. Only then write code matching actual patterns

### Logging Best Practices

**Per-Item vs Batch Logging:**
- Use `DEBUG` level for per-item operations (file processing, individual queries)
- Use `INFO` level for batch summaries (e.g., "Processing 50 new files")
- Use `WARNING` for recoverable errors
- Use `ERROR` for failures requiring attention

**Example:**
```python
# ❌ WRONG - logs INFO for every file (causes "logging too frequently")
for file_path in files:
    _LOGGER.info("Processing %s", file_path)

# ✅ CORRECT - summary at INFO, details at DEBUG
_LOGGER.info("Processing %d files", len(files))
for file_path in files:
    _LOGGER.debug("Processing %s", file_path)
```

### Common Pitfalls

1. ❌ Using `vol.Coerce()` in schemas - causes type conversion issues with JSON
2. ❌ Logging at INFO for per-item operations - causes log spam
3. ❌ Not using `call_soon_threadsafe` in watchdog handlers - race conditions
4. ❌ Blocking I/O in async context - use `run_in_executor`
5. ❌ Inventing database column names - always check CREATE TABLE
6. ❌ Not checking if method exists before calling - grep first
7. ❌ Updating version numbers without permission - ASK FIRST

## Deployment

**🚨 CRITICAL: ALWAYS USE THE DEPLOYMENT SCRIPT 🚨**

**NEVER** use individual PowerShell commands for deployment!

### Standard Deployment

```powershell
cd C:\Users\marka\ha-media-index

.\scripts\deploy-media-index.ps1 `
    -VerifyEntity "sensor.media_index_media_photo_photolibrary_total_files" `
    -DumpErrorLogOnFail
```

### Force Restart

```powershell
.\scripts\deploy-media-index.ps1 `
    -VerifyEntity "sensor.media_index_media_photo_photolibrary_total_files" `
    -DumpErrorLogOnFail `
    -AlwaysRestart
```

### What the Script Does

1. Copies changed files to HA `custom_components/`
2. Validates HA configuration
3. Restarts Home Assistant via REST API
4. Waits for HA to come back online
5. Verifies integration loaded by checking sensor
6. Validates sensor attributes are populated
7. Captures error log on failure
8. Returns exit code 0 (success) or 2 (failure)

### Environment Setup

**See `LOCAL_SETUP.md` in the repository root for actual machine-specific values**

```powershell
# Set in PowerShell profile for your HA test instance
$env:HA_BASE_URL = "http://YOUR_HA_IP:8123"
$env:HA_TOKEN = "YOUR_LONG_LIVED_ACCESS_TOKEN"
$env:HA_VERIFY_ENTITY = "sensor.media_index_YOUR_INSTANCE_NAME_total_files"
$env:WM_SAVE_ERROR_LOG_TO_TEMP = "1"
```

### File Locations

**See `LOCAL_SETUP.md` for actual workspace and server paths**

- **Development**: `<your_workspace>\ha-media-index\custom_components\media_index\`
- **HA Server**: `\\YOUR_HA_IP\config\custom_components\media_index\`
- **Cache Database**: `\\YOUR_HA_IP\config\.storage\media_index.db` (auto-created)

## Git Workflow

**CRITICAL: ALL development work must be done on the `dev` branch**

### Branch Protection Rules

- **NEVER push directly to `master` branch**
- Master is for stable releases only
- All development on `dev` branch

### Development Workflow

```powershell
# Work on dev branch
git checkout dev
git pull origin dev
# Make changes
git add .
git commit -m "fix: description"
git push origin dev
```

### Merge to Master (Releases Only)

```powershell
# Create PR from dev to master
# After review and testing, merge via GitHub
# Tag release on master branch
```

## Service Architecture

### Service Registration

Services are registered in `__init__.py` via `_register_services()` function.

**Service Categories:**
1. **Scanning**: `scan_files`, `scan_file`
2. **Queries**: `get_random_items`, `get_ordered_files`, `get_related_files`
3. **Metadata**: `update_favorite_status`, `update_burst_metadata`
4. **Geocoding**: `geocode_file`, `geocode_coordinates`
5. **Maintenance**: `cleanup_database`, `install_libmediainfo`

### Query Services

**get_random_items:**
- Random selection from database
- Supports filters (folder, date range, favorites, anniversaries)
- Queue size configurable (default 100)
- Priority for new files with exhaustion detection

**get_ordered_files:**
- Sequential/ordered queries with cursor pagination
- Compound cursor: `(after_value, after_id)` for stable ordering
- Supports: date_taken, filename, path, modified_time
- Direction: asc/desc

**get_related_files:**
- Burst mode: time+GPS proximity detection
- Anniversary mode: same date across years
- Favorites mode: favorited files in same folder

## Testing

### Manual Testing Scripts

Located in `scripts/`:
- `test-*.ps1` - Various feature tests
- `check-*.ps1` - Database verification scripts

### Testing Workflow

1. Make changes to source files
2. Deploy using `deploy-media-index.ps1`
3. Check HA logs for errors
4. Test services via Developer Tools → Services
5. Verify database changes with check scripts

## Common Tasks

### Adding a New Service

1. Define service constant in `const.py`
2. Add schema in `__init__.py` (SERVICE_*_SCHEMA)
3. Implement handler function in `__init__.py`
4. Add to `_register_services()` function
5. Document in `docs/SERVICES.md`
6. Update CHANGELOG.md (WITHOUT version bump)

### Adding Database Column

1. Add column to CREATE TABLE in `cache_manager.py`
2. Add migration in `_ensure_schema()` method
3. Update INSERT/UPDATE queries to include new column
4. Test on fresh install and existing database
5. Update CHANGELOG.md (WITHOUT version bump)

### Fixing a Bug

1. Verify the bug on HADev deployment
2. Search for similar code patterns
3. Make fix in source files
4. Deploy with script and verify fix
5. Update CHANGELOG.md Fixed section (WITHOUT version bump)
6. Commit and push to dev branch

## Version Management

**CRITICAL RULE:** Only update version when creating a release!

### When Making Changes

1. Make code changes
2. Update CHANGELOG.md under **current version** (e.g., v1.5.10)
3. Add to appropriate section (Added/Changed/Fixed)
4. **DO NOT** update version in manifest.json
5. Commit with descriptive message

### When Creating Release

1. **ASK FOR PERMISSION** to bump version
2. Update version in manifest.json
3. Update CHANGELOG.md date if needed
4. Commit: "Bump version from X.Y.Z to X.Y.Z+1"
5. Merge dev to master
6. Tag release on master
7. Create GitHub release

## Resources

- Main repo: https://github.com/markaggar/ha-media-index
- Used by: ha-media-card (frontend Lovelace card)
- HA Docs: https://developers.home-assistant.io/docs/creating_integration_manifest
