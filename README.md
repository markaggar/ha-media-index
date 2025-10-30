# Media Index for Home Assistant

A custom Home Assistant integration that indexes media files (images and videos) from local folders, extracts EXIF/metadata, provides geocoding, and offers services for random media selection, favorites management, and file operations.

## Features

### 📸 Media Indexing
- **Automatic scanning** of configured media folders
- **File type detection** for images (JPG, PNG, HEIC, etc.) and videos (MP4, MOV, AVI, etc.)
- **Real-time monitoring** with file system watcher for instant updates
- **EXIF metadata extraction** for images (GPS, date taken, camera info)
- **Video metadata extraction** for MP4/MOV files (creation date, GPS coordinates)

### 🌍 Geocoding
- **Reverse geocoding** of GPS coordinates to location names
- **Smart caching** to minimize API calls (Nominatim)
- **Progressive geocoding** during scans
- Location hierarchy: name → city → state → country

### ⭐ Favorites & Ratings
- **Star ratings** (0-5 stars) extracted from EXIF/XMP metadata
- **Automatic favoriting** of 5-star rated files
- **Rating persistence**:
  - ✅ **Images**: Writes back to EXIF/XMP metadata
  - ⚠️ **Videos**: Database-only (file writes disabled - see limitations below)
- **Database tracking** of favorite status

### 🎲 Random Media Services
- **Smart random selection** with exclusion tracking
- **Filter by** folder, file type, date range, favorites
- **Session management** to avoid repetition within slideshow sessions
- **Efficient querying** from SQLite database

### 🗑️ File Management
- **Delete media** - moves files to `_Junk` folder
- **Mark for editing** - moves files to `_Edit` folder
- **Database cleanup** - automatically removes deleted files from index

## Installation

### HACS (Recommended)
1. Open HACS in Home Assistant
2. Click "Integrations"
3. Click the three dots in the top right and select "Custom repositories"
4. Add `https://github.com/markaggar/ha-media-index` as an Integration
5. Click "Install"
6. Restart Home Assistant

### Manual
1. Copy the `custom_components/media_index` folder to your Home Assistant `custom_components` directory
2. Restart Home Assistant

## Configuration

1. Go to **Settings** → **Devices & Services**
2. Click **Add Integration**
3. Search for "Media Index"
4. Enter your base media folder path (e.g., `/media/Photos`)
5. Configure optional settings (watched folders, EXIF extraction, geocoding)

### Reconfiguration

After setup, you can reconfigure options via:
**Settings** → **Devices & Services** → **Media Index** → **Configure**

## Services

### `media_index.get_random_items`

Get random media files from the index.

**Parameters:**
- `count` (optional, default: 10): Number of items to return
- `folder` (optional): Filter by specific folder
- `file_type` (optional): Filter by `image` or `video`
- `date_from` (optional): ISO date string (YYYY-MM-DD)
- `date_to` (optional): ISO date string (YYYY-MM-DD)
- `favorites_only` (optional): Return only favorited items

**Returns:** List of media items with metadata

**Example:**
```yaml
service: media_index.get_random_items
data:
  count: 20
  file_type: image
  favorites_only: true
```

### `media_index.mark_favorite`

Mark a file as favorite (writes to database and EXIF).

**Parameters:**
- `file_path` (required): Full path to media file
- `is_favorite` (optional, default: true): Favorite status

**Example:**
```yaml
service: media_index.mark_favorite
data:
  file_path: /media/photo/PhotoLibrary/sunset.jpg
  is_favorite: true
```

### `media_index.delete_media`

Delete a media file (moves to `_Junk` folder).

**Parameters:**
- `file_path` (required): Full path to media file

### `media_index.mark_for_edit`

Mark a file for editing (moves to `_Edit` folder).

**Parameters:**
- `file_path` (required): Full path to media file

### `media_index.get_file_metadata`

Get detailed metadata for a specific file.

**Parameters:**
- `file_path` (required): Full path to media file

**Returns:** Complete metadata including EXIF, location, and ratings

### `media_index.geocode_file`

Force geocoding of a file's GPS coordinates.

**Parameters:**
- `file_path` (required): Full path to media file

### `media_index.scan_folder`

Trigger a manual scan of media folders.

**Parameters:**
- `folder_path` (optional): Specific folder to scan (defaults to all watched folders)
- `force_rescan` (optional, default: false): Re-extract metadata for existing files

## Sensors

The integration creates the following sensors for each configured entry:

### `sensor.media_index_{entry_name}_total_files`

**State:** Total number of files in database

**Attributes:**
- `scan_status`: Current scan status (idle, scanning, error)
- `last_scan_time`: Timestamp of last scan
- `total_folders`: Number of watched folders
- `geocoded_files`: Number of files with location data
- `favorited_files`: Number of favorited files

## Architecture

### Components

**`scanner.py`**
- Media file discovery and indexing
- EXIF/video metadata extraction
- Geocoding integration
- Scan orchestration

**`cache_manager.py`**
- SQLite database operations
- Query optimization
- CRUD operations for files, EXIF data, geocoding cache

**`exif_parser.py`**
- Image EXIF metadata extraction (PIL/piexif)
- GPS coordinate parsing
- Date/time extraction
- Rating read/write

**`video_parser.py`**
- Video metadata extraction (mutagen)
- MP4/MOV support
- GPS coordinate extraction from QuickTime atoms
- Creation date extraction

**`geocoding.py`**
- Nominatim API integration
- Rate limiting (1 req/sec)
- Location hierarchy extraction
- Error handling and retries

**`watcher.py`**
- File system monitoring (watchdog)
- Real-time event handling
- Automatic re-indexing on changes

### Data Flow

```
File System → Scanner → EXIF/Video Parser → Cache Manager → Database
                ↓
            Geocoding Service → Geocode Cache
```

## Frontend Integration

Media Index is designed to work with the [ha-media-card](https://github.com/markaggar/ha-media-card) custom Lovelace card for displaying media slideshows.

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

#### `media_index.restore_edited_files`
Move files from `_Edit` folder back to their original locations.

**How it works:**
- Track original file paths when moving to `_Edit`
- Store move history in database or separate log file
- Service reads history and restores files to original locations

**Use cases:**
- Complete editing workflow
- Bulk restore after batch editing
- Undo accidental moves

## Development

### Deployment Script

Use the included deployment script for development:

```powershell
cd ha-media-index
.\scripts\deploy-media-index.ps1 `
    -VerifyEntity "sensor.media_index_..._total_files" `
    -DumpErrorLogOnFail `
    -AlwaysRestart
```

This script:
1. Copies files to HA `custom_components/`
2. Validates HA configuration
3. Restarts Home Assistant
4. Verifies integration loaded successfully
5. Captures error logs on failure

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

- **Issues**: [GitHub Issues](https://github.com/markaggar/media-index/issues)
- **Discussions**: [GitHub Discussions](https://github.com/markaggar/media-index/discussions)
