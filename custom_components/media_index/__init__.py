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
    DEFAULT_ENABLE_WATCHER,
    DEFAULT_GEOCODE_ENABLED,
    SERVICE_GET_RANDOM_ITEMS,
    SERVICE_GET_FILE_METADATA,
    SERVICE_GEOCODE_FILE,
    SERVICE_SCAN_FOLDER,
)
from .cache_manager import CacheManager
from .scanner import MediaScanner
from .watcher import MediaWatcher
from .geocoding import GeocodeService

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]

# Service schemas
SERVICE_GET_RANDOM_ITEMS_SCHEMA = vol.Schema({
    vol.Optional("count", default=10): cv.positive_int,
    vol.Optional("folder"): cv.string,
    vol.Optional("file_type"): vol.In(["image", "video"]),
    vol.Optional("date_from"): cv.string,
    vol.Optional("date_to"): cv.string,
})

SERVICE_GET_FILE_METADATA_SCHEMA = vol.Schema({
    vol.Required("file_path"): cv.string,
})

SERVICE_GEOCODE_FILE_SCHEMA = vol.Schema({
    vol.Optional("file_id"): cv.positive_int,
    vol.Optional("latitude"): vol.Coerce(float),
    vol.Optional("longitude"): vol.Coerce(float),
})

SERVICE_SCAN_FOLDER_SCHEMA = vol.Schema({
    vol.Optional("folder_path"): cv.string,
    vol.Optional("force_rescan", default=False): cv.boolean,
})

SERVICE_MARK_FAVORITE_SCHEMA = vol.Schema({
    vol.Required("file_path"): cv.string,
    vol.Optional("is_favorite", default=True): cv.boolean,
})

SERVICE_DELETE_MEDIA_SCHEMA = vol.Schema({
    vol.Required("file_path"): cv.string,
})


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
    geocode_service = None
    
    if enable_geocoding:
        geocode_service = GeocodeService(hass)
        _LOGGER.info("Geocoding service enabled")
    
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
    
    # Register services
    async def handle_get_random_items(call):
        """Handle get_random_items service call."""
        cache_manager = hass.data[DOMAIN][entry.entry_id]["cache_manager"]
        
        items = await cache_manager.get_random_files(
            count=call.data.get("count", 10),
            folder=call.data.get("folder"),
            file_type=call.data.get("file_type"),
            date_from=call.data.get("date_from"),
            date_to=call.data.get("date_to"),
        )
        
        _LOGGER.info("Retrieved %d random items", len(items))
        return {"items": items}
    
    async def handle_get_file_metadata(call):
        """Handle get_file_metadata service call."""
        cache_manager = hass.data[DOMAIN][entry.entry_id]["cache_manager"]
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
        cache_manager = hass.data[DOMAIN][entry.entry_id]["cache_manager"]
        geocode_service = hass.data[DOMAIN][entry.entry_id].get("geocode_service")
        
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
        cache_manager = hass.data[DOMAIN][entry.entry_id]["cache_manager"]
        file_path = call.data["file_path"]
        is_favorite = call.data.get("is_favorite", True)
        
        _LOGGER.info("Marking file as favorite: %s (favorite=%s)", file_path, is_favorite)
        
        try:
            # Update database
            await cache_manager.update_favorite(file_path, is_favorite)
            
            # TODO: Update EXIF metadata in file (future enhancement)
            # For now, just update database
            
            return {
                "file_path": file_path,
                "is_favorite": is_favorite,
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
        
        cache_manager = hass.data[DOMAIN][entry.entry_id]["cache_manager"]
        config = hass.data[DOMAIN][entry.entry_id]["config"]
        
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
    
    async def handle_scan_folder(call):
        """Handle scan_folder service call."""
        scanner = hass.data[DOMAIN][entry.entry_id]["scanner"]
        config = hass.data[DOMAIN][entry.entry_id]["config"]
        
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
    
    _LOGGER.info("Registered 6 services")

    # Register update listener for config changes
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    _LOGGER.info("Media Index integration setup complete")
    return True


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


