# gps_tag.sh — GPS Backfill Tool

A Bash script that automatically backfills GPS coordinates into photos and videos that lack them, by matching timestamps against nearby GPS-enabled files (phone photos, etc.).

---

## Table of Contents

1. [Overview](#overview)
2. [Requirements](#requirements)
3. [How it Works](#how-it-works)
4. [Modes of Operation](#modes-of-operation)
   - [Auto Mode](#auto-mode)
   - [Donor Mode](#donor-mode)
   - [Merge Caches Mode](#merge-caches-mode)
   - [DSLR Recursive Mode](#dslr-recursive-mode)
   - [Fix Refs Mode](#fix-refs-mode)
   - [Fix Tagged Mode](#fix-tagged-mode)
5. [State Files](#state-files)
6. [Configuration](#configuration)
7. [Typical Workflows](#typical-workflows)
8. [Scheduling on Synology](#scheduling-on-synology)
9. [Troubleshooting](#troubleshooting)

---

## Overview

When you shoot with a DSLR or a camera that has no GPS, those images land in your photo library without location data. `gps_tag.sh` solves this by correlating the timestamp of each GPS-less file against a pool of "donor" GPS coordinates from phone photos taken at the same time, then writing the matched coordinates directly into the file's EXIF/XMP metadata.

**Supported file types:** JPG, JPEG, MP4, MOV

---

## Requirements

| Tool | Notes |
|------|-------|
| **exiftool** | v12+. Path auto-detected or set via `EXIFTOOL_BIN`. |
| **bash** | v4+ (for arrays, `[[ ]]`, process substitution). |
| **awk** | Any POSIX awk (`gawk`, `mawk`, `nawk` all work). |
| **sort / comm / find** | Standard GNU/BSD coreutils. |

On Synology NAS, install exiftool via Entware (`opkg install perl-image-exiftool`) or place it at `/usr/share/applications/ExifTool/exiftool`.

---

## How it Works

### Timestamp matching

For every GPS-less file the script finds a "donor" — a photo or video taken within **±24 hours** that *does* have GPS coordinates. The donor with the smallest time delta wins.

The ±24h window is intentionally wide to handle situations where:
- You put the DSLR away and only took out your phone later
- Timezone offsets were set incorrectly in the camera

### GPS writing

- **JPG/JPEG**: Uses absolute-value latitude/longitude + `GPSLatitudeRef` / `GPSLongitudeRef` for maximum compatibility with Synology, Windows Photos, and Apple Photos.
- **MP4/MOV**: Writes the ISO 6709 location string to the QuickTime `©xyz` user-data atom in the format Windows Photos and Synology recognize (e.g. `+47.5914-122.2308/`).
- **Corrupt files**: Automatically retries with a clean-copy approach (`-o TMPFILE`) for Samsung files with truncated trailers, panoramas with appended data, and similar edge cases.

### Incremental runs

Each target folder gets a hidden `.gps_cache` TSV that records every file already processed. On subsequent runs only *new* files are read, keeping re-runs very fast.

---

## Modes of Operation

### Auto Mode

> Best for: Synology scheduled tasks, cron jobs on phone camera-roll folders.

```bash
gps_tag.sh FOLDER
```

Reads all media in `FOLDER`, builds / updates `.gps_cache`, and attempts to donate GPS coordinates among files within the same folder. Files that gain GPS from a phone photo shot at nearly the same time get tagged automatically.

**Example:**
```bash
gps_tag.sh "/volume1/photo/Mark/Camera/2024-05"
```

---

### Donor Mode

> Best for: one-shot backfills where you know exactly which phone folder was the GPS source.

```bash
gps_tag.sh --donor DONOR_FOLDER TARGET_FOLDER
```

Builds (or reuses) a GPS cache for `DONOR_FOLDER` and matches coordinates to GPS-less files in `TARGET_FOLDER`. The donor cache is saved to `DONOR_FOLDER/.gps_cache` for reuse on future runs.

**Example:**
```bash
gps_tag.sh --donor "/volume1/photo/Mark/Camera/2024-05" \
           "/volume1/photo/DSLR/events/2024-05-wedding"
```

---

### Merge Caches Mode

> Best for: combining GPS histories from multiple phones before a recursive DSLR backfill.

```bash
gps_tag.sh --merge-caches OUTPUT.tsv CACHE [CACHE ...]
gps_tag.sh --merge-caches OUTPUT.tsv --scan DIR [--scan DIR ...]
```

Collects all GPS-populated rows from the specified `.gps_cache` files, deduplicates by filename (keeping the most recently seen entry), sorts by UTC epoch, and writes a single merged TSV. A comment header is prepended with the epoch range — this is required by `--dslr-recurse`.

| Argument | Description |
|----------|-------------|
| `OUTPUT.tsv` | Path for the merged output file |
| `CACHE [...]` | One or more explicit `.gps_cache` file paths |
| `--scan DIR` | Recursively find all `.gps_cache` files under `DIR` |

`--scan` and explicit cache paths can be freely mixed.

**Example — scan all phone camera folders:**
```bash
gps_tag.sh --merge-caches /volume1/scripts/merged_donors.tsv \
    --scan /volume1/photo/Mark/Camera \
    --scan /volume1/photo/Tanya/Camera
```

---

### DSLR Recursive Mode

> Best for: backfilling years of archived DSLR photos in one run.

```bash
gps_tag.sh --dslr-recurse MERGED.tsv DSLR_ROOT
```

Walks every subfolder of `DSLR_ROOT` and for each one:

1. **Pre-scan gate**: Uses `exiftool -fast2` to read only `DateTimeOriginal` from uncached files. If no file falls within the donor epoch range (±24h of donor cache bounds), the folder is skipped cheaply without a full metadata read.
2. **Date-range check**: After batch-reading, confirms at least one unresolved file overlaps the donor window.
3. **Matching**: Runs the same ±24h timestamp matching used in other modes against the merged donor cache.
4. **Cache update**: Each subfolder's `.gps_cache` is updated so future runs skip already-processed files.

Synology `@eaDir` thumbnail directories are excluded automatically from the folder walk.

**Example:**
```bash
gps_tag.sh --dslr-recurse /volume1/scripts/merged_donors.tsv \
           "/volume1/photo/DSLR"
```

#### Recommended workflow for large archives

```bash
# Step 1: build the merged donor cache (run once, re-run to refresh)
gps_tag.sh --merge-caches /volume1/scripts/merged_donors.tsv \
    --scan /volume1/photo/Mark/Camera \
    --scan /volume1/photo/Tanya/Camera

# Step 2: backfill the entire DSLR archive
gps_tag.sh --dslr-recurse /volume1/scripts/merged_donors.tsv \
           "/volume1/photo/DSLR"
```

---

### Fix Refs Mode

> Best for: correcting files written by older versions of this script that used signed-decimal GPS without the required `GPSLatitudeRef`/`GPSLongitudeRef` reference tags.

```bash
gps_tag.sh --fix-refs FOLDER
```

Scans `FOLDER` for JPG/JPEG files that have GPS latitude/longitude but no Ref tags, and adds the correct `N`/`S` and `E`/`W` references in-place. These Ref tags are required for correct rendering in Synology Photos, Windows Photos, and Apple Photos.

**Example:**
```bash
gps_tag.sh --fix-refs "/volume1/photo/DSLR/2019"
```

---

### Fix Tagged Mode

> Best for: manually correcting specific files that were previously tagged with wrong GPS data, using Synology Photos keyword workflow.

```bash
gps_tag.sh --fix-tagged FOLDER [--ref REF_FILE] [--recurse]
```

Searches `FOLDER` for media files that contain a `fixgps` keyword in their EXIF `Subject`/`Keywords` field, writes the correct GPS coordinates, and removes the keyword afterward.

Add `--recurse` to walk all subfolders of `FOLDER` instead of just its root level. Synology `@eaDir` thumbnail directories are excluded automatically.

#### Tag forms

You set these keywords in **Synology Photos** (or any IPTC/XMP keyword editor):

| Keyword | Coordinates source |
|---------|--------------------|
| `fixgps` | From `--ref REF_FILE` — a photo that already has the correct GPS location |
| `fixgps:47.6205:-122.3493` | Embedded in the tag itself (colon-separated decimal coordinates) |

Both forms can coexist in the same folder: files tagged `fixgps:LAT:LON` use the embedded coords; files tagged `fixgps` use the `--ref` file's coords.

#### Examples

```bash
# All files tagged 'fixgps:47.6205:-122.3493' — no --ref needed:
gps_tag.sh --fix-tagged "/volume1/photo/DSLR/2019-07"

# Files tagged 'fixgps' — supply a phone photo with correct coords:
gps_tag.sh --fix-tagged "/volume1/photo/DSLR/2019-07" \
           --ref "/volume1/photo/Mark/Camera/2019-07/IMG_1234.jpg"

# Recursively process an entire year folder (embedded coords):
gps_tag.sh --fix-tagged "/volume1/photo/DSLR/2019" --recurse

# Recursively process an entire year folder with --ref coords:
gps_tag.sh --fix-tagged "/volume1/photo/DSLR/2019" --recurse \
           --ref "/volume1/photo/Mark/Camera/2019-07/IMG_1234.jpg"
```

#### Step-by-step workflow

1. In Synology Photos, select the photo(s) with wrong GPS.
2. Find a photo/video with the correct GPS coordinates, or go to Google Maps and find the location where the image was taken - right click on a pin, and copy the coordinates.
3. Add keyword `fixgps:47.6205:-122.3493` (replace with actual coordinates - you will need to edit to make them colon-delimited, no spaces at all), **or** add keyword `fixgps` if you intend to supply a `--ref` file.
4. Run `gps_tag.sh --fix-tagged FOLDER` (with `--ref` if needed).
5. The keyword is automatically removed from each file after GPS is written.

---

## State Files

Each folder managed by this script contains hidden state files:

| File | Contents |
|------|----------|
| `.gps_cache` | TSV: `filename \| utc_epoch \| lat \| lon \| first_seen_epoch`. Empty `lat`/`lon` = GPS not yet resolved. `WRITE_FAILED` = permanently skipped. |
| `.gps_state` | Unix epoch timestamp of the last auto-mode run. |
| `.gps_lock` | PID of the currently running instance (auto mode only, prevents overlapping runs). |
| `.gps_tag.log` | Append-only log of all actions. |

---

## Configuration

### `EXIFTOOL_BIN`

Set this environment variable to override the automatically detected exiftool path. Required in cron/scheduled-task environments where `PATH` is minimal.

```bash
EXIFTOOL_BIN=/usr/local/bin/exiftool gps_tag.sh FOLDER

# Or export it in your profile / task environment:
export EXIFTOOL_BIN=/usr/share/applications/ExifTool/exiftool
```

### `WINDOW_SECS`

Defined in the script header (default: `86400` = 24 hours). The maximum timestamp difference allowed between a target file and its donor.

### `PENDING_EXPIRY_DAYS`

Defined in the script header (default: 30 days). Auto mode will stop retrying GPS-less files older than this threshold and remove them from the cache.

---

## Typical Workflows

### New camera roll → automatic backfill

Run once per camera-roll folder (e.g. via Synology scheduled task):
```bash
gps_tag.sh "/volume1/photo/DSLR/Camera Roll/2024-06"
```

### DSLR event shoot alongside a phone

```bash
# After the event, run donor mode:
gps_tag.sh --donor "/volume1/photo/Mark/Camera/2024-06" \
           "/volume1/photo/DSLR/2024-06-concert"
```

### Bulk backfill of an entire DSLR archive

```bash
# 1. Build merged donor cache from all phone libraries:
gps_tag.sh --merge-caches /volume1/scripts/merged_donors.tsv \
    --scan /volume1/photo/Mark/Camera \
    --scan /volume1/photo/Tanya/Camera

# 2. Walk and tag the whole DSLR tree:
gps_tag.sh --dslr-recurse /volume1/scripts/merged_donors.tsv \
           "/volume1/photo/DSLR"
```

### Fix a handful of photos with wrong location

```bash
# In Synology Photos: tag target files with 'fixgps:47.6205:-122.3493'
# Then run:
gps_tag.sh --fix-tagged "/volume1/photo/DSLR/2019-07"
```

---

## Scheduling on Synology

1. Open **Control Panel → Task Scheduler → Create → Scheduled Task → User-defined script**.
2. Set a run schedule (e.g. daily at 2:00 AM).
3. In the **Task Settings** body:
   ```bash
   EXIFTOOL_BIN=/usr/share/applications/ExifTool/exiftool
   /volume1/scripts/gps_tag.sh "/volume1/photo/Mark/Camera/$(date +%Y-%m)"
   ```
4. **Important**: Always set `EXIFTOOL_BIN` explicitly in the task body — scheduled tasks run with a minimal `PATH` that won't find exiftool automatically.

---

## Troubleshooting

### "exiftool not found"
Set `EXIFTOOL_BIN` to the full path of your exiftool binary.

### "merged cache missing epoch range header"
The `MERGED.tsv` was not created by `--merge-caches` (or was hand-edited). Regenerate it with `--merge-caches`.

### Files not getting tagged despite a phone photo from the same day
- Check `.gps_tag.log` for `SKIP (no donor in ±24h)` messages.
- Verify the phone photo actually has GPS embedded: `exiftool -GPSLatitude PHONE_PHOTO.jpg`
- Confirm the phone's `.gps_cache` is included in the merged donor cache.

### `WRITE FAILED` entries in the log
The file may be on a read-only share, or exiftool encountered an unrecoverable format error. Check file permissions and try writing manually: `exiftool -GPSLatitude=47.59 -GPSLongitude=-122.23 FILE.jpg`

### GPS shows in wrong hemisphere (S instead of N, etc.)
Run `--fix-refs` on the folder to add the missing `GPSLatitudeRef`/`GPSLongitudeRef` reference tags.
