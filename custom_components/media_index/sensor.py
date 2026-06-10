"""Sensor platform for Media Index integration."""
import logging
from datetime import datetime

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    CONF_BASE_FOLDER,
    CONF_MEDIA_SOURCE_URI,
    CONF_GEOCODE_ENABLED,
    CONF_WATCHED_FOLDERS,
    DEFAULT_GEOCODE_ENABLED,
    ATTR_SCAN_STATUS,
    ATTR_LAST_SCAN_TIME,
    ATTR_TOTAL_FOLDERS,
    ATTR_TOTAL_IMAGES,
    ATTR_TOTAL_VIDEOS,
    ATTR_WATCHED_FOLDERS,
    ATTR_MEDIA_PATH,
    ATTR_CACHE_SIZE_MB,
    ATTR_GEOCODE_ENABLED,
    ATTR_GEOCODE_CACHE_ENTRIES,
    ATTR_GEOCODE_HIT_RATE,
    ATTR_FILES_WITH_LOCATION,
    ATTR_GEOCODE_ATTRIBUTION,
    GEOCODE_ATTRIBUTION,
    SCAN_STATUS_IDLE,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Media Index sensors from a config entry."""
    _LOGGER.info("Setting up Media Index sensor")

    sensor = MediaIndexTotalFilesSensor(hass, entry)
    async_add_entities([sensor], True)

    # When a cast session starts or stops, push a sensor state update immediately
    # so automations can react to cast_active / cast_targets attribute changes.
    session_manager = hass.data.get(DOMAIN, {}).get(entry.entry_id, {}).get("cast_session_manager")
    if session_manager:
        def _on_cast_changed():
            sensor.async_schedule_update_ha_state()
        session_manager.register_update_callback(_on_cast_changed)
        _LOGGER.debug("Registered cast state update callback on sensor")


class MediaIndexTotalFilesSensor(SensorEntity):
    """Sensor showing total indexed files."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the sensor."""
        self.hass = hass
        self._entry = entry
        
        # Use config entry title for sensor name (e.g., "Media Index (/media/Photo)")
        # This ensures unique sensor names for multiple instances
        base_name = entry.title or "Media Index"
        self._attr_name = f"{base_name} Total Files"
        self._attr_unique_id = f"{entry.entry_id}_total_files"
        self._attr_icon = "mdi:folder-multiple-image"
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_value = 0
        
        # Attributes
        self._attr_extra_state_attributes = {
            ATTR_SCAN_STATUS: SCAN_STATUS_IDLE,
            ATTR_LAST_SCAN_TIME: None,
            ATTR_TOTAL_FOLDERS: 0,
            ATTR_TOTAL_IMAGES: 0,
            ATTR_TOTAL_VIDEOS: 0,
            ATTR_WATCHED_FOLDERS: [],
            ATTR_CACHE_SIZE_MB: 0.0,
            ATTR_GEOCODE_CACHE_ENTRIES: 0,
            ATTR_GEOCODE_HIT_RATE: 0.0,
            ATTR_FILES_WITH_LOCATION: 0,
        }
    
    @property
    def device_info(self):
        """Return device information about this sensor."""
        # Use config entry title for device name to support multiple instances
        device_name = self._entry.title or "Media Index"
        return {
            "identifiers": {(DOMAIN, self._entry.entry_id)},
            "name": device_name,
            "manufacturer": "markaggar",
            "model": "Media Index",
        }
    
    async def async_update(self) -> None:
        """Update the sensor."""
        _LOGGER.debug("Updating Media Index sensor")
        
        # Get cache manager from hass data
        cache_manager = self.hass.data[DOMAIN][self._entry.entry_id].get("cache_manager")
        scanner = self.hass.data[DOMAIN][self._entry.entry_id].get("scanner")
        
        if not cache_manager:
            _LOGGER.warning("Cache manager not initialized")
            return
        
        # Get cache statistics
        stats = await cache_manager.get_cache_stats()
        
        # Update sensor state (total files)
        self._attr_native_value = stats.get("total_files", 0)
        
        # Update attributes
        scan_status = SCAN_STATUS_IDLE
        if scanner and scanner.is_scanning:
            scan_status = "scanning"
        
        # Get config
        config = self.hass.data[DOMAIN][self._entry.entry_id].get("config", {})
        geocode_enabled = config.get(CONF_GEOCODE_ENABLED, DEFAULT_GEOCODE_ENABLED)
        watched_folders = config.get(CONF_WATCHED_FOLDERS, [])
        base_folder = config.get(CONF_BASE_FOLDER, "/media")
        media_source_uri = config.get(CONF_MEDIA_SOURCE_URI, "")
        
        # Get libmediainfo availability status
        pymediainfo_available = self.hass.data[DOMAIN][self._entry.entry_id].get("pymediainfo_available", False)

        # Cast session state — read directly from the session manager
        session_manager = self.hass.data[DOMAIN][self._entry.entry_id].get("cast_session_manager")
        if session_manager:
            cast_targets = session_manager.active_targets()
            cast_active = len(cast_targets) > 0
        else:
            cast_targets = []
            cast_active = False

        self._attr_extra_state_attributes = {
            ATTR_SCAN_STATUS: scan_status,
            ATTR_LAST_SCAN_TIME: stats.get("last_scan_time"),
            ATTR_TOTAL_FOLDERS: stats.get("total_folders", 0),
            ATTR_TOTAL_IMAGES: stats.get("total_images", 0),
            ATTR_TOTAL_VIDEOS: stats.get("total_videos", 0),
            ATTR_WATCHED_FOLDERS: watched_folders,
            ATTR_MEDIA_PATH: base_folder,
            "media_source_uri": media_source_uri,
            "libmediainfo_available": pymediainfo_available,
            ATTR_CACHE_SIZE_MB: stats.get("cache_size_mb", 0.0),
            ATTR_GEOCODE_ENABLED: geocode_enabled,
            ATTR_GEOCODE_CACHE_ENTRIES: stats.get("geocode_cache_entries", 0),
            ATTR_GEOCODE_HIT_RATE: stats.get("geocode_hit_rate", 0.0),
            ATTR_FILES_WITH_LOCATION: stats.get("files_with_location", 0),
            ATTR_GEOCODE_ATTRIBUTION: GEOCODE_ATTRIBUTION if geocode_enabled else None,
            "cast_active": cast_active,
            "cast_targets": cast_targets,
        }
