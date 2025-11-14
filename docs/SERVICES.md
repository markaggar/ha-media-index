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

## Media Query Services

### `media_index.get_random_items`

Get random media files from the index (used by Media Card).

**Parameters:**
- `count` (optional, default: 1): Number of items to return (1-100)
- `folder` (optional): Filter by specific folder path
- `file_type` (optional): Filter by `image` or `video`
- `date_from` (optional): ISO date string (YYYY-MM-DD)
- `date_to` (optional): ISO date string (YYYY-MM-DD)
- `priority_new_files` (optional, default: false): Prioritize recently scanned files
- `new_files_threshold_seconds` (optional, default: 3600): Seconds threshold for "new" files

**Returns:** List of media items with metadata

**Examples:**
```yaml
# Basic random selection
service: media_index.get_random_items
data:
  count: 20
  file_type: image

# Priority for recent files (v5 feature)
service: media_index.get_random_items
data:
  count: 50
  priority_new_files: true
  new_files_threshold_seconds: 2592000  # 30 days
```

### `media_index.get_ordered_files`

**New in v5** - Get ordered media files with configurable sort field and direction.

**Parameters:**
- `count` (optional, default: 50): Maximum number of files to return (1-1000)
- `folder` (optional): Filter by specific folder path
- `recursive` (optional, default: true): Include subfolders
- `file_type` (optional): Filter by `image` or `video`
- `order_by` (optional, default: `date_taken`): Sort field (`date_taken`, `filename`, `path`, `modified_time`)
- `order_direction` (optional, default: `desc`): Sort direction (`asc` or `desc`)

**Returns:** List of ordered media items with metadata

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
```

### `media_index.get_file_metadata`

Get detailed metadata for a specific file.

**Parameters:**
- `file_path` (required): Full path to media file

**Returns:** Complete metadata including EXIF, location, GPS, and ratings

## File Management Services

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

**Example:**
```yaml
service: media_index.delete_media
data:
  file_path: /media/photo/PhotoLibrary/blurry.jpg
```

### `media_index.mark_for_edit`

Mark a file for editing (moves to `_Edit` folder).

**Parameters:**
- `file_path` (required): Full path to media file

**Example:**
```yaml
service: media_index.mark_for_edit
data:
  file_path: /media/photo/PhotoLibrary/needs_crop.jpg
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
- `latitude` (optional): GPS latitude (alternative to file_id)
- `longitude` (optional): GPS longitude (alternative to file_id)

**Example:**
```yaml
# Geocode by file ID
service: media_index.geocode_file
data:
  file_id: 12345

# Geocode by coordinates
service: media_index.geocode_file
data:
  latitude: 37.7749
  longitude: -122.4194
```

## Service Usage with Media Card

The Media Index services integrate seamlessly with the [Home Assistant Media Card](https://github.com/markaggar/ha-media-card):

- **`get_random_items`** - Used automatically by Media Card for random slideshow mode
- **`get_ordered_files`** - Used automatically by Media Card for sequential slideshow mode (v5)
- **`mark_favorite`** - Called when clicking favorite button on Media Card
- **`delete_media`** - Called when clicking delete button on Media Card
- **`mark_for_edit`** - Called when clicking edit button on Media Card
- **`restore_edited_files`** - Run periodically to restore edited files

## v5 Enhancements Summary

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
