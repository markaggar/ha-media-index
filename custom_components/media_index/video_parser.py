"""Video metadata parser for MP4, MOV, and other video formats."""
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

try:
    from mutagen.mp4 import MP4
except ImportError:
    MP4 = None

_LOGGER = logging.getLogger(__name__)


class VideoMetadataParser:
    """Extract metadata from video files using mutagen."""

    @staticmethod
    def extract_metadata(file_path: str) -> Optional[Dict[str, Any]]:
        """Extract metadata from a video file.
        
        Args:
            file_path: Path to the video file
            
        Returns:
            Dictionary containing extracted metadata, or None if extraction fails
        """
        if MP4 is None:
            _LOGGER.warning("mutagen not available, cannot extract video metadata")
            return None
            
        try:
            # Only process video files
            path = Path(file_path)
            if path.suffix.lower() not in {'.mp4', '.m4v', '.mov'}:
                _LOGGER.debug(f"Skipping non-MP4 file: {file_path}")
                return None
            
            # Check if file exists and is readable
            import os
            if not os.path.exists(file_path):
                _LOGGER.error(f"[VIDEO] File not found: {file_path}")
                return None
            
            file_size = os.path.getsize(file_path)
            _LOGGER.info(f"[VIDEO] Extracting metadata from: {path.name} (size: {file_size} bytes, path: {file_path})")
            
            try:
                video = MP4(file_path)
            except Exception as load_error:
                _LOGGER.error(f"[VIDEO] Mutagen failed to load MP4 {path.name}: {type(load_error).__name__}: {load_error}")
                # Even if mutagen fails, try to extract from filename
                video = None
                
            if not video:
                _LOGGER.warning(f"[VIDEO] Mutagen returned empty object for: {path.name} - will try filename fallback only")
                result: Dict[str, Any] = {}
            else:
                result: Dict[str, Any] = {}
                
                # Extract basic video properties from video.info
                if hasattr(video, 'info') and video.info:
                    _LOGGER.info(f"[VIDEO] video.info attributes: {dir(video.info)}")
                    
                    # Duration in seconds
                    if hasattr(video.info, 'length'):
                        result['duration'] = round(video.info.length, 2)
                        _LOGGER.info(f"[VIDEO] Duration: {result['duration']}s")
                    
                    # Dimensions (width x height)
                    if hasattr(video.info, 'width') and hasattr(video.info, 'height'):
                        result['width'] = video.info.width
                        result['height'] = video.info.height
                        _LOGGER.info(f"[VIDEO] Dimensions: {result['width']}x{result['height']}")
            
            # Extract creation date from MP4 atoms
            # MP4 files store creation dates in the movie/media/track header atoms
            # These are stored as seconds since midnight, January 1, 1904 UTC (MP4 epoch)
            creation_timestamp = None
            
            # Try to access raw MP4 atoms through mutagen's internal structure
            # Mutagen-mp4 exposes atoms through the private _mdat attribute
            try:
                # Method 1: Try to access movie header atoms directly
                # MP4 structure: moov.mvhd contains creation_time
                if hasattr(video, 'tags') and hasattr(video.tags, '_DictProxy__dict'):
                    tags_dict = video.tags._DictProxy__dict
                    _LOGGER.debug(f"[VIDEO] Available tags: {list(tags_dict.keys())}")
                
                # Method 1b: Check if mutagen exposes creation_time through any attribute
                for attr_name in dir(video):
                    if 'creat' in attr_name.lower() or 'time' in attr_name.lower():
                        try:
                            attr_val = getattr(video, attr_name)
                            if attr_val and isinstance(attr_val, (int, float)) and attr_val > 0:
                                _LOGGER.debug(f"[VIDEO] Found time attribute: {attr_name} = {attr_val}")
                        except:
                            pass
                            
            except Exception as e:
                _LOGGER.debug(f"[VIDEO] Failed to inspect MP4 atoms: {e}")
            
            # Method 2: Try QuickTime metadata tags (for files created by Apple/Android devices)
            if not creation_timestamp and video:
                qt_tags = [
                    'com.apple.quicktime.creationdate',
                    '©day',  # Copyright date
                    'date',  # Generic date tag
                ]
                _LOGGER.debug(f"[VIDEO] Available video tags: {list(video.keys())}")
                
                for tag in qt_tags:
                    if tag in video and video[tag]:
                        creation_date_str = str(video[tag][0])
                        _LOGGER.info(f"[VIDEO] Found {tag}: {creation_date_str}")
                        parsed_date = VideoMetadataParser._parse_datetime(creation_date_str)
                        if parsed_date:
                            try:
                                dt = datetime.strptime(parsed_date, '%Y-%m-%d %H:%M:%S')
                                creation_timestamp = int(dt.timestamp())
                                _LOGGER.info(f"[VIDEO] Extracted date from {tag}: {datetime.fromtimestamp(creation_timestamp)}")
                                break
                            except ValueError:
                                pass
            
            # Method 3: Fallback to filename date extraction (YYYYMMDD_HHMMSS pattern)
            if not creation_timestamp:
                import re
                filename = path.stem  # Filename without extension
                _LOGGER.debug(f"[VIDEO] Attempting filename date extraction from: {filename}")
                
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
                            
                            creation_timestamp = int(dt.timestamp())
                            _LOGGER.info(f"[VIDEO] Extracted date from filename: {datetime.fromtimestamp(creation_timestamp)}")
                            break
                        except ValueError as e:
                            _LOGGER.debug(f"[VIDEO] Failed to parse date from pattern {pattern}: {e}")
                            continue
            
            if creation_timestamp:
                result['date_taken'] = creation_timestamp
            else:
                # Final fallback: Use filesystem created_time (earliest of ctime/mtime)
                # This ensures videos always have a date_taken for proper sorting
                import os
                try:
                    stat = os.stat(file_path)
                    # Use the earlier of creation time or modification time
                    # This gives us the "oldest" timestamp which is likely closer to actual creation
                    fs_timestamp = min(stat.st_ctime, stat.st_mtime)
                    result['date_taken'] = int(fs_timestamp)
                    _LOGGER.warning(f"[VIDEO] No date in metadata or filename for {path.name} - using filesystem date: {datetime.fromtimestamp(fs_timestamp)}")
                except Exception as e:
                    _LOGGER.error(f"[VIDEO] Failed to get filesystem dates for {path.name}: {e}")
                    # Even if filesystem check fails, don't give up - return partial result
                    pass
            
            # Extract GPS coordinates from XMP if available
            # MP4 files can store GPS in com.apple.quicktime.location.ISO6709 or XMP
            if video and 'com.apple.quicktime.location.ISO6709' in video:
                iso6709 = video['com.apple.quicktime.location.ISO6709'][0]
                _LOGGER.info(f"[VIDEO] Found GPS (ISO6709): {iso6709}")
                coords = VideoMetadataParser._parse_iso6709(iso6709)
                if coords:
                    result['latitude'] = coords[0]
                    result['longitude'] = coords[1]
                    result['has_coordinates'] = True
                    _LOGGER.info(f"[VIDEO] GPS coordinates: {coords[0]}, {coords[1]}")
            
            # Extract rating
            # iTunes-style rating is stored in 'rate' or '----:com.apple.iTunes:rating'
            rating = None
            
            if video:
                # Try iTunes rating first (0-5 stars * 20 = 0-100)
                if 'rate' in video:
                    rate_value = video['rate'][0] if video['rate'] else None
                    if rate_value:
                        # Convert 0-100 to 0-5 stars
                        rating = int(rate_value / 20)
                        _LOGGER.info(f"[VIDEO] Found rating (rate): {rating} stars")
            
                # Try custom iTunes rating tag
                if rating is None and '----:com.apple.iTunes:rating' in video:
                    rating_bytes = video['----:com.apple.iTunes:rating'][0]
                    try:
                        rating = int(rating_bytes.decode('utf-8'))
                        _LOGGER.info(f"[VIDEO] Found rating (iTunes): {rating} stars")
                    except (ValueError, UnicodeDecodeError):
                        pass
            
            if rating is not None and 0 <= rating <= 5:
                result['rating'] = rating
            
            _LOGGER.info(f"[VIDEO] Extraction complete - found {len(result)} metadata fields: {list(result.keys())}")
            return result if result else None
            
        except Exception as e:
            _LOGGER.error(f"[VIDEO] Failed to extract video metadata from {file_path}: {e}", exc_info=True)
            return None
    
    @staticmethod
    def _parse_datetime(date_str: str) -> Optional[str]:
        """Parse datetime from various video metadata formats.
        
        Args:
            date_str: Date string from video metadata
            
        Returns:
            ISO format datetime string, or None if parsing fails
        """
        if not date_str:
            return None
            
        # Try common video metadata date formats
        date_formats = [
            '%Y-%m-%dT%H:%M:%SZ',      # ISO 8601 with Z
            '%Y-%m-%dT%H:%M:%S',       # ISO 8601 without timezone
            '%Y-%m-%d %H:%M:%S',       # Standard datetime
            '%Y-%m-%d',                # Date only
        ]
        
        for fmt in date_formats:
            try:
                dt = datetime.strptime(date_str, fmt)
                return dt.strftime('%Y-%m-%d %H:%M:%S')
            except ValueError:
                continue
                
        # If no format matches, return original string
        return date_str
    
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
