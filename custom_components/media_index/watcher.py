"""File system watcher for media files."""
import logging
import os
from pathlib import Path
from typing import Optional

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileSystemEvent

from homeassistant.core import HomeAssistant

from .cache_manager import CacheManager
from .scanner import MediaScanner, IMAGE_EXTENSIONS, VIDEO_EXTENSIONS

_LOGGER = logging.getLogger(__name__)


class MediaFileEventHandler(FileSystemEventHandler):
    """Handler for media file system events."""
    
    def __init__(self, scanner: MediaScanner, cache: CacheManager, hass: HomeAssistant):
        """Initialize the event handler."""
        super().__init__()
        self.scanner = scanner
        self.cache = cache
        self.hass = hass
    
    def _is_media_file(self, file_path: str) -> bool:
        """Check if a file is a media file."""
        ext = Path(file_path).suffix.lower()
        return ext in IMAGE_EXTENSIONS or ext in VIDEO_EXTENSIONS
    
    def on_created(self, event: FileSystemEvent):
        """Handle file creation events."""
        if event.is_directory:
            return
        
        if not self._is_media_file(event.src_path):
            return
        
        _LOGGER.debug("New media file detected: %s", event.src_path)
        self.hass.loop.call_soon_threadsafe(
            self.hass.async_create_task, self._handle_new_file(event.src_path)
        )
    
    def on_modified(self, event: FileSystemEvent):
        """Handle file modification events."""
        if event.is_directory:
            return
        
        if not self._is_media_file(event.src_path):
            return
        
        _LOGGER.debug("Media file modified: %s", event.src_path)
        self.hass.loop.call_soon_threadsafe(
            self.hass.async_create_task, self._handle_modified_file(event.src_path)
        )
    
    def on_deleted(self, event: FileSystemEvent):
        """Handle file deletion events."""
        if event.is_directory:
            return
        
        if not self._is_media_file(event.src_path):
            return
        
        _LOGGER.debug("Media file deleted: %s", event.src_path)
        # Use call_soon_threadsafe to schedule task from watchdog thread
        self.hass.loop.call_soon_threadsafe(
            self.hass.async_create_task, self._handle_deleted_file(event.src_path)
        )
    
    def on_moved(self, event: FileSystemEvent):
        """Handle file move/rename events."""
        if event.is_directory:
            return
        
        # Check if either source or dest is a media file
        src_is_media = self._is_media_file(event.src_path)
        dest_is_media = self._is_media_file(event.dest_path)
        
        if not src_is_media and not dest_is_media:
            return
        
        _LOGGER.debug("Media file moved: %s -> %s", event.src_path, event.dest_path)
        
        # Remove old path and add new path (use thread-safe scheduling)
        if src_is_media:
            self.hass.loop.call_soon_threadsafe(
                self.hass.async_create_task, self._handle_deleted_file(event.src_path)
            )
        if dest_is_media:
            self.hass.loop.call_soon_threadsafe(
                self.hass.async_create_task, self._handle_new_file(event.dest_path)
            )
    
    async def _handle_new_file(self, file_path: str):
        """Handle new file addition."""
        try:
            metadata = await self.hass.async_add_executor_job(
                self.scanner._get_file_metadata, file_path
            )
            if metadata:
                await self.cache.add_file(metadata)
                _LOGGER.info("Added new file to cache: %s", file_path)
        except Exception as err:
            _LOGGER.error("Failed to add new file %s: %s", file_path, err)
    
    async def _handle_modified_file(self, file_path: str):
        """Handle file modification."""
        try:
            metadata = await self.hass.async_add_executor_job(
                self.scanner._get_file_metadata, file_path
            )
            if metadata:
                await self.cache.add_file(metadata)
                _LOGGER.info("Updated file in cache: %s", file_path)
        except Exception as err:
            _LOGGER.error("Failed to update file %s: %s", file_path, err)
    
    async def _handle_deleted_file(self, file_path: str):
        """Handle file deletion."""
        try:
            await self.cache.remove_file(file_path)
            _LOGGER.info("Removed file from cache: %s", file_path)
        except Exception as err:
            _LOGGER.error("Failed to remove file %s: %s", file_path, err)


class MediaWatcher:
    """File system watcher for media folders."""
    
    def __init__(self, scanner: MediaScanner, cache: CacheManager, hass: HomeAssistant):
        """Initialize the watcher."""
        self.scanner = scanner
        self.cache = cache
        self.hass = hass
        self.observer: Optional[Observer] = None
        self.event_handler = MediaFileEventHandler(scanner, cache, hass)
        self._watched_paths = []
        _LOGGER.info("MediaWatcher initialized")
    
    def start_watching(self, base_folder: str, watched_folders: Optional[list] = None):
        """Start watching media folders for changes.
        
        Args:
            base_folder: Base media folder path
            watched_folders: Optional list of subfolders to watch (empty = watch all)
        """
        if self.observer is not None:
            _LOGGER.warning("Watcher already running")
            return
        
        try:
            self.observer = Observer()
            
            # Determine paths to watch
            watch_paths = []
            if watched_folders:
                for folder in watched_folders:
                    folder_path = os.path.join(base_folder, folder)
                    if os.path.exists(folder_path):
                        watch_paths.append(folder_path)
                    else:
                        _LOGGER.warning("Watched folder not found: %s", folder_path)
            else:
                # Watch entire base folder
                watch_paths = [base_folder]
            
            # Schedule observers for each path
            for path in watch_paths:
                self.observer.schedule(
                    self.event_handler,
                    path,
                    recursive=True
                )
                self._watched_paths.append(path)
                _LOGGER.info("Watching for changes: %s", path)
            
            # Start observer
            self.observer.start()
            _LOGGER.info("File system watcher started")
        
        except Exception as err:
            _LOGGER.error("Failed to start watcher: %s", err)
            self.observer = None
    
    def stop_watching(self):
        """Stop watching media folders."""
        if self.observer is None:
            return
        
        try:
            self.observer.stop()
            self.observer.join(timeout=5)
            self.observer = None
            self._watched_paths = []
            _LOGGER.info("File system watcher stopped")
        except Exception as err:
            _LOGGER.error("Error stopping watcher: %s", err)
    
    @property
    def is_watching(self) -> bool:
        """Return whether the watcher is active."""
        return self.observer is not None and self.observer.is_alive()
