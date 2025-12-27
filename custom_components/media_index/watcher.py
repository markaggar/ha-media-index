"""File system watcher for media files."""
import asyncio
import logging
import os
from pathlib import Path
from typing import Optional, Dict, Set
from datetime import datetime

from watchdog.observers import Observer
from watchdog.observers.polling import PollingObserver
from watchdog.events import FileSystemEventHandler, FileSystemEvent

from homeassistant.core import HomeAssistant

from .cache_manager import CacheManager
from .scanner import MediaScanner, IMAGE_EXTENSIONS, VIDEO_EXTENSIONS

_LOGGER = logging.getLogger(__name__)

# Throttling configuration
BATCH_DELAY = 2.0  # seconds - wait this long to collect events before processing
MAX_BATCH_SIZE = 50  # files - process this many files at once max
RATE_LIMIT_DELAY = 0.5  # seconds - delay between processing batches


class MediaFileEventHandler(FileSystemEventHandler):
    """Handler for media file system events with throttling."""
    
    def __init__(self, scanner: MediaScanner, cache: CacheManager, hass: HomeAssistant):
        """Initialize the event handler."""
        super().__init__()
        self.scanner = scanner
        self.cache = cache
        self.hass = hass
        
        # Event queues for batching
        self._pending_new: Dict[str, datetime] = {}  # path -> timestamp
        self._pending_modified: Dict[str, datetime] = {}
        self._pending_deleted: Set[str] = set()
        
        # Processing control
        self._processor_task = None
        self._is_processing = False
    
    def _is_media_file(self, file_path: str) -> bool:
        """Check if a file is a media file."""
        ext = Path(file_path).suffix.lower()
        return ext in IMAGE_EXTENSIONS or ext in VIDEO_EXTENSIONS
    
    def _start_processor_if_needed(self):
        """Start the batch processor task if not already running."""
        if self._processor_task is None or self._processor_task.done():
            self._processor_task = self.hass.async_create_task(
                self._process_event_batches()
            )
    
    def on_created(self, event: FileSystemEvent):
        """Handle file creation events (batched)."""
        if event.is_directory:
            return
        
        if not self._is_media_file(event.src_path):
            return
        
        # Add to batch queue (thread-safe) and remove from other queues for deduplication
        def add_to_new_queue():
            self._pending_new[event.src_path] = datetime.now()
            # Remove from other queues to avoid duplicate processing
            self._pending_modified.pop(event.src_path, None)
            self._pending_deleted.discard(event.src_path)
            self._start_processor_if_needed()
        
        self.hass.loop.call_soon_threadsafe(add_to_new_queue)
    
    def on_modified(self, event: FileSystemEvent):
        """Handle file modification events (batched)."""
        if event.is_directory:
            return
        
        if not self._is_media_file(event.src_path):
            return
        
        # Add to batch queue (thread-safe) - only if not already in new queue
        def add_to_modified_queue():
            # Don't add to modified if already pending as new file
            if event.src_path not in self._pending_new:
                self._pending_modified[event.src_path] = datetime.now()
                self._pending_deleted.discard(event.src_path)
            self._start_processor_if_needed()
        
        self.hass.loop.call_soon_threadsafe(add_to_modified_queue)
    
    def on_deleted(self, event: FileSystemEvent):
        """Handle file deletion events (batched)."""
        if event.is_directory:
            return
        
        if not self._is_media_file(event.src_path):
            return
        
        # Add to batch queue (thread-safe) and remove from other queues for deduplication
        def add_to_deleted_queue():
            self._pending_deleted.add(event.src_path)
            # Remove from other queues to avoid processing dead file
            self._pending_new.pop(event.src_path, None)
            self._pending_modified.pop(event.src_path, None)
            self._start_processor_if_needed()
        
        self.hass.loop.call_soon_threadsafe(add_to_deleted_queue)
    
    def on_moved(self, event: FileSystemEvent):
        """Handle file move/rename events (batched)."""
        if event.is_directory:
            return
        
        # Check if either source or dest is a media file
        src_is_media = self._is_media_file(event.src_path)
        dest_is_media = self._is_media_file(event.dest_path)
        
        if not src_is_media and not dest_is_media:
            return
        
        # Treat move as delete + create (thread-safe)
        def handle_move():
            if src_is_media:
                self._pending_deleted.add(event.src_path)
                self._pending_new.pop(event.src_path, None)
                self._pending_modified.pop(event.src_path, None)
            if dest_is_media:
                self._pending_new[event.dest_path] = datetime.now()
                self._pending_modified.pop(event.dest_path, None)
                self._pending_deleted.discard(event.dest_path)
            self._start_processor_if_needed()
        
        self.hass.loop.call_soon_threadsafe(handle_move)
    
    async def _process_event_batches(self):
        """Process batched events with throttling to prevent resource exhaustion."""
        try:
            while True:
                try:
                    # Wait for batch delay to collect events
                    await asyncio.sleep(BATCH_DELAY)
                    
                    # Check if we have any pending events
                    has_events = (
                        len(self._pending_new) > 0 or 
                        len(self._pending_modified) > 0 or 
                        len(self._pending_deleted) > 0
                    )
                    
                    if not has_events:
                        # No events for a while, exit processor
                        break
                    
                    # Mark as processing
                    self._is_processing = True
                    
                    # Process deletions first (fast, no EXIF extraction)
                    if self._pending_deleted:
                        deleted_batch = list(self._pending_deleted)[:MAX_BATCH_SIZE]
                        self._pending_deleted -= set(deleted_batch)
                        
                        _LOGGER.info("Processing %d deleted files", len(deleted_batch))
                        for file_path in deleted_batch:
                            await self._handle_deleted_file(file_path)
                    
                    # Process new files (expensive: EXIF, geocoding, etc.)
                    if self._pending_new:
                        # Get oldest files first
                        sorted_new = sorted(self._pending_new.items(), key=lambda x: x[1])
                        new_batch = sorted_new[:MAX_BATCH_SIZE]
                        
                        for file_path, _ in new_batch:
                            del self._pending_new[file_path]
                        
                        _LOGGER.info("Processing %d new files (batched)", len(new_batch))
                        for file_path, _ in new_batch:
                            await self._handle_new_file(file_path)
                    
                    # Process modified files
                    if self._pending_modified:
                        sorted_mod = sorted(self._pending_modified.items(), key=lambda x: x[1])
                        mod_batch = sorted_mod[:MAX_BATCH_SIZE]
                        
                        for file_path, _ in mod_batch:
                            del self._pending_modified[file_path]
                        
                        _LOGGER.info("Processing %d modified files (batched)", len(mod_batch))
                        for file_path, _ in mod_batch:
                            await self._handle_modified_file(file_path)
                    
                    # Always yield to event loop after each iteration for consistent rate limiting
                    await asyncio.sleep(RATE_LIMIT_DELAY)
                    
                except Exception as err:
                    _LOGGER.error("Error in batch processor: %s", err)
                    await asyncio.sleep(RATE_LIMIT_DELAY)
        finally:
            # Ensure flag is reset even on exception
            self._is_processing = False
            _LOGGER.debug("Batch processor stopped (no pending events)")
    
    async def _handle_new_file(self, file_path: str):
        """Handle new file addition."""
        try:
            # Use scanner's scan_file which properly extracts and stores EXIF
            success = await self.scanner.scan_file(file_path)
            if success:
                _LOGGER.info("Added new file to cache: %s", file_path)
            else:
                _LOGGER.warning("Failed to add new file: %s", file_path)
        except Exception as err:
            _LOGGER.error("Failed to add new file %s: %s", file_path, err)
    
    async def _handle_modified_file(self, file_path: str):
        """Handle file modification."""
        try:
            # Use scanner's scan_file which properly extracts and updates EXIF
            success = await self.scanner.scan_file(file_path)
            if success:
                _LOGGER.info("Updated file in cache: %s", file_path)
            else:
                _LOGGER.warning("Failed to update file: %s", file_path)
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
    
    async def start_watching(self, base_folder: str, watched_folders: Optional[list] = None):
        """Start watching media folders for changes.
        
        Args:
            base_folder: Base media folder path
            watched_folders: Optional list of subfolders to watch (empty = watch all)
        """
        if self.observer is not None:
            _LOGGER.warning("Watcher already running")
            return
        
        try:
            # Use PollingObserver for network filesystems (inotify doesn't work on CIFS/SMB)
            # Polling is less efficient but works reliably on network shares
            _LOGGER.info("Using PollingObserver for network filesystem compatibility")
            self.observer = PollingObserver()
            
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
            
            # Start observer (wrapped to avoid blocking scandir call)
            await self.hass.async_add_executor_job(self.observer.start)
            _LOGGER.info("File system watcher started")
        
        except Exception as err:
            _LOGGER.error("Failed to start watcher: %s", err)
            self.observer = None
    
    def stop_watching(self):
        """Stop watching media folders and cancel any pending processor tasks."""
        if self.observer is None:
            return
        
        try:
            # Cancel the processor task if running
            if self.event_handler._processor_task and not self.event_handler._processor_task.done():
                self.event_handler._processor_task.cancel()
                _LOGGER.debug("Cancelled batch processor task")
            
            # Clear pending events
            self.event_handler._pending_new.clear()
            self.event_handler._pending_modified.clear()
            self.event_handler._pending_deleted.clear()
            
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
