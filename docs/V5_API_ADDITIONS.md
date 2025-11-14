# Media Index v5 API Additions

This document describes new and enhanced services added in the v5 release.

## New Services

### `media_index.get_ordered_files`

Retrieve media files in a specific order with cursor-based pagination. Perfect for sequential slideshows and organized browsing.

#### Parameters

- `count` (optional, default: 50): Maximum number of files to return (1-1000)
- `folder` (optional): Filter by specific folder path
- `recursive` (optional, default: true): Include subfolders
- `file_type` (optional): Filter by `image` or `video`
- `order_by` (optional, default: `date_taken`): Sort field
  - `date_taken` - EXIF creation date
  - `filename` - Alphabetical by filename
  - `path` - Full path (folder hierarchy)
  - `modified_time` - Filesystem modification time
- `order_direction` (optional, default: `desc`): Sort direction (`asc` or `desc`)

#### WebSocket Example

```javascript
const wsResponse = await this.hass.callWS({
  type: 'call_service',
  domain: 'media_index',
  service: 'get_ordered_files',
  service_data: {
    count: 100,
    folder: '/media/Photo/2023',
    recursive: true,
    order_by: 'date_taken',
    order_direction: 'desc'
  },
  return_response: true
});

const response = wsResponse?.response || wsResponse;
console.log(`Received ${response.items.length} ordered items`);
```

#### YAML Example

```yaml
service: media_index.get_ordered_files
data:
  count: 50
  folder: /media/photo/Photos/2023
  order_by: filename
  order_direction: asc
  recursive: false
```

#### Use Cases

- **Sequential slideshows**: Display photos in chronological order
- **Album browsing**: Navigate through folders alphabetically
- **Date-sorted galleries**: Show newest or oldest photos first
- **Folder hierarchy**: Traverse directory structure systematically

#### Response Structure

Same as `get_random_items` with the addition of an `order_value` field that contains the value used for ordering.

```javascript
{
  items: [
    {
      id: 1234,
      path: "/media/Photo/2023/vacation.jpg",
      filename: "vacation.jpg",
      date_taken: "2023-08-15T14:30:00",
      order_value: "2023-08-15T14:30:00",  // The value used for ordering
      // ... other metadata
    }
  ]
}
```

## Enhanced Services

### `media_index.get_random_items`

#### New Parameters (v5)

- `priority_new_files` (optional, default: false): Prioritize recently scanned files
- `new_files_threshold_seconds` (optional, default: 3600): Threshold for considering files "new"

#### Priority New Files Mode

When `priority_new_files: true`, the service uses a 70/30 weighted random selection:
- 70% chance: Select from files scanned within threshold (e.g., last hour or 30 days)
- 30% chance: Fall back to older files if not enough recent files available

This is perfect for "What's New" slideshows that prioritize recent content while still showing older media.

#### WebSocket Example

```javascript
const wsResponse = await this.hass.callWS({
  type: 'call_service',
  domain: 'media_index',
  service: 'get_random_items',
  service_data: {
    count: 50,
    priority_new_files: true,
    new_files_threshold_seconds: 2592000  // 30 days
  },
  return_response: true
});
```

#### YAML Example

```yaml
service: media_index.get_random_items
data:
  count: 100
  priority_new_files: true
  new_files_threshold_seconds: 86400  # 24 hours
```

### `media_index.restore_edited_files`

#### New Parameters (v5)

- `file_path` (optional): Restore only this specific file instead of all files in `_Edit`

#### WebSocket Example

```javascript
// Restore single file
const response = await this.hass.callWS({
  type: 'call_service',
  domain: 'media_index',
  service: 'restore_edited_files',
  service_data: {
    file_path: '/media/Photo/_Edit/vacation.jpg'
  },
  return_response: true
});

// Restore all edited files
const response = await this.hass.callWS({
  type: 'call_service',
  domain: 'media_index',
  service: 'restore_edited_files',
  return_response: true
});
```

### `media_index.geocode_file`

#### New Parameters (v5)

- `file_id` (optional): Database ID of file to geocode
- `latitude` (optional): GPS latitude (alternative to file_id)
- `longitude` (optional): GPS longitude (alternative to file_id)

Previously required `file_path`, now supports direct coordinate lookups.

#### WebSocket Example

```javascript
// Geocode by coordinates
const response = await this.hass.callWS({
  type: 'call_service',
  domain: 'media_index',
  service: 'geocode_file',
  service_data: {
    latitude: 37.7749,
    longitude: -122.4194
  },
  return_response: true
});

console.log('Location:', response.location_name);
// Output: "San Francisco, California, United States"
```

## Performance Enhancements

### Home Assistant 2025.x Compatibility

All blocking I/O operations (file reads, EXIF parsing) are now wrapped in executor jobs to prevent "Blocking call" warnings in HA 2025.x and later.

### Reduced Logging Noise

Service call logs changed from `WARNING` to `DEBUG` level. Enable debug mode in integration configuration to see detailed service call logs.

### EXIF Caching

Metadata parsing results are cached to reduce redundant file I/O operations for frequently accessed files.

## Media Card v5 Integration

The v5 service enhancements are designed to work seamlessly with Media Card v5.0:

| Media Card Provider | Service Used | Key Features |
|---------------------|--------------|--------------|
| **MediaIndexProvider** | `get_random_items` | Random slideshow with priority_new_files mode |
| **SequentialMediaIndexProvider** | `get_ordered_files` | Sequential slideshow with configurable ordering |

### Example: Media Card Configuration

```yaml
type: custom:media-card
title: "Sequential Photo Gallery"
media_type: image
folder_mode: sequential
media_index:
  entity_id: sensor.media_index_photos_total_files
  order_by: date_taken
  order_direction: desc
auto_refresh_seconds: 10
```

## Migration Notes

### Backward Compatibility

- ✅ All existing service calls continue to work unchanged
- ✅ New parameters are optional with sensible defaults
- ✅ Legacy entity_id formats supported (with/without `_total_files` suffix)

### Breaking Changes

- ⚠️ None - v5 is fully backward compatible

## Complete Service List

| Service | Status | Description |
|---------|--------|-------------|
| `get_random_items` | ✨ Enhanced | Random selection with priority mode |
| `get_ordered_files` | 🆕 New | Sequential retrieval with ordering |
| `get_file_metadata` | ✅ Unchanged | File metadata retrieval |
| `mark_favorite` | ✅ Unchanged | Toggle favorite status |
| `delete_media` | ✅ Unchanged | Move to `_Junk` folder |
| `mark_for_edit` | ✅ Unchanged | Move to `_Edit` folder |
| `restore_edited_files` | ✨ Enhanced | Restore with optional file filter |
| `scan_folder` | ✅ Unchanged | Manual folder scan |
| `geocode_file` | ✨ Enhanced | Now supports direct coordinates |

## Additional Resources

- [Full Services Documentation](SERVICES.md)
- [Developer API Guide](DEVELOPER_API.md)
- [Media Card Integration Guide](https://github.com/markaggar/ha-media-card)
