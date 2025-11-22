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
- `folder` (optional): Filter by folder (filesystem path or media-source URI)
- `file_type` (optional): Filter by `image` or `video`
- `date_from` (optional): ISO date string (YYYY-MM-DD) - uses EXIF date_taken if available, falls back to created_time. Null means "no lower limit"
- `date_to` (optional): ISO date string (YYYY-MM-DD) - uses EXIF date_taken if available, falls back to created_time. Null means "no upper limit"
- `favorites_only` (optional, default: false): Only return files marked as favorites
- `priority_new_files` (optional, default: false): Prioritize recently scanned files
- `new_files_threshold_seconds` (optional, default: 3600): Seconds threshold for "new" files
- `recursive` (optional, default: true): Include subfolders

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
```

### `media_index.get_ordered_files`

**New in v1.3** - Get ordered media files with configurable sort field and direction.

**Parameters:**
- `count` (optional, default: 50): Maximum number of files to return (1-1000)
- `folder` (optional): Filter by folder (filesystem path or media-source URI)
- `recursive` (optional, default: true): Include subfolders
- `file_type` (optional): Filter by `image` or `video`
- `order_by` (optional, default: `date_taken`): Sort field (`date_taken`, `filename`, `path`, `modified_time`)
- `order_direction` (optional, default: `desc`): Sort direction (`asc` or `desc`)

**Returns:** List of ordered media items with metadata (includes `media_source_uri` in v1.4+)

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

## Service Usage with Media Card

The Media Index services integrate seamlessly with the [Home Assistant Media Card](https://github.com/markaggar/ha-media-card):

- **`get_random_items`** - Used automatically by Media Card for random slideshow mode
- **`get_ordered_files`** - Used automatically by Media Card for sequential slideshow mode (v1.3)
- **`mark_favorite`** - Called when clicking favorite button on Media Card
- **`delete_media`** - Called when clicking delete button on Media Card
- **`mark_for_edit`** - Called when clicking edit button on Media Card
- **`restore_edited_files`** - Run periodically to restore edited files

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
