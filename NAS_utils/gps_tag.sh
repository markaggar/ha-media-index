#!/usr/bin/env bash
# =============================================================================
# gps_tag.sh — Incremental GPS backfill for photo/video folders
# Version: 1.0
#
# Usage:
#   Auto mode (cron, per camera-roll folder):
#     gps_tag.sh /path/to/camera_roll
#
#   DSLR mode (one-shot, external donor folder):
#     gps_tag.sh --donor /path/to/donor_folder /path/to/dslr_folder
#
#   Merge phone caches into a single donor TSV:
#     gps_tag.sh --merge-caches /path/to/output.tsv \
#         /phone1/.gps_cache /phone2/.gps_cache ...
#     gps_tag.sh --merge-caches /path/to/output.tsv \
#         --scan /tanya/phone/root --scan /mark/phone/root
#     (--scan and explicit cache paths can be mixed)
#
#   Recursive DSLR backfill using merged donor cache:
#     gps_tag.sh --dslr-recurse /path/to/merged.tsv /path/to/dslr_root
#
#   Fix missing GPSRef tags on already-tagged JPGs:
#     gps_tag.sh --fix-refs /path/to/folder
#
# Supported file types: JPG, JPEG, MP4, MOV
# Requires: exiftool, bash 4+, awk, sort, comm, find
#
# State files written to TARGET_DIR (hidden):
#   .gps_cache   — TSV: filename<TAB>utc_epoch<TAB>lat<TAB>lon<TAB>first_seen_epoch
#                  lat/lon empty = no GPS found yet (pending resolution)
#   .gps_state   — single epoch: timestamp of last run
#   .gps_lock    — pid of running instance
#   .gps_tag.log — append-only log
# =============================================================================

set -uo pipefail

# Full path to exiftool — required for scheduled/cron environments with minimal PATH.
# Override by setting EXIFTOOL_BIN in the environment before running the script.
EXIFTOOL_BIN="${EXIFTOOL_BIN:-$(command -v exiftool 2>/dev/null || echo /usr/share/applications/ExifTool/exiftool)}"
if [[ ! -x "$EXIFTOOL_BIN" ]]; then
    echo "ERROR: exiftool not found at '$EXIFTOOL_BIN'. Set EXIFTOOL_BIN or install exiftool." >&2
    exit 1
fi

CHUNK_SIZE=500
WINDOW_SECS=86400        # ±24h donor search window
PENDING_EXPIRY_DAYS=30

# ── Argument parsing ──────────────────────────────────────────────────────────
DONOR_MODE=0
FIX_REFS_MODE=0
FIX_TAGGED_MODE=0
FIX_TAGGED_RECURSE=0
MERGE_CACHES_MODE=0
DSLR_RECURSE_MODE=0
DONOR_DIR=""
TARGET_DIR=""
MERGED_CACHE=""
DSLR_ROOT=""
FIX_REF_FILE=""
CACHE_INPUTS=()
SCAN_DIRS=()

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    cat <<'EOF'
gps_tag.sh v1.0 — Incremental GPS backfill for photo/video folders

USAGE
  gps_tag.sh [MODE] [OPTIONS] [PATHS...]

MODES

  Auto mode  (default — suited for cron / Synology scheduled task)
    gps_tag.sh FOLDER
      Reads all media in FOLDER, builds a .gps_cache, and auto-donates GPS
      coordinates from phone photos to DSLR/GPS-less files within ±24 hours.
      Runs are incremental: only new files are read on subsequent runs.

  Donor mode  (one-shot: use a specific donor folder)
    gps_tag.sh --donor DONOR_FOLDER TARGET_FOLDER
      Builds (or reuses) a GPS cache for DONOR_FOLDER and matches coordinates
      to GPS-less files in TARGET_FOLDER within ±24 hours.

  Merge caches  (combine multiple phone .gps_cache files into one donor TSV)
    gps_tag.sh --merge-caches OUTPUT.tsv CACHE [CACHE ...]
    gps_tag.sh --merge-caches OUTPUT.tsv --scan DIR [--scan DIR ...]
      Collects all GPS-populated rows from the input caches, deduplicates,
      sorts by epoch, and writes a single TSV with an epoch-range header.
      --scan DIR  recursively finds all .gps_cache files under DIR.
      Explicit cache paths and --scan dirs can be freely mixed.

  DSLR recursive backfill  (walk an entire DSLR archive using merged cache)
    gps_tag.sh --dslr-recurse MERGED.tsv DSLR_ROOT
      Walks every subfolder of DSLR_ROOT. For each folder, performs a fast
      date-range pre-scan (exiftool -fast2) to skip folders whose media
      predates or postdates the donor epoch window, then matches remaining
      files against the merged donor cache. Each subfolder gets its own
      .gps_cache so runs are incremental. Synology @eaDir thumbnail dirs
      are excluded automatically.

  Fix GPS Ref tags  (repair legacy files missing GPSLatitudeRef/LongitudeRef)
    gps_tag.sh --fix-refs FOLDER
      Scans FOLDER for JPG/JPEG files that have signed-decimal GPS coordinates
      but no Ref tags (written by older versions of this script), and adds the
      correct N/S/E/W reference tags in-place.

  Fix tagged  (apply GPS to files you have manually keyword-tagged)
    gps_tag.sh --fix-tagged FOLDER [--ref REF_FILE] [--recurse]
      Scans FOLDER for media files containing the keyword 'fixgps' or
      'fixgps:LAT:LON' in their EXIF Subject/Keywords, writes the GPS
      coordinates, and removes the keyword tag afterward.

      Tag forms (set in Synology Photos or any IPTC keyword editor):
        fixgps              — coordinates come from --ref REF_FILE (a photo
                              that already has correct GPS)
        fixgps:47.6205:-122.3493  — coordinates embedded in the tag itself;
                              no --ref needed

      --ref REF_FILE        A photo whose GPS coordinates should be applied
                            to all plain 'fixgps'-tagged files in FOLDER.
      --recurse             Walk all subfolders of FOLDER recursively
                            (Synology @eaDir thumbnail dirs are excluded).

OPTIONS
  -h, --help    Show this help and exit.

ENVIRONMENT
  EXIFTOOL_BIN  Full path to exiftool binary. Defaults to $(command -v exiftool)
                or /usr/share/applications/ExifTool/exiftool.
                Override for cron/scheduled-task environments:
                  EXIFTOOL_BIN=/usr/local/bin/exiftool gps_tag.sh FOLDER

STATE FILES  (written to each target folder, hidden)
  .gps_cache    TSV: filename | utc_epoch | lat | lon | first_seen_epoch
                Empty lat/lon = no GPS found yet (pending resolution).
  .gps_state    Epoch timestamp of last auto-mode run.
  .gps_lock     PID of the currently running instance (auto mode only).
  .gps_tag.log  Append-only processing log.

SUPPORTED FILE TYPES
  JPG / JPEG / MP4 / MOV

REQUIRES
  exiftool (version 12+), bash 4+, awk, sort, comm, find

EXAMPLES
  # Backfill a camera-roll folder (add to Synology scheduled task):
  gps_tag.sh "/volume1/photo/Camera/2024-05"

  # One-shot: donate GPS from Mark's phone to a DSLR event folder:
  gps_tag.sh --donor "/volume1/photo/Mark/Camera/2024-05" \
             "/volume1/photo/DSLR/2024-05-wedding"

  # Build a merged donor cache from all phone caches:
  gps_tag.sh --merge-caches /volume1/scripts/merged_donors.tsv \
      --scan /volume1/photo/Mark/Camera \
      --scan /volume1/photo/Tanya/Camera

  # Backfill entire DSLR archive using merged cache:
  gps_tag.sh --dslr-recurse /volume1/scripts/merged_donors.tsv \
             "/volume1/photo/DSLR"

  # Fix files you tagged 'fixgps:47.6205:-122.3493' in Synology Photos:
  gps_tag.sh --fix-tagged "/volume1/photo/DSLR/2019-07"

  # Fix files tagged 'fixgps' using coords from a reference phone photo:
  gps_tag.sh --fix-tagged "/volume1/photo/DSLR/2019-07" \
             --ref "/volume1/photo/Mark/Camera/2019-07/IMG_1234.jpg"

  # Recursively fix all tagged files under an entire DSLR year folder:
  gps_tag.sh --fix-tagged "/volume1/photo/DSLR/2019" --recurse
  gps_tag.sh --fix-tagged "/volume1/photo/DSLR/2019" --recurse \
             --ref "/volume1/photo/Mark/Camera/2019-07/IMG_1234.jpg"
EOF
    exit 0
fi

if [[ "${1:-}" == "--donor" ]]; then
    DONOR_MODE=1
    DONOR_DIR="${2:?'--donor requires a donor folder path'}"
    TARGET_DIR="${3:?'--donor requires a target folder path'}"
elif [[ "${1:-}" == "--fix-refs" ]]; then
    FIX_REFS_MODE=1
    TARGET_DIR="${2:?'--fix-refs requires a folder path'}"
elif [[ "${1:-}" == "--merge-caches" ]]; then
    MERGE_CACHES_MODE=1
    MERGED_CACHE="${2:?'--merge-caches requires an output file path'}"
    shift 2
    # Parse remaining args: --scan DIR expands to all .gps_cache files under DIR;
    # anything else is treated as a literal cache file path.
    CACHE_INPUTS=()
    SCAN_DIRS=()
    while [[ $# -gt 0 ]]; do
        if [[ "$1" == "--scan" ]]; then
            SCAN_DIRS+=("${2:?'--scan requires a directory path'}")
            shift 2
        else
            CACHE_INPUTS+=("$1")
            shift
        fi
    done
    [[ ${#CACHE_INPUTS[@]} -eq 0 && ${#SCAN_DIRS[@]} -eq 0 ]] && \
        { echo "ERROR: --merge-caches requires at least one cache file or --scan DIR" >&2; exit 1; }
elif [[ "${1:-}" == "--dslr-recurse" ]]; then
    DSLR_RECURSE_MODE=1
    MERGED_CACHE="${2:?'--dslr-recurse requires a merged cache file path'}"
    DSLR_ROOT="${3:?'--dslr-recurse requires a DSLR root folder path'}"
elif [[ "${1:-}" == "--fix-tagged" ]]; then
    FIX_TAGGED_MODE=1
    TARGET_DIR="${2:?'--fix-tagged requires a folder path'}"
    shift 2
    while [[ $# -gt 0 ]]; do
        if [[ "$1" == "--ref" ]]; then
            FIX_REF_FILE="${2:?'--ref requires a file path'}"
            shift 2
        elif [[ "$1" == "--recurse" ]]; then
            FIX_TAGGED_RECURSE=1
            shift
        else
            echo "ERROR: unknown argument for --fix-tagged: '$1'" >&2; exit 1
        fi
    done
else
    TARGET_DIR="${1:?'Usage: gps_tag.sh [--donor DONOR_DIR | --fix-refs | --fix-tagged FOLDER [--ref FILE] | --merge-caches OUT CACHE... | --dslr-recurse CACHE ROOT] TARGET_DIR'}"
fi

if [[ $MERGE_CACHES_MODE -eq 0 && $DSLR_RECURSE_MODE -eq 0 ]]; then
    [[ -d "$TARGET_DIR" ]] || { echo "ERROR: target folder not found: $TARGET_DIR" >&2; exit 1; }
    [[ $DONOR_MODE -eq 1 && ! -d "$DONOR_DIR" ]] && { echo "ERROR: donor folder not found: $DONOR_DIR" >&2; exit 1; }
fi
if [[ $DSLR_RECURSE_MODE -eq 1 ]]; then
    [[ -f "$MERGED_CACHE" ]] || { echo "ERROR: merged cache not found: $MERGED_CACHE" >&2; exit 1; }
    [[ -d "$DSLR_ROOT" ]]    || { echo "ERROR: DSLR root not found: $DSLR_ROOT" >&2; exit 1; }
fi

# ── State file paths (set when TARGET_DIR is known) ──────────────────────────
if [[ -n "$TARGET_DIR" ]]; then
    CACHE_FILE="$TARGET_DIR/.gps_cache"
    STATE_FILE="$TARGET_DIR/.gps_state"
    LOCK_FILE="$TARGET_DIR/.gps_lock"
    LOG_FILE="$TARGET_DIR/.gps_tag.log"
elif [[ $DSLR_RECURSE_MODE -eq 1 ]]; then
    # Log to DSLR root; individual subfolder caches are managed per-folder
    LOG_FILE="$DSLR_ROOT/.gps_tag.log"
else
    LOG_FILE="/tmp/gps_tag_merge.log"
fi

# ── Logging ───────────────────────────────────────────────────────────────────
log() { printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" | tee -a "$LOG_FILE" >&2; }

# ── Lock (auto mode only) ─────────────────────────────────────────────────────
acquire_lock() {
    if [[ -f "$LOCK_FILE" ]]; then
        local pid; pid=$(cat "$LOCK_FILE" 2>/dev/null || true)
        if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
            log "ERROR: already running (pid $pid)"; exit 1
        fi
        rm -f "$LOCK_FILE"
    fi
    echo $$ > "$LOCK_FILE"
    # EXIT trap handles cleanup for all exit paths (including INT/TERM below).
    # INT/TERM traps call exit so the EXIT trap fires and the lock is removed.
    trap 'rm -f "$LOCK_FILE"' EXIT
    trap 'log "Interrupted."; exit 130' INT TERM
}

# ── Portable date-to-epoch via awk (no date -d needed) ───────────────────────
# Input: "YYYY:MM:DD HH:MM:SS" and offset "+09:00"/"-05:30"/"" → UTC epoch
AWK_DATE_FUNC='
function is_leap(y)         { return (y%4==0 && (y%100!=0 || y%400==0)) }
function days_in_month(y,m,  t) {
    t = m+0
    if (t==2)              return is_leap(y) ? 29 : 28
    if (t==4||t==6||t==9||t==11) return 30
    return 31
}
function dto_to_epoch(dto, offset,  p,y,mo,d,h,mi,s,days,i,sign,op) {
    if (dto=="" || dto=="-") return ""
    split(dto, p, /[: ]/)
    y=p[1]+0; mo=p[2]+0; d=p[3]+0; h=p[4]+0; mi=p[5]+0; s=p[6]+0
    if (y < 1970 || mo < 1 || d < 1) return ""
    days = 0
    for (i=1970; i<y; i++) days += is_leap(i) ? 366 : 365
    for (i=1;    i<mo; i++) days += days_in_month(y, i)
    days += d - 1
    epoch = days*86400 + h*3600 + mi*60 + s
    if (offset != "" && offset != "-" && offset ~ /^[+-][0-9]/) {
        sign = (substr(offset,1,1)=="+") ? 1 : -1
        split(substr(offset,2), op, /:/)
        epoch -= sign * (op[1]*3600 + op[2]*60)
    }
    return epoch
}
'

# ── Batch-read files, append to a cache TSV ───────────────────────────────────
# Usage: batch_read FILE [FILE ...] >> some_cache.tsv
# Each output line: filename<TAB>utc_epoch<TAB>lat<TAB>lon<TAB>first_seen_epoch
batch_read() {
    [[ $# -eq 0 ]] && return
    local first_seen; first_seen=$(date +%s)

    # exiftool -T outputs tab-separated values, one row per file, no header.
    # -n returns GPS as decimal degrees (positive/negative floats).
    # NOTE: do NOT add -m here. Files with truncated trailers (Samsung MP4 variants)
    # must appear GPS-less so resolve_pass can donate correct coords to them.
    # The write path in write_gps() uses -m + temp-file fallback separately.
    # Fields: FileName, DateTimeOriginal, OffsetTimeOriginal, CreateDate,
    #         GPSLatitude, GPSLongitude
    "$EXIFTOOL_BIN" -T -FileName -n \
        -DateTimeOriginal -OffsetTimeOriginal -CreateDate \
        -GPSLatitude -GPSLongitude \
        "$@" 2>/dev/null \
    | awk -F'\t' -v first_seen="$first_seen" "$AWK_DATE_FUNC"'
    {
        fname=$1; dto=$2; offset=$3; cdate=$4; lat=$5; lon=$6

        # UTC epoch: JPG uses DateTimeOriginal+offset; MP4 CreateDate is UTC
        utc = dto_to_epoch(dto, offset)
        if (utc == "") utc = dto_to_epoch(cdate, "")
        if (utc == "") next  # no usable date, skip file

        # Validate GPS: must be non-empty, non-zero, and within geographic bounds.
        # Upper-bound check (lat≤90, lon≤180) rejects corrupted fields where a
        # timestamp or other large integer was misread as a GPS coordinate.
        clat=""; clon=""
        if (lat!="" && lat!="-" && lon!="" && lon!="-") {
            al = (lat<0) ? -lat : lat
            ao = (lon<0) ? -lon : lon
            if ((al > 0.001 || ao > 0.001) && al <= 90 && ao <= 180) {
                clat = sprintf("%.4f", lat)
                clon = sprintf("%.4f", lon)
            }
        }
        printf "%s\t%d\t%s\t%s\t%d\n", fname, utc, clat, clon, first_seen
    }'
}

# ── GPS hemisphere refs from signed decimal coords ───────────────────────────
# Sets lat_ref/lon_ref and abs_lat/abs_lon in the caller's scope.
_gps_refs() {
    if [[ "${1:0:1}" == "-" ]]; then lat_ref="S"; abs_lat="${1#-}"; else lat_ref="N"; abs_lat="$1"; fi
    if [[ "${2:0:1}" == "-" ]]; then lon_ref="W"; abs_lon="${2#-}"; else lon_ref="E"; abs_lon="$2"; fi
}

# ── Write GPS to a single file ────────────────────────────────────────────────
# Returns 0 on success, 1 on failure.
# Handles: MP4/MOV (©xyz udta atom), JPG (EXIF GPS), OtherImageStart repair.
write_gps() {
    local filepath="$1" lat="$2" lon="$3"
    local ext="${filepath##*.}"; ext="${ext,,}"
    local argfile; argfile=$(mktemp /tmp/gps_tag_arg.XXXXXX)
    local result
    local lat_ref lon_ref abs_lat abs_lon
    _gps_refs "$lat" "$lon"

    if [[ "$ext" == "mp4" || "$ext" == "mov" ]]; then
        # Write to udta/©xyz with QuickTime language "eng" (0x15C7).
        # Windows Photos requires this exact atom + language code.
        # -UserData:GPSCoordinates-eng routes to ©xyz; ISO 6709 format.
        # ISO 6709: use %+.4f so sign is always explicit (avoids "+-34.xx" for S lat)
        local iso; iso="$(printf '%+.4f' "$lat")$(printf '%+.4f' "$lon")/"
        # -n bypasses PrintConv so the ISO 6709 string is written verbatim to ©xyz.
        # Required on exiftool 13.57+ where PrintConvInv rejects the format string.
        printf '%s\n' "-m" "-n" \
            "-UserData:GPSCoordinates-eng=$iso" \
            "-overwrite_original" \
            "$filepath" > "$argfile"
        result=$("$EXIFTOOL_BIN" -@ "$argfile" 2>&1)

        # Fallback for exiftool 13.57+ where "Possible garbage at end of file"
        # and truncated-trailer errors are no longer suppressed by -m.
        # Write a clean copy to a temp file, then replace the original.
        if ! echo "$result" | grep -q "1 image files updated"; then
            if echo "$result" | grep -qi "garbage\|trailer\|truncated\|PrintConv"; then
                local tmpfile; tmpfile=$(mktemp /tmp/gps_mp4_clean.XXXXXX); rm -f "$tmpfile"
                printf '%s\n' "-m" "-n" \
                    "-UserData:GPSCoordinates-eng=$iso" \
                    "-o" "$tmpfile" \
                    "$filepath" > "$argfile"
                local clean_result
                clean_result=$("$EXIFTOOL_BIN" -@ "$argfile" 2>&1)
                if echo "$clean_result" | grep -q "1 image files created"; then
                    mv -f "$tmpfile" "$filepath"
                    result="    1 image files updated"
                else
                    rm -f "$tmpfile"
                    result="$clean_result"
                fi
            fi
        fi
    else
        # JPG: write GPS with Ref tags so PIL / Synology / Windows Photos resolves coords.
        # Always use absolute value + Ref tag (not signed decimal) for maximum compatibility.
        printf '%s\n' "-m" \
            "-GPSLatitude=$abs_lat" \
            "-GPSLatitudeRef=$lat_ref" \
            "-GPSLongitude=$abs_lon" \
            "-GPSLongitudeRef=$lon_ref" \
            "-overwrite_original" \
            "$filepath" > "$argfile"
        result=$("$EXIFTOOL_BIN" -@ "$argfile" 2>&1)

        # Fallback for exiftool 13.57+ where "JPEG EOI marker not found" and similar
        # trailer/garbage errors are hard failures that -m cannot suppress.
        # Samsung panoramas often have data appended after the EOI marker.
        if ! echo "$result" | grep -q "1 image files updated"; then
            if echo "$result" | grep -qi "EOI\|garbage\|trailer\|truncated"; then
                local tmpfile; tmpfile=$(mktemp /tmp/gps_jpg_clean.XXXXXX); rm -f "$tmpfile"
                printf '%s\n' "-m" \
                    "-GPSLatitude=$abs_lat" \
                    "-GPSLatitudeRef=$lat_ref" \
                    "-GPSLongitude=$abs_lon" \
                    "-GPSLongitudeRef=$lon_ref" \
                    "-o" "$tmpfile" \
                    "$filepath" > "$argfile"
                local clean_result
                clean_result=$("$EXIFTOOL_BIN" -@ "$argfile" 2>&1)
                if echo "$clean_result" | grep -q "1 image files created"; then
                    mv -f "$tmpfile" "$filepath"
                    result="    1 image files updated"
                else
                    rm -f "$tmpfile"
                    result="$clean_result"
                fi
            fi
        fi

        if echo "$result" | grep -q "OtherImageStart"; then
            # Samsung SM-Gxxx corrupt IFD: strip all metadata, restore dates+GPS.
            log "  Repairing OtherImageStart: ${filepath##*/}"

            # Save original timestamps in one exiftool read
            local dto cdate mdate offset
            IFS=$'\t' read -r dto cdate mdate offset < <(
                "$EXIFTOOL_BIN" -T \
                    -DateTimeOriginal -CreateDate -ModifyDate -OffsetTimeOriginal \
                    "$filepath" 2>/dev/null || printf '\t\t\t'
            )

            # Strip all metadata (clears corrupt IFD)
            "$EXIFTOOL_BIN" -all= -overwrite_original "$filepath" >/dev/null 2>&1

            # Restore dates + write GPS (with Refs) in one pass
            printf '%s\n' "-m" "-overwrite_original" > "$argfile"
            [[ -n "$dto"    && "$dto"    != "-" ]] && printf -- '-DateTimeOriginal=%s\n'  "$dto"    >> "$argfile"
            [[ -n "$cdate"  && "$cdate"  != "-" ]] && printf -- '-CreateDate=%s\n'        "$cdate"  >> "$argfile"
            [[ -n "$mdate"  && "$mdate"  != "-" ]] && printf -- '-ModifyDate=%s\n'        "$mdate"  >> "$argfile"
            [[ -n "$offset" && "$offset" != "-" ]] && printf -- '-OffsetTimeOriginal=%s\n-OffsetTime=%s\n' "$offset" "$offset" >> "$argfile"
            printf -- '-GPSLatitude=%s\n-GPSLatitudeRef=%s\n-GPSLongitude=%s\n-GPSLongitudeRef=%s\n%s\n' \
                "$abs_lat" "$lat_ref" "$abs_lon" "$lon_ref" "$filepath" >> "$argfile"
            result=$("$EXIFTOOL_BIN" -@ "$argfile" 2>&1)
        fi
    fi

    rm -f "$argfile"
    if echo "$result" | grep -q "1 image files updated"; then
        return 0
    fi
    log "  WRITE ERROR: ${filepath##*/}: $(printf '%s' "$result" | grep -v '^[[:space:]]*$' | head -1)"
    return 1
}

# ── Fix missing GPSLatitudeRef/GPSLongitudeRef on already-tagged JPGs ─────────
# Scans TARGET_DIR for JPG/JPEG files that have GPS lat/lon but no Ref tags
# (written by the old version of this script), and adds them.
fix_refs() {
    local folder="$1"
    log "=== fix-refs mode: $folder ==="
    local fixes=0 failures=0

    # Batch-read FileName, GPSLatitude, GPSLongitude, GPSLatitudeRef for all JPGs.
    # Use awk to select rows with GPS coords but empty/missing Ref — avoids relying
    # on exiftool -if which needs Perl 'defined' (broken on older exiftool builds).
    while IFS=$'\t' read -r fname raw_lat raw_lon; do
        [[ -z "$raw_lat" || "$raw_lat" == "-" || -z "$raw_lon" || "$raw_lon" == "-" ]] && continue
        local filepath="$folder/$fname"
        [[ ! -f "$filepath" ]] && continue

        local lat_ref lon_ref abs_lat abs_lon
        _gps_refs "$raw_lat" "$raw_lon"

        local argfile; argfile=$(mktemp /tmp/gps_fix_arg.XXXXXX)
        printf '%s\n' "-m" "-overwrite_original" \
            "-GPSLatitude=$abs_lat" "-GPSLatitudeRef=$lat_ref" \
            "-GPSLongitude=$abs_lon" "-GPSLongitudeRef=$lon_ref" \
            "$filepath" > "$argfile"
        local result; result=$("$EXIFTOOL_BIN" -@ "$argfile" 2>&1); rm -f "$argfile"

        if echo "$result" | grep -q "1 image files updated"; then
            log "  FIXED  $fname ($lat_ref $abs_lat, $lon_ref $abs_lon)"
            fixes=$(( fixes + 1 ))
        else
            log "  FAILED $fname: $result"
            failures=$(( failures + 1 ))
        fi
    done < <(
        "$EXIFTOOL_BIN" -T -FileName -n -GPSLatitude -GPSLongitude -GPSLatitudeRef \
            -ext jpg -ext jpeg "$folder" 2>/dev/null \
        | awk -F'\t' '$2 != "" && $2 != "-" && ($4 == "" || $4 == "-")'
    )

    log "=== Done: $fixes fixed, $failures failed ==="
}

# ── Resolution pass ───────────────────────────────────────────────────────────
# Scans CACHE_FILE for entries with empty lat/lon, attempts to match a GPS
# donor within WINDOW_SECS. Writes GPS, updates cache in-place.
# Sets global G_WRITTEN with count of files written this pass.
G_WRITTEN=0
resolve_pass() {
    local cache_file="$1" folder="$2"
    G_WRITTEN=0

    # Sorted donors (entries that have GPS)
    local donors_file; donors_file=$(mktemp /tmp/gps_tag_donors.XXXXXX)
    awk -F'\t' '$3!=""' "$cache_file" | sort -t$'\t' -k2 -n > "$donors_file"
    if [[ ! -s "$donors_file" ]]; then
        log "  No donors in cache yet."
        rm -f "$donors_file"; return
    fi

    # Unresolved targets: no GPS and not permanently marked as unwritable
    local targets_file; targets_file=$(mktemp /tmp/gps_tag_targets.XXXXXX)
    awk -F'\t' '$3==""' "$cache_file" > "$targets_file"
    if [[ ! -s "$targets_file" ]]; then
        rm -f "$donors_file" "$targets_file"; return
    fi

    # Match every target to its best donor in ONE awk invocation.
    # donors_file (NR==FNR) is loaded into arrays; targets scanned against them.
    # Donors are time-sorted so we break out of inner loop early once past window.
    # Output: fname<TAB>best_lat<TAB>best_lon<TAB>donor_fname
    local matches_file; matches_file=$(mktemp /tmp/gps_tag_match.XXXXXX)
    awk -F'\t' -v ws="$WINDOW_SECS" '
        NR==FNR {
            nd++
            dt[nd]=$2; dlat[nd]=$3; dlon[nd]=$4; dfile[nd]=$1
            next
        }
        {   # target row
            te=$2
            best_diff=ws+1; best_lat=""; best_lon=""; best_file=""
            for (i=1; i<=nd; i++) {
                if (dt[i] > te+ws) break          # donors sorted asc: rest are later
                diff = te - dt[i]; if (diff<0) diff=-diff
                if (diff > ws)   continue         # still before window
                if (diff < best_diff) {
                    best_diff=diff
                    best_lat=dlat[i]; best_lon=dlon[i]; best_file=dfile[i]
                }
            }
            if (best_lat != "") printf "%s\t%s\t%s\t%s\n", $1, best_lat, best_lon, best_file
        }
    ' "$donors_file" "$targets_file" > "$matches_file"
    rm -f "$donors_file" "$targets_file"

    # Write GPS for each match; collect successful writes
    local updates_file; updates_file=$(mktemp /tmp/gps_tag_upd.XXXXXX)
    while IFS=$'\t' read -r fname dlat dlon donor_file; do
        local filepath="$folder/$fname"
        [[ ! -f "$filepath" ]] && continue
        if write_gps "$filepath" "$dlat" "$dlon"; then
            log "  WRITTEN  ${fname} <- ${donor_file} (${dlat}, ${dlon})"
            printf '%s\t%s\t%s\n' "$fname" "$dlat" "$dlon" >> "$updates_file"
            G_WRITTEN=$(( G_WRITTEN + 1 ))
        else
            log "  WRITE FAILED: $fname"
            # Mark as permanently unwritable in cache (WRITE_FAILED sentinel).
            # Prevents the file from being re-matched and re-attempted every chunk.
            printf '%s\t%s\n' "$fname" "WRITE_FAILED" >> "$updates_file"
        fi
    done < "$matches_file"
    rm -f "$matches_file"

    # Bulk-update cache: set lat/lon for all successfully written files
    if [[ -s "$updates_file" ]]; then
        local tmp; tmp=$(mktemp /tmp/gps_tag_cache.XXXXXX)
        awk -F'\t' -v OFS='\t' '
            NR==FNR {
                new_lat[$1]=$2; new_lon[$1]=$3; next
            }
            {
                if ($1 in new_lat) {
                    if (new_lat[$1] == "WRITE_FAILED") {
                        # Keep the row but mark lat=WRITE_FAILED so it is skipped
                        # by resolve_pass (col3 non-empty) and never re-queued.
                        $3="WRITE_FAILED"; $4=""
                    } else {
                        $3=new_lat[$1]; $4=new_lon[$1]
                    }
                }
                print
            }
        ' "$updates_file" "$cache_file" > "$tmp" && mv "$tmp" "$cache_file"
    fi
    rm -f "$updates_file"
}

# ── List media files in a folder that are NOT already in a cache ──────────────
# Outputs full paths, one per line (newline-separated; assumes no newlines in filenames).
uncached_files() {
    local folder="$1" cache="$2"
    local cached_tmp; cached_tmp=$(mktemp /tmp/gps_tag_cached.XXXXXX)
    cut -f1 "$cache" 2>/dev/null | sort > "$cached_tmp"

    find "$folder" -maxdepth 1 -type f \
        \( -iname "*.jpg" -o -iname "*.jpeg" -o -iname "*.mp4" -o -iname "*.mov" \) \
        | awk -F'/' '{print $NF}' | sort \
        | comm -23 - "$cached_tmp" \
        | awk -v dir="$folder" '{print dir "/" $0}'

    rm -f "$cached_tmp"
}

# ── AUTO MODE ─────────────────────────────────────────────────────────────────
main_auto() {
    acquire_lock
    touch "$CACHE_FILE"
    log "=== Auto mode: $TARGET_DIR ==="

    # Find all media files not yet in cache
    local uncached=()
    while IFS= read -r f; do
        [[ -n "$f" ]] && uncached+=("$f")
    done < <(uncached_files "$TARGET_DIR" "$CACHE_FILE")
    log "Files to read: ${#uncached[@]}"

    # Process in chunks: read → resolve → repeat
    local total=${#uncached[@]} done=0 total_written=0
    local i=0
    while [[ $i -lt $total ]]; do
        local end=$(( i + CHUNK_SIZE < total ? i + CHUNK_SIZE : total ))
        local chunk=("${uncached[@]:$i:$(( end - i ))}")
        i=$end; done=$(( done + ${#chunk[@]} ))

        log "Reading metadata chunk ($done / $total)..."
        batch_read "${chunk[@]}" >> "$CACHE_FILE"

        log "Resolving..."
        resolve_pass "$CACHE_FILE" "$TARGET_DIR"
        total_written=$(( total_written + G_WRITTEN ))
        log "  ${G_WRITTEN} written this chunk."
    done

    # Final pass: catches targets whose best donor arrived in a later chunk
    log "Final resolution pass..."
    resolve_pass "$CACHE_FILE" "$TARGET_DIR"
    total_written=$(( total_written + G_WRITTEN ))

    # Expire pending entries older than PENDING_EXPIRY_DAYS
    local now; now=$(date +%s)
    local expiry=$(( now - PENDING_EXPIRY_DAYS * 86400 ))
    local expired=0
    local tmp; tmp=$(mktemp /tmp/gps_tag_expire.XXXXXX)
    while IFS=$'\t' read -r fname utc lat lon first_seen; do
        if [[ -z "$lat" && "$first_seen" -lt "$expiry" ]]; then
            log "  EXPIRED (no donor in ${PENDING_EXPIRY_DAYS}d): $fname"
            expired=$(( expired + 1 ))
        else
            printf '%s\t%s\t%s\t%s\t%s\n' "$fname" "$utc" "$lat" "$lon" "$first_seen" >> "$tmp"
        fi
    done < "$CACHE_FILE"
    mv "$tmp" "$CACHE_FILE"

    date +%s > "$STATE_FILE"
    log "=== Done: ${total_written} written, ${expired} expired ==="
}

# ── DSLR MODE ─────────────────────────────────────────────────────────────────
main_dslr() {
    log "=== DSLR mode: donor=$DONOR_DIR  target=$TARGET_DIR ==="

    local donor_cache="$DONOR_DIR/.gps_cache"
    local tmp_donor tmp_target
    tmp_donor=$(mktemp /tmp/gps_tag_donor.XXXXXX)
    tmp_target=""
    # Single trap covers both tmp files; tmp_target may still be empty on early exit
    trap 'rm -f "$tmp_donor" ${tmp_target:+"$tmp_target"}' RETURN

    # Build/refresh donor GPS index
    if [[ -f "$donor_cache" ]]; then
        log "Loading existing donor cache..."
        cp "$donor_cache" "$tmp_donor"

        # Top-up: read any donor files not yet in cache
        local new_donors=()
        while IFS= read -r f; do
            [[ -n "$f" ]] && new_donors+=("$f")
        done < <(uncached_files "$DONOR_DIR" "$tmp_donor")

        if [[ ${#new_donors[@]} -gt 0 ]]; then
            log "Reading ${#new_donors[@]} new donor files..."
            batch_read "${new_donors[@]}" >> "$tmp_donor"
            # Persist updated cache back to donor folder
            cp "$tmp_donor" "$donor_cache"
        else
            log "Donor cache is up to date."
        fi
    else
        log "No donor cache found, reading entire donor folder..."
        local donor_files=()
        while IFS= read -r f; do
            [[ -n "$f" ]] && donor_files+=("$f")
        done < <(find "$DONOR_DIR" -maxdepth 1 -type f \
            \( -iname "*.jpg" -o -iname "*.jpeg" \
               -o -iname "*.mp4" -o -iname "*.mov" \))
        log "Reading ${#donor_files[@]} donor files..."
        batch_read "${donor_files[@]}" > "$tmp_donor"
        # Save cache to donor folder so future runs are faster
        cp "$tmp_donor" "$donor_cache"
    fi

    # Sort donors by UTC, keep only entries with GPS; write to temp file
    local donors_sorted_file; donors_sorted_file=$(mktemp /tmp/gps_tag_dslr_d.XXXXXX)
    awk -F'\t' '$3!=""' "$tmp_donor" | sort -t$'\t' -k2 -n > "$donors_sorted_file"
    local donor_count; donor_count=$(wc -l < "$donors_sorted_file" || echo 0)
    log "Donor GPS entries available: $donor_count"

    # Read entire target folder (no cache — DSLR folders are one-shot)
    local target_files=()
    while IFS= read -r f; do
        [[ -n "$f" ]] && target_files+=("$f")
    done < <(find "$TARGET_DIR" -maxdepth 1 -type f \
        \( -iname "*.jpg" -o -iname "*.jpeg" \
           -o -iname "*.mp4" -o -iname "*.mov" \))
    log "Reading ${#target_files[@]} target files..."

    tmp_target=$(mktemp /tmp/gps_tag_target.XXXXXX)
    batch_read "${target_files[@]}" > "$tmp_target"

    # Match all targets in ONE awk invocation.
    # donors_sorted_file (NR==FNR) loaded into arrays; targets scanned against them.
    # Donors are time-sorted so inner loop breaks early once past the window.
    # Skipped files are reported to stderr; only matches go to stdout.
    local matches_file; matches_file=$(mktemp /tmp/gps_tag_dslr_m.XXXXXX)
    awk -F'\t' -v ws="$WINDOW_SECS" '
        NR==FNR {
            nd++
            dt[nd]=$2; dlat[nd]=$3; dlon[nd]=$4; dfile[nd]=$1
            next
        }
        $3 != "" { next }   # target already has GPS, skip silently
        $2 == ""  { print "SKIP_NODATE\t" $1 > "/dev/stderr"; next }
        {   # unresolved target
            te=$2
            best_diff=ws+1; best_lat=""; best_lon=""; best_file=""
            for (i=1; i<=nd; i++) {
                if (dt[i] > te+ws) break
                diff=te-dt[i]; if(diff<0)diff=-diff
                if (diff>ws) continue
                if (diff<best_diff) {
                    best_diff=diff; best_lat=dlat[i]; best_lon=dlon[i]; best_file=dfile[i]
                }
            }
            if (best_lat != "") printf "%s\t%s\t%s\t%s\n", $1, best_lat, best_lon, best_file
            else                 printf "SKIP_NODONOR\t%s\n", $1 > "/dev/stderr"
        }
    ' "$donors_sorted_file" "$tmp_target" > "$matches_file" \
      2> >(while IFS=$'\t' read -r reason fname; do
               if   [[ "$reason" == "SKIP_NODATE"   ]]; then log "  SKIP (no date): $fname"
               elif [[ "$reason" == "SKIP_NODONOR"  ]]; then log "  SKIP (no donor in ±24h): $fname"
               fi
           done)
    rm -f "$donors_sorted_file"

    local written=0 failed=0
    while IFS=$'\t' read -r fname dlat dlon donor_file; do
        if write_gps "$TARGET_DIR/$fname" "$dlat" "$dlon"; then
            log "  WRITTEN  ${fname} <- ${donor_file} (${dlat}, ${dlon})"
            written=$(( written + 1 ))
        else
            log "  WRITE FAILED: $fname"
            failed=$(( failed + 1 ))
        fi
    done < "$matches_file"
    rm -f "$matches_file"

    log "=== Done: ${written} written, ${failed} failed ==="
}

# ── MERGE CACHES MODE ─────────────────────────────────────────────────────────
# Reads N .gps_cache files, keeps only GPS-populated rows, deduplicates by
# filename, and writes a sorted merged TSV.  A header comment records the
# epoch range so --dslr-recurse can gate folders without re-reading the file.
main_merge_caches() {
    local output="$1"; shift
    local inputs=("$@")

    # Expand any --scan directories: find all .gps_cache files recursively
    if [[ ${#SCAN_DIRS[@]} -gt 0 ]]; then
        for scan_dir in "${SCAN_DIRS[@]}"; do
            if [[ ! -d "$scan_dir" ]]; then
                log "  WARNING: --scan directory not found, skipping: $scan_dir"
                continue
            fi
            local found=0
            while IFS= read -r cache_file; do
                inputs+=("$cache_file")
                found=$(( found + 1 ))
            done < <(find "$scan_dir" -type f -name ".gps_cache" | sort)
            log "  --scan $scan_dir: found $found .gps_cache file(s)"
        done
    fi

    if [[ ${#inputs[@]} -eq 0 ]]; then
        log "ERROR: no cache files found (check --scan directories or explicit paths)."
        exit 1
    fi

    log "=== Merging ${#inputs[@]} cache file(s) into $output ==="

    local tmp; tmp=$(mktemp /tmp/gps_tag_merge.XXXXXX)

# Concatenate all caches, drop rows with no GPS or WRITE_FAILED sentinel
    # Epoch 946684800 = 2000-01-01: rows older than this are flagged as suspect
    # (camera clock reset / dead battery). They are kept but warned about.
    local suspect_epoch=946684800
    for f in "${inputs[@]}"; do
        if [[ ! -f "$f" ]]; then
            log "  WARNING: cache not found, skipping: $f"
            continue
        fi
        awk -F'\t' '
            function valid_gps(v,  a) { a=(v<0)?-v:v; return (a>0.001 && a<=180) }
            $3!="" && $3!="WRITE_FAILED" && valid_gps($3+0) && valid_gps($4+0)
        ' "$f" >> "$tmp"
        local n; n=$(awk -F'\t' '
            function valid_gps(v,  a) { a=(v<0)?-v:v; return (a>0.001 && a<=180) }
            $3!="" && $3!="WRITE_FAILED" && valid_gps($3+0) && valid_gps($4+0)
        ' "$f" | wc -l)
        log "  $f: $n GPS rows"
        # Warn about entries with valid GPS but suspiciously old dates
        awk -F'\t' -v se="$suspect_epoch" -v src="$f" -v logf="$LOG_FILE" \
            'function valid_gps(v,  a) { a=(v<0)?-v:v; return (a>0.001 && a<=180) }
             BEGIN { ts=strftime("[%Y-%m-%d %H:%M:%S]", systime()) }
             $3!="" && $3!="WRITE_FAILED" && valid_gps($3+0) && valid_gps($4+0) && $2+0 < se {
                 msg=ts " WARNING: suspect date in " src ": " $1 " epoch=" $2 " (" $3 ", " $4 ")"
                 print msg >> logf
                 print msg > "/dev/stderr"
             }' "$f"
    done

    if [[ ! -s "$tmp" ]]; then
        log "ERROR: no GPS rows found in any input cache."
        rm -f "$tmp"; exit 1
    fi

    # Deduplicate by filename (col1), keeping the row with the most-recent first_seen (col5)
    sort -t$'\t' -k1,1 -k5,5rn "$tmp" | awk -F'\t' '!seen[$1]++' > "${tmp}.dedup"

    # Sort final output by utc_epoch (col2) for fast binary-search in resolve_pass
    sort -t$'\t' -k2,2n "${tmp}.dedup" > "${tmp}.sorted"

    # Compute epoch range and prepend as a comment header
    local min_epoch max_epoch row_count
    min_epoch=$(awk -F'\t' 'NR==1{print $2}' "${tmp}.sorted")
    max_epoch=$(awk -F'\t' 'END{print $2}' "${tmp}.sorted")
    row_count=$(wc -l < "${tmp}.sorted")

    {
        printf '# gps_tag merged donor cache\n'
        printf '# min_epoch=%s\n' "$min_epoch"
        printf '# max_epoch=%s\n' "$max_epoch"
        printf '# rows=%s\n' "$row_count"
        cat "${tmp}.sorted"
    } > "$output"

    rm -f "$tmp" "${tmp}.dedup" "${tmp}.sorted"

    local min_date max_date
    min_date=$(awk -v e="$min_epoch" 'BEGIN{
        t=e; d=int(t/86400); t-=d*86400
        h=int(t/3600); t-=h*3600; m=int(t/60); s=t-m*60
        y=1970; while(1){ly=(y%4==0&&(y%100!=0||y%400==0));days=ly?366:365;if(d<days)break;d-=days;y++}
        mn=1; while(1){if(mn==2)dm=(ly?29:28);else if(mn==4||mn==6||mn==9||mn==11)dm=30;else dm=31;if(d<dm)break;d-=dm;mn++}
        printf "%04d-%02d-%02d", y, mn, d+1
    }')
    max_date=$(awk -v e="$max_epoch" 'BEGIN{
        t=e; d=int(t/86400); t-=d*86400
        h=int(t/3600); t-=h*3600; m=int(t/60); s=t-m*60
        y=1970; while(1){ly=(y%4==0&&(y%100!=0||y%400==0));days=ly?366:365;if(d<days)break;d-=days;y++}
        mn=1; while(1){if(mn==2)dm=(ly?29:28);else if(mn==4||mn==6||mn==9||mn==11)dm=30;else dm=31;if(d<dm)break;d-=dm;mn++}
        printf "%04d-%02d-%02d", y, mn, d+1
    }')

    log "=== Done: $row_count GPS donors, date range $min_date → $max_date ==="
}

# ── DSLR RECURSE MODE ─────────────────────────────────────────────────────────
# Recursively walks DSLR_ROOT. For each subfolder containing media files:
#   1. Skips the folder entirely if no file's EXIF date could overlap the
#      donor cache date range (± WINDOW_SECS).  This avoids burning time on
#      folders from before GPS phones existed.
#   2. Otherwise, batch-reads the folder, runs resolve_pass against the merged
#      donor cache, and writes GPS to matched files.
# Each subfolder gets its own .gps_cache so runs are incremental.
main_dslr_recurse() {
    log "=== DSLR recurse mode: $DSLR_ROOT ==="
    log "    Donor cache: $MERGED_CACHE"

    # Read epoch range from the comment header written by --merge-caches
    local donor_min donor_max
    donor_min=$(grep '^# min_epoch=' "$MERGED_CACHE" | head -1 | sed 's/^# min_epoch=//')
    donor_max=$(grep '^# max_epoch=' "$MERGED_CACHE" | head -1 | sed 's/^# max_epoch=//')

    if [[ -z "$donor_min" || -z "$donor_max" ]]; then
        log "ERROR: merged cache missing epoch range header. Regenerate with --merge-caches."
        exit 1
    fi

    local gate_min=$(( donor_min - WINDOW_SECS ))
    local gate_max=$(( donor_max + WINDOW_SECS ))
    log "    Donor range gate: epochs $gate_min – $gate_max"

    # Build a sorted donors file (strip header comments, keep GPS rows)
    local donors_file; donors_file=$(mktemp /tmp/gps_dslr_donors.XXXXXX)
    grep -v '^#' "$MERGED_CACHE" | awk -F'\t' '$3!="" && $3!="WRITE_FAILED"' \
        | sort -t$'\t' -k2,2n > "$donors_file"
    local donor_count; donor_count=$(wc -l < "$donors_file")
    log "    $donor_count GPS donors loaded."
    trap 'rm -f "$donors_file"' RETURN

    local total_written=0 total_skipped=0 total_folders=0

    # Walk all subfolders (including root itself) that contain media files
    while IFS= read -r folder; do
        # Quick check: are there any media files in this (non-recursive) folder?
        local media_count
        media_count=$(find "$folder" -maxdepth 1 -type f \
            \( -iname "*.jpg" -o -iname "*.jpeg" -o -iname "*.mp4" -o -iname "*.mov" \) \
            | wc -l)
        [[ "$media_count" -eq 0 ]] && continue

        total_folders=$(( total_folders + 1 ))
        local folder_cache="$folder/.gps_cache"
        local folder_log="$folder/.gps_tag.log"
        touch "$folder_cache"

        # Find uncached files in this folder
        local uncached=()
        while IFS= read -r f; do
            [[ -n "$f" ]] && uncached+=("$f")
        done < <(uncached_files "$folder" "$folder_cache")

        if [[ ${#uncached[@]} -eq 0 ]]; then
            # All files already in cache — still run resolve_pass in case
            # new donors have been added to the merged cache since last run.
            # But only if there are unresolved entries.
            local pending; pending=$(awk -F'\t' '$3==""' "$folder_cache" | wc -l)
            if [[ "$pending" -eq 0 ]]; then
                continue   # fully resolved, nothing to do
            fi
        else
            # Fast pre-scan: read only DateTimeOriginal (-fast2) to check if any
            # uncached file falls within the donor epoch gate before the full
            # batch_read. Avoids expensive multi-tag EXIF reads on out-of-range folders.
            local prescan_result
            # uncached[] already contains full paths (from uncached_files)
            local prescan_raw
            prescan_raw=$("$EXIFTOOL_BIN" -fast2 -T -d '%s' -DateTimeOriginal \
                "${uncached[@]}" 2>/dev/null)
            prescan_result=$(printf '%s\n' "$prescan_raw" | \
                awk -v lo="$gate_min" -v hi="$gate_max" '
                    /^[0-9]/ { e=$1+0; if (e>=lo && e<=hi) { f=1; exit } }
                    END { print f+0 }
                ')
            if [[ "$prescan_result" -eq 0 ]]; then
                log "  SKIP (out of donor range): ${folder##*/} (${#uncached[@]} files pre-scanned)"
                total_skipped=$(( total_skipped + 1 ))
                continue
            fi
            # Read new files into folder cache
            batch_read "${uncached[@]}" >> "$folder_cache"
        fi

        # Date-range gate: check if any target epoch falls within donor range
        local in_range
        in_range=$(awk -F'\t' -v lo="$gate_min" -v hi="$gate_max" \
            '$3=="" && $2!="" && $2+0>=lo && $2+0<=hi {found=1; exit} END{print found+0}' \
            "$folder_cache")

        if [[ "$in_range" -eq 0 ]]; then
            local earliest latest
            earliest=$(awk -F'\t' '$2!=""' "$folder_cache" | sort -t$'\t' -k2,2n | head -1 | cut -f2)
            latest=$(awk -F'\t' '$2!=""' "$folder_cache" | sort -t$'\t' -k2,2n | tail -1 | cut -f2)
            log "  SKIP (out of donor range [$earliest–$latest]): ${folder##*/}"
            total_skipped=$(( total_skipped + 1 ))
            continue
        fi

        # Use a temporary combined cache: folder targets + external donors
        # resolve_pass reads donors from col3!="" in the same cache file,
        # so we merge folder cache + donors_file into one temp file for it.
        local combined; combined=$(mktemp /tmp/gps_dslr_combined.XXXXXX)
        cat "$folder_cache" "$donors_file" > "$combined"

        # resolve_pass operates on combined, but writes only touch files in $folder
        # We need a modified resolve_pass that accepts a separate donors file.
        # Simpler: call the matching awk inline, then write and update folder_cache.
        local matches_file; matches_file=$(mktemp /tmp/gps_dslr_match.XXXXXX)
        awk -F'\t' -v ws="$WINDOW_SECS" '
            NR==FNR {
                nd++; dt[nd]=$2; dlat[nd]=$3; dlon[nd]=$4; dfile[nd]=$1; next
            }
            $3 != "" { next }
            $2 == "" { next }
            {
                te=$2
                best_diff=ws+1; best_lat=""; best_lon=""; best_file=""
                for (i=1; i<=nd; i++) {
                    if (dt[i] > te+ws) break
                    diff=te-dt[i]; if(diff<0) diff=-diff
                    if (diff>ws) continue
                    if (diff<best_diff) {
                        best_diff=diff; best_lat=dlat[i]; best_lon=dlon[i]; best_file=dfile[i]
                    }
                }
                if (best_lat != "") printf "%s\t%s\t%s\t%s\n", $1, best_lat, best_lon, best_file
            }
        ' "$donors_file" "$folder_cache" > "$matches_file"
        rm -f "$combined"

        local folder_written=0 folder_failed=0
        local updates_file; updates_file=$(mktemp /tmp/gps_dslr_upd.XXXXXX)
        while IFS=$'\t' read -r fname dlat dlon donor_file; do
            local filepath="$folder/$fname"
            [[ ! -f "$filepath" ]] && continue
            if write_gps "$filepath" "$dlat" "$dlon"; then
                log "  WRITTEN  ${fname} <- ${donor_file} (${dlat}, ${dlon})  [${folder##*/}]"
                printf '%s\t%s\t%s\n' "$fname" "$dlat" "$dlon" >> "$updates_file"
                folder_written=$(( folder_written + 1 ))
            else
                log "  WRITE FAILED: $fname  [${folder##*/}]"
                printf '%s\t%s\n' "$fname" "WRITE_FAILED" >> "$updates_file"
                folder_failed=$(( folder_failed + 1 ))
            fi
        done < "$matches_file"
        rm -f "$matches_file"

        # Update folder cache with written GPS coords
        if [[ -s "$updates_file" ]]; then
            local tmp_cache; tmp_cache=$(mktemp /tmp/gps_dslr_cache.XXXXXX)
            awk -F'\t' -v OFS='\t' '
                NR==FNR { new_lat[$1]=$2; new_lon[$1]=$3; next }
                {
                    if ($1 in new_lat) {
                        if (new_lat[$1] == "WRITE_FAILED") { $3="WRITE_FAILED"; $4="" }
                        else { $3=new_lat[$1]; $4=new_lon[$1] }
                    }
                    print
                }
            ' "$updates_file" "$folder_cache" > "$tmp_cache" && mv "$tmp_cache" "$folder_cache"
        fi
        rm -f "$updates_file"

        if [[ $folder_written -gt 0 || $folder_failed -gt 0 ]]; then
            log "  ${folder##*/}: $folder_written written, $folder_failed failed"
        fi
        total_written=$(( total_written + folder_written ))

    done < <(find "$DSLR_ROOT" -type d -not -path '*/@eaDir*' | sort)

    log "=== Done: $total_folders folders examined, $total_skipped skipped (out of range), $total_written written ==="
}

# ── Fix tagged mode ───────────────────────────────────────────────────────────
# Finds all media files tagged with keyword 'fixgps' or 'fixgps:LAT:LON'
# and writes correct GPS coords, then removes the tag.
#   fixgps          — requires --ref FILE to supply coordinates
#   fixgps:LAT:LON  — self-contained; coords embedded in the tag value
# With --recurse, walks all subfolders of TARGET_DIR (excluding @eaDir).

main_fix_tagged() {
    # Get GPS from reference file if provided
    local ref_lat="" ref_lon=""
    if [[ -n "$FIX_REF_FILE" ]]; then
        [[ -f "$FIX_REF_FILE" ]] || { log "ERROR: reference file not found: $FIX_REF_FILE"; exit 1; }
        IFS=$'\t' read -r ref_lat ref_lon < <(
            "$EXIFTOOL_BIN" -T -n -GPSLatitude -GPSLongitude "$FIX_REF_FILE" 2>/dev/null
        )
        if [[ -z "$ref_lat" || "$ref_lat" == "-" ]]; then
            log "ERROR: reference file has no GPS: $FIX_REF_FILE"; exit 1
        fi
        log "    Reference GPS: $ref_lat, $ref_lon  (from ${FIX_REF_FILE##*/})"
    fi

    if [[ $FIX_TAGGED_RECURSE -eq 1 ]]; then
        log "=== fix-tagged mode (recursive): $TARGET_DIR ==="
    else
        log "=== fix-tagged mode: $TARGET_DIR ==="
    fi

    local total_written=0 total_failed=0 total_skipped=0
    local coord_re='fixgps:([+-]?[0-9]+[.]?[0-9]*):([+-]?[0-9]+[.]?[0-9]*)'

    # Build folder list: single folder, or full recursive walk
    local folders=()
    if [[ $FIX_TAGGED_RECURSE -eq 1 ]]; then
        while IFS= read -r d; do folders+=("$d"); done \
            < <(find "$TARGET_DIR" -type d -not -path '*/@eaDir*' | sort)
    else
        folders=("$TARGET_DIR")
    fi

    for folder in "${folders[@]}"; do
        local written=0 failed=0 skipped=0

        while IFS=$'\t' read -r fname keywords; do
            [[ -z "$fname" ]] && continue
            local filepath="$folder/$fname"
            [[ -f "$filepath" ]] || continue

            # Determine coords: embedded in tag, or from --ref
            local lat="" lon="" rm_tag="fixgps"
            if [[ "$keywords" =~ $coord_re ]]; then
                lat="${BASH_REMATCH[1]}"
                lon="${BASH_REMATCH[2]}"
                rm_tag="fixgps:${lat}:${lon}"
            elif [[ -n "$ref_lat" ]]; then
                lat="$ref_lat"
                lon="$ref_lon"
            else
                log "  SKIP (no coords): $fname — use fixgps:LAT:LON tag or provide --ref"
                skipped=$(( skipped + 1 ))
                continue
            fi

            if write_gps "$filepath" "$lat" "$lon"; then
                log "  WRITTEN  $fname  (${lat}, ${lon})"
                written=$(( written + 1 ))
                # Remove the fixgps keyword so the file is clean
                "$EXIFTOOL_BIN" -m -overwrite_original \
                    "-Keywords-=$rm_tag" "-Subject-=$rm_tag" \
                    "$filepath" >/dev/null 2>&1
            else
                log "  WRITE FAILED: $fname"
                failed=$(( failed + 1 ))
            fi
        done < <(
            "$EXIFTOOL_BIN" -T -FileName -Subject \
                -ext jpg -ext jpeg -ext mp4 -ext mov \
                "$folder" 2>/dev/null \
            | awk -F'\t' 'tolower($2) ~ /fixgps/ { print $1 "\t" $2 }'
        )

        if [[ $FIX_TAGGED_RECURSE -eq 1 && $(( written + failed + skipped )) -gt 0 ]]; then
            log "  ${folder##*/}: $written written, $failed failed, $skipped skipped"
        fi
        total_written=$(( total_written + written ))
        total_failed=$(( total_failed + failed ))
        total_skipped=$(( total_skipped + skipped ))
    done

    log "=== Done: $total_written written, $total_failed failed, $total_skipped skipped ==="
}

# ── Entry point ───────────────────────────────────────────────────────────────
if   [[ $FIX_REFS_MODE -eq 1 ]]; then
    fix_refs "$TARGET_DIR"
elif [[ $FIX_TAGGED_MODE -eq 1 ]]; then
    main_fix_tagged
elif [[ $MERGE_CACHES_MODE -eq 1 ]]; then
    main_merge_caches "$MERGED_CACHE" "${CACHE_INPUTS[@]}"
elif [[ $DSLR_RECURSE_MODE -eq 1 ]]; then
    main_dslr_recurse
elif [[ $DONOR_MODE -eq 1 ]]; then
    main_dslr
else
    main_auto
fi
