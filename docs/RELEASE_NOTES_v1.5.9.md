# Media Index v1.5.9 Release Notes

**Release Date:** January 7, 2026

## What's Fixed

### Same Date / Through the Years Date Accuracy

The "Same Date" and "Through the Years" features in Media Card were showing photos spanning 2-3 days instead of the exact date selected. This has been fixed.

**The Problem:**
- Clicking "Same Date" on a photo taken December 25th could return photos from December 24-26
- Timezone conversion was causing ±1 day errors in date filtering

**The Fix:**
- Added Unix timestamp filtering to `get_random_items` service
- Timestamps are compared directly without timezone conversion
- Results now match the exact calendar day requested

> **Requires:** Media Card v5.6.7 or later to use this feature

---

## Upgrade Instructions

1. Update integration via HACS or manually copy files
2. Restart Home Assistant
3. Update Media Card to v5.6.7 to use the Same Date fix

No database changes or re-scan required.

---

## For Developers

### New Service Parameters

The `get_random_items` service now accepts optional timestamp parameters:

```yaml
service: media_index.get_random_items
data:
  count: 20
  timestamp_from: 1577260800  # Start of day (Unix timestamp)
  timestamp_to: 1577347199    # End of day (Unix timestamp)
```

**Details:**
- **Format:** Integer seconds since Unix epoch
- **Precedence:** Timestamps override `date_from`/`date_to` when both provided
- **Backward Compatible:** Existing `date_from`/`date_to` parameters still work
