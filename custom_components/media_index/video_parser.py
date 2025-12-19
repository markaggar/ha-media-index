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
    PYMEDIAINFO_AVAILABLE = True
    # Test if MediaInfo library is actually available
    try:
        MediaInfo.can_parse()
        _LOGGER.debug("[VIDEO] ✅ pymediainfo AND MediaInfo library are both available")
    except Exception as e:
        _LOGGER.warning(f"[VIDEO] ⚠️ pymediainfo installed but MediaInfo library missing: {e}")
        PYMEDIAINFO_AVAILABLE = False
except ImportError:
    PYMEDIAINFO_AVAILABLE = False
    _LOGGER.warning("[VIDEO] ❌ pymediainfo Python package not installed")

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
                _LOGGER.debug(f"[VIDEO] ✅ pymediainfo is AVAILABLE, attempting extraction for {Path(file_path).name}")
                try:
                    media_info = MediaInfo.parse(file_path)
                    
                    for track in media_info.tracks:
                        # Extract datetime from General track
                        if track.track_type == "General":
                            # Priority order for datetime fields
                            datetime_fields = ['encoded_date', 'tagged_date', 'recorded_date', 'mastered_date']
                            for field in datetime_fields:
                                value = getattr(track, field, None)
                                if value:
                                    _LOGGER.debug(f"[VIDEO] Found {field}: {value}")
                                    parsed_dt = VideoMetadataParser._parse_mediainfo_datetime(value)
                                    if parsed_dt:
                                        result['date_taken'] = int(parsed_dt.timestamp())
                                        _LOGGER.debug(f"[VIDEO] Extracted datetime from {field}: {parsed_dt}")
                                        break
                            
                            # Extract GPS coordinates (check both xyz and recorded_location fields)
                            gps_iso6709 = None
                            if hasattr(track, 'recorded_location') and track.recorded_location:
                                gps_iso6709 = track.recorded_location
                                _LOGGER.debug(f"[VIDEO] Found GPS in recorded_location field")
                            elif hasattr(track, 'xyz') and track.xyz:
                                gps_iso6709 = track.xyz
                                _LOGGER.debug(f"[VIDEO] Found GPS in xyz field")
                            
                            if gps_iso6709:
                                coords = VideoMetadataParser._parse_iso6709(gps_iso6709)
                                if coords:
                                    result['latitude'] = coords[0]
                                    result['longitude'] = coords[1]
                                    _LOGGER.debug(f"[VIDEO] GPS coordinates extracted successfully")
                                else:
                                    _LOGGER.warning(f"[VIDEO] Failed to parse ISO6709 GPS format")
                            
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
                            
                            _LOGGER.debug(f"[VIDEO] Dimensions: {result.get('width')}x{result.get('height')}, "
                                        f"Duration: {result.get('duration')}s")
                            
                except Exception as e:
                    _LOGGER.warning(f"[VIDEO] ⚠️ pymediainfo extraction failed for {Path(file_path).name}: {e}, falling back to mutagen")
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
            _LOGGER.error(f"[VIDEO] Failed to extract video metadata from {file_path}: {e}", exc_info=True)
            return None
    
    @staticmethod
    def _parse_mediainfo_datetime(date_str: str) -> Optional[datetime]:
        """Parse datetime from MediaInfo encoded_date field.
        
        MediaInfo typically returns: "2020-05-16 03:37:57 UTC" or "2025-07-06 01:28:44"
        
        Args:
            date_str: Date string from MediaInfo
            
        Returns:
            datetime object, or None if parsing fails
        """
        if not date_str:
            return None
        
        # Remove " UTC" suffix if present
        date_str = date_str.replace(' UTC', '').strip()
        
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
        """Parse ISO 6709 location string into latitude/longitude.
        
        ISO 6709 format: +40.7484-073.9857/ (latitude, longitude, optional altitude)
        
        Args:
            iso6709_str: ISO 6709 formatted location string
            
        Returns:
            Tuple of (latitude, longitude) or None if parsing fails
        """
        try:
            # Remove trailing slash if present
            iso6709_str = iso6709_str.rstrip('/')
            
            # Find the split between lat/lon (look for second +/-)
            # Format: +/-XX.XXXX+/-XXX.XXXX
            lat_end = 1  # Skip first sign
            while lat_end < len(iso6709_str) and iso6709_str[lat_end] not in ['+', '-']:
                lat_end += 1
            
            lat_str = iso6709_str[:lat_end]
            lon_str = iso6709_str[lat_end:].split('/')[0]  # Remove altitude if present
            
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
