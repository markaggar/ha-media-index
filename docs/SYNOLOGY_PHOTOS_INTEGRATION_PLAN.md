# Synology Photos Integration Plan

## Overview

Extend Media Index to scan Synology Photos via API, overcoming the 1000-photo limit in HA's Synology DSM integration. This enables full library indexing with metadata for use with Media Card.

## Problem Statement

**Current Limitations**:
- HA's Synology DSM integration exposes Synology Photos via `media-source://synology_dsm/...`
- Media browser is limited to 1000 photos
- Media Index can't scan these photos (virtual paths, no filesystem access)
- Users with large Synology Photos libraries can't use them with Media Card

**Key Insight**:
- The 1000-item limit is a *browsing* limit, not a fetch limit
- Individual photos can be fetched by direct URI even if not in browse list
- Synology Photos API can be paginated to retrieve ALL photos
- HA already has Synology Photos API client code in `synology_dsm` integration

## Proposed Solution

Create API-based scanner in Media Index that:
1. Connects to existing Synology DSM integration
2. Uses Synology Photos API to scan ALL photos (paginated)
3. Stores metadata in Media Index cache
4. Constructs valid `media-source://` URIs for each photo
5. Media Card displays photos using direct URIs (bypassing browse limit)

## Architecture

### High-Level Flow

```
Synology Photos Library (50K photos)
         ↓
    API Scanner (paginated scan)
         ↓
Media Index Cache (all 50K photos with metadata)
         ↓
Media Card (uses direct URIs to display any photo)
         ↓
HA Synology DSM Integration (fetches individual photos)
```

### Components

#### 1. Synology Photos Scanner (`synology_scanner.py`)

```python
class SynologyPhotosScanner:
    """Scanner for Synology Photos via DSM API."""
    
    def __init__(self, hass, cache_manager, dsm_config_entry_id):
        self.hass = hass
        self.cache = cache_manager
        self.dsm_entry_id = dsm_config_entry_id
        
    async def scan_photos(self) -> int:
        """Scan all photos from Synology Photos API.
        
        Returns:
            Number of photos added to cache
        """
        # 1. Get Synology DSM API client from existing integration
        api_client = await self._get_dsm_api_client()
        
        # 2. Paginate through ALL photos
        offset = 0
        limit = 500  # API batch size
        photos_added = 0
        
        while True:
            batch = await api_client.photos.list_photos(
                limit=limit,
                offset=offset,
                additional=["thumbnail", "exif", "gps"]
            )
            
            if not batch:
                break
                
            # 3. Process each photo
            for photo in batch:
                metadata = self._extract_metadata(photo)
                await self.cache.add_file(metadata)
                photos_added += 1
                
            offset += limit
            
            # Rate limiting
            await asyncio.sleep(0.1)
            
        return photos_added
        
    async def _get_dsm_api_client(self):
        """Access Synology DSM integration's API client."""
        dsm_data = self.hass.data.get("synology_dsm")
        if not dsm_data or self.dsm_entry_id not in dsm_data:
            raise ValueError("Synology DSM integration not found")
            
        return dsm_data[self.dsm_entry_id]["api"]
        
    def _extract_metadata(self, photo) -> dict:
        """Extract metadata from Synology Photos API response.
        
        Returns:
            Dict with Media Index metadata format
        """
        # Construct media-source URI
        device_id = photo.get("device_id")
        photo_id = photo.get("id")
        uri = f"media-source://synology_dsm/{device_id}/photos/{photo_id}"
        
        return {
            "path": uri,
            "filename": photo.get("filename"),
            "file_type": "image" if photo.get("type") == "photo" else "video",
            "file_size": photo.get("filesize"),
            "date_taken": photo.get("exif", {}).get("takendate"),
            "latitude": photo.get("gps", {}).get("lat"),
            "longitude": photo.get("gps", {}).get("lng"),
            "width": photo.get("exif", {}).get("width"),
            "height": photo.get("exif", {}).get("height"),
            "orientation": photo.get("exif", {}).get("orientation"),
            # Synology-specific metadata
            "album": photo.get("album"),
            "tags": photo.get("tags", []),
            "rating": photo.get("rating"),
            "people": photo.get("people", []),
        }
```

#### 2. Scanner Factory (`scanner_factory.py`)

```python
class ScannerFactory:
    """Factory to create appropriate scanner based on backend type."""
    
    @staticmethod
    def create_scanner(hass, cache_manager, config):
        backend_type = config.get("backend_type", "filesystem")
        
        if backend_type == "filesystem":
            return MediaScanner(cache_manager, hass)
        elif backend_type == "synology_photos":
            dsm_entry_id = config.get("synology_dsm_entry_id")
            return SynologyPhotosScanner(hass, cache_manager, dsm_entry_id)
        else:
            raise ValueError(f"Unknown backend type: {backend_type}")
```

#### 3. Config Flow Updates

```python
# In config_flow.py

BACKEND_TYPES = ["filesystem", "synology_photos"]

async def async_step_user(self, user_input=None):
    """Handle initial setup."""
    
    # Step 1: Choose backend type
    if user_input is None:
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required("backend_type", default="filesystem"): vol.In(BACKEND_TYPES)
            })
        )
    
    backend_type = user_input["backend_type"]
    
    # Step 2: Backend-specific configuration
    if backend_type == "filesystem":
        return await self.async_step_filesystem()
    elif backend_type == "synology_photos":
        return await self.async_step_synology()

async def async_step_synology(self, user_input=None):
    """Configure Synology Photos backend."""
    
    # Get list of configured Synology DSM integrations
    dsm_entries = [
        entry for entry in self.hass.config_entries.async_entries()
        if entry.domain == "synology_dsm"
    ]
    
    if not dsm_entries:
        return self.async_abort(reason="no_synology_dsm")
    
    dsm_choices = {entry.entry_id: entry.title for entry in dsm_entries}
    
    return self.async_show_form(
        step_id="synology",
        data_schema=vol.Schema({
            vol.Required("synology_dsm_entry_id"): vol.In(dsm_choices),
            vol.Optional("scan_schedule", default="daily"): vol.In(SCAN_SCHEDULES),
        })
    )
```

#### 4. Media Card URI Handling

```javascript
// In ha-media-card-v5a.js

_getMediaUrl(mediaPath) {
  // Already handles media-source:// URIs correctly
  // Synology URIs like media-source://synology_dsm/{device}/photos/{id}
  // will work automatically through HA's media source resolution
  
  if (mediaPath.startsWith('media-source://')) {
    return `/api/media_source/resolve?media_content_id=${encodeURIComponent(mediaPath)}`;
  }
  
  // ... existing logic
}
```

## Implementation Phases

### Phase 1: Proof of Concept (4 hours)

**Goal**: Verify API access and photo fetch bypass

1. Create test script to access Synology DSM integration data
2. Call Synology Photos API with pagination
3. Test fetching photo #5000 by direct URI (bypassing 1000 browse limit)
4. Document API response structure

**Validation Criteria**:
- Can access DSM integration's API client
- Can paginate through >1000 photos
- Can fetch individual photos by URI regardless of browse position

### Phase 2: Scanner Implementation (2 days)

**Goal**: Build functional Synology Photos scanner

1. Implement `SynologyPhotosScanner` class
2. Add pagination with rate limiting
3. Extract metadata (EXIF, GPS, albums, people)
4. Store in Media Index cache
5. Handle API errors and retries

**Validation Criteria**:
- Scans 10K+ photo library successfully
- Respects rate limits (no API errors)
- Metadata extraction works for all photo types
- Cache stores photos with correct schema

### Phase 3: Config Flow (4 hours)

**Goal**: UI to configure Synology Photos backend

1. Add backend type selection
2. Create Synology-specific config step
3. Dropdown of available DSM integrations
4. Validate DSM integration has Photos enabled

**Validation Criteria**:
- Users can select Synology Photos backend
- UI shows available DSM instances
- Config validates DSM integration exists

### Phase 4: Media Card Integration (2 hours)

**Goal**: Display Synology photos in media card

1. Verify media card handles `media-source://synology_dsm/...` URIs
2. Test random mode with Synology photos
3. Test subfolder mode (Synology albums as folders)
4. Performance testing with large libraries

**Validation Criteria**:
- Photos display correctly in media card
- Random mode works across full library
- Album-based browsing works
- No performance degradation with 50K+ photos

### Phase 5: Testing & Polish (1 day)

**Goal**: Production-ready feature

1. Error handling for API failures
2. Incremental scanning for new photos
3. Deletion detection (photos removed from Synology)
4. Documentation and examples

**Validation Criteria**:
- Handles network failures gracefully
- Detects new photos without full rescan
- Removes deleted photos from cache
- README has Synology Photos setup guide

## Technical Challenges & Solutions

### Challenge 1: API Rate Limiting

**Problem**: Synology Photos API has rate limits (~10 requests/second)

**Solution**:
- Batch requests (500 photos per API call)
- Add configurable delays between batches
- Implement exponential backoff on rate limit errors
- Show progress in sensor attributes

### Challenge 2: Photo URL Construction

**Problem**: Need valid `media-source://` URIs for each photo

**Solution**:
- Study existing Synology DSM integration code
- Use same URI format: `media-source://synology_dsm/{device_id}/photos/{photo_id}`
- Validate URIs work with HA's media source resolution
- Store device_id in config for URI construction

### Challenge 3: Authentication & Sessions

**Problem**: Synology API requires authentication

**Solution**:
- Reuse existing DSM integration's authenticated session
- Access via `hass.data["synology_dsm"][entry_id]["api"]`
- No additional credentials needed
- Handle session expiration (DSM integration handles refresh)

### Challenge 4: Large Library Performance

**Problem**: 50K+ photos takes time to scan

**Solution**:
- Background task with progress reporting
- Store last_scan_time and only scan new photos incrementally
- Use efficient SQL queries for random selection
- Cache thumbnail URLs for faster display

### Challenge 5: Album/Folder Mapping

**Problem**: Synology has albums, Media Card has folders

**Solution**:
- Map Synology albums to virtual "folders"
- Store album info in metadata
- Support folder mode: `get_random_items(folder="Album Name")`
- List albums as subfolders in browser

## Database Schema Extensions

```sql
-- Add Synology-specific columns to media_files table
ALTER TABLE media_files ADD COLUMN synology_album TEXT;
ALTER TABLE media_files ADD COLUMN synology_tags TEXT; -- JSON array
ALTER TABLE media_files ADD COLUMN synology_people TEXT; -- JSON array
ALTER TABLE media_files ADD COLUMN synology_photo_id TEXT;
ALTER TABLE media_files ADD COLUMN synology_device_id TEXT;

-- Index for album-based queries
CREATE INDEX idx_synology_album ON media_files(synology_album);
```

## API Reference

### Synology Photos API Endpoints

Based on studying HA's `synology_dsm` integration:

```python
# List photos (paginated)
api.photos.list_photos(
    limit=500,          # Max 500 per request
    offset=0,           # Pagination offset
    additional=[        # Additional data to include
        "thumbnail",    # Thumbnail URL
        "exif",         # EXIF metadata
        "gps",          # GPS coordinates
        "tag",          # Tags/labels
        "person"        # Face recognition
    ]
)

# Get single photo details
api.photos.get_photo(photo_id)

# List albums
api.photos.list_albums()

# Get photos in album
api.photos.list_photos(album_id=123)
```

## Configuration Examples

### Filesystem Backend (Existing)

```yaml
# Existing behavior - no changes
media_index:
  - base_folder: /media/photos
    scan_schedule: hourly
```

### Synology Photos Backend (New)

```yaml
# New Synology Photos backend
media_index:
  - backend_type: synology_photos
    synology_dsm_entry_id: abc123...
    scan_schedule: daily
    extract_exif: true
    geocode_enabled: true
```

### Media Card Configuration

```yaml
# Works automatically with Synology photos
type: custom:ha-media-card
media_source_type: media_index
media_index:
  entity_id: sensor.media_index_synology_photos_total_files
folder:
  mode: random
  count: 1
```

## Testing Plan

### Unit Tests

1. Mock Synology DSM API responses
2. Test pagination logic
3. Test metadata extraction
4. Test URI construction
5. Test error handling

### Integration Tests

1. Test with real Synology DSM instance
2. Scan library of various sizes (100, 1K, 10K, 50K photos)
3. Verify all photos accessible in media card
4. Test album-based filtering
5. Test incremental scanning

### Performance Tests

1. Measure scan time for 10K photos
2. Measure random photo selection time
3. Measure memory usage during scan
4. Test concurrent access (multiple media cards)

## Documentation Updates

### README.md

Add section:

```markdown
## Synology Photos Support

Media Index can scan your entire Synology Photos library, bypassing the 1000-photo 
limit in Home Assistant's media browser.

### Requirements
- Synology DSM integration configured in Home Assistant
- Synology Photos enabled on your NAS

### Setup
1. Add Media Index integration
2. Select "Synology Photos" as backend type
3. Choose your Synology DSM instance
4. Configure scan schedule

### Features
- ✅ Scan unlimited photos (not limited to 1000)
- ✅ Extract EXIF metadata (GPS, date, camera)
- ✅ Support albums as folders
- ✅ People detection and filtering
- ✅ Tag-based searching
```

### Troubleshooting Guide

```markdown
## Synology Photos Issues

**Problem**: No photos found after scan
- Verify Synology Photos is enabled in DSM
- Check DSM integration has Photos permission
- Review logs for API errors

**Problem**: Slow scanning
- Reduce concurrent_scans setting
- Increase scan_schedule to daily/weekly
- Check network connection to NAS

**Problem**: Photos won't display
- Verify HA can reach NAS
- Check DSM integration is connected
- Test individual photo URI in media browser
```

## Future Enhancements

### Phase 2 Features (Post-MVP)

1. **Smart Album Support**: Scan Synology's smart albums
2. **People Filtering**: Filter by face recognition results
3. **Tag-Based Random**: Random photos matching specific tags
4. **Two-Way Sync**: Delete from Synology when deleted in HA
5. **Shared Library**: Support Synology Photos shared albums
6. **Video Support**: Include videos from Synology Photos
7. **Conditional Albums**: Create virtual albums based on EXIF data

### Integration with Other Services

Same approach could work for:
- **Google Photos**: Via unofficial API
- **Immich**: Self-hosted photo management
- **PhotoPrism**: AI-powered photo app
- **Nextcloud**: Photos app

## Risk Assessment

| Risk | Severity | Mitigation |
|------|----------|------------|
| API changes breaking scanner | Medium | Pin to API version, monitor DSM updates |
| Rate limiting causing failures | Medium | Implement backoff, configurable delays |
| Large scans blocking HA | Low | Background tasks, progress tracking |
| Authentication issues | Low | Reuse DSM integration's auth |
| Photos not displaying | Low | Validate URIs during scan |

## Success Metrics

- **Scan Performance**: 10K photos in <5 minutes
- **Memory Usage**: <500MB during scan
- **API Reliability**: <1% failed requests
- **User Adoption**: 50+ users within 3 months
- **Bug Reports**: <5 critical bugs in first release

## Go/No-Go Decision Criteria

✅ **GO** if:
- PoC successfully bypasses 1000-photo limit
- Can access DSM API client
- Photo fetch by direct URI works
- Community interest confirmed

❌ **NO-GO** if:
- Can't access DSM API client from integration
- Direct photo fetch limited to browse list
- API rate limits too restrictive
- Synology breaks compatibility frequently

## Timeline Estimate

| Phase | Duration | Dependencies |
|-------|----------|--------------|
| PoC | 4 hours | Synology DSM integration installed |
| Scanner | 2 days | PoC successful |
| Config Flow | 4 hours | Scanner complete |
| Media Card | 2 hours | Config flow complete |
| Testing | 1 day | All features implemented |
| Documentation | 4 hours | Testing complete |
| **TOTAL** | **4 days** | - |

## Next Steps

When ready to implement:

1. **Verify Prerequisites**:
   - User has Synology DSM integration configured
   - User has Photos enabled in Synology DSM
   - Confirm HA can access synology_dsm integration data

2. **Run PoC Script** (see `scripts/poc-synology-photos.py`):
   ```python
   # Test API access
   python scripts/poc-synology-photos.py
   ```

3. **Create Feature Branch**:
   ```bash
   git checkout -b feature/synology-photos-backend
   ```

4. **Implement Phase by Phase**:
   - Start with scanner
   - Add config flow
   - Wire up media card
   - Test and polish

5. **Beta Testing**:
   - Release as beta to 5-10 users
   - Gather feedback on API reliability
   - Fix bugs before general release

## Conclusion

**Feasibility**: HIGH ✅  
**Effort**: MEDIUM (4 days)  
**Value**: VERY HIGH 🌟  
**Priority**: Post-v1.0 release

This is a high-value feature that would differentiate Media Index from simple filesystem scanners. The key insight that individual photo fetch bypasses the browse limit makes this architecturally sound. Recommend implementing after v1.0 stable release of media card and media index.
