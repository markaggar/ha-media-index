#!/usr/bin/env bash
###############################################################################
# fix_video_rotation.sh — Re-encode misoriented portrait videos
#
# PROBLEM
#   Some phone cameras record portrait video as a landscape bitstream and attach
#   a 90°/270° rotation tag so players "rotate when displaying."  Roku xcast,
#   older browsers, and some media servers ignore that tag and show the video
#   sideways.
#
# DETECTION  (via exiftool)
#   Rotation ∈ {90, 270}  AND  ImageWidth > ImageHeight  →  needs fix
#   Everything else is either natively portrait or genuinely landscape — skip.
#
# FIX
#   Re-encode via QSV hardware HEVC inside Docker.  The re-encode causes the
#   encoder to write the video in the correct native orientation.  The legacy
#   rotation tag is explicitly set to 0 so no player applies an extra rotation.
#   The original file is preserved according to --backup-root or --rename-hp.
#
# USAGE
#   fix_video_rotation.sh [OPTIONS] FOLDER
#
# PRESERVATION (one required):
#   --backup-root DIR   Move originals into DIR, mirroring the folder hierarchy
#                       relative to FOLDER.  Corrected file replaces the original.
#   --rename-hp         Rename original to filename.mp4_hp before replacing.
#                       The .mp4_hp extension is not indexed by media scanners.
#
# OTHER OPTIONS:
#   --recurse, -r       Descend into sub-folders (default: top-level only)
#   --dry-run           Log candidates without encoding or modifying files
#   --force             Ignore per-folder state file; scan all files
#   -h, --help          Show this help and exit
#
# STATE FILES  (written into each scanned folder, hidden)
#   .fix_rotation.state    Epoch timestamp of last successful run for that folder.
#                          Delete to force a full rescan of that folder.
#   .fix_rotation.log      Append-only log (top-level FOLDER only).
#
# INCREMENTAL
#   After each successful scan of a folder its .fix_rotation.state is touched.
#   The next run only examines files newer than that state file.  With --recurse
#   each subfolder gets its own state file for independent incremental tracking.
#
# ENVIRONMENT
#   EXIFTOOL_BIN   Override path to exiftool binary (useful in cron environments).
#
# SYNOLOGY SCHEDULED TASK
#   User:   root
#   Action: /volume1/scripts/fix_video_rotation.sh \
#             --backup-root /volume1/rotation_originals --recurse \
#             /volume1/photo/PhotoLibrary
#   Schedule: daily, off-peak hours
###############################################################################

###############################################################################
# CONSTANTS / DEFAULTS
###############################################################################

EXIFTOOL_BIN="${EXIFTOOL_BIN:-$(command -v exiftool 2>/dev/null || echo /usr/share/applications/ExifTool/exiftool)}"

# Docker path mapping: HOST_BASE on the NAS maps to CONTAINER_BASE inside the
# container (same convention as batch_convert.sh / plex_encode_worker.sh).
HOST_BASE="/volume1"
CONTAINER_BASE="/data"

# Active-encode guard: wait if these .grab dirs contain live .ts files
GRAB_DIR="/volume1/video/TV/.grab"
GRAB_DIR2="/volume1/video/TVHeadEnd/.grab"
GRAB_DIR3="/volume1/video/Movies/.grab"

# QSV HEVC encode settings — source-matched VBR.
# Each file is probed for its video stream bitrate and the output targets that
# same bitrate.  HEVC is ~40-50% more efficient than H.264, so output files
# will be similar in size to the original with equal or better visual quality.
# -bf 0 disables B-frames; B-frames cause non-monotonic DTS in hevc_qsv output
# which drops frames.  -look_ahead 0 prevents further QSV reordering.
VIDEO_CODEC_OUT="hevc_qsv"
BITRATE_FALLBACK_K="10000"  # kbps — used only when source bitrate cannot be probed

###############################################################################
# ARGUMENT PARSING
###############################################################################

PRESERVE_MODE=""      # "backup-root" | "rename-hp"
BACKUP_ROOT=""
RECURSE=0
DRYRUN=0
FORCE=0
FOLDER=""

usage() {
  cat <<'USAGE'
Usage: fix_video_rotation.sh [OPTIONS] FOLDER

Re-encodes .mp4/.mov files that have a 90°/270° rotation tag with landscape
pixel dimensions (portrait recorded as landscape bitstream).  The original is
preserved before the corrected re-encode replaces it.

PRESERVATION (one required):
  --backup-root DIR   Move originals into DIR, mirroring the folder hierarchy
                      relative to FOLDER.  The corrected file replaces the
                      original at its original path.
                      Example: /photos/2024/sub/vid.mp4
                               backed up to DIR/sub/vid.mp4

  --rename-hp         Rename original to filename.mp4_hp before replacing.
                      The .mp4_hp extension is not recognised by media players
                      or ha-media-index, so the original is kept alongside the
                      corrected file but not indexed or played.
                      Example: vid.mp4 → vid.mp4_hp  (original, kept)
                               vid.mp4               (corrected, new)

OTHER OPTIONS:
  --recurse, -r       Descend into sub-folders (default: top-level only).
  --dry-run           Log what would be done without encoding or moving files.
  --force             Ignore per-folder state files; scan all files.
  -h, --help          Show this help and exit.

STATE FILES  (written into each scanned folder, hidden)
  .fix_rotation.state  Epoch of last successful run.  Delete to force rescan.
  .fix_rotation.log    Append-only log (top-level FOLDER).

ENVIRONMENT
  EXIFTOOL_BIN   Full path to exiftool binary (override for cron/scheduled tasks).

EXAMPLES
  # Preview what would be processed (no files changed):
  fix_video_rotation.sh --backup-root /volume1/originals --dry-run \
      /volume1/photo/Camera/2024

  # Re-encode, backing originals up into a mirror tree:
  fix_video_rotation.sh --backup-root /volume1/originals \
      /volume1/photo/Camera/2024

  # Re-encode recursively, keeping originals alongside as .mp4_hp files:
  fix_video_rotation.sh --rename-hp --recurse /volume1/photo/Camera
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
  echo "       These options preserve the original before it is replaced by the" >&2
  echo "       corrected re-encode.  There is no in-place overwrite mode." >&2
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

LOG_FILE="$FOLDER/.fix_rotation.log"

log() {
  local TS
  TS="$(date '+%Y-%m-%d %H:%M:%S')"
  echo "[$TS] [rotate] $*" | tee -a "$LOG_FILE" >&2
}

###############################################################################
# SINGLETON LOCK  (per scan root)
###############################################################################

PIDFILE="$FOLDER/.fix_rotation.pid"
if [ -f "$PIDFILE" ]; then
  OLD_PID="$(cat "$PIDFILE" 2>/dev/null || echo)"
  if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
    echo "[rotate] Already running (PID $OLD_PID). Exiting." >&2
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
# FIND MISORIENTED VIDEOS IN ONE DIRECTORY  (non-recursive)
#
# $1 = directory to scan
# $2 = path to that directory's .fix_rotation.state file
#
# Uses -maxdepth 1 so each directory is scanned independently, allowing
# per-folder incremental state tracking when --recurse is active.
#
# Outputs one tab-delimited line per misoriented file:  ROTATION<TAB>FULL_PATH
#   ROTATION = 90 or 270 (degrees from the tkhd rotation tag)
###############################################################################

find_misoriented_in_dir() {
  local DIR="$1"
  local DIR_STATE="$2"
  local TMP_LIST TMP_EXIF

  TMP_LIST="$(mktemp /tmp/rotate_files.XXXXXX)"

  if [ -f "$DIR_STATE" ] && [ "$FORCE" = "0" ]; then
    # -maxdepth 1: this directory only — subfolders handled separately in
    # recursive mode so each gets its own incremental window.
    # Match on mtime OR ctime so files copied with a preserved mtime (e.g. via
    # rsync/cp -p) are still detected when they arrive in the folder.
    find "$DIR" -maxdepth 1 -type f \( -iname "*.mp4" -o -iname "*.mov" \) \
      \( -newer "$DIR_STATE" -o -cnewer "$DIR_STATE" \) \
      2>/dev/null > "$TMP_LIST"
    log "Dir '$DIR': incremental scan — $(wc -l < "$TMP_LIST" | tr -d ' ') candidates (since $(date -r "$DIR_STATE" '+%Y-%m-%d %H:%M' 2>/dev/null || echo unknown))"
  else
    find "$DIR" -maxdepth 1 -type f \( -iname "*.mp4" -o -iname "*.mov" \) \
      2>/dev/null > "$TMP_LIST"
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

  # exiftool -@ reads one argument (file path) per line from TMP_LIST.
  # -T          : tab-separated output, no header
  # -n          : numeric values (rotation in degrees, dimensions in pixels)
  # -Rotation   : tkhd track-header rotation tag (0, 90, 180, 270)
  # -ImageWidth : encoded pixel width  (BEFORE any rotation is applied by a player)
  # -ImageHeight: encoded pixel height
  #
  # NOTE: -SourceFile returns "-" in -T mode on this version of exiftool, so
  # we do NOT request it.  Instead, pipe the path list (TMP_LIST) through
  # `paste` alongside the exiftool values — both files have exactly one line
  # per video so they join correctly by line number.
  #
  # awk filter:
  #   Rotation ∈ {90, 270}  AND  encoded width > height  →  print for fixing
  #   Rotation=0  + landscape dims  →  genuine landscape video (skip)
  #   Rotation=0  + portrait  dims  →  natively stored portrait (skip)
  #   Rotation=180                  →  upside-down, not a portrait issue (skip)
  TMP_EXIF="$(mktemp /tmp/rotate_exif.XXXXXX)"
  "$EXIFTOOL_BIN" -T -n \
    -Rotation -ImageWidth -ImageHeight \
    -@ "$TMP_LIST" 2>/dev/null > "$TMP_EXIF"

  paste "$TMP_LIST" "$TMP_EXIF" | awk -F'\t' '
      {
        path = $1; rot = $2 + 0; w = $3 + 0; h = $4 + 0
        if ((rot == 90 || rot == 270) && w > h && path != "") {
          print rot "\t" path
        }
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
#   $1 = rotation value (90 or 270 degrees) — used for logging only
#   $2 = full host path to the input file
#
# The re-encode causes QSV HEVC to write the video in the correct native
# orientation.  Colour-space metadata (primaries, TRC, range) is probed and
# preserved through the transcode.  -metadata:s:v:0 rotate=0 explicitly clears
# the legacy rotation tag so no player applies an additional rotation.
# -bf 0 / -look_ahead 0 prevent non-monotonic DTS from hevc_qsv B-frames.
###############################################################################

encode_file() {
  local ROTATION="$1"
  local INPUT="$2"

  DIR="$(dirname "$INPUT")"
  BASE_WITH_EXT="$(basename "$INPUT")"
  BASE="${BASE_WITH_EXT%.*}"

  # Encode to a .tmp file in the same directory; rename over the original on success
  TMP="${DIR}/${BASE}.rotate.tmp.mp4"

  log "ENCODING: $INPUT  (rotation=${ROTATION}°)"

  if [ "$DRYRUN" = "1" ]; then
    log "DRY RUN: Would re-encode (rotation=${ROTATION}°) → $INPUT"
    return 0
  fi

  # Read GPS from the original BEFORE encoding.  The encode overwrites the
  # original via mv, so we must capture lat/lon now.
  # -n gives decimal degrees (e.g. 47.5942 / -122.2307) — needed for ISO 6709.
  GPS_LAT="$("$EXIFTOOL_BIN" -T -n -GPSLatitude  "$INPUT" 2>/dev/null | tr -d ' ')"
  GPS_LON="$("$EXIFTOOL_BIN" -T -n -GPSLongitude "$INPUT" 2>/dev/null | tr -d ' ')"

  wait_for_safe_window

  # Map host paths to container paths (same pattern as batch_convert.sh)
  REL_INPUT="${INPUT#$HOST_BASE}"
  REL_TMP="${TMP#$HOST_BASE}"
  CIN="${CONTAINER_BASE}${REL_INPUT}"
  COUT="${CONTAINER_BASE}${REL_TMP}"

  log "Docker  in : $CIN"
  log "Docker out : $COUT"

  # Probe colour-space metadata and pixel depth so HDR/HLG is preserved across
  # the transcode.  Uses the same Docker image — no extra pull required.
  COL_META="$( docker run --rm --entrypoint ffprobe \
      --mount type=bind,src="$HOST_BASE",dst="$CONTAINER_BASE" \
      linuxserver/ffmpeg:latest \
      -v quiet -select_streams v:0 \
        -show_entries stream=color_primaries,color_trc,color_space,color_range,pix_fmt,bit_rate \
        -of default=noprint_wrappers=1 \
      "$CIN" 2>/dev/null )"
  COL_PRIMARIES="$(printf '%s\n' "$COL_META" | grep '^color_primaries=' | cut -d= -f2 | tr -cd 'a-zA-Z0-9_-')"
  COL_TRC="$(printf '%s\n'       "$COL_META" | grep '^color_trc='       | cut -d= -f2 | tr -cd 'a-zA-Z0-9_-')"
  # If TRC is untagged but primaries indicate HDR (bt2020), infer HLG —
  # the standard HDR transfer function for phone cameras (Pixel, Samsung, etc.).
  # Without this, hevc_qsv would default to bt709 TRC and players would
  # misinterpret the HLG luminance curve.
  if [ -z "$COL_TRC" ] && [ "$COL_PRIMARIES" = "bt2020" ]; then
    COL_TRC="arib-std-b67"
    log "TRC untagged with bt2020 primaries — inferring HLG (arib-std-b67)"
  fi
  COL_SPACE="$(printf '%s\n'     "$COL_META" | grep '^color_space='     | cut -d= -f2 | tr -cd 'a-zA-Z0-9_-')"
  COL_RANGE="$(printf '%s\n'     "$COL_META" | grep '^color_range='     | cut -d= -f2 | tr -cd 'a-zA-Z0-9_-')"
  SRC_PIX="$(printf '%s\n'       "$COL_META" | grep '^pix_fmt='         | cut -d= -f2 | tr -cd 'a-zA-Z0-9_')"
  # hevc_qsv needs p010le for 10-bit input (HDR/HLG); nv12 is the 8-bit default
  PIX_FMT_FLAG="" ; case "$SRC_PIX" in *10*) PIX_FMT_FLAG="-pix_fmt p010le" ;; esac
  CF_P="" ; [ -n "$COL_PRIMARIES" ] && CF_P="-color_primaries $COL_PRIMARIES"
  CF_T="" ; [ -n "$COL_TRC" ]       && CF_T="-color_trc $COL_TRC"
  CF_S="" ; [ -n "$COL_SPACE" ]     && CF_S="-colorspace $COL_SPACE"
  CF_R="" ; [ -n "$COL_RANGE" ]     && CF_R="-color_range $COL_RANGE"
  log "Source: pix=${SRC_PIX:-?} primaries=${COL_PRIMARIES:-?} trc=${COL_TRC:-?} space=${COL_SPACE:-?} range=${COL_RANGE:-?}"

  # Determine output bitrate to match source quality.
  SRC_BPS="$(printf '%s\n' "$COL_META" | grep '^bit_rate=' | cut -d= -f2 | tr -cd '0-9')"
  # Fallback: container (format) bitrate when the stream-level value is N/A.
  if [ -z "$SRC_BPS" ] || ! [ "$SRC_BPS" -gt 0 ] 2>/dev/null; then
    SRC_BPS="$( docker run --rm --entrypoint ffprobe \
        --mount type=bind,src="$HOST_BASE",dst="$CONTAINER_BASE" \
        linuxserver/ffmpeg:latest \
        -v quiet \
          -show_entries format=bit_rate \
          -of default=noprint_wrappers=1 \
        "$CIN" 2>/dev/null | grep '^bit_rate=' | cut -d= -f2 | tr -cd '0-9' )"
  fi
  if [ -n "$SRC_BPS" ] && [ "$SRC_BPS" -gt 0 ] 2>/dev/null; then
    TARGET_K="$(( SRC_BPS / 1000 ))k"
    MAXRATE_K="$(( SRC_BPS * 3 / 2 / 1000 ))k"
    BUFSIZE_K="$(( SRC_BPS * 2 / 1000 ))k"
    log "Bitrate: source=$(( SRC_BPS / 1000 ))k → target=${TARGET_K}, max=${MAXRATE_K}"
  else
    TARGET_K="${BITRATE_FALLBACK_K}k"
    MAXRATE_K="$(( BITRATE_FALLBACK_K * 3 / 2 ))k"
    BUFSIZE_K="$(( BITRATE_FALLBACK_K * 2 ))k"
    log "Bitrate: source unknown → fallback ${TARGET_K}"
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
    $CF_P $CF_T $CF_S $CF_R \
    -tag:v hvc1 \
    -c:a copy \
    -metadata:s:v:0 rotate=0 \
    -max_muxing_queue_size 9999 \
    -movflags +faststart \
    "$COUT" \
    2>&1 | tee -a "$LOG_FILE"

  STATUS=$?

  if [ "$STATUS" -eq 0 ] && [ -f "$TMP" ]; then
    # Preserve the original before replacing it.
    if ! preserve_original "$INPUT"; then
      log "ERROR: Could not preserve '$INPUT' — aborting replacement, deleting .tmp"
      rm -f "$TMP"
      return 1
    fi

    # Stamp the corrected file with the original's mtime so media scanners
    # (including ha-media-index) don't treat it as a brand-new arrival.
    [ -n "$PRESERVED_PATH" ] && touch -r "$PRESERVED_PATH" "$TMP" 2>/dev/null || true
    mv "$TMP" "$INPUT"

    # Restore GPS to the ©xyz udta atom in ISO 6709 format.
    # Windows Photos (and libmediainfo) require this specific atom + format.
    # ffmpeg's -map_metadata copies EXIF-style GPS tags but does NOT write ©xyz,
    # so we use exiftool post-encode — same approach as gps_tag.sh write_gps().
    if [ -n "$GPS_LAT" ] && [ -n "$GPS_LON" ] && \
       [ "$GPS_LAT" != "-" ] && [ "$GPS_LON" != "-" ]; then
      ISO_GPS="$(printf '%+.4f' "$GPS_LAT")$(printf '%+.4f' "$GPS_LON")/"
      "$EXIFTOOL_BIN" -m -n \
        "-UserData:GPSCoordinates-eng=$ISO_GPS" \
        -overwrite_original \
        "$INPUT" >/dev/null 2>&1 \
        && log "GPS restored: $ISO_GPS → $INPUT" \
        || log "WARNING: GPS restore failed for $INPUT"
    fi

    log "DONE: '$INPUT'"
    post_encode_pause
    return 0
  else
    log "ERROR: Encode failed for '$INPUT' (exit $STATUS)"
    rm -f "$TMP"
    return 1
  fi
}

###############################################################################
# PROCESS ONE DIRECTORY  (top-level only; recursion is handled in main)
# $1 = directory to process
# Updates global TOTAL / FIXED / FAILED counters.
###############################################################################

process_dir() {
  local DIR="$1"
  local DIR_STATE="$DIR/.fix_rotation.state"
  local TMP_WORK
  local LOCAL_TOTAL=0 LOCAL_FIXED=0 LOCAL_FAILED=0

  TMP_WORK="$(mktemp /tmp/rotate_work.XXXXXX)"
  find_misoriented_in_dir "$DIR" "$DIR_STATE" > "$TMP_WORK"

  local FOUND
  FOUND="$(wc -l < "$TMP_WORK" | tr -d ' ')"
  [ "$FOUND" -gt 0 ] && log "--- $FOUND misoriented file(s) to process in '$DIR' ---"

  # Use cut to split tab-delimited lines — avoids IFS=$'\t' portability issues.
  local LINE ROTATION FILEPATH
  while read -r LINE; do
    [ -z "$LINE" ] && continue
    ROTATION="$(printf '%s' "$LINE" | cut -f1)"
    FILEPATH="$(printf '%s' "$LINE" | cut -f2-)"
    [ -z "$FILEPATH" ] && continue
    LOCAL_TOTAL=$(( LOCAL_TOTAL + 1 ))
    encode_file "$ROTATION" "$FILEPATH"
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

log "===== Video Rotation Fix Started ====="
log "Folder:   '$FOLDER'"
log "Recurse:  $([ "$RECURSE" = "1" ] && echo yes || echo no)"
if [ "$PRESERVE_MODE" = "backup-root" ]; then
  log "Preserve: backup-root → '$BACKUP_ROOT'"
else
  log "Preserve: rename-hp  (original renamed to filename.mp4_hp)"
fi
[ "$DRYRUN" = "1" ] && log "DRY RUN MODE ACTIVE — no files will be changed"
[ "$FORCE"  = "1" ] && log "FORCE MODE — ignoring per-folder state files"

TOTAL=0
FIXED=0
FAILED=0

if [ "$RECURSE" = "1" ]; then
  # Build dir list in a temp file to avoid a pipe subshell — a piped while loop
  # runs in a subshell, so TOTAL/FIXED/FAILED updates would be lost on exit.
  TMP_DIRS="$(mktemp /tmp/rotate_dirs.XXXXXX)"
  find "$FOLDER" -type d \
      ! -path '*/@eaDir*' \
      ! -path '*/.@__thumb*' \
      | sort > "$TMP_DIRS"
  while IFS= read -r SUBDIR; do
    [ -z "$SUBDIR" ] && continue
    # Skip backup root if it happens to be under FOLDER
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

log "===== Done: $TOTAL misoriented found, $FIXED re-encoded, $FAILED failed ====="
