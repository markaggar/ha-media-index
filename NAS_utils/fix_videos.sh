#!/usr/bin/env bash
###############################################################################
# fix_videos.sh — Normalize video files in one pass:
#                 · fix misoriented portrait videos (rotation tag)
#                 · convert legacy formats (WMV/AVI/MTS/MOV) to MP4
#                 · re-encode browser-incompatible codecs to HEVC or H.264
#
# All three fixes are applied in a single encode pass per file via Docker QSV
# hardware encoding — the same infrastructure used by fix_video_rotation.sh.
# fix_video_rotation.sh is left untouched; this script is a superset.
#
# DETECTION
#   Rotation fix (R):  tkhd Rotation ∈ {90, 270} AND encoded width > height
#   Format fix  (F):   extension ∈ {wmv, avi, mts} (always when enabled);
#                      .mov files (when --fix-formats is active)
#   Codec fix   (C):   video codec ∉ {avc1, hvc1, hev1}  (not H.264 or HEVC)
#   Audio fix   (A):   audio codec ≠ mp4a  (not AAC)
#
#   "Browser-safe" video: H.264 (avc1) or HEVC (hvc1 / hev1).
#   "Browser-safe" audio: AAC (mp4a).
#   --h264 controls the OUTPUT codec only.  It does NOT trigger re-encoding of
#   already-safe HEVC content.
#
# FAST PATHS  (no QSV hardware needed)
#   F-only on a compatible container (e.g. MOV with good H.264+AAC):
#       → container remux (-c:v copy -c:a copy), no quality loss, very fast.
#   A-only on an MP4 or MOV:
#       → stream-copy video, transcode audio to AAC only.
#
# METADATA PRESERVATION
#   GPS (©xyz udta atom, ISO 6709) and date-taken (QuickTime date fields) are
#   captured from the original BEFORE encoding and written back AFTER via
#   exiftool — ensuring they survive cross-format conversions (WMV/AVI/MTS→MP4)
#   where ffmpeg -map_metadata may not carry dates/GPS across container boundaries.
#
# USAGE
#   fix_videos.sh [OPTIONS] FOLDER
#
# PRESERVATION (one required — originals are never silently overwritten):
#   --backup-root DIR   Move originals into DIR, mirroring the folder hierarchy
#                       relative to FOLDER.  Corrected file takes the original path.
#   --rename-hp         Rename original to filename_hp before replacing.
#                       The _hp suffix is not recognised by ha-media-index.
#
# FIX SELECTION (all enabled by default):
#   --no-rotation       Skip rotation detection and correction.
#   --no-formats        Skip format conversion (WMV/AVI/MTS/MOV → MP4).
#   --no-codecs         Skip codec normalization (C/A detection disabled).
#
# OTHER OPTIONS:
#   --h264              Output H.264 QSV instead of HEVC QSV.
#                       Does NOT re-encode already-safe HEVC content.
#   --recurse, -r       Descend into sub-folders (default: top-level only).
#   --dry-run           Log candidates without encoding or modifying any files.
#   --force             Ignore per-folder state files; scan all files.
#                       Also allows overwriting conflicting output files.
#   -h, --help          Show this help and exit.
#
# STATE FILES  (written per scanned folder, hidden)
#   .fix_videos.state   Epoch timestamp of last successful run for that folder.
#                       Delete to force a full rescan of that folder.
#   .fix_videos.log     Append-only log (top-level FOLDER only).
#
# INCREMENTAL
#   After each successful scan of a folder its .fix_videos.state is touched.
#   The next run examines only files newer than that marker.  With --recurse
#   each subfolder gets its own state file for independent incremental tracking.
#
# ENVIRONMENT
#   EXIFTOOL_BIN   Override path to exiftool binary.
#
# SYNOLOGY SCHEDULED TASK
#   User:   root
#   Action: /volume1/scripts/fix_videos.sh \
#             --backup-root /volume1/video_originals --recurse \
#             /volume1/photo/PhotoLibrary
#   Schedule: daily, off-peak hours
###############################################################################

###############################################################################
# CONSTANTS / DEFAULTS
###############################################################################

EXIFTOOL_BIN="${EXIFTOOL_BIN:-$(command -v exiftool 2>/dev/null || echo /usr/share/applications/ExifTool/exiftool)}"

# Docker path mapping: HOST_BASE on the NAS maps to CONTAINER_BASE inside the
# container (same convention as fix_video_rotation.sh / gps_tag.sh).
HOST_BASE="/volume1"
CONTAINER_BASE="/data"

# Active-encode guard: wait if these .grab dirs contain live .ts files
GRAB_DIR="/volume1/video/TV/.grab"
GRAB_DIR2="/volume1/video/TVHeadEnd/.grab"
GRAB_DIR3="/volume1/video/Movies/.grab"

# Fallback bitrate (kbps) used when the source bitrate cannot be probed.
BITRATE_FALLBACK_K="10000"

###############################################################################
# ARGUMENT PARSING
###############################################################################

PRESERVE_MODE=""      # "backup-root" | "rename-hp"
BACKUP_ROOT=""
RECURSE=0
DRYRUN=0
FORCE=0
H264_MODE=0
FIX_ROTATION=1
FIX_FORMATS=1
FIX_CODECS=1
FOLDER=""

usage() {
  cat <<'USAGE'
Usage: fix_videos.sh [OPTIONS] FOLDER

Normalizes video files: fixes misoriented portrait videos, converts legacy
formats (WMV/AVI/MTS/MOV) to MP4, and re-encodes browser-incompatible codecs.
All applicable fixes are applied in a single encode pass per file via Docker
QSV hardware encoding (hevc_qsv by default; --h264 for h264_qsv).

PRESERVATION (one required — originals are never silently overwritten):
  --backup-root DIR   Move originals to DIR, mirroring the folder hierarchy
                      relative to FOLDER.  Corrected file takes the original path.
                        Example: /photos/2024/sub/vid.wmv
                                 backed up to DIR/sub/vid.wmv
                                 corrected file at /photos/2024/sub/vid.mp4

  --rename-hp         Rename original to filename_hp before replacing.
                      The _hp suffix is not recognised by ha-media-index,
                      so the original is kept alongside but not indexed.
                        Example: vid.wmv → vid.wmv_hp  (original, kept)
                                 vid.mp4               (corrected, new)

FIX SELECTION (all enabled by default):
  --no-rotation       Skip rotation detection and correction.
  --no-formats        Skip format conversion (WMV/AVI/MTS/MOV → MP4).
  --no-codecs         Skip codec normalization (video/audio codec checks).

OTHER OPTIONS:
  --h264              Use h264_qsv output instead of hevc_qsv.
                      Does NOT re-encode already browser-safe HEVC content.
  --recurse, -r       Descend into sub-folders (default: top-level only).
  --dry-run           Log candidates without encoding or modifying any files.
  --force             Ignore per-folder state files; scan all files.
                      Also allows overwriting conflicting output files.
  -h, --help          Show this help and exit.

CODEC CRITERIA
  "Browser-safe" video: H.264 (avc1) or HEVC (hvc1/hev1)
  "Browser-safe" audio: AAC (mp4a)
  Anything else (MPEG-4 Part 2, WMV codec, MP3/WMA/AC3 audio, etc.) is flagged
  for re-encoding.  --h264 sets what we encode TO, not what triggers re-encoding.

FAST PATHS  (avoid unnecessary QSV re-encodes)
  MOV → MP4 with already-compatible codecs: container remux (no quality loss)
  MP4/MOV with audio-only fix: stream-copy video, transcode audio only

METADATA
  GPS coordinates and date-taken are captured before encoding and restored
  afterwards via exiftool.  Critical for WMV/AVI/MTS → MP4 conversions where
  ffmpeg's -map_metadata may not carry dates/GPS across format boundaries.

STATE FILES  (per folder, hidden)
  .fix_videos.state   Epoch of last successful run.  Delete to force rescan.
  .fix_videos.log     Append-only log (top-level FOLDER only).

EXAMPLES
  # Preview (no files changed):
  fix_videos.sh --backup-root /volume1/originals --dry-run /volume1/photo/Camera

  # Convert and fix recursively, backing up originals:
  fix_videos.sh --backup-root /volume1/originals --recurse /volume1/photo/Camera

  # Keep originals alongside as _hp files:
  fix_videos.sh --rename-hp --recurse /volume1/photo/Camera

  # Rotation-only pass (skip format/codec checks):
  fix_videos.sh --rename-hp --no-formats --no-codecs /volume1/photo/Camera
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --backup-root)
      PRESERVE_MODE="backup-root"
      BACKUP_ROOT="${2:?'--backup-root requires a directory path'}"
      shift 2 ;;
    --rename-hp)
      PRESERVE_MODE="rename-hp"
      shift ;;
    --recurse|-r)
      RECURSE=1
      shift ;;
    --dry-run)
      DRYRUN=1
      shift ;;
    --force)
      FORCE=1
      shift ;;
    --h264)
      H264_MODE=1
      shift ;;
    --no-rotation)
      FIX_ROTATION=0
      shift ;;
    --no-formats)
      FIX_FORMATS=0
      shift ;;
    --no-codecs)
      FIX_CODECS=0
      shift ;;
    -h|--help)
      usage; exit 0 ;;
    -*)
      echo "ERROR: unknown option '$1'" >&2; echo >&2; usage >&2; exit 1 ;;
    *)
      if [ -z "$FOLDER" ]; then
        FOLDER="$1"
      else
        echo "ERROR: unexpected argument '$1'" >&2; exit 1
      fi
      shift ;;
  esac
done

# Set output codec based on --h264 flag (must be after arg parsing)
if [ "$H264_MODE" = "1" ]; then
  VIDEO_CODEC_OUT="h264_qsv"
else
  VIDEO_CODEC_OUT="hevc_qsv"
fi

###############################################################################
# VALIDATION
###############################################################################

if [ -z "$FOLDER" ]; then
  echo "ERROR: FOLDER is required." >&2; echo >&2; usage >&2; exit 1
fi
if [ ! -d "$FOLDER" ]; then
  echo "ERROR: folder not found: '$FOLDER'" >&2; exit 1
fi
if [ -z "$PRESERVE_MODE" ]; then
  echo "ERROR: one of --backup-root DIR or --rename-hp is required." >&2
  echo "       These options preserve originals before they are replaced." >&2
  echo "       There is no silent in-place overwrite mode." >&2
  exit 1
fi
if [ ! -x "$EXIFTOOL_BIN" ]; then
  echo "ERROR: exiftool not found at '$EXIFTOOL_BIN'." >&2
  echo "       Install exiftool or set the EXIFTOOL_BIN environment variable." >&2
  exit 1
fi

# Canonicalize FOLDER to an absolute path (no trailing slash)
FOLDER="$(cd "$FOLDER" && pwd)"

if [ "$PRESERVE_MODE" = "backup-root" ]; then
  mkdir -p "$BACKUP_ROOT" 2>/dev/null || true
  if [ ! -d "$BACKUP_ROOT" ]; then
    echo "ERROR: backup root '$BACKUP_ROOT' does not exist and could not be created." >&2
    exit 1
  fi
  BACKUP_ROOT="$(cd "$BACKUP_ROOT" && pwd)"
  case "$BACKUP_ROOT" in
    "$FOLDER"|"$FOLDER"/*)
      echo "ERROR: --backup-root DIR must not be inside FOLDER." >&2; exit 1 ;;
  esac
fi

###############################################################################
# LOGGING  (log file lives in the top-level scan folder)
###############################################################################

LOG_FILE="$FOLDER/.fix_videos.log"

log() {
  local TS
  TS="$(date '+%Y-%m-%d %H:%M:%S')"
  echo "[$TS] [fix_videos] $*" | tee -a "$LOG_FILE" >&2
}

###############################################################################
# SINGLETON LOCK  (per scan root)
###############################################################################

PIDFILE="$FOLDER/.fix_videos.pid"
if [ -f "$PIDFILE" ]; then
  OLD_PID="$(cat "$PIDFILE" 2>/dev/null || echo)"
  if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
    echo "[fix_videos] Already running (PID $OLD_PID). Exiting." >&2
    exit 0
  fi
fi
echo "$$" > "$PIDFILE"
trap 'rm -f "$PIDFILE"; exit 0' INT TERM EXIT

###############################################################################
# WAIT FOR SAFE ENCODE WINDOW
###############################################################################

wait_for_safe_window() {
  while :; do
    if pgrep -f "docker.*ffmpeg" >/dev/null 2>&1; then
      log "Active ffmpeg encode detected, waiting 30s..."
      sleep 30; continue
    fi
    if find "$GRAB_DIR"  -type f -name "*.ts" 2>/dev/null | grep -q .; then
      log "$GRAB_DIR active, waiting 30s..."; sleep 30; continue
    fi
    if find "$GRAB_DIR2" -type f -name "*.ts" 2>/dev/null | grep -q .; then
      log "$GRAB_DIR2 active, waiting 30s..."; sleep 30; continue
    fi
    if find "$GRAB_DIR3" -type f -name "*.ts" 2>/dev/null | grep -q .; then
      log "$GRAB_DIR3 active, waiting 30s..."; sleep 30; continue
    fi
    break
  done
}

###############################################################################
# POST-ENCODE COOLDOWN
###############################################################################

post_encode_pause() {
  log "Cooldown: 5s pause to allow other encode jobs to interleave..."
  sleep 5
  while pgrep -f "docker.*ffmpeg" >/dev/null 2>&1; do
    log "Cooldown: waiting for other encode to finish..."
    sleep 20
  done
}

###############################################################################
# FIND CANDIDATES IN ONE DIRECTORY  (non-recursive)
#
# $1 = directory to scan
# $2 = path to that directory's .fix_videos.state file
#
# Uses -maxdepth 1 so each directory is scanned independently, giving each
# folder its own incremental window when --recurse is active.
#
# Outputs one tab-delimited line per file needing work:  REASONS<TAB>FULL_PATH
#   REASONS is a string of flag characters (order: F, R, C, A):
#     F = needs format / container conversion to MP4
#     R = needs rotation fix (portrait-as-landscape bitstream)
#     C = needs video codec re-encode (not H.264 or HEVC)
#     A = needs audio codec re-encode (not AAC)
###############################################################################

find_candidates_in_dir() {
  local DIR="$1"
  local DIR_STATE="$2"
  local TMP_LIST TMP_EXIF

  TMP_LIST="$(mktemp /tmp/fixvids_files.XXXXXX)"

  # Build candidate file list — incremental (since state) or full scan.
  if [ -f "$DIR_STATE" ] && [ "$FORCE" = "0" ]; then
    # Incremental: match mtime OR ctime so files copied with preserved mtime
    # (e.g. rsync -a) are still detected when they arrive in the folder.
    find "$DIR" -maxdepth 1 -type f \
        \( -iname "*.mp4" -o -iname "*.mov" \) \
        \( -newer "$DIR_STATE" -o -cnewer "$DIR_STATE" \) \
        2>/dev/null >> "$TMP_LIST"
    if [ "$FIX_FORMATS" = "1" ]; then
      find "$DIR" -maxdepth 1 -type f \
          \( -iname "*.wmv" -o -iname "*.avi" -o -iname "*.mts" \) \
          \( -newer "$DIR_STATE" -o -cnewer "$DIR_STATE" \) \
          2>/dev/null >> "$TMP_LIST"
    fi
    log "Dir '$DIR': incremental scan — $(wc -l < "$TMP_LIST" | tr -d ' ') candidates (since $(date -r "$DIR_STATE" '+%Y-%m-%d %H:%M' 2>/dev/null || echo unknown))"
  else
    find "$DIR" -maxdepth 1 -type f \
        \( -iname "*.mp4" -o -iname "*.mov" \) \
        2>/dev/null >> "$TMP_LIST"
    if [ "$FIX_FORMATS" = "1" ]; then
      find "$DIR" -maxdepth 1 -type f \
          \( -iname "*.wmv" -o -iname "*.avi" -o -iname "*.mts" \) \
          2>/dev/null >> "$TMP_LIST"
    fi
    if [ "$FORCE" = "1" ]; then
      log "Dir '$DIR': forced full scan — $(wc -l < "$TMP_LIST" | tr -d ' ') candidates"
    else
      log "Dir '$DIR': first-time full scan — $(wc -l < "$TMP_LIST" | tr -d ' ') candidates"
    fi
  fi

  if [ ! -s "$TMP_LIST" ]; then
    rm -f "$TMP_LIST"
    return
  fi

  # Batch exiftool read: rotation tag, dimensions, video codec, audio codec.
  # -T            : tab-separated output, no header
  # -n            : numeric values (rotation in degrees, pixel dimensions)
  # -Rotation     : tkhd track-header rotation tag (0, 90, 180, 270)
  # -ImageWidth   : encoded pixel width  (BEFORE any display rotation is applied)
  # -ImageHeight  : encoded pixel height
  # -CompressorID : video codec FourCC from the sample description box
  #                 (avc1, hvc1, hev1, mp4v, ...) — more reliable than VideoCodecID
  # -AudioFormat  : audio codec FourCC (mp4a, .mp3, sowt, WMA, ...)
  #
  # NOTE: -SourceFile returns "-" in -T mode, so we use paste to rejoin paths.
  TMP_EXIF="$(mktemp /tmp/fixvids_exif.XXXXXX)"
  "$EXIFTOOL_BIN" -T -n \
    -Rotation -ImageWidth -ImageHeight -CompressorID -AudioFormat \
    -@ "$TMP_LIST" 2>/dev/null > "$TMP_EXIF"

  # awk filter: determine what fix(es) each file needs.
  #
  # wmv/avi/mts: only flag F (format conversion).  These containers never hold
  #   H.264/HEVC+AAC in a form that can be stream-copied to MP4, so codec/audio
  #   re-encoding is handled automatically in encode_file based on the extension.
  # mov:  F (when do_formats=1), then R/C/A as applicable.
  # mp4:  R/C/A as applicable (already the target container; no F needed).
  #
  # "Good" video: avc1 (H.264), hvc1, hev1 (HEVC)
  # "Good" audio: mp4a (AAC)
  # --h264 controls output codec only — it does NOT change what we detect here.
  paste "$TMP_LIST" "$TMP_EXIF" | awk -F'\t' \
    -v do_rotation="$FIX_ROTATION" \
    -v do_formats="$FIX_FORMATS" \
    -v do_codecs="$FIX_CODECS" \
    '
    {
      path = $1; rot = $2+0; w = $3+0; h = $4+0
      vid = tolower($5); aud = tolower($6)

      if (path == "") next

      # Extract file extension (lowercase)
      ext = tolower(path)
      sub(/.*\./, "", ext)

      reasons = ""

      # ---- WMV / AVI / MTS ------------------------------------------------
      # Legacy formats: flag F only.  Codec/audio handling is determined in
      # encode_file based on the input extension (always full re-encode).
      if (ext == "wmv" || ext == "avi" || ext == "mts") {
        if (do_formats) reasons = "F"
        if (reasons != "") print reasons "\t" path
        next
      }

      # ---- MOV / MP4 -------------------------------------------------------

      # F: container conversion (MOV → MP4) when format fixes are enabled
      if (do_formats && ext == "mov") reasons = reasons "F"

      # R: misoriented portrait (landscape bitstream + rotation tag)
      if (do_rotation && (rot == 90 || rot == 270) && w > h) reasons = reasons "R"

      # C: video codec is not browser-safe (not H.264 or HEVC).
      # Treat absent ("-") or empty values as unknown — do not flag as bad.
      if (do_codecs && vid != "" && vid != "-" && \
          vid != "avc1" && vid != "hvc1" && vid != "hev1") reasons = reasons "C"

      # A: audio codec is not AAC.
      # Treat absent ("-") or empty values as unknown — do not flag as bad.
      if (do_codecs && aud != "" && aud != "-" && aud != "mp4a") reasons = reasons "A"

      if (reasons != "") print reasons "\t" path
    }
    '

  rm -f "$TMP_LIST" "$TMP_EXIF"
}

###############################################################################
# PRESERVE ORIGINAL
#
# $1 = full path to original file (must exist)
# Sets global PRESERVED_PATH to the new location of the original on success.
# Returns 0 on success, 1 on failure.
###############################################################################

PRESERVED_PATH=""

preserve_original() {
  local ORIG="$1"
  PRESERVED_PATH=""

  if [ "$PRESERVE_MODE" = "backup-root" ]; then
    # Strip FOLDER prefix (with trailing slash) to get the relative path,
    # then mirror that hierarchy under BACKUP_ROOT.
    local REL="${ORIG#${FOLDER}/}"
    local DEST="$BACKUP_ROOT/$REL"
    local DEST_DIR
    DEST_DIR="$(dirname "$DEST")"
    if [ "$DRYRUN" = "1" ]; then
      log "  DRY RUN: would backup '$(basename "$ORIG")' → '$DEST'"
      PRESERVED_PATH="$DEST"
      return 0
    fi
    mkdir -p "$DEST_DIR" || { log "ERROR: cannot create backup dir '$DEST_DIR'"; return 1; }
    mv "$ORIG" "$DEST"   || { log "ERROR: cannot move '$ORIG' → '$DEST'"; return 1; }
    PRESERVED_PATH="$DEST"
    log "  Backup: '$(basename "$ORIG")' → '$DEST'"

  elif [ "$PRESERVE_MODE" = "rename-hp" ]; then
    local HP="${ORIG}_hp"
    if [ "$DRYRUN" = "1" ]; then
      log "  DRY RUN: would rename '$(basename "$ORIG")' → '$(basename "$HP")'"
      PRESERVED_PATH="$HP"
      return 0
    fi
    mv "$ORIG" "$HP" || { log "ERROR: cannot rename '$ORIG' → '$HP'"; return 1; }
    PRESERVED_PATH="$HP"
    log "  Renamed: '$(basename "$ORIG")' → '$(basename "$HP")'"
  fi

  return 0
}

###############################################################################
# ENCODE ONE FILE
#
# $1 = REASONS  — string of fix flags: F (format), R (rotation), C (video codec),
#                 A (audio codec).  Multiple flags combined, e.g. "FRC".
# $2 = INPUT    — full host path to the source file
# $3 = OUTPUT   — full host path to the destination .mp4 file
#                 (may differ from INPUT for format conversions: wmv/avi/mts/mov)
#
# Three encode paths depending on REASONS:
#   "F" only on a compatible container (non-legacy, e.g. MOV → MP4 with
#       already-safe codecs): Docker stream remux (-c:v copy -c:a copy), fast.
#   "A" only on an MP4 or MOV: Docker stream-copy video, transcode audio.
#   Anything else: full Docker QSV hardware re-encode (hevc_qsv or h264_qsv).
#
# GPS coordinates and date-taken are captured before encoding and restored
# afterwards via exiftool.
###############################################################################

encode_file() {
  local REASONS="$1"
  local INPUT="$2"
  local OUTPUT="$3"

  # Temp output path (always .mp4 — ffmpeg always writes MP4 container).
  # Derived from OUTPUT so it lands in the same directory.
  local TMP="${OUTPUT%.*}.fix.tmp.mp4"

  # Human-readable description of what's being done
  local DESC=""
  case "$REASONS" in *F*) DESC="${DESC}format-convert " ;; esac
  case "$REASONS" in *R*) DESC="${DESC}fix-rotation " ;; esac
  case "$REASONS" in *C*) DESC="${DESC}recode-video " ;; esac
  case "$REASONS" in *A*) DESC="${DESC}recode-audio " ;; esac
  log "PROCESSING: '$(basename "$INPUT")'  [${DESC% }]  → '$(basename "$OUTPUT")'"

  if [ "$DRYRUN" = "1" ]; then
    log "  DRY RUN: would encode '$INPUT' → '$OUTPUT'"
    return 0
  fi

  # ---- Pre-encode metadata capture ----------------------------------------
  # Must happen before preserve_original moves or renames the source file.
  local GPS_LAT GPS_LON DATE_TAKEN
  GPS_LAT="$("$EXIFTOOL_BIN" -T -n -GPSLatitude  "$INPUT" 2>/dev/null | tr -d ' ')"
  GPS_LON="$("$EXIFTOOL_BIN" -T -n -GPSLongitude "$INPUT" 2>/dev/null | tr -d ' ')"
  # Take the first non-empty, non-dash value across DateTimeOriginal and CreateDate.
  # Two-field read gives one tab-delimited line; split to find the first real value.
  DATE_TAKEN="$("$EXIFTOOL_BIN" -T -d '%Y:%m:%d %H:%M:%S' \
    -DateTimeOriginal -CreateDate "$INPUT" 2>/dev/null \
    | tr '\t' '\n' | grep -v '^-$' | grep -v '^$' | head -1)"

  wait_for_safe_window

  # Map host paths to container paths (HOST_BASE prefix → CONTAINER_BASE)
  local REL_INPUT REL_TMP CIN COUT
  REL_INPUT="${INPUT#$HOST_BASE}"
  REL_TMP="${TMP#$HOST_BASE}"
  CIN="${CONTAINER_BASE}${REL_INPUT}"
  COUT="${CONTAINER_BASE}${REL_TMP}"

  # Derive input extension for audio/path decisions in PATH 3
  local INPUT_EXT="${INPUT##*.}"
  INPUT_EXT="$(printf '%s' "$INPUT_EXT" | tr 'A-Z' 'a-z')"

  local STATUS

  # =========================================================================
  # PATH 1: No video re-encode — REASONS has no C (bad video codec) and no R
  # (rotation fix).  Video stream is always copied; audio is copied or
  # transcoded to AAC depending on whether an A flag is present or the source
  # is a legacy container (wmv/avi/mts whose audio is never AAC).
  #
  # Covers all combinations that don't need the video touched:
  #   F only (non-legacy)  → -c:v copy -c:a copy   (pure container remux)
  #   A only               → -c:v copy -c:a aac     (audio-only fix)
  #   F+A (non-legacy)     → -c:v copy -c:a aac     (remux + audio recode)
  #   F (legacy wmv/…)     → -c:v copy -c:a aac     (legacy audio always bad)
  # =========================================================================
  local _need_recode_video=0
  case "$REASONS" in *C*) _need_recode_video=1 ;; esac
  case "$REASONS" in *R*) _need_recode_video=1 ;; esac

  if [ "$_need_recode_video" = "0" ]; then
    # Decide audio: transcode when A flag set, or when F + legacy container
    local PATH1_AUDIO_ARG="-c:a copy"
    local PATH1_AUDIO_DESC="stream copy"
    case "$REASONS" in
      *A*) PATH1_AUDIO_ARG="-c:a aac -b:a 128k -ac 2"; PATH1_AUDIO_DESC="transcode→AAC" ;;
      *F*) case "$INPUT_EXT" in
             wmv|avi|mts) PATH1_AUDIO_ARG="-c:a aac -b:a 128k -ac 2"; PATH1_AUDIO_DESC="transcode→AAC (legacy)" ;;
           esac ;;
    esac
    log "  Path: stream-copy video, audio ${PATH1_AUDIO_DESC} (no re-encode)"
    docker run --rm \
      --mount type=bind,src="$HOST_BASE",dst="$CONTAINER_BASE" \
      linuxserver/ffmpeg:latest \
      -hide_banner -loglevel warning \
      -i "$CIN" \
      -map 0:v:0 -map 0:a:0? \
      -map_metadata 0 \
      -c:v copy \
      $PATH1_AUDIO_ARG \
      -metadata:s:v:0 rotate=0 \
      -movflags +faststart \
      "$COUT" \
      2>&1 | tee -a "$LOG_FILE"
    STATUS=$?

  # =========================================================================
  # PATH 2: Full QSV hardware re-encode.
  # Used when REASONS contains C (bad video codec) or R (rotation fix),
  # possibly combined with A (bad audio) or F (format/container conversion).
  # Probes and preserves colour metadata (HDR/HLG primaries, TRC, range).
  # =========================================================================
  else
    log "  Path: QSV hardware re-encode ($VIDEO_CODEC_OUT)"
    log "  Docker  in : $CIN"
    log "  Docker out : $COUT"

    # Determine audio handling:
    #   A flag:    audio codec was explicitly detected as bad → transcode
    #   otherwise: copy audio (preserves quality for already-AAC tracks)
    local AUDIO_ARG="-c:a copy"
    case "$REASONS" in
      *A*) AUDIO_ARG="-c:a aac -b:a 128k -ac 2" ;;
    esac

    # Probe colour-space metadata and pixel depth.
    # Preserves HDR/HLG colour through the transcode — same technique as
    # fix_video_rotation.sh.
    local COL_META
    COL_META="$( docker run --rm --entrypoint ffprobe \
        --mount type=bind,src="$HOST_BASE",dst="$CONTAINER_BASE" \
        linuxserver/ffmpeg:latest \
        -v quiet -select_streams v:0 \
          -show_entries stream=color_primaries,color_trc,color_space,color_range,pix_fmt,bit_rate \
          -of default=noprint_wrappers=1 \
        "$CIN" 2>/dev/null )"

    local COL_PRIMARIES COL_TRC COL_SPACE COL_RANGE SRC_PIX
    COL_PRIMARIES="$(printf '%s\n' "$COL_META" | grep '^color_primaries=' | cut -d= -f2 | tr -cd 'a-zA-Z0-9_-')"
    COL_TRC="$(printf '%s\n'       "$COL_META" | grep '^color_trc='       | cut -d= -f2 | tr -cd 'a-zA-Z0-9_-')"
    # Untagged TRC with bt2020 primaries → infer HLG (phone camera HDR).
    # Without this, hevc_qsv defaults to bt709 TRC and players misread luminance.
    if [ -z "$COL_TRC" ] && [ "$COL_PRIMARIES" = "bt2020" ]; then
      COL_TRC="arib-std-b67"
      log "  TRC untagged with bt2020 primaries — inferring HLG (arib-std-b67)"
    fi
    COL_SPACE="$(printf '%s\n'     "$COL_META" | grep '^color_space='     | cut -d= -f2 | tr -cd 'a-zA-Z0-9_-')"
    COL_RANGE="$(printf '%s\n'     "$COL_META" | grep '^color_range='     | cut -d= -f2 | tr -cd 'a-zA-Z0-9_-')"
    SRC_PIX="$(printf '%s\n'       "$COL_META" | grep '^pix_fmt='         | cut -d= -f2 | tr -cd 'a-zA-Z0-9_')"
    log "  Source: pix=${SRC_PIX:-?} primaries=${COL_PRIMARIES:-?} trc=${COL_TRC:-?} space=${COL_SPACE:-?} range=${COL_RANGE:-?}"

    # Codec-specific pixel format and stream tag
    local PIX_FMT_FLAG="" TAG_FLAG=""
    if [ "$H264_MODE" = "1" ]; then
      # H.264 QSV: always 8-bit NV12 (required QSV surface format)
      PIX_FMT_FLAG="-pix_fmt nv12"
      TAG_FLAG=""
    else
      # HEVC QSV: p010le for 10-bit content (HDR/HLG), nv12 for 8-bit
      case "$SRC_PIX" in *10*) PIX_FMT_FLAG="-pix_fmt p010le" ;; esac
      TAG_FLAG="-tag:v hvc1"
    fi

    local CF_P="" CF_T="" CF_S="" CF_R=""
    [ -n "$COL_PRIMARIES" ] && CF_P="-color_primaries $COL_PRIMARIES"
    [ -n "$COL_TRC" ]       && CF_T="-color_trc $COL_TRC"
    [ -n "$COL_SPACE" ]     && CF_S="-colorspace $COL_SPACE"
    [ -n "$COL_RANGE" ]     && CF_R="-color_range $COL_RANGE"

    # Full-range source (yuvj420p or color_range=pc): tell swscaler explicitly
    # that the input is full range so it doesn't silently clip to limited range
    # during the yuvjXXXp → nv12 conversion.  The scale filter carries range
    # metadata through; -color_range pc tags the output container.
    # 10-bit HDR/HLG content is never full range, so this branch won't fire for
    # p010le sources.
    local VF_FLAG=""
    if [ "${SRC_PIX#yuvj}" != "$SRC_PIX" ] || [ "$COL_RANGE" = "pc" ]; then
      VF_FLAG="-vf scale=in_range=full:out_range=full"
      # hevc_qsv may not have set PIX_FMT_FLAG for 8-bit content; nv12 is
      # required to give QSV a well-defined surface format after the scale step.
      [ -z "$PIX_FMT_FLAG" ] && PIX_FMT_FLAG="-pix_fmt nv12"
      log "  Full-range source (${SRC_PIX}) — adding scale=in_range/out_range=full"
    fi

    # Source-matched VBR bitrate targeting.
    # Same probing logic as fix_video_rotation.sh.
    local SRC_BPS TARGET_K MAXRATE_K BUFSIZE_K
    SRC_BPS="$(printf '%s\n' "$COL_META" | grep '^bit_rate=' | cut -d= -f2 | tr -cd '0-9')"
    # Fallback: container (format-level) bitrate when stream-level value is N/A
    if [ -z "$SRC_BPS" ] || ! [ "$SRC_BPS" -gt 0 ] 2>/dev/null; then
      SRC_BPS="$( docker run --rm --entrypoint ffprobe \
          --mount type=bind,src="$HOST_BASE",dst="$CONTAINER_BASE" \
          linuxserver/ffmpeg:latest \
          -v quiet -show_entries format=bit_rate -of default=noprint_wrappers=1 \
          "$CIN" 2>/dev/null | grep '^bit_rate=' | cut -d= -f2 | tr -cd '0-9' )"
    fi
    if [ -n "$SRC_BPS" ] && [ "$SRC_BPS" -gt 0 ] 2>/dev/null; then
      TARGET_K="$(( SRC_BPS / 1000 ))k"
      MAXRATE_K="$(( SRC_BPS * 3 / 2 / 1000 ))k"
      BUFSIZE_K="$(( SRC_BPS * 2 / 1000 ))k"
      log "  Bitrate: source=$(( SRC_BPS / 1000 ))k → target=${TARGET_K}, max=${MAXRATE_K}"
    else
      TARGET_K="${BITRATE_FALLBACK_K}k"
      MAXRATE_K="$(( BITRATE_FALLBACK_K * 3 / 2 ))k"
      BUFSIZE_K="$(( BITRATE_FALLBACK_K * 2 ))k"
      log "  Bitrate: source unknown → fallback ${TARGET_K}"
    fi

    docker run --rm \
      --device /dev/dri:/dev/dri \
      --mount type=bind,src="$HOST_BASE",dst="$CONTAINER_BASE" \
      linuxserver/ffmpeg:latest \
      -hide_banner -loglevel warning -stats_period 10 \
      -i "$CIN" \
      -map 0:v:0 -map 0:a:0? \
      -map_metadata 0 \
      -c:v "$VIDEO_CODEC_OUT" \
        -bf 0 \
        -b:v "$TARGET_K" -maxrate "$MAXRATE_K" -bufsize "$BUFSIZE_K" \
        -look_ahead 0 \
      $PIX_FMT_FLAG \
      $VF_FLAG \
      $CF_P $CF_T $CF_S $CF_R \
      $TAG_FLAG \
      $AUDIO_ARG \
      -metadata:s:v:0 rotate=0 \
      -max_muxing_queue_size 9999 \
      -movflags +faststart \
      "$COUT" \
      2>&1 | tee -a "$LOG_FILE"
    STATUS=$?
  fi

  # ---- Post-encode: preserve original, install output, restore metadata ----
  if [ "$STATUS" -eq 0 ] && [ -f "$TMP" ]; then
    # Preserve the original before the corrected file takes its place.
    if ! preserve_original "$INPUT"; then
      log "ERROR: Could not preserve '$INPUT' — aborting, deleting .tmp"
      rm -f "$TMP"
      return 1
    fi

    # Stamp the output with the original's mtime so media scanners (including
    # ha-media-index) don't treat it as a brand-new arrival.
    [ -n "$PRESERVED_PATH" ] && touch -r "$PRESERVED_PATH" "$TMP" 2>/dev/null || true
    mv "$TMP" "$OUTPUT"

    # Restore GPS coordinates to the ©xyz udta atom (ISO 6709 format).
    # ffmpeg's -map_metadata copies EXIF-style GPS but does NOT write ©xyz;
    # exiftool is required — same approach as fix_video_rotation.sh / gps_tag.sh.
    if [ -n "$GPS_LAT" ] && [ -n "$GPS_LON" ] && \
       [ "$GPS_LAT" != "-" ] && [ "$GPS_LON" != "-" ]; then
      local ISO_GPS
      ISO_GPS="$(printf '%+.4f' "$GPS_LAT")$(printf '%+.4f' "$GPS_LON")/"
      "$EXIFTOOL_BIN" -m -n \
        "-UserData:GPSCoordinates-eng=$ISO_GPS" \
        -overwrite_original \
        "$OUTPUT" >/dev/null 2>&1 \
        && log "  GPS restored: $ISO_GPS → '$(basename "$OUTPUT")'" \
        || log "  WARNING: GPS restore failed for '$(basename "$OUTPUT")'"
    fi

    # Restore date-taken fields (QuickTime CreateDate + MediaCreateDate + TrackCreateDate).
    # Critical for cross-format conversions where ffmpeg -map_metadata may not
    # carry QuickTime date atoms across container format boundaries.
    if [ -n "$DATE_TAKEN" ]; then
      "$EXIFTOOL_BIN" -m -overwrite_original \
        "-QuickTime:CreateDate=$DATE_TAKEN" \
        "-QuickTime:MediaCreateDate=$DATE_TAKEN" \
        "-QuickTime:TrackCreateDate=$DATE_TAKEN" \
        "$OUTPUT" >/dev/null 2>&1 \
        && log "  Date restored: $DATE_TAKEN → '$(basename "$OUTPUT")'" \
        || log "  WARNING: Date restore failed for '$(basename "$OUTPUT")'"
    fi

    log "DONE: '$(basename "$OUTPUT")'"
    post_encode_pause
    return 0
  else
    log "ERROR: Encode failed for '$(basename "$INPUT")' (exit $STATUS)"
    rm -f "$TMP"
    return 1
  fi
}

###############################################################################
# PROCESS ONE DIRECTORY  (top-level only; recursion is handled in MAIN)
# $1 = directory to process
# Updates global TOTAL / FIXED / FAILED counters.
###############################################################################

process_dir() {
  local DIR="$1"
  local DIR_STATE="$DIR/.fix_videos.state"
  local TMP_WORK
  local LOCAL_TOTAL=0 LOCAL_FIXED=0 LOCAL_FAILED=0

  TMP_WORK="$(mktemp /tmp/fixvids_work.XXXXXX)"
  find_candidates_in_dir "$DIR" "$DIR_STATE" > "$TMP_WORK"

  local FOUND
  FOUND="$(wc -l < "$TMP_WORK" | tr -d ' ')"
  [ "$FOUND" -gt 0 ] && log "--- $FOUND file(s) to process in '$DIR' ---"

  local LINE REASONS INPUT OUTPUT INPUT_EXT INPUT_BASE_NOEXT
  while read -r LINE; do
    [ -z "$LINE" ] && continue
    REASONS="$(printf '%s' "$LINE" | cut -f1)"
    INPUT="$(printf '%s' "$LINE" | cut -f2-)"
    [ -z "$INPUT" ] && continue

    # Determine output path.
    INPUT_EXT="${INPUT##*.}"
    INPUT_EXT="$(printf '%s' "$INPUT_EXT" | tr 'A-Z' 'a-z')"
    INPUT_BASE_NOEXT="${INPUT%.*}"

    case "$REASONS" in
      *F*)
        # Format conversion: new .mp4 file alongside (or replacing) the source.
        OUTPUT="${INPUT_BASE_NOEXT}.mp4"
        # Conflict check: skip if the target .mp4 already exists and --force
        # was not given.  With --force, the existing file is overwritten.
        if [ "$INPUT" != "$OUTPUT" ] && [ -f "$OUTPUT" ] && [ "$FORCE" = "0" ]; then
          log "SKIP: '$(basename "$OUTPUT")' already exists — use --force to overwrite"
          continue
        fi
        ;;
      *)
        # In-place fix (rotation / codec on existing MP4 or MOV): output = input.
        # The corrected file is written to a .fix.tmp.mp4 temp then moved back.
        OUTPUT="$INPUT"
        ;;
    esac

    LOCAL_TOTAL=$(( LOCAL_TOTAL + 1 ))
    encode_file "$REASONS" "$INPUT" "$OUTPUT"
    if [ $? -eq 0 ]; then
      LOCAL_FIXED=$(( LOCAL_FIXED + 1 ))
    else
      LOCAL_FAILED=$(( LOCAL_FAILED + 1 ))
    fi
  done < "$TMP_WORK"
  rm -f "$TMP_WORK"

  # Update this directory's state file only when all encodes succeeded.
  # Failed files remain newer than the old state pointer so the next run retries.
  if [ "$DRYRUN" = "0" ] && [ "$LOCAL_FAILED" = "0" ]; then
    date +%s > "$DIR_STATE"
    [ "$LOCAL_TOTAL" -gt 0 ] && log "State updated for '$DIR'"
  elif [ "$LOCAL_FAILED" -gt 0 ]; then
    log "State NOT updated for '$DIR' ($LOCAL_FAILED failed — will retry next run)"
  fi

  TOTAL=$((  TOTAL  + LOCAL_TOTAL  ))
  FIXED=$((  FIXED  + LOCAL_FIXED  ))
  FAILED=$(( FAILED + LOCAL_FAILED ))
}

###############################################################################
# MAIN
###############################################################################

log "===== Video Fixer Started ====="
log "Folder:   '$FOLDER'"
log "Recurse:  $([ "$RECURSE" = "1" ] && echo yes || echo no)"

ACTIVE_FIXES=""
[ "$FIX_ROTATION" = "1" ] && ACTIVE_FIXES="${ACTIVE_FIXES}rotation "
[ "$FIX_FORMATS"  = "1" ] && ACTIVE_FIXES="${ACTIVE_FIXES}formats "
[ "$FIX_CODECS"   = "1" ] && ACTIVE_FIXES="${ACTIVE_FIXES}codecs "
log "Fixes:    ${ACTIVE_FIXES:-none (nothing to do)}"
log "Codec:    $VIDEO_CODEC_OUT"

if [ "$PRESERVE_MODE" = "backup-root" ]; then
  log "Preserve: backup-root → '$BACKUP_ROOT'"
else
  log "Preserve: rename-hp  (original renamed with _hp suffix)"
fi
[ "$DRYRUN" = "1" ] && log "DRY RUN MODE ACTIVE — no files will be changed"
[ "$FORCE"  = "1" ] && log "FORCE MODE — ignoring state files and output conflicts"

TOTAL=0
FIXED=0
FAILED=0

if [ "$RECURSE" = "1" ]; then
  # Build dir list into a temp file to avoid a pipe subshell — a piped while loop
  # runs in a subshell, so TOTAL/FIXED/FAILED updates would be lost on exit.
  TMP_DIRS="$(mktemp /tmp/fixvids_dirs.XXXXXX)"
  find "$FOLDER" -type d \
      ! -path '*/@eaDir*' \
      ! -path '*/.@__thumb*' \
      | sort > "$TMP_DIRS"
  while IFS= read -r SUBDIR; do
    [ -z "$SUBDIR" ] && continue
    # Skip backup root if it happens to fall under FOLDER
    if [ "$PRESERVE_MODE" = "backup-root" ]; then
      case "$SUBDIR" in
        "$BACKUP_ROOT"|"$BACKUP_ROOT"/*) log "Skipping backup-root dir: '$SUBDIR'"; continue ;;
      esac
    fi
    process_dir "$SUBDIR"
  done < "$TMP_DIRS"
  rm -f "$TMP_DIRS"
else
  process_dir "$FOLDER"
fi

log "===== Done: $TOTAL candidates, $FIXED fixed, $FAILED failed ====="
