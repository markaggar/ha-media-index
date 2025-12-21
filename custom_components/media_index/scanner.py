"""Media file scanner for Home Assistant."""
import asyncio
import os
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from homeassistant.core import HomeAssistant

from .cache_manager import CacheManager
from .exif_parser import ExifParser
from .video_parser import VideoMetadataParser
from .geocoding import GeocodeService

_LOGGER = logging.getLogger(__name__)

# Media file extensions
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.tiff', '.heic'}
VIDEO_EXTENSIONS = {'.mp4', '.mov', '.avi', '.mkv', '.webm', '.m4v', '.mpg', '.mpeg'}

class MediaScanner:
    """Scanner for media files."""
    
    def __init__(
        self, 
        cache_manager: CacheManager, 
        hass: HomeAssistant = None,
        geocode_service: Optional[GeocodeService] = None,
        enable_geocoding: bool = False
    ):
        """Initialize the scanner."""
        self.cache = cache_manager
        self.hass = hass
        self.geocode_service = geocode_service
        self.enable_geocoding = enable_geocoding
        self._is_scanning = False
        self._scan_error_count = 0  # Track errors to prevent log spam
        _LOGGER.info("MediaScanner initialized (geocoding: %s)", enable_geocoding)
    
    @property
    def is_scanning(self) -> bool:
        """Return whether a scan is currently in progress."""
        return self._is_scanning
    
    def _is_media_file(self, file_path: str) -> bool:
        """Check if a file is a media file based on extension."""
        ext = Path(file_path).suffix.lower()
        return ext in IMAGE_EXTENSIONS or ext in VIDEO_EXTENSIONS
    
    def _get_file_type(self, file_path: str) -> Optional[str]:
        """Determine file type (image or video)."""
        ext = Path(file_path).suffix.lower()
        if ext in IMAGE_EXTENSIONS:
            return "image"
        elif ext in VIDEO_EXTENSIONS:
            return "video"
        return None
    
    def _get_file_metadata(self, file_path: str) -> Optional[dict]:
        """Extract metadata from a media file."""
        try:
            if not os.path.exists(file_path):
                return None
            
            stat = os.stat(file_path)
            path_obj = Path(file_path)
            
            # On Linux/Unix, st_ctime is inode change time, NOT creation time
            # On Windows, st_ctime is creation time
            # Use st_birthtime if available (macOS, some BSD), else fall back to st_ctime
            created_time = None
            if hasattr(stat, 'st_birthtime'):
                created_time = datetime.fromtimestamp(stat.st_birthtime).isoformat()
            else:
                # Fall back to st_ctime (Windows: creation, Linux: change time)
                created_time = datetime.fromtimestamp(stat.st_ctime).isoformat()
            
            return {
                "path": file_path,
                "filename": path_obj.name,
                "folder": str(path_obj.parent),
                "file_type": self._get_file_type(file_path),
                "file_size": stat.st_size,
                "created_time": created_time,
                "modified_time": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                "accessed_time": datetime.fromtimestamp(stat.st_atime).isoformat(),
            }
        except Exception as err:
            _LOGGER.warning("Failed to get metadata for %s: %s", file_path, err)
            return None
    
    def _walk_directory(self, scan_path: str, max_depth: Optional[int] = None) -> list:
        """Walk directory tree and collect media files (blocking call for executor)."""
        media_files = []
        
        try:
            for root, dirs, files in os.walk(scan_path):
                # Skip special folders (where deleted/edit files are moved)
                dirs[:] = [d for d in dirs if d not in ('_Junk', '_Edit')]
                
                # Check depth limit
                if max_depth is not None:
                    depth = root[len(scan_path):].count(os.sep)
                    if depth > max_depth:
                        continue
                
                # Process files in this directory
                for filename in files:
                    file_path = os.path.join(root, filename)
                    
                    # Skip non-media files
                    if not self._is_media_file(file_path):
                        continue
                    
                    # Get file metadata
                    metadata = self._get_file_metadata(file_path)
                    if metadata:
                        media_files.append(metadata)
        
        except Exception as err:
            _LOGGER.error("Error walking directory %s: %s", scan_path, err)
        
        return media_files
    
    async def scan_folder(
        self,
        base_folder: str,
        watched_folders: Optional[list] = None,
        max_depth: Optional[int] = None,
    ) -> int:
        """Scan a folder for media files and update cache.
        
        Args:
            base_folder: Base media folder path
            watched_folders: Optional list of subfolders to watch (empty = watch all)
            max_depth: Maximum depth to scan (None = unlimited)
        
        Returns:
            Number of files added to cache
        """
        if self._is_scanning:
            _LOGGER.warning("Scan already in progress")
            return 0
        
        self._is_scanning = True
        files_added = 0
        
        try:
            _LOGGER.info("Starting full scan of %s", base_folder)
            
            # Record scan start
            scan_id = await self.cache.record_scan(base_folder, "full")
            
            # Reset error counter for this scan
            self._scan_error_count = 0
            
            # Full scan always scans the entire base folder
            # Watched folders are for file system monitoring only, not for limiting full scans
            scan_paths = [base_folder]
            
            _LOGGER.info("Full scan will cover entire base folder: %s (watched folders are for monitoring only)", base_folder)
            
            # Scan each path (run blocking I/O in executor)
            for scan_path in scan_paths:
                if not os.path.exists(scan_path):
                    _LOGGER.warning("Path does not exist: %s", scan_path)
                    continue
                
                _LOGGER.info("Scanning: %s", scan_path)
                
                # Run blocking directory walk in executor
                if self.hass:
                    media_files = await self.hass.async_add_executor_job(
                        self._walk_directory, scan_path, max_depth
                    )
                else:
                    # Fallback for testing without hass
                    media_files = self._walk_directory(scan_path, max_depth)
                
                # Add files to cache
                for metadata in media_files:
                    try:
                        # Extract EXIF first for images to get width/height/orientation
                        exif_data = None
                        if metadata['file_type'] == 'image':
                            if self.hass:
                                exif_data = await self.hass.async_add_executor_job(
                                    ExifParser.extract_exif, metadata['path']
                                )
                            else:
                                exif_data = ExifParser.extract_exif(metadata['path'])
                            
                            # Add image dimensions to metadata for media_files table
                            if exif_data:
                                metadata['width'] = exif_data.get('width')
                                metadata['height'] = exif_data.get('height')
                                metadata['orientation'] = exif_data.get('orientation')
                        elif metadata['file_type'] == 'video':
                            # Extract video metadata BEFORE adding file to get dimensions/duration
                            if self.hass:
                                exif_data = await self.hass.async_add_executor_job(
                                    VideoMetadataParser.extract_metadata, metadata['path']
                                )
                            else:
                                exif_data = VideoMetadataParser.extract_metadata(metadata['path'])
                            
                            # Add video dimensions/duration to metadata for media_files table
                            if exif_data:
                                metadata['width'] = exif_data.get('width')
                                metadata['height'] = exif_data.get('height')
                                metadata['duration'] = exif_data.get('duration')
                        
                        file_id = await self.cache.add_file(metadata)
                        files_added += 1
                        
                        # Store EXIF data in exif_data table
                        if exif_data and file_id > 0:
                            await self.cache.add_exif_data(file_id, exif_data)
                            # Debug logging removed to prevent excessive logs
                            
                            # Set is_favorited based on XMP:Rating (5 stars = favorite, < 5 = not favorite)
                            rating = exif_data.get('rating') or 0
                            is_favorite = rating >= 5
                            await self.cache.update_favorite(metadata['path'], is_favorite)
                            # Favorite marking logged in summary only
                        
                        # Geocode GPS coordinates if available, enabled, and not already geocoded
                        # Only run if we have exif_data (from images or videos)
                        if exif_data and file_id > 0:
                            has_coords = exif_data.get('latitude') and exif_data.get('longitude')
                            
                            # Check if this file already has geocoded location
                            already_geocoded = await self.cache.has_geocoded_location(file_id)
                            
                            # Debug logging removed to prevent excessive logs during scans
                            
                            if (self.enable_geocoding and 
                                self.geocode_service and 
                                has_coords and 
                                not already_geocoded):
                                
                                lat = exif_data['latitude']
                                lon = exif_data['longitude']
                                
                                # Check geocode cache first
                                cached_location = await self.cache.get_geocode_cache(lat, lon)
                                
                                if cached_location:
                                    # Use cached location
                                    await self.cache.update_exif_location(file_id, cached_location)
                                else:
                                    # Fetch from geocoding service
                                    location_data = await self.geocode_service.reverse_geocode(lat, lon)
                                    
                                    if location_data:
                                        # Cache the result
                                        await self.cache.add_geocode_cache(lat, lon, location_data)
                                        # Update EXIF record
                                        await self.cache.update_exif_location(file_id, location_data)
                        
                        # Yield control back to event loop every 10 files to prevent blocking startup
                        if files_added % 10 == 0:
                            await asyncio.sleep(0)
                        
                        if files_added % 100 == 0:
                            _LOGGER.info("Scan progress: indexed %d files so far...", files_added)
                    
                    except Exception as err:
                        # Check if database connection was closed (during integration unload/reload)
                        if "no active connection" in str(err):
                            _LOGGER.warning(
                                "Database connection closed during scan (integration unloading/reloading). "
                                "Aborting scan at file %d. Scan will resume on next startup.",
                                files_added
                            )
                            # Database is closed - abort scan immediately to prevent log spam
                            if scan_id:
                                # Try to update scan status, but it will likely fail
                                try:
                                    await self.cache.update_scan(scan_id, files_added, "aborted")
                                except Exception:
                                    pass  # Expected to fail if DB is closed
                            return files_added
                        
                        # For other errors, log but continue (with rate limiting)
                        self._scan_error_count += 1
                        if self._scan_error_count <= 10:
                            _LOGGER.error("Failed to add file to cache: %s - %s", metadata.get("path"), err)
                        elif self._scan_error_count == 11:
                            _LOGGER.error(
                                "Too many scan errors (%d so far). Suppressing further error logs for this scan.",
                                self._scan_error_count
                            )
            
            # Update scan record
            await self.cache.update_scan(scan_id, files_added, "completed")
            
            # Flush any pending geocoding stats
            await self.cache._flush_geocode_stats()
            
            _LOGGER.info("Scan complete. Added %d files to cache", files_added)
            return files_added
        
        except Exception as err:
            # Check if database connection was closed
            if "no active connection" in str(err):
                _LOGGER.warning(
                    "Scan aborted: Database connection closed (integration unloading/reloading). "
                    "This is normal during HA restart or integration reload."
                )
            else:
                _LOGGER.error("Scan failed: %s", err)
            
            # Try to update scan status, but may fail if database is closed
            if scan_id:
                try:
                    await self.cache.update_scan(scan_id, files_added, "failed")
                except Exception as update_err:
                    _LOGGER.debug("Could not update scan status (expected if database closed): %s", update_err)
            
            return files_added
        
        finally:
            self._is_scanning = False
    
    async def scan_file(self, file_path: str) -> bool:
        """Scan and index a single file.
        
        Args:
            file_path: Full path to the file to scan
            
        Returns:
            True if successfully indexed, False otherwise
        """
        try:
            if not os.path.exists(file_path):
                _LOGGER.warning("File does not exist: %s", file_path)
                return False
            
            if not self._is_media_file(file_path):
                _LOGGER.debug("Not a media file: %s", file_path)
                return False
            
            # Get basic file metadata
            metadata = self._get_file_metadata(file_path)
            if not metadata:
                return False
            
            # Extract EXIF first for images to get width/height/orientation
            exif_data = None
            if metadata['file_type'] == 'image':
                if self.hass:
                    exif_data = await self.hass.async_add_executor_job(
                        ExifParser.extract_exif, file_path
                    )
                else:
                    exif_data = ExifParser.extract_exif(file_path)
                
                # Add image dimensions to metadata for media_files table
                if exif_data:
                    metadata['width'] = exif_data.get('width')
                    metadata['height'] = exif_data.get('height')
                    metadata['orientation'] = exif_data.get('orientation')
            elif metadata['file_type'] == 'video':
                if self.hass:
                    exif_data = await self.hass.async_add_executor_job(
                        VideoMetadataParser.extract_metadata, file_path
                    )
                else:
                    exif_data = VideoMetadataParser.extract_metadata(file_path)
            
            # Add to database
            file_id = await self.cache.add_file(metadata)
            if file_id <= 0:
                _LOGGER.warning("Failed to add file to database: %s", file_path)
                return False
            
            # Store EXIF/video metadata in exif_data table
            if exif_data:
                await self.cache.add_exif_data(file_id, exif_data)
                
                # Check for favorite rating
                rating = exif_data.get('rating') or 0
                if rating >= 5:
                    await self.cache.update_favorite(file_path, True)
                
                # Geocode if enabled and has coordinates
                if self.enable_geocoding and self.geocode_service:
                    lat = exif_data.get('latitude')
                    lon = exif_data.get('longitude')
                    
                    if lat and lon:
                        # Check cache first
                        location_data = await self.cache.get_geocode_cache(lat, lon)
                        if not location_data:
                            # Call geocoding API
                            location_data = await self.geocode_service.reverse_geocode(lat, lon)
                            if location_data:
                                await self.cache.add_geocode_cache(lat, lon, location_data)
                        
                        if location_data:
                            await self.cache.update_exif_location(file_id, location_data)
            
            # Success - log removed to prevent excessive logging
            return True
            
        except Exception as e:
            _LOGGER.error("Error scanning file %s: %s", file_path, e)
            return False

