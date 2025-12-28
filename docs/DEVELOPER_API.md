# Media Index Developer API

This guide is for developers integrating with the Media Index custom component via the Home Assistant WebSocket API.

## Table of Contents
- [WebSocket API Overview](#websocket-api-overview)
- [Multi-Instance Support](#multi-instance-support)
- [Calling Services via WebSocket](#calling-services-via-websocket)
- [Service Examples](#service-examples)
- [Response Handling](#response-handling)
- [Error Handling](#error-handling)
- [Best Practices](#best-practices)

## WebSocket API Overview

Media Index services support the `return_response` feature in Home Assistant, allowing services to return data directly via WebSocket instead of using events or state changes.

### Why Use WebSocket API?

- **Immediate responses** - Get data back synchronously
- **No polling** - Don't wait for state updates
- **Rich data** - Return complex objects (arrays, nested data)
- **Better error handling** - Explicit success/failure responses

## Multi-Instance Support

**New in v1.1+**: Media Index supports multiple integration instances, allowing you to index different media libraries separately (e.g., local photos and cloud-synced photos).

### Why Multiple Instances?

- **Separate libraries** - Keep different media sources isolated
- **Different scan settings** - Each instance can have its own watched folders and scan schedules
- **Independent caches** - Each instance maintains its own SQLite database
- **Targeted queries** - Route service calls to specific instances

### Routing Service Calls

When you have multiple Media Index instances, use the `target` parameter to specify which instance to query:

```javascript
const wsResponse = await this.hass.callWS({
  type: 'call_service',
  domain: 'media_index',
  service: 'get_random_items',
  service_data: {
    count: 100,
    folder: '/media/Photo/OneDrive'
  },
  target: {
    entity_id: 'sensor.media_index_media_photo_onedrive_total_files'
  },
  return_response: true
});
```

**Without a target**, the service will default to the first configured instance (backward compatibility).

### Identifying Instances

Each instance creates a sensor entity. Use the entity ID to target specific instances:

```javascript
// Example: Two instances configured
const instances = {
  local: 'sensor.media_index_media_photo_photolibrary_total_files',
  cloud: 'sensor.media_index_media_photo_onedrive_total_files'
};

// Query local library
const localItems = await this.hass.callWS({
  type: 'call_service',
  domain: 'media_index',
  service: 'get_random_items',
  service_data: { count: 50 },
  target: { entity_id: instances.local },
  return_response: true
});

// Query cloud library
const cloudItems = await this.hass.callWS({
  type: 'call_service',
  domain: 'media_index',
  service: 'get_random_items',
  service_data: { count: 50 },
  target: { entity_id: instances.cloud },
  return_response: true
});
```

### Services Supporting Multi-Instance

**All Media Index services** support the `target` parameter:

- ✅ `get_random_items` - Random selection (enhanced in v1.3, anniversary mode in v1.5)
- ✅ `get_ordered_files` - Sequential retrieval (new in v1.3)
- ✅ `get_related_files` - Burst detection and related photos (new in v1.5)
- ✅ `update_burst_metadata` - Save burst review session data (new in v1.5)
- ✅ `check_file_exists` - Lightweight filesystem validation (new in v1.5.6)
- ✅ `get_file_metadata`
- ✅ `mark_favorite`
- ✅ `delete_media`
- ✅ `mark_for_edit`
- ✅ `restore_edited_files` - Restore edited files (enhanced in v1.3)
- ✅ `geocode_file` - Geocoding (enhanced in v1.3)
- ✅ `scan_folder`

### Configuration in Custom Cards

For custom Lovelace cards, add a configuration option for the target entity:

```javascript
// Card config schema
static getConfigElement() {
  return {
    media_index: {
      enabled: true,
      entity_id: 'sensor.media_index_media_photo_onedrive_total_files'
    }
  };
}

// Use in service calls
async queryMedia() {
  const wsCall = {
    type: 'call_service',
    domain: 'media_index',
    service: 'get_random_items',
    service_data: { count: 100 },
    return_response: true
  };

  // Add target if entity is configured
  if (this.config.media_index?.entity_id) {
    wsCall.target = {
      entity_id: this.config.media_index.entity_id
    };
  }

  const wsResponse = await this.hass.callWS(wsCall);
  const response = wsResponse?.response || wsResponse;
  return response.items;
}
```

### Testing Multi-Instance Routing

Verify service calls are routing to the correct instance:

1. **Developer Tools** → **Services**
2. Select `media_index.get_random_items`
3. Add target selector:
   ```yaml
   count: 1
   target:
     entity_id: sensor.media_index_media_photo_onedrive_total_files
   ```
4. Check returned file paths match the expected instance's base folder

## Calling Services via WebSocket

### Basic Pattern

All Media Index services can be called using `hass.callWS()` in custom Lovelace cards:

```javascript
const response = await this.hass.callWS({
  type: 'call_service',
  domain: 'media_index',
  service: 'service_name',
  service_data: {
    // Service parameters
  },
  return_response: true  // CRITICAL: Required to get response data
});
```

### Response Structure

WebSocket responses are wrapped in a standard format:

```javascript
{
  context: {
    id: "...",
    parent_id: null,
    user_id: "..."
  },
  response: {
    // Actual service response data
  }
}
```

**Always extract the inner response:**

```javascript
const response = wsResponse?.response || wsResponse;
```

## Service Examples

### 1. Get Random Media Items

Retrieve random media files with optional filtering.

**Enhanced in v1.3**: Added `priority_new_files` parameter for prioritizing recently scanned files.

```javascript
const wsResponse = await this.hass.callWS({
  type: 'call_service',
  domain: 'media_index',
  service: 'get_random_items',
  service_data: {
    count: 100,                    // Number of items to return (1-100)
    folder: '/media/Photo/New',    // Optional: filter by folder (path or URI)
    // v1.4: folder can be either filesystem path OR media-source URI:
    // folder: 'media-source://media_source/local/photos'
    recursive: true,               // Optional: include subfolders (default: true)
    file_type: 'image',            // Optional: 'image' or 'video'
    favorites_only: false,         // Optional: only favorited files (default: false)
    date_from: '2024-01-01',       // Optional: ISO date string
    date_to: '2024-12-31',         // Optional: ISO date string
    priority_new_files: true,      // v1.3: Prioritize recent files
    new_files_threshold_seconds: 2592000,  // v1.3: 30 days threshold
    // v1.5: Anniversary mode for "Through the Years" feature
    // anniversary_month: '*',     // Optional: '01'-'12' or '*' for any month
    // anniversary_day: '25',      // Optional: '01'-'31' or '*' for any day
    // anniversary_window_days: 3  // Optional: ±N days tolerance (default: 0)
  },
  return_response: true
});

const response = wsResponse?.response || wsResponse;

if (response && response.items && Array.isArray(response.items)) {
  console.log(`Received ${response.items.length} items`);
  
  response.items.forEach(item => {
    console.log('Path:', item.path);
    console.log('Type:', item.media_content_type);
    console.log('Favorited:', item.is_favorited);
    console.log('Metadata:', item.metadata);
  });
}
```

**Priority New Files Mode (v1.3):**

When `priority_new_files: true`, uses 70/30 weighted random selection:
- 70% chance: Select from files scanned within threshold
- 30% chance: Fall back to older files if not enough recent files

Perfect for "What's New" slideshows that prioritize recent content.

**Response Item Structure:**

```javascript
{
  id: 1234,
  path: "/media/Photo/PhotoLibrary/sunset.jpg",
  media_source_uri: "media-source://media_source/media/Photo/PhotoLibrary/sunset.jpg",  // v1.4
  filename: "sunset.jpg",
  folder: "/media/Photo/PhotoLibrary",
  file_type: "image",
  media_content_type: "image/jpeg",
  file_size: 2048576,
  date_taken: "2024-10-15T14:30:00",
  date_added: "2024-10-15T18:45:00",
  is_favorited: false,
  metadata: {
    exif_make: "Canon",
    exif_model: "EOS R5",
    exif_focal_length: 50,
    exif_f_number: 1.8,
    exif_iso: 100,
    gps_latitude: 37.7749,
    gps_longitude: -122.4194,
    location_name: "San Francisco",
    location_city: "San Francisco",
    location_state: "California",
    location_country: "United States",
    rating: 4
  }
}
```

### 2. Get Ordered Media Files

**New in v1.3** - Retrieve media files in a specific order with cursor-based pagination.

```javascript
const wsResponse = await this.hass.callWS({
  type: 'call_service',
  domain: 'media_index',
  service: 'get_ordered_files',
  service_data: {
    count: 50,                     // Max items to return (1-1000)
    folder: '/media/Photo/2023',   // Optional: filter by folder (path or URI)
    // v1.4: folder can be either filesystem path OR media-source URI:
    // folder: 'media-source://media_source/local/photos/2023'
    recursive: true,               // Include subfolders
    file_type: 'image',            // Optional: 'image' or 'video'
    order_by: 'date_taken',        // 'date_taken', 'filename', 'path', 'modified_time'
    order_direction: 'desc'        // 'asc' or 'desc'
  },
  return_response: true
});

const response = wsResponse?.response || wsResponse;

if (response && response.items && Array.isArray(response.items)) {
  console.log(`Received ${response.items.length} ordered items`);
  
  response.items.forEach(item => {
    console.log('Path:', item.path);
    console.log('Date Taken:', item.date_taken);
    console.log('Order Value:', item.order_value);  // Value used for ordering
  });
}
```

**Use Cases:**
- Sequential slideshows (oldest to newest or newest to oldest)
- Alphabetical file listings
- Date-sorted photo galleries
- Folder hierarchy traversal

**Response Structure:** Same as `get_random_items` with additional `order_value` field containing the value used for ordering.

### 3. Get Related Files (Burst Detection & Anniversary Mode)

**New in v1.5** - Find photos taken at the same time and location (burst mode) or from the same date across years (anniversary mode).

**Burst Mode Example:**

```javascript
const wsResponse = await this.hass.callWS({
  type: 'call_service',
  domain: 'media_index',
  service: 'get_related_files',
  service_data: {
    mode: 'burst',
    media_source_uri: 'media-source://media_source/media/Photo/PhotoLibrary/IMG_1234.jpg',
    time_window_seconds: 120,           // ±2 minutes (default)
    prefer_same_location: true,         // Enable GPS filtering (default)
    location_tolerance_meters: 50,      // Max distance in meters (default)
    sort_order: 'time_asc'             // Chronological order (default)
  },
  return_response: true
});

const response = wsResponse?.response || wsResponse;

if (response && response.items && Array.isArray(response.items)) {
  console.log(`Found ${response.items.length} burst photos`);
  
  response.items.forEach(item => {
    console.log('Path:', item.path);
    console.log('Seconds offset:', item.seconds_offset);
    console.log('Distance (meters):', item.distance_meters);
    console.log('Is favorited:', item.is_favorited);
    console.log('Rating:', item.rating);
  });
}
```

**Anniversary Mode Example:**

```javascript
const wsResponse = await this.hass.callWS({
  type: 'call_service',
  domain: 'media_index',
  service: 'get_related_files',
  service_data: {
    mode: 'anniversary',
    media_source_uri: 'media-source://media_source/media/Photo/PhotoLibrary/IMG_1234.jpg',
    window_days: 3,                     // ±3 days around reference date (default)
    years_back: 15,                     // Search up to 15 years back (default)
    sort_order: 'time_desc'            // Newest first (or 'time_asc')
  },
  return_response: true
});

const response = wsResponse?.response || wsResponse;

if (response && response.items && Array.isArray(response.items)) {
  console.log(`Found ${response.items.length} anniversary photos`);
  
  response.items.forEach(item => {
    console.log('Path:', item.path);
    console.log('Date taken:', item.date_taken);
    console.log('Years ago:', item.years_ago);  // How many years before reference
    console.log('Is favorited:', item.is_favorited);
  });
}
```

**Response Item Structure:**

```javascript
{
  id: 1235,
  path: "/media/Photo/PhotoLibrary/IMG_1235.jpg",
  media_source_uri: "media-source://media_source/media/Photo/PhotoLibrary/IMG_1235.jpg",
  filename: "IMG_1235.jpg",
  folder: "/media/Photo/PhotoLibrary",
  file_type: "image",
  media_content_type: "image/jpeg",
  date_taken: "2024-10-15T14:30:15",
  seconds_offset: 15,          // 15 seconds after reference photo
  distance_meters: 2.5,        // 2.5 meters from reference location
  is_favorited: false,
  rating: 0,
  metadata: { /* ... EXIF data ... */ }
}
```

**Use Cases:**
- Burst Review feature - compare rapid-fire shots
- Find all photos from a specific moment
- GPS-filtered photo sequences
- "Through the Years" feature - photos from same date across multiple years
- Anniversary retrospectives with date tolerance

### 4. Update Burst Metadata

**New in v1.5** - Save burst review session data to file metadata.

```javascript
const wsResponse = await this.hass.callWS({
  type: 'call_service',
  domain: 'media_index',
  service: 'update_burst_metadata',
  service_data: {
    burst_files: [
      'media-source://media_source/media/Photo/PhotoLibrary/IMG_1234.jpg',
      'media-source://media_source/media/Photo/PhotoLibrary/IMG_1235.jpg',
      'media-source://media_source/media/Photo/PhotoLibrary/IMG_1236.jpg',
      'media-source://media_source/media/Photo/PhotoLibrary/IMG_1237.jpg'
    ],
    favorited_files: [
      'media-source://media_source/media/Photo/PhotoLibrary/IMG_1235.jpg',
      'media-source://media_source/media/Photo/PhotoLibrary/IMG_1236.jpg'
    ]
  },
  return_response: true
});

const response = wsResponse?.response || wsResponse;

console.log('Burst metadata saved:', {
  files_updated: response.files_updated,
  burst_count: response.burst_count,
  favorites_count: response.favorites_count
});
```

**Response:**

```javascript
{
  files_updated: 4,
  burst_count: 4,
  favorites_count: 2
}
```

**What gets saved:**
- `burst_favorites`: JSON array of favorited filenames (stored in all burst files)
- `burst_count`: Total files in burst at review time (stored in all burst files)
- Metadata persists even if files are deleted or parameters change

### 5. Mark File as Favorite

Toggle favorite status for a file (writes to database and EXIF).

**v1.4+**: Accepts either `file_path` OR `media_source_uri`

```javascript
// Using filesystem path
const response = await this.hass.callWS({
  type: 'call_service',
  domain: 'media_index',
  service: 'mark_favorite',
  service_data: {
    file_path: '/media/Photo/PhotoLibrary/sunset.jpg',
    is_favorite: true  // or false to unfavorite
  },
  return_response: true
});

// v1.4: Using media-source URI
const uriResponse = await this.hass.callWS({
  type: 'call_service',
  domain: 'media_index',
  service: 'mark_favorite',
  service_data: {
    media_source_uri: 'media-source://media_source/media/Photo/PhotoLibrary/sunset.jpg',
    is_favorite: true
  },
  return_response: true
});

console.log('Favorite status updated:', response);
```

**Response:**

```javascript
{
  success: true,
  file_path: "/media/Photo/PhotoLibrary/sunset.jpg",
  is_favorite: true
}
```

### 6. Delete Media File

Move a file to the `_Junk` folder.

**v1.4+**: Accepts either `file_path` OR `media_source_uri`

```javascript
// Using filesystem path
const response = await this.hass.callWS({
  type: 'call_service',
  domain: 'media_index',
  service: 'delete_media',
  service_data: {
    file_path: '/media/Photo/PhotoLibrary/bad_photo.jpg'
  },
  return_response: true
});

// v1.4: Using media-source URI
const uriResponse = await this.hass.callWS({
  type: 'call_service',
  domain: 'media_index',
  service: 'delete_media',
  service_data: {
    media_source_uri: 'media-source://media_source/media/Photo/PhotoLibrary/bad_photo.jpg'
  },
  return_response: true
});

console.log('File deleted:', response);
```

**Response:**

```javascript
{
  success: true,
  file_path: "/media/Photo/PhotoLibrary/bad_photo.jpg",
  new_path: "/media/Photo/PhotoLibrary/_Junk/bad_photo.jpg"
}
```

### 7. Mark File for Editing

Move a file to the `_Edit` folder.

**v1.4+**: Accepts either `file_path` OR `media_source_uri`

```javascript
// Using filesystem path
const response = await this.hass.callWS({
  type: 'call_service',
  domain: 'media_index',
  service: 'mark_for_edit',
  service_data: {
    file_path: '/media/Photo/PhotoLibrary/needs_editing.jpg'
  },
  return_response: true
});

// v1.4: Using media-source URI
const uriResponse = await this.hass.callWS({
  type: 'call_service',
  domain: 'media_index',
  service: 'mark_for_edit',
  service_data: {
    media_source_uri: 'media-source://media_source/media/Photo/PhotoLibrary/needs_editing.jpg'
  },
  return_response: true
});
```

### 8. Restore Edited Files

**Enhanced in v1.3**: Added `file_path` parameter for single-file restore.

```javascript
// Restore all edited files
const response = await this.hass.callWS({
  type: 'call_service',
  domain: 'media_index',
  service: 'restore_edited_files',
  return_response: true
});

// v1.3: Restore specific file only
const singleResponse = await this.hass.callWS({
  type: 'call_service',
  domain: 'media_index',
  service: 'restore_edited_files',
  service_data: {
    file_path: '/media/Photo/_Edit/vacation.jpg'
  },
  return_response: true
});
```

### 9. Get File Metadata

Retrieve detailed metadata for a specific file.

**v1.4+**: Accepts either `file_path` OR `media_source_uri`

```javascript
// Using filesystem path
const response = await this.hass.callWS({
  type: 'call_service',
  domain: 'media_index',
  service: 'get_file_metadata',
  service_data: {
    file_path: '/media/Photo/PhotoLibrary/sunset.jpg'
  },
  return_response: true
});

// v1.4: Using media-source URI
const uriResponse = await this.hass.callWS({
  type: 'call_service',
  domain: 'media_index',
  service: 'get_file_metadata',
  service_data: {
    media_source_uri: 'media-source://media_source/media/Photo/PhotoLibrary/sunset.jpg'
  },
  return_response: true
});

console.log('File metadata:', response);
```

### 10. Geocode File or Coordinates

**Enhanced in v1.3**: Now supports direct lat/lon lookup (not just file_path).

**Enhanced in v1.4**: Accepts `file_path`, `file_id`, OR `media_source_uri`

```javascript
// Geocode by file path
const response = await this.hass.callWS({
  type: 'call_service',
  domain: 'media_index',
  service: 'geocode_file',
  service_data: {
    file_path: '/media/Photo/PhotoLibrary/sunset.jpg'
  },
  return_response: true
});

// v1.4: Geocode by media-source URI
const uriResponse = await this.hass.callWS({
  type: 'call_service',
  domain: 'media_index',
  service: 'geocode_file',
  service_data: {
    media_source_uri: 'media-source://media_source/media/Photo/PhotoLibrary/sunset.jpg'
  },
  return_response: true
});

// Geocode by file ID
const idResponse = await this.hass.callWS({
  type: 'call_service',
  domain: 'media_index',
  service: 'geocode_file',
  service_data: {
    file_id: 12345
  },
  return_response: true
});

// v1.3: Geocode by coordinates directly
const coordResponse = await this.hass.callWS({
  type: 'call_service',
  domain: 'media_index',
  service: 'geocode_file',
  service_data: {
    latitude: 37.7749,
    longitude: -122.4194
  },
  return_response: true
});

console.log('Location:', coordResponse.location_name);
// Output: "San Francisco, California, United States"
```

### 11. Trigger Manual Scan

Start a manual folder scan.

```javascript
const response = await this.hass.callWS({
  type: 'call_service',
  domain: 'media_index',
  service: 'scan_folder',
  service_data: {
    folder_path: '/media/Photo/PhotoLibrary/New',  // Optional: specific folder
    force_rescan: false  // Optional: re-extract existing files
  },
  return_response: true
});
```

### 12. Cleanup Database

**New in v1.5** - Remove database entries for files that no longer exist on the filesystem.

```javascript
// Preview mode - see what would be removed
const previewResponse = await this.hass.callWS({
  type: 'call_service',
  domain: 'media_index',
  service: 'cleanup_database',
  service_data: {
    dry_run: true  // Default: true (safe preview mode)
  },
  return_response: true
});

const preview = previewResponse?.response || previewResponse;

console.log(`Would remove ${preview.stale_files.length} stale entries`);
preview.stale_files.forEach(file => {
  console.log(`Stale: ${file.path} (ID: ${file.id})`);
});

// Actually remove stale entries
const cleanupResponse = await this.hass.callWS({
  type: 'call_service',
  domain: 'media_index',
  service: 'cleanup_database',
  service_data: {
    dry_run: false  // Actually delete stale entries
  },
  return_response: true
});

const result = cleanupResponse?.response || cleanupResponse;

console.log('Cleanup complete:', {
  files_checked: result.files_checked,
  files_removed: result.files_removed,
  stale_files_count: result.stale_files.length
});
```

### 13. Check File Exists

**New in v1.5.6** - Lightweight filesystem validation for instant 404 detection.

```javascript
// Check by filesystem path
const pathCheckResponse = await this.hass.callWS({
  type: 'call_service',
  domain: 'media_index',
  service: 'check_file_exists',
  service_data: {
    file_path: '/media/photo/Photos/2024/IMG_1234.jpg'
  },
  target: {
    entity_id: 'sensor.media_index_media_photo_photolibrary_total_files'
  },
  return_response: true
});

const pathResult = pathCheckResponse?.response || pathCheckResponse;
console.log('File exists:', pathResult.exists);    // true/false
console.log('Checked path:', pathResult.path);     // resolved path

// Check by media-source URI
const uriCheckResponse = await this.hass.callWS({
  type: 'call_service',
  domain: 'media_index',
  service: 'check_file_exists',
  service_data: {
    media_source_uri: 'media-source://media_source/media/photo/Photos/2024/IMG_1234.jpg'
  },
  target: {
    entity_id: 'sensor.media_index_media_photo_photolibrary_total_files'
  },
  return_response: true
});

const uriResult = uriCheckResponse?.response || uriCheckResponse;

if (uriResult.exists) {
  console.log('✅ File exists at:', uriResult.path);
  // Proceed with loading the image
} else {
  console.log('❌ File not found:', uriResult.path);
  // Skip this file, advance to next
}
```

**Performance:**
- ~1ms response time (just `os.path.exists()` check)
- No metadata loading, no network request, no image decode
- 100x faster than image preload validation

**Security:**
- Path traversal protection enforced
- All paths validated against configured `base_folder`
- Symbolic links resolved via `os.path.realpath()` to prevent symlink attacks
- Rejects `..` components and paths outside media collection

**Use Case:**
Media Card v5.6.5+ uses this to eliminate 404 broken image icons:
1. Database returns file path from query
2. Check if file exists before rendering
3. If `exists: false`, skip file and advance to next
4. If `exists: true`, proceed with loading

**Response Structure:**
```javascript
{
  exists: true,                                    // boolean
  path: "/media/photo/Photos/2024/IMG_1234.jpg",  // resolved path
  error: "..."                                     // optional error message
}
```

**Response:**

```javascript
{
  files_checked: 15234,
  files_removed: 42,  // 0 if dry_run=true
  stale_files: [
    { id: 123, path: "/media/Photo/deleted_file.jpg" },
    { id: 456, path: "/media/Photo/moved_file.jpg" }
  ]
}
```

**Use Cases:**
- After bulk file operations outside Home Assistant
- Fix 404 errors from stale database entries
- Periodic maintenance to sync database with filesystem

## Response Handling

### Success Response Pattern

```javascript
try {
  const wsResponse = await this.hass.callWS({
    type: 'call_service',
    domain: 'media_index',
    service: 'get_random_items',
    service_data: { count: 50 },
    return_response: true
  });

  // Extract inner response
  const response = wsResponse?.response || wsResponse;

  if (response && response.items && Array.isArray(response.items)) {
    // Success - process items
    console.log(`✅ Received ${response.items.length} items`);
    return response.items;
  } else {
    // Unexpected response format
    console.error('❌ Unexpected response format:', response);
    return [];
  }
} catch (error) {
  // Service call failed
  console.error('❌ Service call failed:', error);
  return [];
}
```

### Filtering Excluded Files

If you're maintaining an exclusion list (e.g., deleted/moved files), filter them **before** processing:

```javascript
const response = wsResponse?.response || wsResponse;

if (response && response.items && Array.isArray(response.items)) {
  // Filter out excluded files
  const filteredItems = response.items.filter(item => {
    const isExcluded = this._excludedFiles.has(item.path);
    if (isExcluded) {
      console.log(`⏭️ Filtering out excluded file: ${item.path}`);
    }
    return !isExcluded;
  });
  
  console.log(`Filtered ${response.items.length - filteredItems.length} excluded files`);
  return filteredItems;
}
```

## Error Handling

### Service Call Errors

```javascript
try {
  const response = await this.hass.callWS({
    type: 'call_service',
    domain: 'media_index',
    service: 'mark_favorite',
    service_data: {
      file_path: '/invalid/path.jpg',
      is_favorite: true
    },
    return_response: true
  });
} catch (error) {
  console.error('Service call failed:', error.message);
  
  // Common error scenarios:
  // - File not found in database
  // - File doesn't exist on filesystem
  // - Permission denied
  // - Integration not loaded
}
```

### Validation Checks

Always validate responses before using data:

```javascript
const response = wsResponse?.response || wsResponse;

// Check response exists
if (!response) {
  console.error('No response received');
  return;
}

// Check expected structure
if (!response.items || !Array.isArray(response.items)) {
  console.error('Invalid response structure:', response);
  return;
}

// Check for empty results
if (response.items.length === 0) {
  console.warn('No items returned from query');
  return;
}

// Validate individual items
const validItems = response.items.filter(item => {
  if (!item.path) {
    console.warn('Item missing path:', item);
    return false;
  }
  return true;
});
```

## Best Practices

### 1. Use Appropriate Counts

For slideshow/gallery applications:

```javascript
// Good: Request enough for smooth operation
service_data: { count: 100 }

// Bad: Requesting too many can be slow
service_data: { count: 10000 }  // ❌ Overkill for most use cases
```

### 2. Filter Efficiently

Apply filters server-side rather than client-side:

```javascript
// Good: Filter on server
service_data: {
  count: 100,
  folder: '/media/Photo/Favorites',
  file_type: 'image',
  favorites_only: true
}

// Bad: Get all and filter client-side
service_data: { count: 1000 }  // Then filter 900 items locally
```

### 3. Handle Missing Data Gracefully

Not all files have complete metadata:

```javascript
response.items.forEach(item => {
  // Use optional chaining and defaults
  const location = item.metadata?.location_city || 'Unknown location';
  const rating = item.metadata?.rating || 0;
  const camera = item.metadata?.exif_model || 'Unknown camera';
});
```

### 4. Cache Resolved URLs

If you're resolving media paths to signed URLs, cache them:

```javascript
const urlCache = new Map();

async function getMediaUrl(path) {
  if (urlCache.has(path)) {
    return urlCache.get(path);
  }
  
  const url = await resolveMediaPath(path);
  urlCache.set(path, url);
  return url;
}
```

### 5. Maintain Exclusion Lists

Track files that have been deleted/moved during the session:

```javascript
class MediaGallery extends LitElement {
  constructor() {
    super();
    this._excludedFiles = new Set();
  }

  async deleteFile(filePath) {
    await this.hass.callWS({
      type: 'call_service',
      domain: 'media_index',
      service: 'delete_media',
      service_data: { file_path: filePath },
      return_response: true
    });
    
    // Add to exclusion list
    this._excludedFiles.add(filePath);
  }

  async refreshMedia() {
    const wsResponse = await this.hass.callWS({
      type: 'call_service',
      domain: 'media_index',
      service: 'get_random_items',
      service_data: { count: 100 },
      return_response: true
    });

    const response = wsResponse?.response || wsResponse;
    
    // Filter out excluded files
    const items = response.items.filter(item => 
      !this._excludedFiles.has(item.path)
    );
    
    return items;
  }
}
```

### 6. Update Local State After Mutations

When marking favorites or deleting files, update your local data structures:

```javascript
async toggleFavorite(item, index) {
  const newState = !item.is_favorited;
  
  // Call service
  await this.hass.callWS({
    type: 'call_service',
    domain: 'media_index',
    service: 'mark_favorite',
    service_data: {
      file_path: item.path,
      is_favorite: newState
    },
    return_response: true
  });

  // Update local state immediately for responsive UI
  if (this._items && this._items[index]) {
    this._items[index].is_favorited = newState;
    if (this._items[index].metadata) {
      this._items[index].metadata.is_favorited = newState;
    }
  }
  
  this.requestUpdate();
}
```

## Real-World Example

Here's a complete example from the [ha-media-card](https://github.com/markaggar/ha-media-card) custom Lovelace card:

```javascript
async _queryMediaIndex(count = 100) {
  if (!this.hass) {
    console.error('No hass object available');
    return null;
  }

  const folderFilter = this.config?.folder || this.config?.media_path;
  const configuredMediaType = this.config.media_type || 'all';

  try {
    const wsResponse = await this.hass.callWS({
      type: 'call_service',
      domain: 'media_index',
      service: 'get_random_items',
      service_data: {
        count: count,
        folder: folderFilter,
        file_type: configuredMediaType === 'all' ? undefined : configuredMediaType
      },
      return_response: true
    });

    const response = wsResponse?.response || wsResponse;

    if (response && response.items && Array.isArray(response.items)) {
      console.log(`✅ Received ${response.items.length} items from media_index`);
      
      // Filter out excluded files (deleted/moved during session)
      const filteredItems = response.items.filter(item => {
        const isExcluded = this._excludedFiles.has(item.path);
        if (isExcluded) {
          console.log(`⏭️ Filtering out excluded file: ${item.path}`);
        }
        return !isExcluded;
      });
      
      // Transform items to include resolved URLs
      const items = await Promise.all(filteredItems.map(async (item) => {
        return {
          path: item.path || item.file_path,
          type: item.media_content_type?.startsWith('video') ? 'video' : 'image',
          metadata: item.metadata || {},
          entity_id: item.entity_id,
          _metadata: item  // Store full backend item
        };
      }));

      return items;
    } else {
      console.error('❌ Invalid response format from media_index:', response);
      return null;
    }
  } catch (error) {
    console.error('❌ Failed to query media_index:', error);
    return null;
  }
}
```

## Integration Requirements

To use the WebSocket API with Media Index:

1. **Home Assistant 2023.7+** - `return_response` feature added
2. **Media Index integration** - Installed and configured
3. **WebSocket connection** - Available via `this.hass` in custom cards

## Troubleshooting

### Issue: Service Returns Empty Response

**Symptom:** Service call succeeds but returns no items or empty array.

**Common Causes:**

1. **Missing `target` parameter** - Service routing to wrong instance
   ```javascript
   // ❌ Wrong - routes to first instance by default
   const response = await this.hass.callWS({
     type: 'call_service',
     domain: 'media_index',
     service: 'get_random_items',
     service_data: { count: 100 },
     return_response: true
   });
   
   // ✅ Correct - explicitly specify target instance
   const response = await this.hass.callWS({
     type: 'call_service',
     domain: 'media_index',
     service: 'get_random_items',
     service_data: { count: 100 },
     target: {
       entity_id: 'sensor.media_index_media_photo_photolibrary_total_files'
     },
     return_response: true
   });
   ```

2. **Incorrect response extraction** - Not handling nested response structure
   ```javascript
   // ❌ Wrong - may get undefined if structure varies
   const items = wsResponse.response.items;
   
   // ✅ Correct - safely extract response
   const response = wsResponse?.response || wsResponse;
   const items = response?.items || [];
   ```

3. **No media indexed** - Database is empty or scan hasn't run yet
   - Check sensor state: `sensor.media_index_*_total_files` should be > 0
   - Trigger manual scan: `media_index.scan_folder`
   - Verify `watched_folders` configuration

### Issue: Service Call to Wrong Instance

**Symptom:** Getting files from unexpected folder or no results when files exist.

**Solution:** Always specify `target` when multiple instances are configured:

```javascript
// Determine which instance to query
const entityId = this.config.media_index?.entity_id || 
                 'sensor.media_index_media_photo_photolibrary_total_files';

// Include target in all service calls
const wsCall = {
  type: 'call_service',
  domain: 'media_index',
  service: 'get_random_items',
  service_data: { count: 100 },
  target: { entity_id: entityId },
  return_response: true
};
```

### Issue: WebSocket Call Returns Undefined

**Symptom:** `await this.hass.callWS()` returns `undefined` or throws error.

**Common Causes:**

1. **Missing `return_response: true`**
   ```javascript
   // ❌ Wrong - returns undefined
   const response = await this.hass.callWS({
     type: 'call_service',
     domain: 'media_index',
     service: 'get_random_items',
     service_data: { count: 100 }
   });
   
   // ✅ Correct - returns data
   const response = await this.hass.callWS({
     type: 'call_service',
     domain: 'media_index',
     service: 'get_random_items',
     service_data: { count: 100 },
     return_response: true  // CRITICAL
   });
   ```

2. **Service doesn't support `return_response`**
   - Verify Media Index version supports `SupportsResponse.OPTIONAL`
   - Check Home Assistant logs for service registration errors

3. **WebSocket not ready**
   ```javascript
   // Wait for hass to be fully initialized
   if (!this.hass) {
     console.warn('Home Assistant connection not ready');
     return;
   }
   ```

### Issue: Folder Filter Returns No Results

**Symptom:** Specifying `folder` parameter returns empty array even though files exist in that folder.

**Common Causes:**

1. **Incorrect folder path format**
   ```javascript
   // ❌ Wrong - full path or leading slash
   folder: '/media/Photo/PhotoLibrary/New'
   folder: '/New'
   
   // ✅ Correct - relative to base_folder
   folder: 'New'
   folder: 'Vacation/2024'
   ```

2. **Files not in database** - Folder exists but hasn't been scanned
   ```javascript
   // Check total files in instance
   const sensor = this.hass.states['sensor.media_index_media_photo_photolibrary_total_files'];
   console.log('Total indexed files:', sensor.state);
   console.log('Watched folders:', sensor.attributes.watched_folders);
   ```

### Debugging Service Calls

Add comprehensive logging to diagnose issues:

```javascript
async queryMediaIndex() {
  const entityId = this.config.media_index?.entity_id;
  console.log('🔍 Querying media_index:', {
    entity: entityId,
    hasTarget: !!entityId
  });
  
  try {
    const wsCall = {
      type: 'call_service',
      domain: 'media_index',
      service: 'get_random_items',
      service_data: { count: 100 },
      return_response: true
    };
    
    if (entityId) {
      wsCall.target = { entity_id: entityId };
    }
    
    console.log('📤 WebSocket request:', wsCall);
    const wsResponse = await this.hass.callWS(wsCall);
    console.log('📥 WebSocket response:', wsResponse);
    
    const response = wsResponse?.response || wsResponse;
    console.log('✅ Extracted response:', response);
    
    return response?.items || [];
  } catch (error) {
    console.error('❌ Service call failed:', error);
    return [];
  }
}
```

Check Home Assistant logs for backend errors:
```bash
# View integration logs
grep "media_index" /config/home-assistant.log

# Real-time monitoring
tail -f /config/home-assistant.log | grep "media_index"
```

## Testing

Test your integration using the Home Assistant Developer Tools:

1. Go to **Developer Tools** → **Services**
2. Select `media_index.get_random_items`
3. Enter service data:
   ```yaml
   count: 10
   file_type: image
   ```
4. Click **Call Service**
5. Check response in browser console

## Support

- **Media Index Issues**: [GitHub Issues](https://github.com/markaggar/ha-media-index/issues)
- **Example Implementation**: [ha-media-card source](https://github.com/markaggar/ha-media-card)
- **Home Assistant WebSocket API**: [HA Developer Docs](https://developers.home-assistant.io/docs/api/websocket)

---

## Version History

### v1.3 Enhancements

#### New Services

- ✨ **`get_ordered_files`** - Sequential file retrieval with configurable ordering
  - Supports ordering by: date_taken, filename, path, modified_time
  - Ascending/descending sort direction
  - Cursor-based pagination for large collections
  - Perfect for sequential slideshows

#### Enhanced Services

**`get_random_items`**

- Added `priority_new_files` parameter for prioritizing recently scanned files
- Added `new_files_threshold_seconds` parameter (default: 3600)
- 70/30 weighted random selection when priority mode enabled
- Ideal for "What's New" slideshows

**`restore_edited_files`**

- Added `file_path` parameter for single-file restore
- Can now restore specific files instead of all files in `_Edit`

**`geocode_file`**

- Added `latitude` and `longitude` parameters
- Now supports direct coordinate lookup without file_path
- Useful for arbitrary location lookups

#### Performance Improvements

- All blocking I/O operations wrapped in executor jobs (HA 2025.x compatibility)
- Service call logging changed from WARNING to DEBUG level
- Optimized EXIF parsing with caching
- Reduced redundant file system operations

#### Media Card v5 Integration

The v1.3 enhancements are designed to work seamlessly with Media Card v5.0:

- **MediaIndexProvider** uses `get_random_items` with `priority_new_files` mode
- **SequentialMediaIndexProvider** uses `get_ordered_files` with cursor pagination

### v1.1 Features

- Multi-instance support for independent media libraries
- Target selector for routing service calls to specific instances
- Independent SQLite databases per instance
- Configurable scan schedules per instance

### v1.0 Initial Release

- Core metadata extraction and indexing
- WebSocket API with `return_response` support
- EXIF, GPS, and location data
- File management services (favorite, delete, edit)
- Geocoding integration

