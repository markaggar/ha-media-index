"""Video metadata parser for MP4, MOV, and other video formats."""
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

_LOGGER = logging.getLogger(__name__)

try:
    from pymediainfo import MediaInfo
    PYMEDIAINFO_IMPORTABLE = True
except ImportError:
    MediaInfo = None
    PYMEDIAINFO_IMPORTABLE = False
    _LOGGER.warning("[VIDEO] ❌ pymediainfo Python package not installed")


def _check_pymediainfo_availability() -> bool:
    """Test whether pymediainfo is importable AND the libmediainfo shared
    library it wraps is actually loadable.

    NOTE: This is only evaluated when video_parser.py is first imported into
    the running Home Assistant Python process. If libmediainfo is installed
    or reinstalled *after* this module has already been imported (e.g. via
    the `install_libmediainfo` service, or auto-install-on-setup), this cached
    result goes stale. Callers that install/repair libmediainfo at runtime
    MUST call `refresh_pymediainfo_availability()` afterwards to re-test and
    update the module-level `PYMEDIAINFO_AVAILABLE` flag - otherwise
    extraction will keep silently skipping pymediainfo until Home Assistant
    Core itself is restarted (a fresh Python process), even though install
    logs/services report success.
    """
    if not PYMEDIAINFO_IMPORTABLE:
        return False
    try:
        # Test if MediaInfo library is actually available
        MediaInfo.can_parse()
        _LOGGER.debug("[VIDEO] ✅ pymediainfo AND MediaInfo library are both available")
        return True
    except Exception as e:
        _LOGGER.warning(f"[VIDEO] ⚠️ pymediainfo installed but MediaInfo library missing: {e}")
        return False


def refresh_pymediainfo_availability() -> bool:
    """Re-test libmediainfo availability and update PYMEDIAINFO_AVAILABLE.

    Unlike the initial import-time check (which only calls `MediaInfo.can_parse()`
    on the module already in memory), this reloads the `pymediainfo` Python module
    first to clear any cached ctypes library handle, then performs a real
    `MediaInfo.parse()` on a temp file to confirm the C library actually loads and
    works end-to-end - not just that the Python wrapper module is importable.
    This matters because ctypes caches the loaded library handle at import time;
    simply reinstalling libmediainfo.so on disk after pymediainfo has already been
    imported can leave a stale handle that a bare `can_parse()` call won't catch.

    Must be called after installing/repairing the libmediainfo system library
    at runtime (e.g. from the `install_libmediainfo` service or auto-install
    logic in __init__.py). See `_check_pymediainfo_availability` docstring for
    why this can't just be re-evaluated automatically.
    """
    global PYMEDIAINFO_AVAILABLE, MediaInfo
    import sys
    import importlib
    import tempfile

    try:
        if 'pymediainfo' in sys.modules:
            try:
                importlib.reload(sys.modules['pymediainfo'])
            except Exception:
                pass  # Reload can fail (e.g. no __spec__ in some test environments); fall through

        from pymediainfo import MediaInfo as _MediaInfo

        test_fd, test_path = tempfile.mkstemp(suffix='.mp4')
        os.close(test_fd)
        try:
            _MediaInfo.parse(test_path)
        finally:
            os.unlink(test_path)

        # Rebind the module-level MediaInfo symbol to the (possibly reloaded) class.
        # extract_metadata() calls the global MediaInfo.parse() directly, so without
        # this rebind it would keep using the pre-reload class/ctypes handle even
        # after a successful refresh, defeating the purpose of the reload above.
        MediaInfo = _MediaInfo

        PYMEDIAINFO_AVAILABLE = True
        _LOGGER.info("[VIDEO] ✅ libmediainfo availability refreshed - pymediainfo extraction re-enabled")
        return True
    except Exception as e:
        PYMEDIAINFO_AVAILABLE = False
        _LOGGER.warning(f"[VIDEO] ⚠️ libmediainfo availability refreshed - still NOT available: {e}")
        return False


PYMEDIAINFO_AVAILABLE = _check_pymediainfo_availability()

try:
    from mutagen.mp4 import MP4
    MUTAGEN_AVAILABLE = True
except ImportError:
    MUTAGEN_AVAILABLE = False


class VideoMetadataParser:
    """Extract metadata from video files using pymediainfo and mutagen."""

    @staticmethod
    def extract_metadata(file_path: str) -> Optional[Dict[str, Any]]:
        """Extract metadata from a video file.
        
        Args:
            file_path: Path to the video file
            
        Returns:
            Dictionary containing extracted metadata, or None if extraction fails
        """
        if not PYMEDIAINFO_AVAILABLE and not MUTAGEN_AVAILABLE:
            _LOGGER.warning("Neither pymediainfo nor mutagen available, cannot extract video metadata")
            return None
            
        try:
            # Only process video files
            path = Path(file_path)
            if path.suffix.lower() not in {'.mp4', '.m4v', '.mov', '.avi', '.mkv'}:
                _LOGGER.debug(f"Skipping non-video file: {file_path}")
                return None
            
            # Check if file exists and is readable
            if not os.path.exists(file_path):
                _LOGGER.error(f"[VIDEO] File not found: {file_path}")
                return None
            
            file_size = os.path.getsize(file_path)
            _LOGGER.debug(f"[VIDEO] Extracting metadata from: {path.name} (size: {file_size} bytes)")
            
            result: Dict[str, Any] = {}
            
            # ===================================================================
            # METHOD 1: pymediainfo - Most reliable for datetime extraction
            # ===================================================================
            if PYMEDIAINFO_AVAILABLE:
                try:
                    # Ensure path is properly encoded for libmediainfo
                    # Use Path to handle unicode properly, then convert to string
                    path_obj = Path(file_path)
                    
                    # UTF-8/Unicode Protection: pymediainfo handles non-ASCII paths correctly
                    # when passed as proper Python strings
                    # No need to log warnings - this is normal operation
                    
                    media_info = MediaInfo.parse(str(path_obj))
                    
                    for track in media_info.tracks:
                        # Extract datetime from General track
                        if track.track_type == "General":
                            # Priority order for datetime fields
                            # Apple QuickTime creation date is most reliable for iPhone videos
                            datetime_fields = ['comapplequicktimecreationdate', 'recorded_date', 'encoded_date', 'tagged_date', 'mastered_date']
                            for field in datetime_fields:
                                value = getattr(track, field, None)
                                if value:
                                    parsed_dt = VideoMetadataParser._parse_mediainfo_datetime(value)
                                    if parsed_dt:
                                        # Naive datetime uses local timezone (same as EXIF parser)
                                        result['date_taken'] = int(parsed_dt.timestamp())
                                        break
                            
                            # Extract GPS coordinates (check Apple QuickTime, xyz, and recorded_location fields)
                            gps_iso6709 = None
                            gps_field_used = None
                            if hasattr(track, 'comapplequicktimelocationiso6709') and track.comapplequicktimelocationiso6709:
                                gps_iso6709 = track.comapplequicktimelocationiso6709
                                gps_field_used = 'comapplequicktimelocationiso6709'
                            elif hasattr(track, 'recorded_location') and track.recorded_location:
                                gps_iso6709 = track.recorded_location
                                gps_field_used = 'recorded_location'
                            elif hasattr(track, 'xyz') and track.xyz:
                                gps_iso6709 = track.xyz
                                gps_field_used = 'xyz'

                            if gps_iso6709:
                                coords = VideoMetadataParser._parse_iso6709(gps_iso6709)
                                if coords:
                                    result['latitude'] = coords[0]
                                    result['longitude'] = coords[1]
                                    _LOGGER.debug(
                                        f"[VIDEO] GPS extracted via '{gps_field_used}': {coords[0]}, {coords[1]}"
                                    )
                            else:
                                # DIAGNOSTIC: None of the 3 known field names matched. Rather than
                                # silently reporting "no GPS", scan the full track field dump for
                                # anything that looks GPS/location-related so we can catch cases
                                # where a libmediainfo/pymediainfo version change renamed the field
                                # (this has been observed to happen across HA Core upgrades that
                                # reinstall a newer libmediainfo package).
                                try:
                                    track_data = track.to_data()
                                    candidates = {
                                        k: v for k, v in track_data.items()
                                        if v and any(hint in k.lower() for hint in ('locat', 'gps', 'xyz', 'iso6709'))
                                    }
                                    if candidates:
                                        from .const import sanitize_unicode_to_ascii
                                        sanitized_path = sanitize_unicode_to_ascii(str(file_path))
                                        _LOGGER.warning(
                                            "[VIDEO] ⚠️ No GPS extracted for %s using known field names, but found "
                                            "possible GPS/location field(s) under different name(s): %s. "
                                            "libmediainfo may have renamed this field after a package update - "
                                            "please report this so the known field list can be updated.",
                                            sanitized_path, sanitize_unicode_to_ascii(str(candidates))
                                        )
                                    else:
                                        _LOGGER.debug(
                                            "[VIDEO] No GPS/location fields found in General track for %s",
                                            Path(file_path).name
                                        )
                                except Exception:
                                    pass
                            
                            # Extract rating (if available)
                            if hasattr(track, 'rating') and track.rating:
                                try:
                                    rating = int(track.rating)
                                    if 0 <= rating <= 5:
                                        result['rating'] = rating
                                        _LOGGER.debug(f"[VIDEO] Found rating: {rating}/5")
                                except (ValueError, TypeError):
                                    pass
                        
                        # Extract video dimensions and duration
                        if track.track_type == "Video":
                            if track.width:
                                result['width'] = track.width
                            if track.height:
                                result['height'] = track.height
                            if track.duration:
                                # Duration is in milliseconds, convert to seconds
                                result['duration'] = round(track.duration / 1000.0, 2)
                            # Extract rotation for portrait video detection (e.g. iPhone MOV/MP4).
                            # pymediainfo reports coded-frame rotation in degrees; map to our
                            # orientation strings so the Roku cast handler can swap w/h correctly.
                            if hasattr(track, 'rotation') and track.rotation:
                                try:
                                    rotation_deg = float(track.rotation)
                                    if abs(rotation_deg - 90) < 1:
                                        result['orientation'] = '90_cw'
                                    elif abs(rotation_deg - 270) < 1:
                                        result['orientation'] = '90_ccw'
                                    elif abs(rotation_deg - 180) < 1:
                                        result['orientation'] = '180'
                                    # 0° = normal; omit so callers treat absence as normal
                                except (ValueError, TypeError):
                                    pass
                            
                except Exception as e:
                    # ASCII-sanitize exception to prevent Python 3.13+ encoding errors
                    # pymediainfo track objects contain Unicode file paths that can crash HA's logging
                    from .const import sanitize_unicode_to_ascii
                    error_msg = sanitize_unicode_to_ascii(str(e))
                    # Log full path (sanitized) to help identify problematic files
                    sanitized_path = sanitize_unicode_to_ascii(str(file_path))
                    _LOGGER.warning(
                        f"[VIDEO] ⚠️ pymediainfo/libmediainfo extraction failed: {error_msg}\n"
                        f"  File: {sanitized_path}\n"
                        f"  This may indicate Unicode path issues or libmediainfo crash. Falling back to mutagen."
                    )
            else:
                _LOGGER.warning(f"[VIDEO] ❌ pymediainfo NOT AVAILABLE for {Path(file_path).name} - install with 'pip install pymediainfo'. Falling back to mutagen.")
            
            # ===================================================================
            # METHOD 2: mutagen - For additional metadata (rating, etc.)
            # ===================================================================
            if MUTAGEN_AVAILABLE and path.suffix.lower() in {'.mp4', '.m4v', '.mov'}:
                try:
                    video = MP4(file_path)
                    
                    # Extract rating from mutagen tags
                    # iTunes-style rating is stored in 'rate' or '----:com.apple.iTunes:rating'
                    if 'rate' in video and video['rate']:
                        rate_value = video['rate'][0]
                        if rate_value:
                            # Convert 0-100 to 0-5 stars
                            result['rating'] = int(rate_value / 20)
                            _LOGGER.debug(f"[VIDEO] Found rating (rate): {result['rating']} stars")
                
                    # Try custom iTunes rating tag
                    if 'rating' not in result and '----:com.apple.iTunes:rating' in video:
                        rating_bytes = video['----:com.apple.iTunes:rating'][0]
                        try:
                            rating = int(rating_bytes.decode('utf-8'))
                            if 0 <= rating <= 5:
                                result['rating'] = rating
                                _LOGGER.debug(f"[VIDEO] Found rating (iTunes): {rating} stars")
                        except (ValueError, UnicodeDecodeError) as e:
                            _LOGGER.debug(f"[VIDEO] Failed to decode iTunes rating: {e}")
                    
                    # Extract GPS coordinates
                    if 'com.apple.quicktime.location.ISO6709' in video:
                        iso6709 = video['com.apple.quicktime.location.ISO6709'][0]
                        _LOGGER.debug(f"[VIDEO] Found GPS (ISO6709 via mutagen): {iso6709}")
                        coords = VideoMetadataParser._parse_iso6709(iso6709)
                        if coords:
                            result['latitude'] = coords[0]
                            result['longitude'] = coords[1]
                            _LOGGER.debug(f"[VIDEO] GPS coordinates from mutagen: {coords[0]}, {coords[1]}")
                    
                    # If pymediainfo didn't find duration/dimensions, try mutagen
                    if 'duration' not in result and hasattr(video, 'info') and hasattr(video.info, 'length'):
                        result['duration'] = round(video.info.length, 2)
                    
                    if 'width' not in result and hasattr(video, 'info'):
                        if hasattr(video.info, 'width'):
                            result['width'] = video.info.width
                        if hasattr(video.info, 'height'):
                            result['height'] = video.info.height
                    
                except Exception as e:
                    _LOGGER.debug(f"[VIDEO] mutagen extraction failed: {e}")
            
            # ===================================================================
            # METHOD 3: Filename pattern extraction (fallback for datetime)
            # ===================================================================
            if 'date_taken' not in result:
                filename = path.stem  # Filename without extension
                _LOGGER.debug(f"[VIDEO] Trying filename datetime extraction from: {filename}")
                
                # Match patterns like: 20221204_184255, 2022-12-04_18-42-55, etc.
                patterns = [
                    r'(\d{8})_(\d{6})',      # 20221204_184255
                    r'(\d{8})-(\d{6})',      # 20221204-184255
                    r'(\d{4}-\d{2}-\d{2})_(\d{2}-\d{2}-\d{2})',  # 2022-12-04_18-42-55
                    r'(\d{8})',              # 20221204 (date only)
                ]
                
                for pattern in patterns:
                    match = re.search(pattern, filename)
                    if match:
                        try:
                            if len(match.groups()) == 2:
                                date_str = match.group(1).replace('-', '')
                                time_str = match.group(2).replace('-', '')
                                dt_str = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]} {time_str[:2]}:{time_str[2:4]}:{time_str[4:6]}"
                                dt = datetime.strptime(dt_str, '%Y-%m-%d %H:%M:%S')
                            else:
                                date_str = match.group(1).replace('-', '')
                                dt = datetime.strptime(date_str, '%Y%m%d')
                            
                            # Naive datetime uses local timezone (same as EXIF parser)
                            result['date_taken'] = int(dt.timestamp())
                            _LOGGER.debug(f"[VIDEO] Extracted date from filename: {dt}")
                            break
                        except ValueError as e:
                            _LOGGER.debug(f"[VIDEO] Failed to parse date from pattern {pattern}: {e}")
                            continue
            
            # ===================================================================
            # METHOD 4: Filesystem timestamp (final fallback)
            # ===================================================================
            if 'date_taken' not in result:
                try:
                    stat = os.stat(file_path)
                    # Use the earlier of creation time or modification time
                    fs_timestamp = min(stat.st_ctime, stat.st_mtime)
                    result['date_taken'] = int(fs_timestamp)
                    _LOGGER.debug(f"[VIDEO] No date in metadata or filename - using filesystem date: {datetime.fromtimestamp(fs_timestamp)}")
                except Exception as e:
                    _LOGGER.error(f"[VIDEO] Failed to get filesystem dates: {e}")
            
            _LOGGER.debug(f"[VIDEO] Extraction complete - found {len(result)} metadata fields: {list(result.keys())}")
            return result if result else None
            
        except Exception as e:
            # ASCII-sanitize exception to prevent Python 3.13+ encoding errors
            # Avoid exc_info=True as traceback may contain pymediainfo track objects with Unicode paths
            from .const import sanitize_unicode_to_ascii
            error_msg = sanitize_unicode_to_ascii(str(e))
            _LOGGER.error(f"[VIDEO] Failed to extract video metadata from {file_path}: {error_msg}")
            return None
    
    @staticmethod
    def _parse_mediainfo_datetime(date_str: str) -> Optional[datetime]:
        """Parse datetime from MediaInfo date field.
        
        MediaInfo returns various formats:
        - "2020-05-16 03:37:57 UTC" (generic videos)
        - "2021-07-10T12:37:11+0200" (Apple QuickTime with timezone)
        - "2025-07-06 01:28:44" (simple format)
        - "2021-07-10T12:37:11+0200 / 2021-07-10T12:37:11+0200 / 2021-07-10T12:37:11+0200" (multiple values)
        
        Args:
            date_str: Date string from MediaInfo
            
        Returns:
            datetime object, or None if parsing fails
        """
        if not date_str:
            return None
        
        # Handle multiple values separated by " / " - take the first one
        if ' / ' in date_str:
            date_str = date_str.split(' / ')[0].strip()
            _LOGGER.debug(f"[VIDEO] Split multiple values, using first: {date_str}")
        
        # Remove " UTC" suffix if present
        date_str = date_str.replace(' UTC', '').strip()
        
        # Try ISO 8601 with timezone first (handles Apple QuickTime format)
        try:
            dt = datetime.fromisoformat(date_str)
            _LOGGER.debug(f"[VIDEO] Parsed with fromisoformat: {dt}")
            return dt
        except (ValueError, AttributeError) as e:
            _LOGGER.debug(f"[VIDEO] fromisoformat failed for '{date_str}': {e}")
        
        # Try common MediaInfo formats
        date_formats = [
            '%Y-%m-%d %H:%M:%S',       # 2020-05-16 03:37:57
            '%Y-%m-%dT%H:%M:%SZ',      # ISO 8601 with Z
            '%Y-%m-%dT%H:%M:%S',       # ISO 8601 without timezone
            '%Y-%m-%d',                # Date only
        ]
        
        for fmt in date_formats:
            try:
                return datetime.strptime(date_str, fmt)
            except ValueError:
                continue
        
        _LOGGER.debug(f"[VIDEO] Could not parse MediaInfo datetime: {date_str}")
        return None
    
    @staticmethod
    def _parse_iso6709(iso6709_str: str) -> Optional[tuple[float, float]]:
        """Parse a MediaInfo GPS/location string into latitude/longitude.

        Supports two formats seen from different libmediainfo versions:
        - Raw ISO 6709 (older libmediainfo):
          - +40.7484-073.9857/ (latitude, longitude)
          - +52.4915+009.6692+049.668/ (latitude, longitude, altitude)
        - Human-readable degrees+cardinal (seen after a libmediainfo package
          update, e.g. following an HA Core upgrade that reinstalled a newer
          version):
          - 47.591095°N 122.230698°W

        Args:
            iso6709_str: MediaInfo location/GPS string in either format

        Returns:
            Tuple of (latitude, longitude) or None if parsing fails
        """
        # Try the human-readable "DD.DDDDDD°N DD.DDDDDD°W" format first, since
        # it doesn't start with +/- and would otherwise fail the ISO 6709 parse.
        cardinal_match = re.match(
            r'\s*(\d+(?:\.\d+)?)\s*°?\s*([NSns])[,\s]+(\d+(?:\.\d+)?)\s*°?\s*([EWew])',
            iso6709_str,
        )
        if cardinal_match:
            try:
                lat = float(cardinal_match.group(1))
                if cardinal_match.group(2).upper() == 'S':
                    lat = -lat
                lon = float(cardinal_match.group(3))
                if cardinal_match.group(4).upper() == 'W':
                    lon = -lon
                return (lat, lon)
            except ValueError as e:
                _LOGGER.debug(f"Failed to parse cardinal-direction location '{iso6709_str}': {e}")
                return None

        try:
            # Remove trailing slash if present
            iso6709_str = iso6709_str.rstrip('/')
            
            # Find the split between lat/lon (look for second +/-)
            # Format: +/-XX.XXXX+/-XXX.XXXX+/-ALT
            lat_end = 1  # Skip first sign
            while lat_end < len(iso6709_str) and iso6709_str[lat_end] not in ['+', '-']:
                lat_end += 1
            
            lat_str = iso6709_str[:lat_end]
            
            # Find end of longitude (look for third +/- which indicates altitude)
            remaining = iso6709_str[lat_end:]
            lon_end = 1  # Skip the lon sign
            while lon_end < len(remaining) and remaining[lon_end] not in ['+', '-']:
                lon_end += 1
            
            lon_str = remaining[:lon_end]
            
            latitude = float(lat_str)
            longitude = float(lon_str)
            
            return (latitude, longitude)
            
        except (ValueError, IndexError) as e:
            _LOGGER.debug(f"Failed to parse ISO 6709 location '{iso6709_str}': {e}")
            return None
    @staticmethod
    def write_rating(file_path: str, rating: int) -> bool:
        """Write rating to video file metadata.
        
        NOTE: Video rating writes are DISABLED due to technical limitations:
        - exiftool not accessible in Home Assistant executor thread context
        - mutagen can corrupt MP4 files when writing custom tags
        - exiftool requires re-encoding entire video for safe metadata writes
        
        Video ratings are persisted in the database only.
        Use the export/import backup services to preserve ratings across DB resets.
        
        Args:
            file_path: Path to the video file
            rating: Rating value (0-5 stars)
            
        Returns:
            False (video file writes disabled)
        """
        _LOGGER.debug(f"Video rating write skipped for {file_path} (database-only mode)")
        return False
