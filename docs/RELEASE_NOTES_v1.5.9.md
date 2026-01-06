# Media Index v1.5.9 Release Notes

**Release Date:** January 5, 2026

## Overview

Version 1.5.9 adds Unix timestamp filtering to the `get_random_items` service, enabling timezone-independent date matching for features like "Same Date" and "Through the Years" in Media Card v5.6.7+.

## What's Fixed

### Timezone-Independent Date Filtering

**The Problem:**
- Media Card's "Same Date" feature was showing photos spanning 2-3 calendar days
- Date string filtering (e.g., `date_from: "2019-12-25"`) converted to UTC, causing timezone offsets
- Photos taken at 11:00 PM on Dec 25 would appear in Dec 26 queries (or vice versa)
- Users expected to see ONLY photos from the selected calendar day

**The Solution:**
- Added `timestamp_from` and `timestamp_to` parameters to `get_random_items` service
- Unix timestamps represent absolute moments in time (seconds since epoch)
- No timezone conversion - direct numerical comparison against database values
- Timestamps take precedence over `date_from`/`date_to` when both are provided

## New Features

### Unix Timestamp Parameters

The `get_random_items` service now accepts:

```yaml
service: media_index.get_random_items
data:
  count: 20
  timestamp_from: 1577260800  # 2019-12-25 00:00:00 PST
  timestamp_to: 1577347199    # 2019-12-25 23:59:59 PST
```

**Key Details:**
- **Format:** Integer seconds since Unix epoch (January 1, 1970 00:00:00 UTC)
- **Optional:** Backward compatible - existing `date_from`/`date_to` still work
- **Precedence:** Timestamps override date strings when both provided
- **Comparison:** Uses `COALESCE(e.date_taken, m.created_time)` from database

## Technical Changes

### Service Schema Updates

**File:** `custom_components/media_index/__init__.py`

Added two optional parameters to `SERVICE_GET_RANDOM_ITEMS_SCHEMA`:

```python
vol.Optional("timestamp_from"): cv.positive_int,
vol.Optional("timestamp_to"): cv.positive_int,
```

### Database Query Updates

**File:** `custom_components/media_index/cache_manager.py`

Modified both execution paths:

1. **Standard Random Mode** (`get_random_files()`, lines 1043-1070):
   - Check `timestamp_from`/`timestamp_to` first
   - Fall back to `date_from`/`date_to` if timestamps not provided
   - Direct SQL comparison: `WHERE timestamp >= ? AND timestamp < ?`

2. **Priority New Files Mode** (`_get_random_excluding()`, lines 873-907):
   - Same timestamp precedence logic
   - Applies filtering before 70/30 weighted selection
   - Ensures new file prioritization respects date range

### Backward Compatibility

✅ **Fully backward compatible** - no breaking changes:

- Existing integrations using `date_from`/`date_to` continue to work
- No configuration changes required
- No database schema changes
- No re-scan needed

## Usage Examples

### Same Date Feature (Media Card v5.6.7+)

When you open "Related Photos" → "Same Date", Media Card:

1. Extracts Unix timestamp from current photo's EXIF data
2. Calculates start/end of that calendar day (00:00:00 - 23:59:59)
3. Calls `get_random_items` with `timestamp_from`/`timestamp_to`
4. Receives ONLY photos from that exact calendar day

**Before v1.5.9:**
- Query for Dec 25, 2019 might include Dec 24/26 photos
- Timezone conversion caused ±1 day errors
- Users confused by wrong dates appearing

**After v1.5.9:**
- Query returns ONLY Dec 25, 2019 photos
- No timezone conversion issues
- Calendar day boundaries respected exactly

### Through the Years Feature

Works with anniversary mode to show photos from the same date across multiple years:

```yaml
service: media_index.get_random_items
data:
  count: 50
  anniversary_month: "12"
  anniversary_day: "25"
  timestamp_from: 1577260800  # Optional: limit to specific year range
  timestamp_to: 1609401599    # End of 2020
```

## Integration with Media Card v5.6.7+

Media Card v5.6.7 now uses timestamp filtering for:

- **Same Date Panel:** Photos from current photo's date
- **On This Day Panel:** Photos from today's date in past years
- **Related Photos:** Burst detection with same-day filtering

**No User Action Required:**
1. Update Media Index to v1.5.9
2. Update Media Card to v5.6.7+
3. Hard refresh browser (Ctrl+Shift+R)
4. Features work automatically - no re-scan needed

## For Developers

### API Documentation

Full documentation available in:
- `docs/SERVICES.md` - Service reference with examples
- `docs/DEVELOPER_API.md` - WebSocket API integration guide

### Timestamp Conversion Examples

**Python:**
```python
import datetime

# Convert date to timestamp
date = datetime.datetime(2019, 12, 25, 0, 0, 0)
timestamp_from = int(date.timestamp())  # 1577260800

# Convert timestamp to date
timestamp = 1577260800
date = datetime.datetime.fromtimestamp(timestamp)  # 2019-12-25 00:00:00
```

**JavaScript:**
```javascript
// Convert Date to timestamp
const date = new Date('2019-12-25T00:00:00');
const timestamp_from = Math.floor(date.getTime() / 1000);  // 1577260800

// Convert timestamp to Date
const timestamp = 1577260800;
const date = new Date(timestamp * 1000);  // 2019-12-25 00:00:00
```

### Service Call Example

```javascript
// WebSocket API call with timestamp filtering
const response = await this.hass.callWS({
  type: 'call_service',
  domain: 'media_index',
  service: 'get_random_items',
  service_data: {
    count: 100,
    timestamp_from: 1577260800,
    timestamp_to: 1577347199
  },
  return_response: true
});
```

## Testing

Verified on Home Assistant Core 2024.x with:
- ✅ Standard random mode filtering
- ✅ Priority new files mode filtering
- ✅ Timestamp precedence over date strings
- ✅ Backward compatibility with date strings
- ✅ Integration with Media Card v5.6.7
- ✅ Same Date feature accuracy
- ✅ Through the Years anniversary matching

## Upgrade Instructions

### For End Users

1. **Update Media Index** to v1.5.9 (via HACS or manual install)
2. **Restart Home Assistant** (Configuration → Server Controls → Restart)
3. **Update Media Card** to v5.6.7+ (separate component)
4. **Hard refresh browser** (Ctrl+Shift+R or Ctrl+F5)

**No re-scan required** - existing database works with new parameters.

### For HACS Users

1. Go to HACS → Integrations
2. Find "Media Index"
3. Click "Update" (if available)
4. Restart Home Assistant
5. Update Media Card separately (HACS → Frontend)

### For Manual Install Users

1. Download release from GitHub
2. Copy `custom_components/media_index/` to your HA config
3. Restart Home Assistant

## Known Limitations

- Timestamp parameters are **optional** - feature detection not automatic
- Media Card v5.6.7+ required to use timestamp filtering
- Older Media Card versions continue using date string filtering (with timezone issues)

## Breaking Changes

**None** - fully backward compatible with v1.5.8 and earlier.

## Related Changes

See [Media Card v5.6.7 Release Notes](../../Media%20Item%20Card/dev-docs/RELEASE_NOTES_v5.6.7.md) for frontend changes.

---

**Questions or Issues?** Please report on GitHub Issues.
