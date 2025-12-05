# Home Assistant Media Index

[![GitHub release (latest by date)](https://img.shields.io/github/v/release/markaggar/ha-media-index)](https://github.com/markaggar/ha-media-index/releases)
[![GitHub](https://img.shields.io/github/license/markaggar/ha-media-index)](LICENSE)
[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)

A custom Home Assistant integration that indexes media files (images and videos) from local folders, extracts EXIF/metadata, provides geocoding, and offers services for random media selection, favorites management, and file operations. Specifically designed for the [Home Assistant Media Card](https://github.com/markaggar/ha-media-card), but can easily be used by any other card, integration or automation/script through Home Assistant Actions (Services) or WebSocket API calls.

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

### 🎲 Random & Sequential Media Selection
- **Smart random selection** with exclusion tracking
- **Ordered selection** by date, path, filename ascending or descending
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
5. If your Media folders in the front end media browse dialogs are not prefixed by 'media-source://media_source/media', then copy and paste the full media-source:// URI (from the HA Media Card config) that points to the same place as the the base media folder you specified in the previous step. It is critical that these both point to the same folder structure, e.g.: 
   - base media folder: /config/www/local
   - media source Uri: media-source://media_source/local  
8. Configure optional settings (watched folders, EXIF extraction, geocoding)

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

- `get_random_items` - Random media selection (used by Media Card)
- `mark_favorite` - Toggle favorite status
- `delete_media` - Move files to `_Junk` folder
- `mark_for_edit` - Move files to `_Edit` folder
- `get_file_metadata` - Get detailed metadata
- `geocode_file` - Force geocoding
- `scan_folder` - Manual folder scanning

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

### Database Performance
The integration uses an optimized SQLite database that:
- **Responds instantly** to random media requests from your slideshow cards
- **Tracks exclusions** to avoid showing the same photos repeatedly
- **Maintains favorites and ratings** with both database and file metadata
- **Grows efficiently** as your media collection expands

This background processing means your Media Cards can display random photos instantly without scanning folders every time!

## Using with Media Cards

Media Index is designed to work seamlessly with the [Home Assistant Media Card](https://github.com/markaggar/ha-media-card):

- **Instant slideshow loading** - No waiting for folder scans
- **Smart random selection** - Avoids repetition within slideshow sessions  
- **Rich metadata display** - Shows location names, ratings, and EXIF data
- **Interactive controls** - Favorite, delete, and mark for editing directly from the card
- **Background updates** - New photos appear automatically without restart

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

### Multi-Instance Support

✅ **Full multi-instance support with target selectors**

You can configure multiple integration instances (e.g., separate folders for Photos and Videos) and services will work with all instances using target selectors:

**Usage:**
```yaml
service: media_index.restore_edited_files
target:
  entity_id: sensor.media_index_photos_total_files
```

**Benefits:**
- Separate instances for different media collections
- Independent configuration per instance (different watched folders, settings)
- Services can target specific instances or all instances

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
