"""Media Index integration for Home Assistant."""
import logging
import os
from pathlib import Path

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.helpers.typing import ConfigType
import homeassistant.helpers.config_validation as cv

from .const import (
    DOMAIN,
    CONF_BASE_FOLDER,
    CONF_WATCHED_FOLDERS,
    CONF_SCAN_ON_STARTUP,
    CONF_ENABLE_WATCHER,
    CONF_GEOCODE_ENABLED,
    CONF_GEOCODE_NATIVE_LANGUAGE,
    DEFAULT_ENABLE_WATCHER,
    DEFAULT_GEOCODE_ENABLED,
    DEFAULT_GEOCODE_NATIVE_LANGUAGE,
    SERVICE_GET_RANDOM_ITEMS,
    SERVICE_GET_ORDERED_FILES,
    SERVICE_GET_FILE_METADATA,
    SERVICE_GEOCODE_FILE,
    SERVICE_SCAN_FOLDER,
    SERVICE_MARK_FOR_EDIT,
    SERVICE_RESTORE_EDITED_FILES,
)
from .cache_manager import CacheManager
from .scanner import MediaScanner
from .watcher import MediaWatcher
from .exif_parser import ExifParser
from .video_parser import VideoMetadataParser
from .geocoding import GeocodeService

_LOGGER = logging.getLogger(__name__)

# Config entry only - no YAML configuration supported
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS: list[Platform] = [Platform.SENSOR]

# Service schemas (all allow extra fields for target selector support)
SERVICE_GET_RANDOM_ITEMS_SCHEMA = vol.Schema({
    vol.Optional("count", default=10): cv.positive_int,
    vol.Optional("folder"): cv.string,
    vol.Optional("file_type"): vol.In(["image", "video"]),
    vol.Optional("date_from"): cv.string,
    vol.Optional("date_to"): cv.string,
    vol.Optional("priority_new_files", default=False): cv.boolean,
    vol.Optional("new_files_threshold_seconds", default=3600): cv.positive_int,
}, extra=vol.ALLOW_EXTRA)

SERVICE_GET_ORDERED_FILES_SCHEMA = vol.Schema({
    vol.Optional("count", default=50): cv.positive_int,
    vol.Optional("folder"): cv.string,
    vol.Optional("recursive", default=True): cv.boolean,
    vol.Optional("file_type"): vol.In(["image", "video"]),
    vol.Optional("order_by", default="date_taken"): vol.In(["date_taken", "filename", "path", "modified_time"]),
    vol.Optional("order_direction", default="desc"): vol.In(["asc", "desc"]),
}, extra=vol.ALLOW_EXTRA)

SERVICE_GET_FILE_METADATA_SCHEMA = vol.Schema({
    vol.Required("file_path"): cv.string,
}, extra=vol.ALLOW_EXTRA)

SERVICE_GEOCODE_FILE_SCHEMA = vol.Schema({
    vol.Optional("file_id"): cv.positive_int,
    vol.Optional("latitude"): vol.Coerce(float),
    vol.Optional("longitude"): vol.Coerce(float),
}, extra=vol.ALLOW_EXTRA)

SERVICE_SCAN_FOLDER_SCHEMA = vol.Schema({
    vol.Optional("folder_path"): cv.string,
    vol.Optional("force_rescan", default=False): cv.boolean,
}, extra=vol.ALLOW_EXTRA)

SERVICE_MARK_FAVORITE_SCHEMA = vol.Schema({
    vol.Required("file_path"): cv.string,
    vol.Optional("is_favorite", default=True): cv.boolean,
}, extra=vol.ALLOW_EXTRA)

SERVICE_DELETE_MEDIA_SCHEMA = vol.Schema({
    vol.Required("file_path"): cv.string,
}, extra=vol.ALLOW_EXTRA)

SERVICE_MARK_FOR_EDIT_SCHEMA = vol.Schema({
    vol.Required("file_path"): cv.string,
}, extra=vol.ALLOW_EXTRA)

SERVICE_RESTORE_EDITED_FILES_SCHEMA = vol.Schema({
    vol.Optional("folder_filter"): cv.string,  # e.g., "_Edit"
    vol.Optional("file_path"): cv.string,  # Restore specific file
    vol.Optional("entity_id"): cv.entity_ids,  # Target entity (from UI)
}, extra=vol.ALLOW_EXTRA)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up Media Index integration from YAML (not supported)."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Media Index from a config entry."""
    _LOGGER.info("Setting up Media Index integration")

    # Create integration data storage
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {}

    # Initialize cache manager with unique database per instance
    cache_db_path = os.path.join(
        hass.config.path(".storage"), 
        f"media_index_{entry.entry_id}.db"
    )
    cache_manager = CacheManager(cache_db_path)
    
    if not await cache_manager.async_setup():
        _LOGGER.error("Failed to initialize cache manager")
        return False
    
    _LOGGER.info("Cache manager initialized successfully")
    
    # Initialize geocoding service
    config = {**entry.data, **entry.options}
    enable_geocoding = config.get(CONF_GEOCODE_ENABLED, DEFAULT_GEOCODE_ENABLED)
    use_native_language = config.get(CONF_GEOCODE_NATIVE_LANGUAGE, DEFAULT_GEOCODE_NATIVE_LANGUAGE)
    geocode_service = None
    
    if enable_geocoding:
        geocode_service = GeocodeService(hass, use_native_language=use_native_language)
        _LOGGER.info("Geocoding service enabled (native_language=%s)", use_native_language)
    
    # Initialize scanner with geocoding support
    scanner = MediaScanner(
        cache_manager, 
        hass,
        geocode_service=geocode_service,
        enable_geocoding=enable_geocoding
    )
    
    # Initialize watcher
    watcher = MediaWatcher(scanner, cache_manager, hass)
    
    # Store instances
    hass.data[DOMAIN][entry.entry_id]["cache_manager"] = cache_manager
    hass.data[DOMAIN][entry.entry_id]["scanner"] = scanner
    hass.data[DOMAIN][entry.entry_id]["watcher"] = watcher
    hass.data[DOMAIN][entry.entry_id]["geocode_service"] = geocode_service
    hass.data[DOMAIN][entry.entry_id]["config"] = {**entry.data, **entry.options}
    
    # Set up platforms BEFORE starting scan so sensor exists
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    
    # Trigger initial scan if configured
    config = {**entry.data, **entry.options}
    base_folder = config.get(CONF_BASE_FOLDER, "/media")
    watched_folders = config.get(CONF_WATCHED_FOLDERS, [])
    
    if config.get(CONF_SCAN_ON_STARTUP, True):
        _LOGGER.info("Starting initial scan of %s (watched: %s)", base_folder, watched_folders)
        
        # Start scan as background task
        hass.async_create_task(
            scanner.scan_folder(base_folder, watched_folders)
        )
    
    # Start file system watcher if enabled
    if config.get(CONF_ENABLE_WATCHER, DEFAULT_ENABLE_WATCHER):
        _LOGGER.info("Starting file system watcher")
        watcher.start_watching(base_folder, watched_folders)
    
    # Register services (only once, on first entry setup)
    if not hass.services.has_service(DOMAIN, SERVICE_GET_RANDOM_ITEMS):
        _register_services(hass)
    
    # Add entry update listener
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    
    return True


def _get_entry_id_from_call(hass: HomeAssistant, call: ServiceCall) -> str:
    """Get entry_id from service call target or use default.
    
    Supports multiple integration instances by extracting entry_id from target entity.
    
    Args:
        hass: Home Assistant instance
        call: Service call with optional target selector
        
    Returns:
        Entry ID to use for this service call
        
    Raises:
        ValueError: If no integration instance found
    """
    # Check for target in multiple locations (HA passes it differently depending on context)
    entity_id = None
    
    # Method 1: Check call.data['target'] (Developer Tools, automations, REST API)
    if 'target' in call.data:
        target = call.data['target']
        if isinstance(target, dict) and 'entity_id' in target:
            entity_id = target['entity_id']
            if isinstance(entity_id, list):
                entity_id = entity_id[0]  # Use first entity
            _LOGGER.debug("Found target entity in call.data['target']: %s", entity_id)
    
    # Method 2: Check call.data['entity_id'] directly (WebSocket with target selector)
    # HA WebSocket transforms target.entity_id -> call.data['entity_id']
    if not entity_id and 'entity_id' in call.data:
        entity_id = call.data['entity_id']
        if isinstance(entity_id, list):
            entity_id = entity_id[0]  # Use first entity
        _LOGGER.debug("Found entity_id directly in call.data: %s", entity_id)
    
    # Method 3: Check call.context.target (some service call contexts)
    if not entity_id and hasattr(call, 'context') and hasattr(call.context, 'target'):
        target = call.context.target
        if isinstance(target, dict) and 'entity_id' in target:
            entity_id = target['entity_id']
            if isinstance(entity_id, list):
                entity_id = entity_id[0]  # Use first entity
            _LOGGER.debug("Found target entity in call.context: %s", entity_id)
    
    if entity_id:
        # Extract entry_id from entity registry
        from homeassistant.helpers import entity_registry as er
        entity_registry = er.async_get(hass)
        entity_entry = entity_registry.async_get(entity_id)
        
        if entity_entry and entity_entry.config_entry_id:
            _LOGGER.info("Routing to integration instance from entity %s: %s", entity_id, entity_entry.config_entry_id)
            return entity_entry.config_entry_id
        else:
            _LOGGER.warning("Entity %s not found in registry or missing config_entry_id", entity_id)
    
    # Fallback: use first available entry_id (single-instance compatibility)
    if DOMAIN in hass.data and hass.data[DOMAIN]:
        entry_id = next(iter(hass.data[DOMAIN].keys()))
        _LOGGER.info("No target specified, using first entry_id: %s", entry_id)
        return entry_id
    
    raise ValueError("No Media Index integration instance found")


def _register_services(hass: HomeAssistant):
    """Register all Media Index services.
    
    Services use target selector to support multiple instances.
    If no target specified, defaults to first instance (backward compatibility).
    """
    
    # Register services
    async def handle_get_random_items(call):
        """Handle get_random_items service call."""
        entry_id = _get_entry_id_from_call(hass, call)
        cache_manager = hass.data[DOMAIN][entry_id]["cache_manager"]
        
        _LOGGER.debug("get_random_items: entry_id=%s, call.data=%s", entry_id, call.data)
        
        items = await cache_manager.get_random_files(
            count=call.data.get("count", 10),
            folder=call.data.get("folder"),
            file_type=call.data.get("file_type"),
            date_from=call.data.get("date_from"),
            date_to=call.data.get("date_to"),
            priority_new_files=call.data.get("priority_new_files", False),
            new_files_threshold_seconds=call.data.get("new_files_threshold_seconds", 3600),
        )
        
        result = {"items": items}
        _LOGGER.debug("Retrieved %d random items from entry_id %s", len(items), entry_id)
        return result
    
    async def handle_get_ordered_files(call):
        """Handle get_ordered_files service call."""
        entry_id = _get_entry_id_from_call(hass, call)
        cache_manager = hass.data[DOMAIN][entry_id]["cache_manager"]
        
        _LOGGER.debug("get_ordered_files: entry_id=%s, call.data=%s", entry_id, call.data)
        
        items = await cache_manager.get_ordered_files(
            count=call.data.get("count", 50),
            folder=call.data.get("folder"),
            recursive=call.data.get("recursive", True),
            file_type=call.data.get("file_type"),
            order_by=call.data.get("order_by", "date_taken"),
            order_direction=call.data.get("order_direction", "desc"),
        )
        
        result = {"items": items}
        _LOGGER.debug("Retrieved %d ordered items from entry_id %s", len(items), entry_id)
        return result
    
    async def handle_get_file_metadata(call):
        """Handle get_file_metadata service call."""
        entry_id = _get_entry_id_from_call(hass, call)
        cache_manager = hass.data[DOMAIN][entry_id]["cache_manager"]
        file_path = call.data["file_path"]
        
        metadata = await cache_manager.get_file_by_path(file_path)
        
        if metadata:
            _LOGGER.info("Retrieved metadata for: %s", file_path)
            return metadata
        else:
            _LOGGER.warning("File not found in index: %s", file_path)
            return {"error": "File not found"}
    
    async def handle_geocode_file(call):
        """Handle geocode_file service call for progressive geocoding."""
        entry_id = _get_entry_id_from_call(hass, call)
        cache_manager = hass.data[DOMAIN][entry_id]["cache_manager"]
        geocode_service = hass.data[DOMAIN][entry_id].get("geocode_service")
        
        if not geocode_service:
            _LOGGER.error("Geocoding service not enabled")
            return {"error": "Geocoding not enabled"}
        
        file_id = call.data.get("file_id")
        lat = call.data.get("latitude")
        lon = call.data.get("longitude")
        
        # Get coordinates from file_id if not provided
        if file_id and not (lat and lon):
            file_data = await cache_manager.get_file_by_id(file_id)
            if not file_data:
                return {"error": "File not found"}
            
            # Get EXIF data for coordinates
            exif_data = await cache_manager.get_exif_by_file_id(file_id)
            if not exif_data or not exif_data.get("latitude"):
                return {"error": "File has no GPS coordinates"}
            
            lat = exif_data["latitude"]
            lon = exif_data["longitude"]
        
        if not (lat and lon):
            return {"error": "Either file_id or latitude/longitude required"}
        
        _LOGGER.info("Progressive geocoding request for (%s, %s)", lat, lon)
        
        # 1. Check geocode cache first (fast)
        cached_location = await cache_manager.get_geocode_cache(lat, lon)
        if cached_location:
            _LOGGER.info("Cache HIT for (%s, %s): %s", round(lat, 3), round(lon, 3), cached_location.get('location_city'))
            # Update exif_data table with cached result
            if file_id:
                await cache_manager.update_exif_location(file_id, cached_location)
            return cached_location
        
        # 2. Call Nominatim API (slow, rate-limited)
        _LOGGER.info("Cache MISS for (%s, %s) - calling Nominatim API", round(lat, 3), round(lon, 3))
        location_data = await geocode_service.reverse_geocode(lat, lon)
        
        if not location_data:
            return {"error": "Geocoding failed"}
        
        # 3. Cache the result
        await cache_manager.add_geocode_cache(lat, lon, location_data)
        
        # 4. Update exif_data table with new location
        if file_id:
            await cache_manager.update_exif_location(file_id, location_data)
        
        _LOGGER.info(
            "Geocoded (%s, %s) to: %s, %s",
            lat, lon,
            location_data.get('location_city'),
            location_data.get('location_country')
        )
        
        # 5. Return location data to caller
        return location_data
    
    async def handle_mark_favorite(call):
        """Handle mark_favorite service call."""
        entry_id = _get_entry_id_from_call(hass, call)
        cache_manager = hass.data[DOMAIN][entry_id]["cache_manager"]
        file_path = call.data["file_path"]
        is_favorite = call.data.get("is_favorite", True)
        
        _LOGGER.info("Marking file as favorite: %s (favorite=%s)", file_path, is_favorite)
        
        try:
            # Update database
            await cache_manager.update_favorite(file_path, is_favorite)
            
            # Write rating to file metadata
            # Rating 5 = favorite, Rating 0 = unfavorited
            rating = 5 if is_favorite else 0
            
            # Determine file type to use appropriate parser
            file_ext = Path(file_path).suffix.lower()
            if file_ext in {'.jpg', '.jpeg', '.png', '.tiff', '.tif', '.heic'}:
                success = await hass.async_add_executor_job(
                    ExifParser.write_rating, file_path, rating
                )
            elif file_ext in {'.mp4', '.m4v', '.mov'}:
                success = await hass.async_add_executor_job(
                    VideoMetadataParser.write_rating, file_path, rating
                )
            else:
                success = False
                _LOGGER.warning("Unsupported file type for rating: %s", file_ext)
            
            if success:
                _LOGGER.debug("Wrote rating=%d to %s", rating, file_path)
            else:
                _LOGGER.warning("Failed to write rating to %s (database updated)", file_path)
            
            return {
                "file_path": file_path,
                "is_favorite": is_favorite,
                "exif_updated": success,
                "status": "success"
            }
        except Exception as e:
            _LOGGER.error("Error marking file as favorite: %s", e)
            return {
                "file_path": file_path,
                "status": "error",
                "error": str(e)
            }
    
    async def handle_delete_media(call):
        """Handle delete_media service call."""
        import shutil
        
        entry_id = _get_entry_id_from_call(hass, call)
        cache_manager = hass.data[DOMAIN][entry_id]["cache_manager"]
        config = hass.data[DOMAIN][entry_id]["config"]
        
        file_path = call.data["file_path"]
        base_folder = config.get(CONF_BASE_FOLDER, "/media")
        
        _LOGGER.info("Deleting media file: %s", file_path)
        
        try:
            # Create junk folder if it doesn't exist
            junk_folder = Path(base_folder) / "_Junk"
            junk_folder.mkdir(exist_ok=True)
            
            # Get file name and create destination path
            file_name = Path(file_path).name
            dest_path = junk_folder / file_name
            
            # Handle duplicate names by appending number
            counter = 1
            while dest_path.exists():
                stem = Path(file_path).stem
                suffix = Path(file_path).suffix
                dest_path = junk_folder / f"{stem}_{counter}{suffix}"
                counter += 1
            
            # Move file to junk folder
            await hass.async_add_executor_job(
                shutil.move,
                file_path,
                str(dest_path)
            )
            
            # Remove from database
            await cache_manager.delete_file(file_path)
            
            _LOGGER.info("Moved file to junk folder: %s -> %s", file_path, dest_path)
            
            return {
                "file_path": file_path,
                "junk_path": str(dest_path),
                "status": "success"
            }
        except Exception as e:
            _LOGGER.error("Error deleting file: %s", e)
            return {
                "file_path": file_path,
                "status": "error",
                "error": str(e)
            }
    
    async def handle_mark_for_edit(call):
        """Handle mark_for_edit service call."""
        import shutil
        
        entry_id = _get_entry_id_from_call(hass, call)
        cache_manager = hass.data[DOMAIN][entry_id]["cache_manager"]
        config = hass.data[DOMAIN][entry_id]["config"]
        
        file_path = call.data["file_path"]
        base_folder = config.get(CONF_BASE_FOLDER, "/media")
        
        _LOGGER.info("Marking file for editing: %s", file_path)
        
        try:
            # Create edit folder if it doesn't exist
            edit_folder = Path(base_folder) / "_Edit"
            edit_folder.mkdir(exist_ok=True)
            
            # Get file name and create destination path
            file_name = Path(file_path).name
            dest_path = edit_folder / file_name
            
            # If destination already exists, we'll overwrite it
            # (Don't add _1 suffix - just move/overwrite)
            
            # Move file to edit folder
            await hass.async_add_executor_job(
                shutil.move,
                file_path,
                str(dest_path)
            )
            
            # Record the move in move_history table (without _1 suffix)
            await cache_manager.record_file_move(
                original_path=file_path,
                new_path=str(dest_path),
                reason="edit"
            )
            
            # Remove from database (will be re-added on next scan if moved back)
            await cache_manager.delete_file(file_path)
            
            _LOGGER.info("Moved file to edit folder: %s -> %s", file_path, dest_path)
            
            return {
                "file_path": file_path,
                "edit_path": str(dest_path),
                "status": "success"
            }
        except Exception as e:
            _LOGGER.error("Error marking file for edit: %s", e)
            return {
                "file_path": file_path,
                "status": "error",
                "error": str(e)
            }
    
    async def handle_restore_edited_files(call):
        """Handle restore_edited_files service call."""
        import shutil
        import os
        
        entry_id = _get_entry_id_from_call(hass, call)
        cache_manager = hass.data[DOMAIN][entry_id]["cache_manager"]
        scanner = hass.data[DOMAIN][entry_id]["scanner"]
        
        folder_filter = call.data.get("folder_filter", "_Edit")
        specific_file = call.data.get("file_path")
        
        _LOGGER.info("Restoring edited files (filter: %s, specific: %s)", folder_filter, specific_file)
        
        try:
            # Get pending restores from move_history
            pending_moves = await cache_manager.get_pending_restores(folder_filter)
            
            if specific_file:
                # Filter to specific file
                pending_moves = [m for m in pending_moves if m["new_path"] == specific_file]
            
            restored_count = 0
            failed_count = 0
            results = []
            
            for move in pending_moves:
                move_id = move["id"]
                original_path = move["original_path"]
                current_path = move["new_path"]
                
                try:
                    # Check if file still exists at new location
                    if not await hass.async_add_executor_job(os.path.exists, current_path):
                        _LOGGER.warning("File not found at %s, skipping restore", current_path)
                        results.append({
                            "original_path": original_path,
                            "current_path": current_path,
                            "status": "not_found"
                        })
                        failed_count += 1
                        continue
                    
                    # Create destination directory if needed
                    dest_dir = Path(original_path).parent
                    if not await hass.async_add_executor_job(dest_dir.exists):
                        await hass.async_add_executor_job(lambda: dest_dir.mkdir(parents=True, exist_ok=True))
                    
                    # Check if destination already exists
                    if await hass.async_add_executor_job(os.path.exists, original_path):
                        _LOGGER.warning("Destination %s already exists, skipping restore", original_path)
                        results.append({
                            "original_path": original_path,
                            "current_path": current_path,
                            "status": "destination_exists"
                        })
                        failed_count += 1
                        continue
                    
                    # Move file back to original location
                    await hass.async_add_executor_job(
                        shutil.move,
                        current_path,
                        original_path
                    )
                    
                    # Mark as restored in database
                    await cache_manager.mark_move_restored(move_id)
                    
                    # Trigger rescan of the file
                    await scanner.scan_file(original_path)
                    
                    _LOGGER.info("Restored file: %s -> %s", current_path, original_path)
                    results.append({
                        "original_path": original_path,
                        "current_path": current_path,
                        "status": "restored"
                    })
                    restored_count += 1
                    
                except Exception as e:
                    _LOGGER.error("Error restoring %s: %s", current_path, e)
                    results.append({
                        "original_path": original_path,
                        "current_path": current_path,
                        "status": "error",
                        "error": str(e)
                    })
                    failed_count += 1
            
            return {
                "total_pending": len(pending_moves),
                "restored": restored_count,
                "failed": failed_count,
                "results": results
            }
            
        except Exception as e:
            _LOGGER.error("Error in restore_edited_files service: %s", e)
            return {
                "status": "error",
                "error": str(e)
            }
    
    async def handle_scan_folder(call):
        """Handle scan_folder service call."""
        entry_id = _get_entry_id_from_call(hass, call)
        scanner = hass.data[DOMAIN][entry_id]["scanner"]
        config = hass.data[DOMAIN][entry_id]["config"]
        
        folder_path = call.data.get("folder_path", config.get(CONF_BASE_FOLDER, "/media"))
        force_rescan = call.data.get("force_rescan", False)
        watched_folders = config.get(CONF_WATCHED_FOLDERS, [])
        
        _LOGGER.info("Manual scan requested: %s (force=%s)", folder_path, force_rescan)
        
        # Start scan as background task
        # TODO: Add force_rescan support to scanner
        hass.async_create_task(
            scanner.scan_folder(folder_path, watched_folders)
        )
        
        return {"status": "scan_started", "folder": folder_path}
    
    # Register all services
    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_RANDOM_ITEMS,
        handle_get_random_items,
        schema=SERVICE_GET_RANDOM_ITEMS_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    
    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_ORDERED_FILES,
        handle_get_ordered_files,
        schema=SERVICE_GET_ORDERED_FILES_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    
    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_FILE_METADATA,
        handle_get_file_metadata,
        schema=SERVICE_GET_FILE_METADATA_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    
    hass.services.async_register(
        DOMAIN,
        SERVICE_GEOCODE_FILE,
        handle_geocode_file,
        schema=SERVICE_GEOCODE_FILE_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    
    hass.services.async_register(
        DOMAIN,
        SERVICE_SCAN_FOLDER,
        handle_scan_folder,
        schema=SERVICE_SCAN_FOLDER_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    
    hass.services.async_register(
        DOMAIN,
        "mark_favorite",
        handle_mark_favorite,
        schema=SERVICE_MARK_FAVORITE_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    
    hass.services.async_register(
        DOMAIN,
        "delete_media",
        handle_delete_media,
        schema=SERVICE_DELETE_MEDIA_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    
    hass.services.async_register(
        DOMAIN,
        SERVICE_MARK_FOR_EDIT,
        handle_mark_for_edit,
        schema=SERVICE_MARK_FOR_EDIT_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    
    hass.services.async_register(
        DOMAIN,
        SERVICE_RESTORE_EDITED_FILES,
        handle_restore_edited_files,
        schema=SERVICE_RESTORE_EDITED_FILES_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    
    _LOGGER.info("Media Index services registered")


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.info("Unloading Media Index integration")
    
    # Stop file watcher if running
    watcher = hass.data[DOMAIN][entry.entry_id].get("watcher")
    if watcher:
        watcher.stop_watching()
    
    # Close geocode service
    geocode_service = hass.data[DOMAIN][entry.entry_id].get("geocode_service")
    if geocode_service:
        await geocode_service.close()
    
    # Close cache manager
    cache_manager = hass.data[DOMAIN][entry.entry_id].get("cache_manager")
    if cache_manager:
        await cache_manager.close()

    # Unload platforms
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle removal of an entry (instance deleted by user)."""
    _LOGGER.info("Removing Media Index integration instance")
    
    # Delete this instance's database file
    cache_db_path = os.path.join(
        hass.config.path(".storage"), 
        f"media_index_{entry.entry_id}.db"
    )
    
    if os.path.exists(cache_db_path):
        try:
            os.remove(cache_db_path)
            _LOGGER.info("Deleted database file: %s", cache_db_path)
        except Exception as e:
            _LOGGER.error("Failed to delete database file %s: %s", cache_db_path, e)


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry when options change."""
    _LOGGER.info("Reloading Media Index integration due to config change")
    await hass.config_entries.async_reload(entry.entry_id)


