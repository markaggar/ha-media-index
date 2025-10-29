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
                return None
                
            video = MP4(file_path)
            if not video:
                return None
                
            result: Dict[str, Any] = {}
            
            # Extract creation date
            # Try multiple fields where creation date might be stored
            creation_date = None
            if '©day' in video:  # Copyright Date (often used for creation date)
                creation_date = video['©day'][0] if video['©day'] else None
            
            if creation_date:
                result['date_taken'] = VideoMetadataParser._parse_datetime(creation_date)
            
            # Extract GPS coordinates from XMP if available
            # MP4 files can store GPS in com.apple.quicktime.location.ISO6709 or XMP
            if 'com.apple.quicktime.location.ISO6709' in video:
                iso6709 = video['com.apple.quicktime.location.ISO6709'][0]
                coords = VideoMetadataParser._parse_iso6709(iso6709)
                if coords:
                    result['latitude'] = coords[0]
                    result['longitude'] = coords[1]
                    result['has_coordinates'] = True
            
            # Extract rating
            # iTunes-style rating is stored in 'rate' or '----:com.apple.iTunes:rating'
            rating = None
            
            # Try iTunes rating first (0-5 stars * 20 = 0-100)
            if 'rate' in video:
                rate_value = video['rate'][0] if video['rate'] else None
                if rate_value:
                    # Convert 0-100 to 0-5 stars
                    rating = int(rate_value / 20)
            
            # Try custom iTunes rating tag
            if rating is None and '----:com.apple.iTunes:rating' in video:
                rating_bytes = video['----:com.apple.iTunes:rating'][0]
                try:
                    rating = int(rating_bytes.decode('utf-8'))
                except (ValueError, UnicodeDecodeError):
                    pass
            
            if rating is not None and 0 <= rating <= 5:
                result['rating'] = rating
            
            return result if result else None
            
        except Exception as e:
            _LOGGER.debug(f"Failed to extract video metadata from {file_path}: {e}")
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
        
        MP4 Rating Storage for Windows Compatibility:
        - Windows Explorer reads Microsoft-specific tags, NOT standard iTunes tags
        - We need to write BOTH for maximum compatibility:
          1. Microsoft:Rating for Windows Properties display
          2. iTunes rating for macOS/iTunes compatibility
        
        Since mutagen doesn't support Microsoft tags directly, we use exiftool
        which is the standard for cross-platform metadata writing.
        
        Args:
            file_path: Path to the video file
            rating: Rating value (0-5 stars)
            
        Returns:
            True if rating was written successfully, False otherwise
        """
        import subprocess
        
        try:
            from pathlib import Path
            
            path = Path(file_path)
            if path.suffix.lower() not in {'.mp4', '.m4v', '.mov'}:
                return False
            
            # Check if exiftool is available
            try:
                subprocess.run(['exiftool', '-ver'], capture_output=True, check=True)
            except (subprocess.CalledProcessError, FileNotFoundError):
                _LOGGER.error("exiftool not found - required for MP4 metadata writing")
                _LOGGER.info("Install with: apk add exiftool (or apt-get install libimage-exiftool-perl on Debian)")
                return False
            
            # Write both Microsoft Rating (for Windows) and standard Rating tag
            # Microsoft Rating: 0-5 scale (what Windows Explorer displays)
            # Standard Rating: Also 0-5 for compatibility
            cmd = [
                'exiftool',
                f'-Microsoft:Rating={rating}',  # Windows-compatible tag
                f'-Rating={rating}',             # Standard rating tag
                '-overwrite_original',           # Don't create backup files
                str(file_path)
            ]
            
            _LOGGER.debug(f"Writing rating {rating} to {file_path} using exiftool")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            
            if result.returncode == 0:
                _LOGGER.info(f"Successfully wrote rating {rating} to {file_path} (Microsoft:Rating for Windows)")
                return True
            else:
                _LOGGER.error(f"exiftool failed for {file_path}: {result.stderr}")
                return False
                
        except subprocess.TimeoutExpired:
            _LOGGER.error(f"exiftool timeout writing rating to {file_path}")
            return False
        except Exception as e:
            _LOGGER.error(f"Failed to write rating for {file_path}: {e}")
            return False
