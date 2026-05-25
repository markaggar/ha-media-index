# fix_videos.sh — Video Normalisation Tool

A Bash script that normalises video files in a single encode pass: fixes misoriented portrait videos, converts legacy formats (WMV/AVI/MTS/MOV) to MP4, and re-encodes browser-incompatible codecs to HEVC or H.264. All applicable fixes are applied in one Docker QSV hardware-encode pass per file.

---

## Table of Contents

1. [Overview](#overview)
2. [Requirements](#requirements)
3. [How it Works](#how-it-works)
4. [Options Reference](#options-reference)
5. [State Files](#state-files)
6. [Typical Workflows](#typical-workflows)
7. [Scheduling on Synology](#scheduling-on-synology)
8. [Troubleshooting](#troubleshooting)

---

## Overview

`fix_videos.sh` detects four kinds of problems and resolves them:

| Flag | Problem | Resolution |
|------|---------|------------|
| **R** | Portrait video encoded sideways (tkhd rotation 90°/270°, width > height) | Re-encode with correct orientation |
| **F** | Legacy container: `.wmv`, `.avi`, `.mts`, or `.mov` | Convert to MP4 |
| **C** | Non-browser-safe video codec (anything other than H.264 `avc1` or HEVC `hvc1`/`hev1`) | Re-encode to HEVC or H.264 |
| **A** | Non-AAC audio codec | Transcode audio to AAC 128 kbps stereo |

Multiple flags can apply at once — the script always encodes in a single pass regardless of how many fixes are needed.

**Fast paths (no QSV required):**
- **F-only, compatible container** (e.g. MOV with H.264+AAC already): stream-copy via `-c:v copy -c:a copy` — no quality loss, very fast.
- **A-only on MP4/MOV**: stream-copy video, transcode audio only.

**Metadata preservation:** GPS coordinates and date-taken are captured from the original *before* encoding via exiftool and written back *after*, ensuring they survive cross-format conversions where ffmpeg `-map_metadata` may not carry all fields across container boundaries (e.g. WMV → MP4).

---

## Requirements

| Component | Notes |
|-----------|-------|
| **Docker** | Must be installed and the current user must be able to run `docker run`. |
| **linuxserver/ffmpeg Docker image** | Pulled automatically on first use. Requires Intel QSV (`/dev/dri/renderD128`) for hardware encodes. |
| **exiftool** | v12+. Auto-detected or overridden via `EXIFTOOL_BIN`. |
| **bash** | v4+ (for arrays and `[[ ]]`). |
| **find / grep / awk** | Standard GNU/BSD coreutils. |

On Synology NAS, install exiftool via Entware:
```bash
opkg install perl-image-exiftool
```
Or place it at `/usr/share/applications/ExifTool/exiftool`.

The Intel QSV GPU must be exposed to Docker via `--device /dev/dri:/dev/dri`. The script handles this automatically using the `/dev/dri/renderD128` device.

---

## How it Works

### Detection

Before touching any file the script runs a lightweight ffprobe pass (via Docker) to detect:

- **Rotation**: reads `tkhd` rotation tag; compares encoded width vs height to avoid false positives on already-corrected files.
- **Format**: checks the file extension — `.wmv`, `.avi`, `.mts` always trigger F; `.mov` triggers F only when `--fix-formats` is active (which is the default).
- **Video codec**: checks the codec tag against the browser-safe list (`avc1`, `hvc1`, `hev1`).
- **Audio codec**: checks for `mp4a` (AAC); any other codec sets the A flag.

### Encode path selection

```
Has C or R flag?           → PATH 2: full QSV hardware re-encode
  └─ hevc_qsv (default) or h264_qsv (--h264)
  └─ fallback to libx264 software if QSV fails

Has F flag only (safe codecs)?  → PATH 1: stream copy (fast remux)
Has A flag only?                → PATH 1: copy video, transcode audio
```

### Colour metadata

For PATH 2 (full re-encode), the script probes `color_primaries`, `color_trc`, `color_space`, `color_range`, and `pix_fmt` from the *actual* video stream (using `-select_streams V:0` to skip embedded thumbnails). HDR/HLG metadata is preserved through the encode:

- Full-range sources (`yuvj420p`, `color_range=pc`) are converted via the filter graph (`format=nv12`) to avoid deprecated-pixel-format warnings.
- BT.2020 primaries without a TRC tag → HLG (`arib-std-b67`) is inferred.

### Bitrate targeting

The output bitrate matches the source bitrate (probed via ffprobe), capped at a sensible max/bufsize. Falls back to 10 Mbps when the source bitrate cannot be read.

### Incremental scanning

Each scanned folder gets a hidden `.fix_videos.state` file recording the epoch of the last successful run. On subsequent runs only files *newer* than that marker are examined — keeping large libraries fast.

---

## Options Reference

```
fix_videos.sh [OPTIONS] FOLDER
```

### Preservation (one is required)

| Option | Description |
|--------|-------------|
| `--backup-root DIR` | Move originals into `DIR`, mirroring the folder hierarchy relative to `FOLDER`. The corrected file takes the original path. |
| `--rename-hp` | Rename the original to `filename_hp.ext` before replacing. The `_hp` suffix is not recognised by ha-media-index as a separate media item. |

### Fix selection

All three fix types are **enabled by default**. Use these flags to disable specific ones.

| Option | Description |
|--------|-------------|
| `--no-rotation` | Skip rotation detection and correction. |
| `--no-formats` | Skip format conversion (WMV/AVI/MTS/MOV → MP4). |
| `--no-codecs` | Skip codec normalisation (C/A flags not checked). |

### Other options

| Option | Description |
|--------|-------------|
| `--h264` | Output H.264 QSV instead of HEVC QSV for re-encodes. Does **not** re-encode already-safe HEVC content. |
| `--recurse`, `-r` | Descend into sub-folders (default: top-level folder only). |
| `--dry-run` | Log candidates without encoding or modifying any files. |
| `--force` | Ignore per-folder state files; scan all files. Also allows overwriting conflicting output files. |
| `-h`, `--help` | Show usage and exit. |

### Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `EXIFTOOL_BIN` | Auto-detected | Override path to exiftool binary. |

---

## State Files

| File | Location | Purpose |
|------|----------|---------|
| `.fix_videos.state` | Each scanned folder | Epoch timestamp of last successful run. Delete to force a full rescan of that folder. |
| `.fix_videos.log` | Top-level `FOLDER` only | Append-only log of all runs and encode results. |

---

## Typical Workflows

### Convert a single WMV folder

```bash
fix_videos.sh \
  --backup-root /volume1/video_originals \
  /volume1/photo/PhotoLibrary/OldHomeVideos
```

### Recurse a large photo library (daily maintenance)

```bash
fix_videos.sh \
  --backup-root /volume1/video_originals \
  --recurse \
  /volume1/photo/PhotoLibrary
```

### Dry run first — see what would be changed

```bash
fix_videos.sh \
  --backup-root /volume1/video_originals \
  --recurse \
  --dry-run \
  /volume1/photo/PhotoLibrary
```

### Re-encode only codec problems (skip rotation/format fixes)

```bash
fix_videos.sh \
  --backup-root /volume1/video_originals \
  --no-rotation \
  --no-formats \
  /volume1/photo/DSLR/2018
```

### Force a full rescan (ignore state files)

```bash
fix_videos.sh \
  --backup-root /volume1/video_originals \
  --recurse \
  --force \
  /volume1/photo/PhotoLibrary
```

### Output H.264 instead of HEVC (for older devices)

```bash
fix_videos.sh \
  --backup-root /volume1/video_originals \
  --h264 \
  --recurse \
  /volume1/photo/PhotoLibrary
```

---

## Scheduling on Synology

Set up a scheduled task in **Control Panel → Task Scheduler** to run nightly during off-peak hours.

| Setting | Value |
|---------|-------|
| **User** | `root` |
| **Schedule** | Daily, e.g. 02:00 |
| **Command** | See below |

```bash
/volume1/photo/scripts/fix_videos.sh \
  --backup-root /volume1/video_originals \
  --recurse \
  /volume1/photo/PhotoLibrary
```

The incremental state files ensure each run only processes new or changed files, so runtime is short after the initial backfill.

---

## Troubleshooting

### "Nothing was encoded but files were detected"

Run with `--dry-run` to confirm the detection logic sees the files, then remove `--dry-run` and add `--force` to bypass state files.

### QSV hardware encode fails

The script automatically falls back to `libx264` software encoding when `hevc_qsv` fails. The fallback output is H.264 MP4 — valid and browser-safe, just slightly larger than HEVC.

Check that the Intel GPU device is available:
```bash
ls -la /dev/dri/
```

If `/dev/dri/renderD128` is absent, QSV is unavailable and all re-encodes will use the `libx264` software fallback.

### Embedded thumbnail treated as video stream

Some WMV/ASF files carry an embedded JPEG thumbnail as the first video stream. The script uses `-map 0:V:0` (capital V) to select only real video streams and skip attached pictures, and `-select_streams V:0` in ffprobe for the same reason.

### Metadata not restored after encode

If exiftool is not on `PATH` and `EXIFTOOL_BIN` is not set, the GPS/date restore step is silently skipped. Verify with:
```bash
which exiftool
# or
/usr/share/applications/ExifTool/exiftool -ver
```

### Log file location

Logs are written to `.fix_videos.log` in the top-level target folder:
```bash
tail -f /volume1/photo/PhotoLibrary/.fix_videos.log
```
