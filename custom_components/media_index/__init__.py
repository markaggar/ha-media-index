"""Media Index integration for Home Assistant."""
import asyncio
import logging
import mimetypes
import os
import time
from datetime import timedelta
from pathlib import Path

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers.event import async_track_time_interval, async_track_time_change
import homeassistant.helpers.config_validation as cv

from .const import (
    DOMAIN,
    CONF_BASE_FOLDER,
    CONF_MEDIA_SOURCE_URI,
    CONF_WATCHED_FOLDERS,
    CONF_SCAN_ON_STARTUP,
    CONF_SCAN_SCHEDULE,
    CONF_ENABLE_WATCHER,
    CONF_GEOCODE_ENABLED,
    CONF_GEOCODE_NATIVE_LANGUAGE,
    CONF_AUTO_INSTALL_LIBMEDIAINFO,
    CONF_SCAN_WITHOUT_LIBMEDIAINFO,
    CONF_AUTO_BURST_INDEX,
    CONF_BURST_TIME_WINDOW_SECONDS,
    CONF_BURST_LOCATION_TOLERANCE_METERS,
    CONF_BURST_AUTO_INDEX_INTERVAL_HOURS,
    CONF_BURST_INDEX_AFTER_SCAN,
    CONF_AUTO_CLEANUP,
    CONF_CLEANUP_SCHEDULE,
    CONF_CLEANUP_TIME,
    DEFAULT_ENABLE_WATCHER,
    DEFAULT_GEOCODE_ENABLED,
    DEFAULT_GEOCODE_NATIVE_LANGUAGE,
    DEFAULT_AUTO_INSTALL_LIBMEDIAINFO,
    DEFAULT_SCAN_WITHOUT_LIBMEDIAINFO,
    DEFAULT_AUTO_BURST_INDEX,
    DEFAULT_BURST_TIME_WINDOW_SECONDS,
    DEFAULT_BURST_LOCATION_TOLERANCE_METERS,
    DEFAULT_BURST_AUTO_INDEX_INTERVAL_HOURS,
    DEFAULT_BURST_INDEX_AFTER_SCAN,
    DEFAULT_AUTO_CLEANUP,
    DEFAULT_CLEANUP_SCHEDULE,
    DEFAULT_CLEANUP_TIME,
    DEFAULT_SCAN_ON_STARTUP,
    DEFAULT_SCAN_SCHEDULE,
    SCAN_SCHEDULE_STARTUP_ONLY,
    SCAN_SCHEDULE_HOURLY,
    SCAN_SCHEDULE_DAILY,
    SCAN_SCHEDULE_WEEKLY,
    SERVICE_GET_RANDOM_ITEMS,
    SERVICE_GET_ORDERED_FILES,
    SERVICE_GET_FILE_METADATA,
    SERVICE_GET_RELATED_FILES,
    SERVICE_GEOCODE_FILE,
    SERVICE_SCAN_FOLDER,
    SERVICE_MARK_FOR_EDIT,
    SERVICE_RESTORE_EDITED_FILES,
    SERVICE_RESTORE_DELETED_FILES,
    SERVICE_CLEANUP_DATABASE,
    SERVICE_UPDATE_BURST_METADATA,
    SERVICE_INDEX_BURST_GROUPS,
    SERVICE_FIND_DUPLICATE_FILES,
    SERVICE_INSTALL_LIBMEDIAINFO,
    SERVICE_CHECK_FILE_EXISTS,
    SERVICE_UPDATE_SYNC_STATE,
    SERVICE_GET_SYNC_STATE,
    SERVICE_GET_STREAM_URL,
    SERVICE_ROKU_ECP_CAST,
    SERVICE_ROKU_ECP_QUERY,
    SERVICE_ROKU_ECP_KEYPRESS,
    SERVICE_START_CAST_SLIDESHOW,
    SERVICE_STOP_CAST_SLIDESHOW,
    SERVICE_MIRROR_TO_CAST,
    SERVICE_STOP_CAST,
    EVENT_SYNC_UPDATED,
)
from .cache_manager import CacheManager
from .scanner import MediaScanner
from .watcher import MediaWatcher
from .exif_parser import ExifParser
from .video_parser import VideoMetadataParser
from .geocoding import GeocodeService
from .cast_manager import CastSessionManager, HaMediaPlayerTransport, RokuEcpTransport, _get_roku_host, run_cast_slideshow, run_mirror_cast

_LOGGER = logging.getLogger(__name__)

# Config entry only - no YAML configuration supported
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS: list[Platform] = [Platform.SENSOR]

# Service schemas (all allow extra fields for target selector support)
SERVICE_GET_RANDOM_ITEMS_SCHEMA = vol.Schema({
    vol.Optional("count", default=10): cv.positive_int,
    vol.Optional("folder"): cv.string,
    vol.Optional("recursive", default=True): cv.boolean,
    vol.Optional("file_type"): vol.In(["image", "video"]),
    vol.Optional("favorites_only", default=False): cv.boolean,
    vol.Optional("date_from"): cv.string,
    vol.Optional("date_to"): cv.string,
    vol.Optional("timestamp_from"): cv.positive_int,
    vol.Optional("timestamp_to"): cv.positive_int,
    vol.Optional("anniversary_month"): cv.string,  # "1"-"12" or "*"
    vol.Optional("anniversary_day"): cv.string,    # "1"-"31" or "*"
    vol.Optional("anniversary_window_days", default=0): cv.positive_int,
    vol.Optional("priority_new_files", default=False): cv.boolean,
    vol.Optional("new_files_threshold_seconds", default=3600): cv.positive_int,
    vol.Optional("auto_select_burst_favorite", default=False): cv.boolean,
}, extra=vol.ALLOW_EXTRA)

SERVICE_GET_ORDERED_FILES_SCHEMA = vol.Schema({
    vol.Optional("count", default=50): cv.positive_int,
    vol.Optional("folder"): cv.string,
    vol.Optional("recursive", default=True): cv.boolean,
    vol.Optional("file_type"): vol.In(["image", "video"]),
    vol.Optional("order_by", default="date_taken"): vol.In(["date_taken", "filename", "path", "modified_time"]),
    vol.Optional("order_direction", default="desc"): vol.In(["asc", "desc"]),
    # v1.5.10: Compound cursor pagination - (after_value, after_id) for stable pagination
    # Accept any type without coercion - type conversion handled in service handler based on order_by
    vol.Optional("after_value"): vol.Any(int, float, str),
    vol.Optional("after_id"): vol.Coerce(int),  # Secondary cursor for tie-breaking
    # Date range filtering
    vol.Optional("date_from"): cv.string,
    vol.Optional("date_to"): cv.string,
    vol.Optional("timestamp_from"): vol.Coerce(int),
    vol.Optional("timestamp_to"): vol.Coerce(int),
}, extra=vol.ALLOW_EXTRA)

# Note: SERVICE_GET_FILE_METADATA_SCHEMA defined later after _validate_path_or_uri function

SERVICE_GET_RELATED_FILES_SCHEMA = vol.Schema({
    vol.Optional("reference_path"): cv.string,
    vol.Optional("media_source_uri"): cv.string,
    vol.Required("mode"): vol.In(["burst", "anniversary"]),
    
    # Anniversary mode parameters
    vol.Optional("window_days", default=3): vol.All(vol.Coerce(int), vol.Range(min=0, max=30)),
    vol.Optional("years_back", default=15): vol.All(vol.Coerce(int), vol.Range(min=1, max=50)),
    
    # Common parameters
    vol.Optional("sort_order", default="time_asc"): vol.In(["time_asc", "time_desc"]),
}, extra=vol.ALLOW_EXTRA)

def _validate_geocode_params(data):
    """Validate that at least one identification parameter is provided for geocode_file."""
    has_file_id = data.get("file_id") is not None
    has_file_path = data.get("file_path") is not None
    has_media_source_uri = data.get("media_source_uri") is not None
    has_coordinates = data.get("latitude") is not None and data.get("longitude") is not None
    
    if not (has_file_id or has_file_path or has_media_source_uri or has_coordinates):
        raise vol.Invalid(
            "At least one identification parameter must be provided: "
            "'file_id', 'file_path', 'media_source_uri', or 'latitude'+'longitude'"
        )
    return data

SERVICE_GEOCODE_FILE_SCHEMA = vol.Schema(
    vol.All(
        {
            vol.Optional("file_id"): cv.positive_int,
            vol.Optional("file_path"): cv.string,
            vol.Optional("media_source_uri"): cv.string,
            vol.Optional("latitude"): vol.Coerce(float),
            vol.Optional("longitude"): vol.Coerce(float),
        },
        _validate_geocode_params,
    ),
    extra=vol.ALLOW_EXTRA,
)

SERVICE_SCAN_FOLDER_SCHEMA = vol.Schema({
    vol.Optional("folder_path"): cv.string,
    vol.Optional("force_rescan", default=False): cv.boolean,
}, extra=vol.ALLOW_EXTRA)

def _validate_path_or_uri(data):
    """Validate that at least one of file_path or media_source_uri is provided."""
    if not data.get("file_path") and not data.get("media_source_uri"):
        raise vol.Invalid("Either 'file_path' or 'media_source_uri' must be provided")
    return data

SERVICE_GET_FILE_METADATA_SCHEMA = vol.Schema(
    vol.All(
        {
            vol.Optional("file_path"): cv.string,
            vol.Optional("media_source_uri"): cv.string,
        },
        _validate_path_or_uri,
    ),
    extra=vol.ALLOW_EXTRA,
)

SERVICE_MARK_FAVORITE_SCHEMA = vol.Schema(
    vol.All(
        {
            vol.Optional("file_path"): cv.string,
            vol.Optional("media_source_uri"): cv.string,
            vol.Optional("is_favorite", default=True): cv.boolean,
        },
        _validate_path_or_uri,
    ),
    extra=vol.ALLOW_EXTRA,
)

SERVICE_DELETE_MEDIA_SCHEMA = vol.Schema(
    vol.All(
        {
            vol.Optional("file_path"): cv.string,
            vol.Optional("media_source_uri"): cv.string,
        },
        _validate_path_or_uri,
    ),
    extra=vol.ALLOW_EXTRA,
)

SERVICE_MARK_FOR_EDIT_SCHEMA = vol.Schema(
    vol.All(
        {
            vol.Optional("file_path"): cv.string,
            vol.Optional("media_source_uri"): cv.string,
        },
        _validate_path_or_uri,
    ),
    extra=vol.ALLOW_EXTRA,
)

SERVICE_RESTORE_EDITED_FILES_SCHEMA = vol.Schema({
    vol.Optional("folder_filter"): cv.string,  # e.g., "_Edit"
    vol.Optional("file_path"): cv.string,  # Restore specific file
    vol.Optional("clear_failed", default=False): cv.boolean,  # Remove failed records from pending queue
    vol.Optional("entity_id"): cv.entity_ids,  # Target entity (from UI)
}, extra=vol.ALLOW_EXTRA)

SERVICE_RESTORE_DELETED_FILES_SCHEMA = vol.Schema({
    vol.Optional("file_path"): cv.string,  # Restore specific file from _Junk
    vol.Optional("clear_failed", default=False): cv.boolean,  # Remove failed records from pending queue
    vol.Optional("entity_id"): cv.entity_ids,  # Target entity (from UI)
}, extra=vol.ALLOW_EXTRA)

SERVICE_START_CAST_SLIDESHOW_SCHEMA = vol.Schema({
    vol.Required("media_player_entity_id"): cv.string,
    vol.Optional("interval", default=10): vol.All(vol.Coerce(int), vol.Range(min=1, max=3600)),
    vol.Optional("video_overlap", default=0): vol.All(vol.Coerce(int), vol.Range(min=0, max=30)),
    vol.Optional("sync_group"): cv.string,
    vol.Optional("also_write_sync", default=False): cv.boolean,
    vol.Optional("folder"): cv.string,
    vol.Optional("recursive", default=True): cv.boolean,
    vol.Optional("file_type"): vol.In(["image", "video"]),
    vol.Optional("date_from"): cv.string,
    vol.Optional("date_to"): cv.string,
    vol.Optional("favorites_only", default=False): cv.boolean,
    vol.Optional("anniversary_month"): cv.string,
    vol.Optional("anniversary_day"): cv.string,
    vol.Optional("anniversary_window_days", default=0): cv.positive_int,
    vol.Optional("priority_new_files", default=False): cv.boolean,
}, extra=vol.ALLOW_EXTRA)

SERVICE_MIRROR_TO_CAST_SCHEMA = vol.Schema({
    vol.Required("media_player_entity_id"): cv.string,
    vol.Required("sync_group"): cv.string,
    vol.Optional("pre_end_pause", default=True): cv.boolean,
    vol.Optional("video_overlap", default=2): vol.All(vol.Coerce(int), vol.Range(min=0, max=30)),
}, extra=vol.ALLOW_EXTRA)

SERVICE_STOP_CAST_SLIDESHOW_SCHEMA = vol.Schema({
    vol.Optional("media_player_entity_id"): cv.string,
}, extra=vol.ALLOW_EXTRA)


def _convert_uri_to_path(media_source_uri: str, base_folder: str, media_source_prefix: str) -> str:
    """Convert media-source URI to filesystem path.
    
    Args:
        media_source_uri: Full media-source URI (e.g., "media-source://media_source/media/Photo/PhotoLibrary/2024/IMG_1234.jpg")
        base_folder: Configured base folder path (e.g., "/media/Photo/PhotoLibrary")
        media_source_prefix: Configured media-source URI prefix (e.g., "media-source://media_source/media/Photo/PhotoLibrary")
        
    Returns:
        Filesystem path (e.g., "/media/Photo/PhotoLibrary/2024/IMG_1234.jpg")
        
    Raises:
        ValueError: If URI doesn't start with configured prefix or if prefix not configured
    """
    if not media_source_prefix:
        raise ValueError(
            "Using media_source_uri parameter requires the media_source_uri option "
            "to be configured in integration settings"
        )
    
    if not media_source_uri.startswith(media_source_prefix):
        raise ValueError(f"URI '{media_source_uri}' does not match configured prefix '{media_source_prefix}'")
    
    # Strip the media_source_prefix and replace with base_folder
    relative_path = media_source_uri[len(media_source_prefix):]
    
    # Prevent path traversal attacks by rejecting any '..' components
    from pathlib import PurePath
    rel_parts = [part for part in PurePath(relative_path).parts if part not in ('', '.')]
    if any(part == '..' for part in rel_parts):
        raise ValueError(f"Path traversal detected in URI: '{media_source_uri}' contains '..' in path")
    
    # Normalize paths after validation
    base_folder_normalized = os.path.normpath(base_folder.rstrip("/"))
    file_path = os.path.normpath(os.path.join(base_folder_normalized, relative_path.lstrip("/")))
    
    # Validate that the resulting path is within base_folder (or is the base_folder itself)
    base_folder_abs = os.path.abspath(base_folder_normalized)
    file_path_abs = os.path.abspath(file_path)
    # Allow exact match (base folder itself) or files/folders within it
    if file_path_abs != base_folder_abs and not file_path_abs.startswith(base_folder_abs + os.sep):
        raise ValueError(
            f"Path traversal detected: resolved path '{file_path_abs}' "
            f"is outside the base folder '{base_folder_abs}'"
        )
    
    return file_path


def _convert_path_to_uri(file_path: str, base_folder: str, media_source_prefix: str) -> str:
    """Convert filesystem path to media-source URI.
    
    Args:
        file_path: Filesystem path (e.g., "/media/Photo/PhotoLibrary/2024/IMG_1234.jpg")
        base_folder: Configured base folder path (e.g., "/media/Photo/PhotoLibrary")
        media_source_prefix: Configured media-source URI prefix (e.g., "media-source://media_source/media/Photo/PhotoLibrary")
        
    Returns:
        Media-source URI (e.g., "media-source://media_source/media/Photo/PhotoLibrary/2024/IMG_1234.jpg")
        Or empty string if media_source_prefix not configured (backward compatibility)
        
    Raises:
        ValueError: If file_path doesn't start with base_folder
    """
    if not media_source_prefix:
        # Backward compatibility: return empty string if not configured
        return ""
    
    if not file_path.startswith(base_folder):
        raise ValueError(f"Path '{file_path}' does not start with base folder '{base_folder}'")
    
    # Strip the base_folder and replace with media_source_prefix
    relative_path = file_path[len(base_folder):]
    
    # Ensure media_source_prefix doesn't end with slash
    media_source_prefix = media_source_prefix.rstrip("/")
    
    # Combine to get URI
    media_source_uri = media_source_prefix + relative_path
    
    return media_source_uri


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up Media Index integration from YAML (not supported)."""
    hass.data.setdefault(DOMAIN, {})

    # Generate a per-boot stream signing secret (regenerated on every HA restart).
    # Used by the streaming endpoint to validate short HMAC-signed URLs sent to Roku.
    # Stored at hass.data level (not inside hass.data[DOMAIN]) so it doesn't
    # collide with entry-ID keys that _get_entry_id_from_call iterates over.
    _STREAM_SECRET_KEY = f"{DOMAIN}.stream_secret"
    if _STREAM_SECRET_KEY not in hass.data:
        hass.data[_STREAM_SECRET_KEY] = os.urandom(32)
        _LOGGER.debug("Media Index stream signing secret generated")

    # Register the streaming view once (shared across all config entries).
    from .stream import MediaIndexStreamView
    hass.http.register_view(MediaIndexStreamView())
    _LOGGER.debug("Media Index stream view registered at /api/media_index/stream/{file_id}")

    return True


def _setup_scheduled_scan(
    hass: HomeAssistant,
    entry: ConfigEntry,
    scanner: MediaScanner,
    base_folder: str,
    watched_folders: list,
    scan_schedule: str,
    cache_manager: "CacheManager" = None,
    auto_burst_index: bool = False,
    burst_index_after_scan: bool = False,
    burst_time_window_seconds: int = 10,
    burst_location_tolerance_meters: int = 50,
) -> None:
    """Setup scheduled scanning based on config.
    
    Args:
        hass: Home Assistant instance
        entry: Config entry
        scanner: MediaScanner instance
        base_folder: Base folder path
        watched_folders: List of watched folders
        scan_schedule: Schedule type (hourly/daily/weekly)
        cache_manager: CacheManager instance (required for burst indexing)
        auto_burst_index: Whether auto burst indexing is enabled
        burst_index_after_scan: Run full-library burst index after each scheduled scan
        burst_time_window_seconds: Max gap between burst shots (seconds)
        burst_location_tolerance_meters: Max GPS distance between burst shots (meters)
    """
    async def _scheduled_scan_callback(now):
        """Run scheduled scan if not already running."""
        # Block if pymediainfo not available (unless user opted to scan without it)
        if not hass.data[DOMAIN][entry.entry_id].get("pymediainfo_available", False):
            entry_config = hass.data[DOMAIN][entry.entry_id].get("config", {})
            scan_without_libmediainfo = entry_config.get(
                CONF_SCAN_WITHOUT_LIBMEDIAINFO, DEFAULT_SCAN_WITHOUT_LIBMEDIAINFO
            )
            if not scan_without_libmediainfo:
                _LOGGER.warning(
                    "⏸️ Scheduled scan SKIPPED [%s]: libmediainfo not available and "
                    "'scan_without_libmediainfo' is disabled. "
                    "Enable 'scan_without_libmediainfo' in options if you only index images, "
                    "or call 'media_index.install_libmediainfo' to install video support.",
                    entry.title or entry.entry_id
                )
                return
            _LOGGER.info(
                "ℹ️ libmediainfo not available but 'scan_without_libmediainfo' is enabled [%s] - "
                "proceeding with scan (video metadata will not be extracted).",
                entry.title or entry.entry_id
            )
        
        # Check if scan already in progress
        if scanner.is_scanning:
            _LOGGER.warning(
                "⚠️ Scheduled scan BLOCKED [%s] - scan already running (possible long-running scan or stuck state). "
                "If scans are taking too long, check for metadata extraction issues.",
                entry.title or entry.entry_id
            )
            return
        
        _LOGGER.info(
            "🔄 TRIGGER: Scheduled scan (%s) starting [instance: %s, folder: %s]", 
            scan_schedule, entry.title or entry.entry_id, base_folder
        )
        await scanner.scan_folder(base_folder, watched_folders)

        # Optionally re-index burst groups across the full library after scan
        if auto_burst_index and burst_index_after_scan and cache_manager is not None:
            _LOGGER.info("Running full-library burst group index after scheduled scan")
            try:
                await cache_manager.index_burst_groups(
                    time_window_seconds=burst_time_window_seconds,
                    location_tolerance_meters=burst_location_tolerance_meters,
                    overwrite_existing=True,
                )
            except Exception as err:
                _LOGGER.error("Post-scan burst index failed: %s", err)
    
    # Determine scan interval
    if scan_schedule == SCAN_SCHEDULE_HOURLY:
        interval = timedelta(hours=1)
    elif scan_schedule == SCAN_SCHEDULE_DAILY:
        interval = timedelta(days=1)
    elif scan_schedule == SCAN_SCHEDULE_WEEKLY:
        interval = timedelta(weeks=1)
    else:
        _LOGGER.warning("Unknown scan schedule: %s", scan_schedule)
        return
    
    _LOGGER.info("Setting up scheduled scan: %s (interval=%s)", scan_schedule, interval)
    
    # Register the scheduled scan
    remove_listener = async_track_time_interval(hass, _scheduled_scan_callback, interval)
    
    # Store the remove listener so we can cancel on unload
    entry.async_on_unload(remove_listener)


async def _install_libmediainfo_internal(hass: HomeAssistant, entry_id: str | None = None) -> dict:
    """Shared helper to install libmediainfo system library.
    
    Args:
        hass: Home Assistant instance
        entry_id: Optional config entry ID for automatic reload
    
    Returns:
        Dictionary with status and message
    """
    import subprocess
    from .const import INSTALL_TIMEOUT_APK, INSTALL_TIMEOUT_APT
    
    _LOGGER.info("📦 Installing libmediainfo system library...")
    
    # Network check removed - apk/apt commands will fail fast if network is down
    # No need to add 5-6 seconds of blocking network check during setup
    
    try:
        # Try apk (Alpine/Home Assistant OS)
        subprocess.run(
            ["apk", "add", "--no-cache", "libmediainfo"],
            capture_output=True,
            text=True,
            timeout=INSTALL_TIMEOUT_APK,
            check=True,
        )
        _LOGGER.info("✅ libmediainfo installed successfully via apk")
        
        # Automatically reload the integration to pick up the new library
        if entry_id:
            _LOGGER.info("🔄 Reloading Media Index integration to enable video metadata extraction...")
            await hass.config_entries.async_reload(entry_id)
            _LOGGER.info("✅ Integration reloaded successfully - video metadata extraction now enabled")
        
        return {
            "status": "success",
            "message": "libmediainfo installed successfully and integration reloaded. Video metadata extraction is now enabled."
        }
        
    except FileNotFoundError:
        # apk not found, try apt (Debian/Ubuntu)
        try:
            subprocess.run(
                ["apt-get", "update"],
                capture_output=True,
                text=True,
                timeout=INSTALL_TIMEOUT_APT,
                check=True,
            )
            subprocess.run(
                ["apt-get", "install", "-y", "libmediainfo0v5"],
                capture_output=True,
                text=True,
                timeout=INSTALL_TIMEOUT_APT,
                check=True,
            )
            _LOGGER.info("✅ libmediainfo installed successfully via apt")
            
            # Automatically reload the integration to pick up the new library
            if entry_id:
                _LOGGER.info("🔄 Reloading Media Index integration to enable video metadata extraction...")
                await hass.config_entries.async_reload(entry_id)
                _LOGGER.info("✅ Integration reloaded successfully - video metadata extraction now enabled")
            
            return {
                "status": "success",
                "message": "libmediainfo installed successfully and integration reloaded. Video metadata extraction is now enabled."
            }
            
        except subprocess.CalledProcessError as apt_error:
            _LOGGER.error(
                "Auto-install via apt-get failed (returncode=%s, stderr=%s)",
                apt_error.returncode,
                apt_error.stderr,
            )
            return {
                "status": "failed",
                "message": f"Auto-install failed. Please manually run: apk add --no-cache libmediainfo OR apt-get install libmediainfo0v5"
            }
            
    except subprocess.CalledProcessError as apk_error:
        _LOGGER.error(
            "Auto-install via apk failed (returncode=%s, stderr=%s)",
            apk_error.returncode,
            apk_error.stderr,
        )
        return {
            "status": "failed",
            "message": f"Auto-install failed. Please manually run: apk add --no-cache libmediainfo OR apt-get install libmediainfo0v5"
        }
    except Exception as e:
        _LOGGER.error("Unexpected error during libmediainfo installation: %s", e, exc_info=True)
        return {
            "status": "failed",
            "message": f"Installation failed with unexpected error: {str(e)}"
        }


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
    
    # Check pymediainfo availability on startup
    # Must actually test library loading, not just Python module import
    pymediainfo_available = False
    libmediainfo_error = None
    try:
        from pymediainfo import MediaInfo
        # Try to actually instantiate to trigger library loading
        # This will fail with OSError if libmediainfo.so.0 is missing
        import tempfile
        import os as os_module
        # Create a minimal test file
        test_fd, test_path = tempfile.mkstemp(suffix='.mp4')
        os_module.close(test_fd)
        try:
            MediaInfo.parse(test_path)
            pymediainfo_available = True
            _LOGGER.info("✅ libmediainfo is available - video metadata extraction enabled")
        finally:
            os_module.unlink(test_path)
    except (ImportError, OSError, RuntimeError) as e:
        libmediainfo_error = str(e)
        _LOGGER.warning(
            "⚠️ libmediainfo system library is NOT available - video metadata extraction DISABLED!\n"
            "This usually happens after Home Assistant Core upgrades.\n"
            "Error: %s\n"
            "Automatic scanning is BLOCKED by default to prevent video metadata loss.\n"
            "To fix: Call 'media_index.install_libmediainfo' service or enable 'auto_install_libmediainfo'.\n"
            "If you only index images (no videos), enable 'scan_without_libmediainfo' to allow scanning.",
            libmediainfo_error
        )
    except Exception as e:
        # Catch-all for unexpected errors
        libmediainfo_error = str(e)
        _LOGGER.warning(
            "⚠️ Unexpected error testing libmediainfo: %s\n"
            "Assuming library is not available - scanning blocked by default.\n"
            "If you only index images (no videos), enable 'scan_without_libmediainfo' to allow scanning.",
            libmediainfo_error
        )
    
    # Store availability status for services to check
    hass.data[DOMAIN][entry.entry_id]["pymediainfo_available"] = pymediainfo_available
    
    # Auto-install if missing and auto_install is enabled - DO IT NOW before continuing setup
    if not pymediainfo_available:
        config = {**entry.data, **entry.options}
        auto_install = config.get(CONF_AUTO_INSTALL_LIBMEDIAINFO, DEFAULT_AUTO_INSTALL_LIBMEDIAINFO)
        
        if auto_install:
            _LOGGER.warning(
                "🔧 Auto-install enabled - installing libmediainfo before continuing setup..."
            )
            _LOGGER.info(
                "Note: If internet is down, installation will timeout after 30-60 seconds and integration will continue loading"
            )
            # Install synchronously during setup (no entry_id needed since we're not reloading)
            result = await _install_libmediainfo_internal(hass, entry_id=None)
            if result["status"] == "success":
                _LOGGER.info("✅ Auto-install successful: %s", result["message"])
                # Re-test library availability after installation
                try:
                    # Clear Python's import cache and reload the module to pick up the newly installed library.
                    # importlib.reload does blocking filesystem I/O (listdir, read_text), so run it in an
                    # executor thread to avoid blocking the event loop.
                    import sys
                    import importlib

                    def _reload_and_verify():
                        if 'pymediainfo' in sys.modules:
                            importlib.reload(sys.modules['pymediainfo'])
                        from pymediainfo import MediaInfo  # noqa: F401

                    await hass.async_add_executor_job(_reload_and_verify)
                    hass.data[DOMAIN][entry.entry_id]["pymediainfo_available"] = True
                    _LOGGER.info("✅ libmediainfo verified working after installation (import successful)")
                except Exception as e:
                    _LOGGER.error(
                        "❌ libmediainfo verification failed after installation: %s\n"
                        "This is expected - Python's process needs to restart to load the new library.\n"
                        "Video metadata extraction will be available after Home Assistant restart.",
                        e
                    )
            else:
                _LOGGER.error("❌ Auto-install failed: %s", result["message"])
        else:
            _LOGGER.info(
                "ℹ️ Auto-install is disabled. To enable, reconfigure the integration and check 'auto_install_libmediainfo'."
            )
    
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
    auto_burst_index = config.get(CONF_AUTO_BURST_INDEX, DEFAULT_AUTO_BURST_INDEX)
    burst_time_window_seconds = config.get(CONF_BURST_TIME_WINDOW_SECONDS, DEFAULT_BURST_TIME_WINDOW_SECONDS)
    burst_location_tolerance_meters = config.get(CONF_BURST_LOCATION_TOLERANCE_METERS, DEFAULT_BURST_LOCATION_TOLERANCE_METERS)
    burst_auto_index_interval_hours = config.get(CONF_BURST_AUTO_INDEX_INTERVAL_HOURS, DEFAULT_BURST_AUTO_INDEX_INTERVAL_HOURS)

    burst_index_callback = None
    if auto_burst_index:
        async def _burst_index_callback(folder: str) -> None:
            """Index burst groups for a specific folder."""
            _LOGGER.debug("Auto burst index triggered for folder: %s", folder)
            await cache_manager.index_burst_groups(
                folder=folder,
                time_window_seconds=burst_time_window_seconds,
                location_tolerance_meters=burst_location_tolerance_meters,
                overwrite_existing=False,
            )
        burst_index_callback = _burst_index_callback

    watcher = MediaWatcher(
        scanner,
        cache_manager,
        hass,
        burst_index_callback=burst_index_callback,
        burst_auto_index_interval_hours=burst_auto_index_interval_hours,
    )
    
    # Construct media_source_uri automatically if not configured
    # This ensures v1.4+ upgrade path works seamlessly without config changes
    config = {**entry.data, **entry.options}
    base_folder = config.get(CONF_BASE_FOLDER, "/media")
    media_source_uri = config.get(CONF_MEDIA_SOURCE_URI)
    
    if not media_source_uri:
        # Auto-construct: media-source://media_source + base_folder
        # Example: /media/Photo/PhotoLibrary -> media-source://media_source/media/Photo/PhotoLibrary
        media_source_uri = f"media-source://media_source{base_folder}"
        config[CONF_MEDIA_SOURCE_URI] = media_source_uri
        _LOGGER.info("Auto-constructed media_source_uri: %s (from base_folder: %s)", media_source_uri, base_folder)
    else:
        _LOGGER.debug("Using configured media_source_uri: %s", media_source_uri)
    
    # Store instances
    hass.data[DOMAIN][entry.entry_id]["cache_manager"] = cache_manager
    hass.data[DOMAIN][entry.entry_id]["scanner"] = scanner
    hass.data[DOMAIN][entry.entry_id]["watcher"] = watcher
    hass.data[DOMAIN][entry.entry_id]["geocode_service"] = geocode_service
    hass.data[DOMAIN][entry.entry_id]["config"] = config
    hass.data[DOMAIN][entry.entry_id]["cast_session_manager"] = CastSessionManager()
    
    # Set up platforms BEFORE starting scan so sensor exists
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    
    # Trigger initial scan AFTER Home Assistant has fully started (not during setup)
    # Use config already constructed above
    watched_folders = config.get(CONF_WATCHED_FOLDERS, [])

    from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
    from homeassistant.core import CoreState

    async def _trigger_startup_scan(_event=None):
        """Trigger scan after Home Assistant has fully started."""
        # Block if pymediainfo not available (unless user opted to scan without it)
        if not hass.data[DOMAIN][entry.entry_id].get("pymediainfo_available", False):
            scan_without_libmediainfo = config.get(
                CONF_SCAN_WITHOUT_LIBMEDIAINFO, DEFAULT_SCAN_WITHOUT_LIBMEDIAINFO
            )
            if not scan_without_libmediainfo:
                _LOGGER.warning(
                    "⏸️ Startup scan SKIPPED: libmediainfo not available and "
                    "'scan_without_libmediainfo' is disabled. "
                    "Enable 'scan_without_libmediainfo' in options if you only index images, "
                    "or call 'media_index.install_libmediainfo' to install video support."
                )
                return
            _LOGGER.info(
                "ℹ️ libmediainfo not available but 'scan_without_libmediainfo' is enabled - "
                "proceeding with scan (video metadata will not be extracted)."
            )

        _LOGGER.info(
            "🔄 TRIGGER: Startup scan beginning [instance: %s, folder: %s, watched: %s, watched_only: %s]",
            entry.title or entry.entry_id, base_folder, watched_folders, _watched_only
        )
        await scanner.scan_folder(base_folder, watched_folders, watched_only=_watched_only)

        # Optionally run burst indexing after startup scan — same behaviour as scheduled scans
        _burst_index_after_scan = config.get(CONF_BURST_INDEX_AFTER_SCAN, DEFAULT_BURST_INDEX_AFTER_SCAN)
        if auto_burst_index and _burst_index_after_scan and cache_manager is not None:
            _LOGGER.info("Running full-library burst group index after startup scan")
            try:
                await cache_manager.index_burst_groups(
                    time_window_seconds=burst_time_window_seconds,
                    location_tolerance_meters=burst_location_tolerance_meters,
                    overwrite_existing=True,
                )
            except Exception as err:
                _LOGGER.error("Post-startup-scan burst index failed: %s", err)

    # Check for scans that were interrupted by a previous HA restart or crash.
    # Must be done before the scan-decision block so we can override scan_on_startup.
    has_interrupted_scan = await cache_manager.check_and_mark_interrupted_scans()

    # If HA is already running (integration added at runtime via UI), always do a full
    # scan immediately — the user just created this instance and expects complete indexing.
    if hass.state is CoreState.running:
        _watched_only = False
        _LOGGER.info("HA already running — triggering full initial scan immediately")
        hass.async_create_task(_trigger_startup_scan(), name=f"media_index_initial_scan_{entry.entry_id}")
    elif has_interrupted_scan:
        # A previous scan was interrupted (HA restarted mid-scan). Always resume with a
        # full scan regardless of scan_on_startup — the library may be partially indexed.
        # _trigger_startup_scan will also run burst indexing afterward if configured.
        _watched_only = False
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _trigger_startup_scan)
        _LOGGER.info(
            "Interrupted scan detected — resuming with full scan after HA starts "
            "(already-indexed files will be skipped)"
        )
    elif config.get(CONF_SCAN_ON_STARTUP, DEFAULT_SCAN_ON_STARTUP):
        # On HA restart, restrict to watched folders when configured (faster — only catches
        # changes that occurred in monitored paths while HA was offline).
        # Falls back to full scan when no watched folders are specified.
        _watched_only = bool(watched_folders)
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _trigger_startup_scan)
        _LOGGER.info(
            "Startup scan scheduled after HA start (watched_only=%s, watched_folders=%s)",
            _watched_only, watched_folders
        )
    else:
        _watched_only = False  # unused, but defined for clarity
        _LOGGER.info("Startup scan disabled by configuration")
    
    # Start file system watcher if enabled AND watched_folders are specified
    # Without watched_folders, the watcher would monitor the entire base folder which
    # is resource-intensive for large collections. Use scheduled scans instead.
    if config.get(CONF_ENABLE_WATCHER, DEFAULT_ENABLE_WATCHER):
        if watched_folders:
            _LOGGER.info("Starting file system watcher for folders: %s", watched_folders)
            await watcher.start_watching(base_folder, watched_folders)
        else:
            _LOGGER.info(
                "File system watcher disabled: no watched_folders specified. "
                "For large collections, use scheduled scans instead of watching the entire base folder."
            )
    else:
        _LOGGER.info("File system watcher disabled by configuration")
    
    # Setup scheduled scanning
    scan_schedule = config.get(CONF_SCAN_SCHEDULE, DEFAULT_SCAN_SCHEDULE)
    burst_index_after_scan = config.get(CONF_BURST_INDEX_AFTER_SCAN, DEFAULT_BURST_INDEX_AFTER_SCAN)
    if scan_schedule != SCAN_SCHEDULE_STARTUP_ONLY:
        _setup_scheduled_scan(
            hass, entry, scanner, base_folder, watched_folders, scan_schedule,
            cache_manager=cache_manager,
            auto_burst_index=auto_burst_index,
            burst_index_after_scan=burst_index_after_scan,
            burst_time_window_seconds=burst_time_window_seconds,
            burst_location_tolerance_meters=burst_location_tolerance_meters,
        )
    
    # Setup weekly VACUUM task to compact database
    async def _weekly_vacuum_callback(now):
        """Run weekly VACUUM to reclaim space."""
        try:
            # Get database size with error handling
            try:
                db_size_before = os.path.getsize(cache_manager.db_path) / (1024 * 1024)
            except FileNotFoundError:
                _LOGGER.warning("Database file not found before VACUUM")
                return
            
            _LOGGER.info("Running weekly database VACUUM (current size: %.2f MB)", db_size_before)
            await cache_manager.vacuum_database()
            
            try:
                db_size_after = os.path.getsize(cache_manager.db_path) / (1024 * 1024)
            except FileNotFoundError:
                _LOGGER.warning("Database file not found after VACUUM")
                return
            
            space_reclaimed = db_size_before - db_size_after
            _LOGGER.info("Weekly VACUUM completed: %.2f MB -> %.2f MB (reclaimed %.2f MB)", 
                        db_size_before, db_size_after, space_reclaimed)
        except Exception as e:
            _LOGGER.error("Weekly VACUUM failed: %s", e)
    
    remove_vacuum_listener = async_track_time_interval(
        hass, _weekly_vacuum_callback, timedelta(weeks=1)
    )
    entry.async_on_unload(remove_vacuum_listener)

    # Setup scheduled cleanup (removes stale DB entries for deleted files/folders)
    auto_cleanup = config.get(CONF_AUTO_CLEANUP, DEFAULT_AUTO_CLEANUP)
    if auto_cleanup:
        cleanup_schedule = config.get(CONF_CLEANUP_SCHEDULE, DEFAULT_CLEANUP_SCHEDULE)
        if cleanup_schedule not in ("daily", "weekly", "monthly"):
            _LOGGER.warning(
                "Unknown cleanup schedule '%s'; defaulting to '%s'", cleanup_schedule, DEFAULT_CLEANUP_SCHEDULE
            )
            cleanup_schedule = DEFAULT_CLEANUP_SCHEDULE
        cleanup_time_str = config.get(CONF_CLEANUP_TIME, DEFAULT_CLEANUP_TIME)
        try:
            cleanup_hour, cleanup_minute = (int(p) for p in cleanup_time_str.split(":"))
        except (ValueError, AttributeError):
            cleanup_hour, cleanup_minute = 2, 0

        # last_cleanup tracks when the time-change callback last fired so we can
        # enforce daily/weekly/monthly frequency without a separate interval timer.
        _cleanup_state = {"last_run": None}

        async def _scheduled_cleanup_callback(now):
            """Run cleanup if the configured frequency has elapsed since last run."""
            from datetime import date
            last = _cleanup_state["last_run"]
            today = now.date() if hasattr(now, "date") else date.today()

            if last is not None:
                if cleanup_schedule == "daily":
                    if (today - last).days < 1:
                        return
                elif cleanup_schedule == "weekly":
                    if (today - last).days < 7:
                        return
                elif cleanup_schedule == "monthly":
                    # Approximate: 28 days minimum
                    if (today - last).days < 28:
                        return

            _cleanup_state["last_run"] = today
            _LOGGER.info(
                "Scheduled cleanup starting (schedule=%s, time=%02d:%02d) [%s]",
                cleanup_schedule, cleanup_hour, cleanup_minute,
                entry.title or entry.entry_id,
            )
            try:
                db_size_before = os.path.getsize(cache_manager.db_path) / (1024 * 1024)

                async with cache_manager._db.execute(
                    "SELECT id, path FROM media_files ORDER BY path"
                ) as cursor:
                    rows = await cursor.fetchall()

                stale_count = 0
                checked = 0
                for row in rows:
                    _, file_path = row
                    checked += 1
                    exists = await hass.async_add_executor_job(os.path.exists, file_path)
                    if not exists:
                        await cache_manager.delete_file(file_path)
                        stale_count += 1
                        _LOGGER.debug("Scheduled cleanup: removed stale entry %s", file_path)
                    if checked % 50 == 0:
                        await asyncio.sleep(0)

                await cache_manager.cleanup_orphaned_exif()
                await cache_manager.vacuum_database()

                db_size_after = os.path.getsize(cache_manager.db_path) / (1024 * 1024)
                _LOGGER.info(
                    "Scheduled cleanup complete: %d stale of %d checked, "
                    "%.2f MB → %.2f MB (reclaimed %.2f MB) [%s]",
                    stale_count, checked,
                    db_size_before, db_size_after, db_size_before - db_size_after,
                    entry.title or entry.entry_id,
                )
            except Exception as cleanup_err:
                _cleanup_state["last_run"] = last
                _LOGGER.error("Scheduled cleanup failed: %s", cleanup_err, exc_info=True)

        remove_cleanup_listener = async_track_time_change(
            hass,
            _scheduled_cleanup_callback,
            hour=cleanup_hour,
            minute=cleanup_minute,
            second=0,
        )
        entry.async_on_unload(remove_cleanup_listener)
        _LOGGER.info(
            "Scheduled cleanup enabled: %s at %02d:%02d [%s]",
            cleanup_schedule, cleanup_hour, cleanup_minute,
            entry.title or entry.entry_id,
        )
    
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
    # Check for target in multiple locations (Home Assistant passes it differently depending on context)
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
    # Home Assistant WebSocket transforms target.entity_id -> call.data['entity_id']
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
        
        # If not found and entity_id doesn't end with _total_files, try adding it
        if not entity_entry and not entity_id.endswith("_total_files"):
            _LOGGER.debug("Entity %s not found, trying with _total_files suffix", entity_id)
            entity_entry = entity_registry.async_get(f"{entity_id}_total_files")
        
        if entity_entry and entity_entry.config_entry_id:
            return entity_entry.config_entry_id
        else:
            _LOGGER.warning("Entity %s not found in registry or missing config_entry_id", entity_id)
    
    # Fallback: use first available entry_id (single-instance compatibility)
    if DOMAIN in hass.data and hass.data[DOMAIN]:
        entry_id = next(iter(hass.data[DOMAIN].keys()))
        _LOGGER.debug("No target specified, using first entry_id: %s", entry_id)
        return entry_id
    
    raise ValueError("No Media Index integration instance found")


def _get_instance_data(hass: HomeAssistant, call: ServiceCall) -> dict:
    """Resolve the entry_id from a service call and return its instance data dict.

    Raises HomeAssistantError with a user-friendly message when the target
    integration instance is disabled or not yet loaded, rather than letting a
    raw KeyError propagate to the HA WebSocket error log.
    """
    entry_id = _get_entry_id_from_call(hass, call)
    try:
        return hass.data[DOMAIN][entry_id]
    except KeyError:
        raise HomeAssistantError(
            f"Media Index instance '{entry_id}' is not loaded. "
            "The integration may be disabled or still starting up."
        )


def _register_services(hass: HomeAssistant):
    """Register all Media Index services.
    
    Services use target selector to support multiple instances.
    If no target specified, defaults to first instance (backward compatibility).
    """
    
    # Register services
    async def handle_get_random_items(call):
        """Handle get_random_items service call."""
        instance = _get_instance_data(hass, call)
        cache_manager = instance["cache_manager"]
        config = instance["config"]
        
        # Debug logging removed to prevent excessive logs during slideshow
        
        # Convert folder URI to path if needed
        folder = call.data.get("folder")
        if folder and folder.startswith("media-source://"):
            base_folder = config.get(CONF_BASE_FOLDER)
            media_source_prefix = config.get(CONF_MEDIA_SOURCE_URI, "")
            
            try:
                folder = _convert_uri_to_path(folder, base_folder, media_source_prefix)
                _LOGGER.debug("Converted folder URI to path: %s", folder)
            except ValueError as e:
                _LOGGER.error("Failed to convert folder URI to path: %s", e)
                return {"items": []}
        
        items = await cache_manager.get_random_files(
            count=call.data.get("count", 10),
            folder=folder,
            recursive=call.data.get("recursive", True),
            file_type=call.data.get("file_type"),
            date_from=call.data.get("date_from"),
            date_to=call.data.get("date_to"),
            timestamp_from=call.data.get("timestamp_from"),
            timestamp_to=call.data.get("timestamp_to"),
            anniversary_month=call.data.get("anniversary_month"),
            anniversary_day=call.data.get("anniversary_day"),
            anniversary_window_days=call.data.get("anniversary_window_days", 0),
            favorites_only=call.data.get("favorites_only", False),
            priority_new_files=call.data.get("priority_new_files", False),
            new_files_threshold_seconds=call.data.get("new_files_threshold_seconds", 3600),
            auto_select_burst_favorite=call.data.get("auto_select_burst_favorite", False),
        )
        
        # Add media_source_uri to each item if configured
        _add_media_source_uris_to_items(items, config)
        
        result = {"items": items}
        # Debug: Retrieved X random items (logging removed)
        return result
    
    def _add_media_source_uris_to_items(items, config):
        """Helper to add media_source_uri to each item in list."""
        base_folder = config.get(CONF_BASE_FOLDER)
        media_source_prefix = config.get(CONF_MEDIA_SOURCE_URI, "")
        
        if media_source_prefix and base_folder:
            for item in items:
                try:
                    item["media_source_uri"] = _convert_path_to_uri(
                        item["path"], base_folder, media_source_prefix
                    )
                except ValueError as e:
                    _LOGGER.warning("Failed to convert path to URI for %s: %s", item.get("path"), e)
                    item["media_source_uri"] = ""
    
    async def handle_get_ordered_files(call):
        """Handle get_ordered_files service call."""
        instance = _get_instance_data(hass, call)
        cache_manager = instance["cache_manager"]
        config = instance["config"]
        
        # Get cursor parameters and ensure proper types
        after_value = call.data.get("after_value")
        after_id = call.data.get("after_id")
        order_by = call.data.get("order_by", "date_taken")
        
        # Convert after_value to proper type based on order_by field
        # For date/time fields, convert to numeric; for string fields, keep as string
        if after_value is not None and order_by in ("date_taken", "modified_time"):
            try:
                after_value = int(after_value)
            except (ValueError, TypeError):
                try:
                    after_value = float(after_value)
                except (ValueError, TypeError):
                    _LOGGER.warning("Could not convert after_value to numeric: %s", after_value)
        
        _LOGGER.debug("get_ordered_files: after_value=%s (type=%s), after_id=%s", 
                       after_value, type(after_value).__name__, after_id)
        
        # Convert folder URI to path if needed
        folder = call.data.get("folder")
        if folder and folder.startswith("media-source://"):
            base_folder = config.get(CONF_BASE_FOLDER)
            media_source_prefix = config.get(CONF_MEDIA_SOURCE_URI, "")
            
            try:
                folder = _convert_uri_to_path(folder, base_folder, media_source_prefix)
                _LOGGER.debug("Converted folder URI to path: %s", folder)
            except ValueError as e:
                _LOGGER.error("Failed to convert folder URI to path: %s", e)
                return {"items": []}
        
        items = await cache_manager.get_ordered_files(
            count=call.data.get("count", 50),
            folder=folder,
            recursive=call.data.get("recursive", True),
            file_type=call.data.get("file_type"),
            order_by=order_by,
            order_direction=call.data.get("order_direction", "desc"),
            after_value=after_value,  # v1.5.10: Cursor-based pagination (properly typed)
            after_id=after_id,  # Secondary cursor for tie-breaking
            date_from=call.data.get("date_from"),
            date_to=call.data.get("date_to"),
            timestamp_from=call.data.get("timestamp_from"),
            timestamp_to=call.data.get("timestamp_to"),
        )
        
        # Add media_source_uri to each item if configured
        _add_media_source_uris_to_items(items, config)
        
        result = {"items": items}
        # Debug: Retrieved X ordered items (logging removed)
        return result
    
    async def handle_get_file_metadata(call):
        """Handle get_file_metadata service call."""
        instance = _get_instance_data(hass, call)
        cache_manager = instance["cache_manager"]
        config = instance["config"]
        
        # Get file_path from either file_path parameter or media_source_uri
        file_path = call.data.get("file_path")
        media_source_uri = call.data.get("media_source_uri")
        
        if not file_path and media_source_uri:
            # Convert URI to path
            base_folder = config.get(CONF_BASE_FOLDER)
            media_source_prefix = config.get(CONF_MEDIA_SOURCE_URI, "")
            
            try:
                file_path = _convert_uri_to_path(media_source_uri, base_folder, media_source_prefix)
            except ValueError as e:
                _LOGGER.error("Failed to convert URI to path: %s", e)
                return {"error": str(e)}
        
        if not file_path:
            return {"error": "Either file_path or media_source_uri required"}
        
        metadata = await cache_manager.get_file_by_path(file_path)
        
        if metadata:
            return metadata
        else:
            _LOGGER.warning("File not found in index: %s", file_path)
            return {"error": "File not found"}
    
    async def handle_get_related_files(call):
        """Handle get_related_files service call (burst or anniversary mode)."""
        instance = _get_instance_data(hass, call)
        cache_manager = instance["cache_manager"]
        config = instance["config"]
        
        mode = call.data.get("mode")
        
        # Get reference_path from either reference_path parameter or media_source_uri
        reference_path = call.data.get("reference_path")
        media_source_uri = call.data.get("media_source_uri")
        
        if not reference_path and media_source_uri:
            # Convert URI to path
            base_folder = config.get(CONF_BASE_FOLDER)
            media_source_prefix = config.get(CONF_MEDIA_SOURCE_URI, "")
            
            try:
                reference_path = _convert_uri_to_path(media_source_uri, base_folder, media_source_prefix)
                _LOGGER.debug("Converted media_source_uri to path: %s -> %s", media_source_uri, reference_path)
            except ValueError as e:
                _LOGGER.error("Failed to convert URI to path: %s", e)
                return {"error": str(e), "items": []}
        
        if not reference_path:
            return {"error": "Either reference_path or media_source_uri required", "items": []}
        
        sort_order = call.data.get("sort_order", "time_asc")
        
        if mode == "burst":
            # Burst detection: use pre-indexed burst_id for fast path, fall back to
            # proximity search (with integration-configured or hardcoded defaults) when
            # the file hasn't been indexed yet.
            reference_file = await cache_manager.get_file_by_path(reference_path)
            ref_exif = (reference_file or {}).get('exif', {}) if reference_file else {}
            burst_id = ref_exif.get('burst_id') if ref_exif else None
            ref_date_taken = ref_exif.get('date_taken') if ref_exif else None

            if burst_id and ref_date_taken is not None:
                _LOGGER.debug("Burst fast path: burst_id=%s for %s", burst_id, reference_path)
                items = await cache_manager.get_burst_photos_by_burst_id(
                    burst_id=burst_id,
                    reference_date_taken=ref_date_taken,
                    sort_order=sort_order,
                )
            else:
                # Fallback: at-query-time proximity search using integration config defaults
                time_window = config.get(
                    CONF_BURST_TIME_WINDOW_SECONDS,
                    DEFAULT_BURST_TIME_WINDOW_SECONDS,
                )
                location_tolerance = config.get(
                    CONF_BURST_LOCATION_TOLERANCE_METERS,
                    DEFAULT_BURST_LOCATION_TOLERANCE_METERS,
                )
                _LOGGER.debug(
                    "Burst fallback path: window=%ds, location=%dm for %s",
                    time_window, location_tolerance, reference_path,
                )
                items = await cache_manager.get_burst_photos(
                    reference_path=reference_path,
                    time_window_seconds=time_window,
                    prefer_same_location=location_tolerance > 0,
                    location_tolerance_meters=location_tolerance,
                    sort_order=sort_order,
                )
            _LOGGER.debug("Found %d burst photos for %s", len(items), reference_path)
            
        elif mode == "anniversary":
            # Anniversary mode - NOT YET IMPLEMENTED
            _LOGGER.error("Anniversary mode is not yet implemented")
            return {
                "error": "Anniversary mode is not yet implemented. Use 'burst' mode or anniversary filters in get_random_items service.",
                "items": []
            }
        
        else:
            return {"error": f"Invalid mode: {mode}", "items": []}
        
        # Add media_source_uri to all items
        _add_media_source_uris_to_items(items, config)
        
        return {
            "reference_path": reference_path,
            "mode": mode,
            "count": len(items),
            "items": items
        }

    
    
    async def handle_geocode_file(call):
        """Handle geocode_file service call for progressive geocoding."""
        instance = _get_instance_data(hass, call)
        cache_manager = instance["cache_manager"]
        config = instance["config"]
        geocode_service = instance.get("geocode_service")
        
        if not geocode_service:
            _LOGGER.error("Geocoding service not enabled")
            return {"error": "Geocoding not enabled"}
        
        file_id = call.data.get("file_id")
        file_path = call.data.get("file_path")
        media_source_uri = call.data.get("media_source_uri")
        lat = call.data.get("latitude")
        lon = call.data.get("longitude")
        
        # Convert media_source_uri to file_path if provided
        if not file_path and media_source_uri:
            base_folder = config.get(CONF_BASE_FOLDER)
            media_source_prefix = config.get(CONF_MEDIA_SOURCE_URI, "")
            
            try:
                file_path = _convert_uri_to_path(media_source_uri, base_folder, media_source_prefix)
            except ValueError as e:
                _LOGGER.error("Failed to convert URI to path: %s", e)
                return {"error": str(e)}
        
        # Get file_id from file_path if provided but file_id not given
        if file_path and not file_id:
            file_data = await cache_manager.get_file_by_path(file_path)
            if file_data:
                file_id = file_data.get("id")
        
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
            return {"error": "Either file_id, file_path, media_source_uri, or latitude/longitude required"}
        
        # 1. Check geocode cache first (fast)
        cached_location = await cache_manager.get_geocode_cache(lat, lon)
        if cached_location:
            # Update exif_data table with cached result
            if file_id:
                await cache_manager.update_exif_location(file_id, cached_location)
            return cached_location
        
        # 2. Call Nominatim API (slow, rate-limited)
        location_data = await geocode_service.reverse_geocode(lat, lon)
        
        if not location_data:
            return {"error": "Geocoding failed"}
        
        # 3. Cache the result
        await cache_manager.add_geocode_cache(lat, lon, location_data)
        
        # 4. Update exif_data table with new location
        if file_id:
            await cache_manager.update_exif_location(file_id, location_data)
        
        # 5. Return location data to caller
        return location_data
    
    async def handle_mark_favorite(call):
        """Handle mark_favorite service call."""
        instance = _get_instance_data(hass, call)
        cache_manager = instance["cache_manager"]
        config = instance["config"]
        
        # Get file_path from either file_path parameter or media_source_uri
        file_path = call.data.get("file_path")
        media_source_uri = call.data.get("media_source_uri")
        
        if not file_path and media_source_uri:
            # Convert URI to path
            base_folder = config.get(CONF_BASE_FOLDER)
            media_source_prefix = config.get(CONF_MEDIA_SOURCE_URI, "")
            
            try:
                file_path = _convert_uri_to_path(media_source_uri, base_folder, media_source_prefix)
                _LOGGER.debug("Converted URI to path: %s -> %s", media_source_uri, file_path)
            except ValueError as e:
                _LOGGER.error("Failed to convert URI to path: %s", e)
                return {
                    "status": "error",
                    "error": str(e)
                }
        
        if not file_path:
            return {
                "status": "error",
                "error": "Either file_path or media_source_uri required"
            }
        
        is_favorite = call.data.get("is_favorite", True)
        
        # Debug logging removed to prevent excessive logs during slideshow
        
        try:
            # Update database
            db_success = await cache_manager.update_favorite(file_path, is_favorite)
            # Debug: Database update result (logging removed)
            
            # Write rating to file metadata
            # Rating 5 = favorite, Rating 0 = unfavorited
            rating = 5 if is_favorite else 0
            
            # Determine file type to use appropriate parser
            file_ext = Path(file_path).suffix.lower()
            # Debug: File extension, rating to write (logging removed)
            
            if file_ext in {'.jpg', '.jpeg', '.png', '.tiff', '.tif', '.heic'}:
                # Debug: Calling ExifParser.write_rating (logging removed)
                success = await hass.async_add_executor_job(
                    ExifParser.write_rating, file_path, rating
                )
                # Debug: ExifParser.write_rating result (logging removed)
            elif file_ext in {'.mp4', '.m4v', '.mov'}:
                # Debug: Calling VideoMetadataParser.write_rating (logging removed)
                success = await hass.async_add_executor_job(
                    VideoMetadataParser.write_rating, file_path, rating
                )
                # Debug: VideoMetadataParser.write_rating result (logging removed)
            else:
                success = False
                _LOGGER.warning("Unsupported file type for rating: %s", file_ext)
            
            if success:
                _LOGGER.debug("Wrote rating=%d to %s", rating, file_path)
            else:
                _LOGGER.warning("❌ Failed to write rating to %s (database updated=%s)", file_path, db_success)
            
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
        
        instance = _get_instance_data(hass, call)
        cache_manager = instance["cache_manager"]
        config = instance["config"]
        
        # Get file_path from either file_path parameter or media_source_uri
        file_path = call.data.get("file_path")
        media_source_uri = call.data.get("media_source_uri")
        
        if not file_path and media_source_uri:
            # Convert URI to path
            base_folder = config.get(CONF_BASE_FOLDER)
            media_source_prefix = config.get(CONF_MEDIA_SOURCE_URI, "")
            
            try:
                file_path = _convert_uri_to_path(media_source_uri, base_folder, media_source_prefix)
                _LOGGER.debug("Converted URI to path: %s -> %s", media_source_uri, file_path)
            except ValueError as e:
                _LOGGER.error("Failed to convert URI to path: %s", e)
                return {
                    "status": "error",
                    "error": str(e)
                }
        
        if not file_path:
            return {
                "status": "error",
                "error": "Either file_path or media_source_uri required"
            }
        
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
            
            # Record the move in move_history so it can be restored later
            await cache_manager.record_file_move(
                original_path=file_path,
                new_path=str(dest_path),
                reason="junk"
            )

            # Remove from database
            await cache_manager.delete_file(file_path)
            
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
        
        instance = _get_instance_data(hass, call)
        cache_manager = instance["cache_manager"]
        config = instance["config"]
        
        # Get file_path from either file_path parameter or media_source_uri
        file_path = call.data.get("file_path")
        media_source_uri = call.data.get("media_source_uri")
        
        if not file_path and media_source_uri:
            # Convert URI to path
            base_folder = config.get(CONF_BASE_FOLDER)
            media_source_prefix = config.get(CONF_MEDIA_SOURCE_URI, "")
            
            try:
                file_path = _convert_uri_to_path(media_source_uri, base_folder, media_source_prefix)
                _LOGGER.debug("Converted URI to path: %s -> %s", media_source_uri, file_path)
            except ValueError as e:
                _LOGGER.error("Failed to convert URI to path: %s", e)
                return {
                    "status": "error",
                    "error": str(e)
                }
        
        if not file_path:
            return {
                "status": "error",
                "error": "Either file_path or media_source_uri required"
            }
        
        base_folder = config.get(CONF_BASE_FOLDER, "/media")
        
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
    
    async def handle_cleanup_database(call):
        """Handle cleanup_database service call."""
        cache_manager = _get_instance_data(hass, call)["cache_manager"]
        
        dry_run = call.data.get("dry_run", True)
        
        _LOGGER.info("Cleanup database (dry_run=%s)", dry_run)
        
        try:
            # Get database size before cleanup
            db_size_before = os.path.getsize(cache_manager.db_path) / (1024 * 1024)
            
            # Get all files from database
            async with cache_manager._db.execute(
                "SELECT id, path FROM media_files ORDER BY path"
            ) as cursor:
                rows = await cursor.fetchall()
            
            stale_files = []
            checked = 0
            
            for row in rows:
                file_id, file_path = row
                checked += 1
                
                # Check if file exists on filesystem
                exists = await hass.async_add_executor_job(os.path.exists, file_path)
                
                if not exists:
                    stale_files.append({"id": file_id, "path": file_path})
                    if not dry_run:
                        # Remove from database
                        await cache_manager.delete_file(file_path)
                        _LOGGER.debug("Removed stale entry: %s", file_path)
                
                # Yield control every 10 files
                if checked % 10 == 0:
                    await asyncio.sleep(0)
            
            # Check for orphaned exif_data rows (always check, even in dry_run)
            # Count using optimized query
            async with cache_manager._db.execute(
                "SELECT COUNT(*) FROM exif_data WHERE file_id NOT IN (SELECT id FROM media_files)"
            ) as cursor:
                row = await cursor.fetchone()
                orphaned_count = row[0] if row else 0
            
            if orphaned_count > 0:
                if dry_run:
                    _LOGGER.warning("Found %d orphaned exif_data rows (dry run, not removed)", orphaned_count)
                else:
                    _LOGGER.warning("Found %d orphaned exif_data rows, removing...", orphaned_count)
                    # Use public method for proper encapsulation
                    await cache_manager.cleanup_orphaned_exif()
            
            # Run VACUUM to reclaim space and compact database
            if not dry_run:
                _LOGGER.info("Running VACUUM to compact database...")
                await cache_manager.vacuum_database()
                db_size_after_vacuum = os.path.getsize(cache_manager.db_path) / (1024 * 1024)
                space_reclaimed = db_size_before - db_size_after_vacuum
                _LOGGER.info("VACUUM completed: %.2f MB -> %.2f MB (reclaimed %.2f MB)", 
                            db_size_before, db_size_after_vacuum, space_reclaimed)
            else:
                db_size_after_vacuum = db_size_before
                space_reclaimed = 0
            
            result = {
                "status": "completed",
                "dry_run": dry_run,
                "checked": checked,
                "stale_count": len(stale_files),
                "stale_files": [f["path"] for f in stale_files],
                "orphaned_exif_removed": orphaned_count,
                "db_size_before_mb": round(db_size_before, 2),
                "db_size_after_mb": round(db_size_after_vacuum, 2),
                "space_reclaimed_mb": round(space_reclaimed, 2)
            }
            
            if dry_run:
                _LOGGER.info("Cleanup dry run: found %d stale files out of %d checked (DB size: %.2f MB)", 
                            len(stale_files), checked, db_size_before)
            else:
                _LOGGER.info("Cleanup completed: removed %d stale files, reclaimed %.2f MB", 
                            len(stale_files), space_reclaimed)
            
            return result
            
        except Exception as err:
            _LOGGER.error("Cleanup database failed: %s", err, exc_info=True)
            return {
                "status": "error",
                "error": str(err)
            }
    
    async def handle_restore_edited_files(call):
        """Handle restore_edited_files service call."""
        import shutil
        import os
        
        instance = _get_instance_data(hass, call)
        cache_manager = instance["cache_manager"]
        scanner = instance["scanner"]
        
        folder_filter = call.data.get("folder_filter", "_Edit")
        specific_file = call.data.get("file_path")
        clear_failed = call.data.get("clear_failed", False)
        
        _LOGGER.info("Restoring edited files (filter: %s, specific: %s, clear_failed: %s)", folder_filter, specific_file, clear_failed)
        
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
                        if clear_failed:
                            await cache_manager.mark_move_restored(move_id)
                            _LOGGER.info("Cleared failed restore record for %s", current_path)
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
                        if clear_failed:
                            await cache_manager.mark_move_restored(move_id)
                            _LOGGER.info("Cleared failed restore record for %s", current_path)
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
                    if clear_failed:
                        await cache_manager.mark_move_restored(move_id)
                        _LOGGER.info("Cleared failed restore record for %s", current_path)
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
    
    async def handle_restore_deleted_files(call):
        """Handle restore_deleted_files service call.

        Restores files that were moved to the _Junk folder by delete_media back
        to their original filesystem locations, using the move_history table.
        """
        import shutil
        import os

        instance = _get_instance_data(hass, call)
        cache_manager = instance["cache_manager"]
        scanner = instance["scanner"]

        specific_file = call.data.get("file_path")
        clear_failed = call.data.get("clear_failed", False)

        _LOGGER.info("Restoring deleted files from _Junk (specific: %s, clear_failed: %s)", specific_file, clear_failed)

        try:
            pending_moves = await cache_manager.get_pending_restores("_Junk")

            if specific_file:
                pending_moves = [m for m in pending_moves if m["new_path"] == specific_file]

            restored_count = 0
            failed_count = 0
            results = []

            for move in pending_moves:
                move_id = move["id"]
                original_path = move["original_path"]
                current_path = move["new_path"]

                try:
                    if not await hass.async_add_executor_job(os.path.exists, current_path):
                        _LOGGER.warning("File not found at %s, skipping restore", current_path)
                        if clear_failed:
                            await cache_manager.mark_move_restored(move_id)
                            _LOGGER.info("Cleared failed restore record for %s", current_path)
                        results.append({
                            "original_path": original_path,
                            "current_path": current_path,
                            "status": "not_found",
                        })
                        failed_count += 1
                        continue

                    dest_dir = Path(original_path).parent
                    if not await hass.async_add_executor_job(dest_dir.exists):
                        await hass.async_add_executor_job(lambda: dest_dir.mkdir(parents=True, exist_ok=True))

                    if await hass.async_add_executor_job(os.path.exists, original_path):
                        _LOGGER.warning("Destination %s already exists, skipping restore", original_path)
                        if clear_failed:
                            await cache_manager.mark_move_restored(move_id)
                            _LOGGER.info("Cleared failed restore record for %s", current_path)
                        results.append({
                            "original_path": original_path,
                            "current_path": current_path,
                            "status": "destination_exists",
                        })
                        failed_count += 1
                        continue

                    await hass.async_add_executor_job(shutil.move, current_path, original_path)
                    await cache_manager.mark_move_restored(move_id)
                    await scanner.scan_file(original_path)

                    _LOGGER.info("Restored deleted file: %s -> %s", current_path, original_path)
                    results.append({
                        "original_path": original_path,
                        "current_path": current_path,
                        "status": "restored",
                    })
                    restored_count += 1

                except Exception as e:
                    _LOGGER.error("Error restoring %s: %s", current_path, e)
                    if clear_failed:
                        await cache_manager.mark_move_restored(move_id)
                        _LOGGER.info("Cleared failed restore record for %s", current_path)
                    results.append({
                        "original_path": original_path,
                        "current_path": current_path,
                        "status": "error",
                        "error": str(e),
                    })
                    failed_count += 1

            return {
                "total_pending": len(pending_moves),
                "restored": restored_count,
                "failed": failed_count,
                "results": results,
            }

        except Exception as e:
            _LOGGER.error("Error in restore_deleted_files service: %s", e)
            return {"status": "error", "error": str(e)}

    async def handle_update_burst_metadata(call):
        """Handle update_burst_metadata service call."""
        instance = _get_instance_data(hass, call)
        cache_manager = instance["cache_manager"]
        config = instance["config"]

        base_folder = config.get(CONF_BASE_FOLDER, "/media")
        media_source_prefix = config.get(CONF_MEDIA_SOURCE_URI, "")
        
        burst_files = call.data.get("burst_files", [])
        favorited_files = call.data.get("favorited_files", [])
        
        _LOGGER.info(
            "update_burst_metadata: %d burst files, %d favorited", 
            len(burst_files), 
            len(favorited_files)
        )
        
        try:
            # Convert URIs to filesystem paths
            burst_paths = []
            for uri in burst_files:
                try:
                    path = _convert_uri_to_path(uri, base_folder, media_source_prefix)
                    if path:
                        burst_paths.append(path)
                except Exception as e:
                    _LOGGER.warning("Failed to convert URI %s: %s", uri, e)
            
            favorited_paths = []
            for uri in favorited_files:
                try:
                    path = _convert_uri_to_path(uri, base_folder, media_source_prefix)
                    if path:
                        favorited_paths.append(path)
                except Exception as e:
                    _LOGGER.warning("Failed to convert URI %s: %s", uri, e)
            
            # Update burst metadata in database
            updated_count = await cache_manager.update_burst_metadata(burst_paths, favorited_paths)
            
            _LOGGER.info(
                "Updated burst metadata for %d files (burst_count=%d, %d favorited)", 
                updated_count,
                len(burst_paths), 
                len(favorited_paths)
            )
            
            return {
                "status": "success",
                "files_updated": updated_count,
                "burst_count": len(burst_paths),
                "favorites_count": len(favorited_paths)
            }
            
        except Exception as e:
            _LOGGER.error("Error in update_burst_metadata service: %s", e)
            return {
                "status": "error",
                "error": str(e)
            }

    async def handle_index_burst_groups(call):
        """Handle index_burst_groups service call.

        Scans the entire indexed library (or a sub-folder) and writes burst_count /
        burst_favorites to every file that belongs to a detected burst group.  Designed
        for large collections (200 K+ items) — uses a single sorted query then an
        in-process walk rather than per-file DB round-trips.
        """
        cache_manager = _get_instance_data(hass, call)["cache_manager"]

        folder              = call.data.get("folder", None)
        time_window         = call.data.get("time_window_seconds", 10)
        location_tolerance  = call.data.get("location_tolerance_meters", 50)
        min_group_size      = call.data.get("min_group_size", 2)
        overwrite_existing  = call.data.get("overwrite_existing", True)

        _LOGGER.info(
            "index_burst_groups service called: folder=%s, window=%ds, min_size=%d, overwrite=%s",
            folder or "all", time_window, min_group_size, overwrite_existing,
        )

        try:
            result = await cache_manager.index_burst_groups(
                folder=folder,
                time_window_seconds=time_window,
                location_tolerance_meters=location_tolerance,
                min_group_size=min_group_size,
                overwrite_existing=overwrite_existing,
            )
            return {
                "status": "success",
                **result,
            }
        except Exception as e:
            _LOGGER.error("Error in index_burst_groups service: %s", e)
            return {
                "status": "error",
                "error": str(e),
            }

    async def handle_find_duplicate_files(call):
        """Handle find_duplicate_files service call.

        Finds files within burst groups that share identical file_size, date_taken,
        width, and height — indicating filesystem-level duplicates (e.g. uploaded twice).
        Keeper selection is folder-pair aware: the folder contributing more files to
        duplicate pairs is kept globally; non-keepers come from the other folder.
        With dry_run=True (default) returns the groups without touching anything.
        With dry_run=False and auto_delete=True, moves all non-keepers to _Junk.
        """
        import shutil

        instance = _get_instance_data(hass, call)
        cache_manager = instance["cache_manager"]
        config = instance["config"]

        folder = call.data.get("folder")
        # prefer_folders accepts a comma-delimited string of folder paths/suffixes
        raw_pf = call.data.get("prefer_folders", "")
        prefer_folders = [p.strip() for p in raw_pf.split(",") if p.strip()] if raw_pf else []
        dry_run = call.data.get("dry_run", True)
        auto_delete = call.data.get("auto_delete", False)

        _LOGGER.info(
            "find_duplicate_files: folder=%s, prefer_folders=%s, dry_run=%s, auto_delete=%s",
            folder or "all", prefer_folders or "none", dry_run, auto_delete,
        )

        try:
            # Always run burst indexing first so we have a fresh view of burst groups
            # before determining which files are duplicates.
            inst_config = instance["config"]
            _btime = inst_config.get(CONF_BURST_TIME_WINDOW_SECONDS, DEFAULT_BURST_TIME_WINDOW_SECONDS)
            _bloc = inst_config.get(CONF_BURST_LOCATION_TOLERANCE_METERS, DEFAULT_BURST_LOCATION_TOLERANCE_METERS)
            _LOGGER.info("find_duplicate_files: running burst index first (time_window=%ss, gps_tolerance=%sm)", _btime, _bloc)
            await cache_manager.index_burst_groups(
                folder=folder,
                time_window_seconds=_btime,
                location_tolerance_meters=_bloc,
                overwrite_existing=True,
            )

            result = await cache_manager.find_duplicate_files(
                folder=folder, prefer_folders=prefer_folders
            )
            sets = result["sets"]
            folder_pairs = result["folder_pairs"]

            duplicate_sets = len(sets)
            total_duplicates = sum(len(s["duplicates"]) for s in sets)
            deleted = 0
            delete_errors = 0

            if not dry_run and auto_delete and sets:
                base_folder = config.get(CONF_BASE_FOLDER, "/media")
                junk_folder = Path(base_folder) / "_Junk"
                await hass.async_add_executor_job(lambda: junk_folder.mkdir(parents=True, exist_ok=True))

                for grp in sets:
                    # Safety check: verify the keeper actually exists on disk before
                    # moving its duplicates. If the keeper is missing, skip this group
                    # entirely rather than orphaning files with no surviving copy.
                    keeper_path = grp["keeper"]["path"]
                    keeper_exists = await hass.async_add_executor_job(os.path.exists, keeper_path)
                    if not keeper_exists:
                        _LOGGER.warning(
                            "find_duplicate_files: keeper file not found on disk, skipping group: %s",
                            keeper_path,
                        )
                        continue

                    # Propagate favorite status: if any duplicate being moved is
                    # favorited but the keeper isn't, mark the keeper as favorited so
                    # the status is not silently lost.
                    if grp["keeper"]["is_favorited"] == 0 and any(
                        d["is_favorited"] for d in grp["duplicates"]
                    ):
                        _LOGGER.debug(
                            "find_duplicate_files: propagating favorite to keeper: %s",
                            keeper_path,
                        )
                        await cache_manager.update_favorite(keeper_path, True)

                    for dup in grp["duplicates"]:
                        dup_path = dup["path"]
                        try:
                            file_name = Path(dup_path).name
                            dest_path = junk_folder / file_name
                            counter = 1
                            while await hass.async_add_executor_job(dest_path.exists):
                                stem = Path(dup_path).stem
                                suffix = Path(dup_path).suffix
                                dest_path = junk_folder / f"{stem}_{counter}{suffix}"
                                counter += 1

                            await hass.async_add_executor_job(shutil.move, dup_path, str(dest_path))
                            await cache_manager.record_file_move(
                                original_path=dup_path,
                                new_path=str(dest_path),
                                reason="junk"
                            )
                            await cache_manager.delete_file(dup_path)
                            deleted += 1
                            _LOGGER.debug("Moved duplicate to junk: %s -> %s", dup_path, dest_path)
                        except Exception as del_err:
                            _LOGGER.error("Failed to delete duplicate %s: %s", dup_path, del_err)
                            delete_errors += 1

            return {
                "status": "success",
                "dry_run": dry_run,
                "duplicate_sets": duplicate_sets,
                "total_duplicates": total_duplicates,
                "deleted": deleted,
                "delete_errors": delete_errors,
                "folder_pairs": folder_pairs,
                "groups": sets,
            }
        except Exception as e:
            _LOGGER.error("Error in find_duplicate_files service: %s", e)
            return {"status": "error", "error": str(e)}

    async def handle_scan_folder(call):
        """Handle scan_folder service call."""
        entry_id = _get_entry_id_from_call(hass, call)
        instance = _get_instance_data(hass, call)
        
        # Block scanning if pymediainfo is not available (unless user opted to scan without it)
        if not instance.get("pymediainfo_available", False):
            entry_config = instance.get("config", {})
            scan_without_libmediainfo = entry_config.get(
                CONF_SCAN_WITHOUT_LIBMEDIAINFO, DEFAULT_SCAN_WITHOUT_LIBMEDIAINFO
            )
            if not scan_without_libmediainfo:
                _LOGGER.warning(
                    "⏸️ Scan BLOCKED: libmediainfo not available and 'scan_without_libmediainfo' is disabled.\n"
                    "Enable 'scan_without_libmediainfo' in options if you only index images, "
                    "or call 'media_index.install_libmediainfo' to install video support."
                )
                return {"status": "blocked", "reason": "pymediainfo_not_available"}
            _LOGGER.info(
                "ℹ️ libmediainfo not available but 'scan_without_libmediainfo' is enabled - "
                "proceeding with scan (video metadata will not be extracted)."
            )
        
        scanner = instance["scanner"]
        config = instance["config"]
        
        folder_path = call.data.get("folder_path", config.get(CONF_BASE_FOLDER, "/media"))
        force_rescan = call.data.get("force_rescan", False)
        watched_folders = config.get(CONF_WATCHED_FOLDERS, [])
        
        _LOGGER.info("🔄 TRIGGER: Manual scan service call for %s (force=%s)", folder_path, force_rescan)

        auto_burst_index = config.get(CONF_AUTO_BURST_INDEX, DEFAULT_AUTO_BURST_INDEX)
        burst_index_after_scan = config.get(CONF_BURST_INDEX_AFTER_SCAN, DEFAULT_BURST_INDEX_AFTER_SCAN)
        burst_time_window_seconds = config.get(CONF_BURST_TIME_WINDOW_SECONDS, DEFAULT_BURST_TIME_WINDOW_SECONDS)
        burst_location_tolerance_meters = config.get(CONF_BURST_LOCATION_TOLERANCE_METERS, DEFAULT_BURST_LOCATION_TOLERANCE_METERS)
        cache_manager = instance["cache_manager"]

        async def _scan_and_burst():
            await scanner.scan_folder(folder_path, watched_folders, force=force_rescan)
            if auto_burst_index and burst_index_after_scan:
                _LOGGER.info("Running burst group index for %s after manual scan", folder_path)
                try:
                    await cache_manager.index_burst_groups(
                        folder=folder_path,
                        time_window_seconds=burst_time_window_seconds,
                        location_tolerance_meters=burst_location_tolerance_meters,
                        overwrite_existing=False,
                    )
                except Exception as err:
                    _LOGGER.error("Post-scan burst index failed: %s", err)

        # Start scan (+ optional burst index) as background task
        hass.async_create_task(_scan_and_burst())

        return {"status": "scan_started", "folder": folder_path}
    
    async def handle_check_file_exists(call):
        """Handle check_file_exists service call - lightweight filesystem check."""
        instance = _get_instance_data(hass, call)
        config = instance["config"]
        
        # Get file_path from either file_path parameter or media_source_uri
        file_path = call.data.get("file_path")
        media_source_uri = call.data.get("media_source_uri")
        
        if not file_path and media_source_uri:
            # Convert URI to path (includes security validation)
            base_folder = config.get(CONF_BASE_FOLDER)
            media_source_prefix = config.get(CONF_MEDIA_SOURCE_URI, "")
            
            try:
                file_path = _convert_uri_to_path(media_source_uri, base_folder, media_source_prefix)
            except ValueError as e:
                _LOGGER.error("Failed to convert URI to path: %s", e)
                return {"exists": False, "error": str(e)}
        
        if not file_path:
            return {"exists": False, "error": "Either file_path or media_source_uri required"}
        
        # Security: Validate file_path is within base_folder (prevent directory traversal)
        base_folder = config.get(CONF_BASE_FOLDER)
        base_folder_abs = os.path.realpath(base_folder)
        file_path_abs = os.path.realpath(file_path)
        
        # Check if path is within base_folder or is the base_folder itself
        if file_path_abs != base_folder_abs and not file_path_abs.startswith(base_folder_abs + os.sep):
            _LOGGER.warning(
                "Security: Rejected file_path outside base_folder: '%s' (base: '%s')",
                file_path_abs, base_folder_abs
            )
            return {"exists": False, "error": "Path outside configured base folder"}
        
        # Perform lightweight filesystem check
        try:
            exists = await hass.async_add_executor_job(os.path.exists, file_path)
            return {"exists": exists, "path": file_path}
        except Exception as e:
            _LOGGER.error("Error checking file existence: %s", e)
            return {"exists": False, "path": file_path, "error": str(e)}
    
    async def handle_install_libmediainfo(call):
        """Install libmediainfo system library."""
        entry_id = _get_entry_id_from_call(hass, call)
        return await _install_libmediainfo_internal(hass, entry_id)

    async def handle_get_stream_url(call):
        """Return a short HMAC-signed stream URL for a media file (PoC for Roku ECP cast).

        Accepts either:
          - file_id (int): direct DB lookup
          - path_contains (str): substring match against stored path (first match returned)
        Returns url, file_id, path, file_type, mime_type so the caller can verify and test.
        """
        from .stream import generate_stream_url

        instance = _get_instance_data(hass, call)
        cache_manager = instance["cache_manager"]

        file_id = call.data.get("file_id")
        path_contains = call.data.get("path_contains")
        ttl = int(call.data.get("ttl", 3600))

        row = None
        if file_id is not None:
            row = await cache_manager.get_file_by_id(int(file_id))
            if not row:
                raise HomeAssistantError(f"File with id={file_id} not found in database")
        elif path_contains:
            rows = await cache_manager.search_files_by_path(path_contains, limit=1)
            if not rows:
                raise HomeAssistantError(f"No file matching '{path_contains}' found in database")
            row = rows[0]
        else:
            raise HomeAssistantError("Provide either 'file_id' or 'path_contains'")

        fid = row["id"]
        file_path = row.get("path", "")
        mime_type, _ = mimetypes.guess_type(file_path)
        # Build a filename hint with the real extension so Roku's URL-extension
        # MIME detection works (e.g. "photo.jpg", "video.mp4").
        import os as _os
        ext = _os.path.splitext(file_path)[1].lower()  # e.g. ".jpg"
        file_type = row.get("file_type", "")
        if ext:
            filename_hint = ("video" if file_type == "video" else "photo") + ext
        else:
            filename_hint = ""
        url = generate_stream_url(hass, fid, ttl, filename=filename_hint)

        return {
            "url": url,
            "file_id": fid,
            "path": file_path,
            "file_type": row.get("file_type", "unknown"),
            "mime_type": mime_type or "application/octet-stream",
            "file_size": row.get("file_size", 0),
        }

    async def handle_roku_ecp_cast(call):
        """Cast media to a Roku device via the xcast app (ECP app ID 687485).

        Generates a signed stream URL, properly percent-encodes it, then POSTs
        to the xcast ECP endpoint server-side — avoiding browser CORS restrictions.

        Supports both video and image content with correct orientation/rotation.
        For images, passes width, height, and EXIF-derived rotation (in radians).
        """
        from .stream import generate_stream_url
        from homeassistant.helpers import entity_registry as er, device_registry as dr
        from homeassistant.helpers.aiohttp_client import async_get_clientsession
        import urllib.parse
        import os as _os
        from yarl import URL as YarlURL

        instance = _get_instance_data(hass, call)
        cache_manager = instance["cache_manager"]
        config = instance["config"]

        roku_entity_id = call.data.get("roku_entity_id", "").strip()
        if not roku_entity_id:
            raise HomeAssistantError("'roku_entity_id' is required")

        # If a mirror or slideshow session is running for this entity, stop it so
        # it doesn't overwrite what the card is about to push.
        session_manager = instance.get("cast_session_manager")
        if session_manager and session_manager.is_active(roku_entity_id):
            _LOGGER.info(
                "roku_ecp_cast: stopping active cast session for %s (card is taking over)",
                roku_entity_id,
            )
            session_manager.stop(roku_entity_id)

        file_id = call.data.get("file_id")
        file_path_param = call.data.get("file_path")
        media_source_uri_param = call.data.get("media_source_uri")
        path_contains = call.data.get("path_contains")
        ttl = int(call.data.get("ttl", 3600))
        start_position_seconds = call.data.get("start_position_seconds")

        # Resolve file row from DB
        row = None
        if file_id is not None:
            row = await cache_manager.get_file_by_id(int(file_id))
            if not row:
                raise HomeAssistantError(f"File with id={file_id} not found")
        elif file_path_param:
            row = await cache_manager.get_file_by_path(file_path_param)
        elif media_source_uri_param:
            base_folder = config.get(CONF_BASE_FOLDER)
            media_source_prefix = config.get(CONF_MEDIA_SOURCE_URI, "")
            try:
                fp = _convert_uri_to_path(media_source_uri_param, base_folder, media_source_prefix)
                row = await cache_manager.get_file_by_path(fp)
            except ValueError as e:
                raise HomeAssistantError(f"Failed to resolve media_source_uri: {e}") from e
        elif path_contains:
            rows = await cache_manager.search_files_by_path(path_contains, limit=1)
            if rows:
                row = rows[0]

        if not row:
            raise HomeAssistantError("File not found in database")

        fid = row["id"]
        file_path_actual = row.get("path", "")
        file_type = row.get("file_type", "image")
        orientation = row.get("orientation")  # 'normal', '90_cw', '90_ccw', '180', or None
        width = row.get("width")
        height = row.get("height")

        ext = _os.path.splitext(file_path_actual)[1].lower()
        filename_hint = ("video" if file_type == "video" else "photo") + ext if ext else ""

        # Generate HMAC-signed stream URL
        stream_url = generate_stream_url(hass, fid, ttl, filename=filename_hint)

        # Resolve Roku host from device registry / config entry
        entity_reg = er.async_get(hass)
        device_reg = dr.async_get(hass)
        entity_entry = entity_reg.async_get(roku_entity_id)
        roku_host = None
        if entity_entry and entity_entry.device_id:
            device = device_reg.async_get(entity_entry.device_id)
            if device:
                for ceid in device.config_entries:
                    ce = hass.config_entries.async_get_entry(ceid)
                    if ce and ce.domain == "roku":
                        roku_host = ce.data.get("host")
                        break

        if not roku_host:
            raise HomeAssistantError(
                f"Cannot determine Roku host for '{roku_entity_id}'. "
                "Ensure the entity belongs to a configured Roku integration."
            )

        # Map file extension → xcast format string
        _FORMAT_MAP = {
            '.jpg': 'jpeg', '.jpeg': 'jpeg', '.png': 'png', '.gif': 'gif',
            '.webp': 'webp', '.bmp': 'bmp', '.heic': 'heic', '.tiff': 'tiff',
            '.mp4': 'mp4', '.mov': 'mov', '.avi': 'avi', '.mkv': 'mkv',
            '.m4v': 'm4v', '.webm': 'webm', '.mpg': 'mpeg', '.mpeg': 'mpeg',
        }
        fmt = _FORMAT_MAP.get(ext, ext.lstrip('.'))

        # For images, always derive w/h from Pillow using the same pipeline as
        # stream.py (exif_transpose + thumbnail to 4K max).  DB dimensions are
        # the *physical* pixel dimensions before EXIF rotation, which can differ
        # from what gets served — wrong DB metadata or unusual orientation tags
        # cause Roku to stretch the image.  Pillow is always authoritative.
        # For video, Pillow can't help; fall back to DB dimensions with the
        # 90°/270° swap for rotated recordings.
        if file_type != "video":
            from .stream import get_display_dimensions
            try:
                pil_w, pil_h = await hass.async_add_executor_job(
                    get_display_dimensions, file_path_actual
                )
                ecp_w, ecp_h = pil_w, pil_h
            except Exception as exc:
                _LOGGER.warning(
                    "roku_ecp_cast: could not read dimensions from %s: %s — "
                    "sending cast without w/h",
                    file_path_actual, exc,
                )
                ecp_w, ecp_h = None, None
        else:
            # Video: use DB dimensions, swapping for 90°/270° rotations
            _SWAP_ORIENTATIONS = {'90_cw', '90_ccw'}
            ori = orientation or 'normal'
            if ori in _SWAP_ORIENTATIONS:
                ecp_w, ecp_h = height, width
            else:
                ecp_w, ecp_h = width, height

        # Extract HA host/port from stream URL for xcast host param
        parsed_stream = urllib.parse.urlparse(stream_url)
        ha_host = parsed_stream.hostname or "localhost"
        ha_port = parsed_stream.port or 8123

        # Build xcast ECP query params
        enc_url = urllib.parse.quote(stream_url, safe="")
        title = _os.path.basename(file_path_actual) or filename_hint
        enc_title = urllib.parse.quote(title, safe="")

        if file_type == "video":
            params = (
                f"title={enc_title}&mediaType=video&format={fmt}"
                f"&url={enc_url}&host={ha_host}&port={ha_port}"
            )
            # Pass display dimensions so xcast/Roku know the correct aspect ratio.
            # For portrait videos (orientation 90_cw/90_ccw) ecp_w/ecp_h are already
            # swapped above (coded 1920x1080 → display 1080x1920).
            if ecp_w and ecp_h:
                params += f"&w={ecp_w}&h={ecp_h}"
            # Pass rotation angle so xcast applies the correct orientation.
            # Portrait recordings store landscape pixels + a rotation tag;
            # xcast needs an explicit r= to rotate the video to the correct orientation.
            _VIDEO_ROTATION_MAP = {'90_cw': '90.0', '90_ccw': '270.0', '180': '180.0'}
            params += f"&r={_VIDEO_ROTATION_MAP.get(ori, '0.0')}"
            # Seek: tell xcast to start buffering from this position instead of the beginning.
            # contentPosition is in milliseconds; this is the reliable way to seek for xcast.
            if start_position_seconds is not None:
                try:
                    pos_ms = max(0, int(float(start_position_seconds) * 1000))
                    params += f"&contentPosition={pos_ms}"
                    _LOGGER.debug("roku_ecp_cast: seek to %dms via contentPosition", pos_ms)
                except (TypeError, ValueError):
                    pass
        else:
            params = f"title={enc_title}&mediaType=image&format={fmt}&url={enc_url}"
            if ecp_w and ecp_h:
                params += f"&w={ecp_w}&h={ecp_h}"
            params += "&r=0.0&ri=0.0"  # rotation already baked in by exif_transpose

        ecp_full_url = YarlURL(f"http://{roku_host}:8060/input/687485?{params}", encoded=True)

        _LOGGER.info(
            "roku_ecp_cast (xcast): %s → file_id=%s type=%s orientation=%s",
            roku_entity_id, fid, file_type, orientation,
        )

        session = async_get_clientsession(hass)
        ecp_url_sent = None
        ecp_response_body = None
        try:
            async with session.post(ecp_full_url, data=b"") as resp:
                ecp_status = resp.status
                ecp_url_sent = str(resp.url)
                if ecp_status != 200:
                    try:
                        ecp_response_body = await resp.text()
                    except Exception:
                        ecp_response_body = "(unreadable)"
        except Exception as e:
            _LOGGER.error("roku_ecp_cast: xcast HTTP call failed: %s", e)
            raise HomeAssistantError(f"Roku xcast call failed: {e}") from e

        _LOGGER.info("roku_ecp_cast: xcast sent: %s → HTTP %s", ecp_url_sent, ecp_status)
        if ecp_response_body:
            _LOGGER.warning("roku_ecp_cast: xcast %s response body: %s", ecp_status, ecp_response_body)

        return {
            "url": stream_url,
            "file_id": fid,
            "roku_host": roku_host,
            "ecp_status": ecp_status,
            "ecp_url_sent": ecp_url_sent,
            "ecp_response_body": ecp_response_body,
            "media_type": file_type,
        }

    async def handle_stop_cast(call):
        """Send an ECP keypress/Home to a Roku device, clearing the cast image.

        Accepts ``roku_entity_id`` (a media_player entity from the Roku HA
        integration).  Resolves the Roku host from the device/config registry
        and POSTs to ``http://{host}:8060/keypress/Home``.
        """
        from homeassistant.helpers import entity_registry as er, device_registry as dr
        from homeassistant.helpers.aiohttp_client import async_get_clientsession
        from yarl import URL as YarlURL

        roku_entity_id = call.data.get("roku_entity_id", "").strip()
        if not roku_entity_id:
            raise HomeAssistantError("'roku_entity_id' is required")

        entity_reg = er.async_get(hass)
        device_reg = dr.async_get(hass)
        entity_entry = entity_reg.async_get(roku_entity_id)
        roku_host = None
        if entity_entry and entity_entry.device_id:
            device = device_reg.async_get(entity_entry.device_id)
            if device:
                for ceid in device.config_entries:
                    ce = hass.config_entries.async_get_entry(ceid)
                    if ce and ce.domain == "roku":
                        roku_host = ce.data.get("host")
                        break

        if not roku_host:
            raise HomeAssistantError(
                f"Cannot determine Roku host for '{roku_entity_id}'. "
                "Ensure the entity belongs to a configured Roku integration."
            )

        ecp_url = YarlURL(f"http://{roku_host}:8060/keypress/Home")
        session = async_get_clientsession(hass)
        try:
            async with session.post(ecp_url, data=b"") as resp:
                status = resp.status
        except Exception as exc:
            _LOGGER.error("stop_cast: ECP keypress/Home failed for %s: %s", roku_entity_id, exc)
            raise HomeAssistantError(f"Roku ECP stop failed: {exc}") from exc

        _LOGGER.info("stop_cast: keypress/Home → %s (%s) HTTP %s", roku_entity_id, roku_host, status)
        return {"roku_host": roku_host, "ecp_status": status}

    async def handle_roku_ecp_keypress(call):
        """Send an arbitrary ECP keypress to a Roku device.

        Use this instead of media_player services for timing-sensitive actions —
        the HA Roku integration polls the device every ~8 s so HA service calls
        arrive far too late (e.g. pausing a video 3 s before it ends).

        Common keypresses: Play (toggle play/pause), Pause, Home, Back, Fwd, Rev.
        """
        from homeassistant.helpers import entity_registry as er, device_registry as dr
        from homeassistant.helpers.aiohttp_client import async_get_clientsession
        from yarl import URL as YarlURL
        import re as _re

        roku_entity_id = call.data.get("roku_entity_id", "").strip()
        if not roku_entity_id:
            raise HomeAssistantError("'roku_entity_id' is required")

        keyname = call.data.get("keyname", "").strip()
        if not keyname:
            raise HomeAssistantError("'keyname' is required")

        # Basic allow-list to prevent path traversal in the URL
        if not _re.match(r'^[A-Za-z0-9_-]+$', keyname):
            raise HomeAssistantError(
                f"Invalid keyname '{keyname}'. Must contain only letters, digits, hyphens, or underscores."
            )

        entity_reg = er.async_get(hass)
        device_reg = dr.async_get(hass)
        entity_entry = entity_reg.async_get(roku_entity_id)
        roku_host = None
        if entity_entry and entity_entry.device_id:
            device = device_reg.async_get(entity_entry.device_id)
            if device:
                for ceid in device.config_entries:
                    ce = hass.config_entries.async_get_entry(ceid)
                    if ce and ce.domain == "roku":
                        roku_host = ce.data.get("host")
                        break

        if not roku_host:
            raise HomeAssistantError(
                f"Cannot determine Roku host for '{roku_entity_id}'. "
                "Ensure the entity belongs to a configured Roku integration."
            )

        ecp_url = YarlURL(f"http://{roku_host}:8060/keypress/{keyname}")
        session = async_get_clientsession(hass)
        try:
            async with session.post(ecp_url, data=b"") as resp:
                status = resp.status
        except Exception as exc:
            _LOGGER.error("roku_ecp_keypress: ECP keypress/%s failed for %s: %s", keyname, roku_entity_id, exc)
            raise HomeAssistantError(f"Roku ECP keypress failed: {exc}") from exc

        _LOGGER.debug("roku_ecp_keypress: %s → %s (%s) HTTP %s", keyname, roku_entity_id, roku_host, status)
        return {"roku_host": roku_host, "ecp_status": status}

    async def handle_roku_ecp_query(call):
        """Query the current playback state of a Roku device via ECP.

        GETs http://{roku_host}:8060/query/media-player and parses the XML
        response, returning the player state and position so the card can sync
        its local clock without relying on the HA media_player entity (which
        has up to an 8-second polling lag).

        Returns:
            state:        'play' | 'pause' | 'stop' | 'close' | 'none'
            position_ms:  current playback position in milliseconds (0 if unknown)
            duration_ms:  total media duration in milliseconds (0 if unknown)
            is_live:      true if the stream is live (no duration)
        """
        import xml.etree.ElementTree as ET
        from homeassistant.helpers import entity_registry as er, device_registry as dr
        from homeassistant.helpers.aiohttp_client import async_get_clientsession

        roku_entity_id = call.data.get("roku_entity_id", "").strip()
        if not roku_entity_id:
            raise HomeAssistantError("'roku_entity_id' is required")

        # Resolve Roku host from device registry (same pattern as roku_ecp_cast)
        entity_reg = er.async_get(hass)
        device_reg = dr.async_get(hass)
        entity_entry = entity_reg.async_get(roku_entity_id)
        roku_host = None
        if entity_entry and entity_entry.device_id:
            device = device_reg.async_get(entity_entry.device_id)
            if device:
                for ceid in device.config_entries:
                    ce = hass.config_entries.async_get_entry(ceid)
                    if ce and ce.domain == "roku":
                        roku_host = ce.data.get("host")
                        break

        if not roku_host:
            raise HomeAssistantError(
                f"Cannot determine Roku host for '{roku_entity_id}'. "
                "Ensure the entity belongs to a configured Roku integration."
            )

        session = async_get_clientsession(hass)
        query_url = f"http://{roku_host}:8060/query/media-player"
        try:
            async with session.get(query_url, timeout=3) as resp:
                if resp.status != 200:
                    raise HomeAssistantError(
                        f"ECP query/media-player returned HTTP {resp.status}"
                    )
                body = await resp.text()
        except HomeAssistantError:
            raise
        except Exception as exc:
            raise HomeAssistantError(f"Roku ECP query failed: {exc}") from exc

        # Parse XML: <player state="play"><position>5000</position><runtime>30000</runtime>...
        # Note: Roku may return values with a unit suffix, e.g. "47 ms" — strip non-digits.
        def _parse_ms(text):
            if not text:
                return 0
            import re as _re
            m = _re.match(r'(\d+)', text.strip())
            return int(m.group(1)) if m else 0

        try:
            root = ET.fromstring(body)
            state = root.get("state", "none")
            position_ms = _parse_ms(root.findtext("position"))
            duration_ms = _parse_ms(root.findtext("runtime"))
            is_live = (root.findtext("is_live") or "false").lower() == "true"
        except ET.ParseError as exc:
            raise HomeAssistantError(f"Failed to parse ECP response: {exc}") from exc

        _LOGGER.debug(
            "roku_ecp_query: %s (%s) state=%s pos=%dms dur=%dms",
            roku_entity_id, roku_host, state, position_ms, duration_ms,
        )
        return {
            "state": state,
            "position_ms": position_ms,
            "duration_ms": duration_ms,
            "is_live": is_live,
        }

    # ── Cast services ────────────────────────────────────────────────────────

    async def handle_start_cast_slideshow(call):
        """Start an unattended random-batch slideshow cast to a media_player entity."""
        instance = _get_instance_data(hass, call)
        cache_manager = instance["cache_manager"]
        config = instance["config"]
        session_manager = instance.get("cast_session_manager")
        if session_manager is None:
            raise HomeAssistantError("Cast session manager not initialised")

        target_entity_id = call.data["media_player_entity_id"]
        interval = call.data.get("interval", 10)
        video_overlap = call.data.get("video_overlap", 0)
        sync_group = call.data.get("sync_group")
        also_write_sync = call.data.get("also_write_sync", False)

        # Convert folder URI to path if needed (same pattern as handle_get_random_items)
        folder = call.data.get("folder")
        if folder and folder.startswith("media-source://"):
            base_folder = config.get(CONF_BASE_FOLDER)
            media_source_prefix = config.get(CONF_MEDIA_SOURCE_URI, "")
            try:
                folder = _convert_uri_to_path(folder, base_folder, media_source_prefix)
            except ValueError as err:
                _LOGGER.error("start_cast_slideshow: failed to convert folder URI: %s", err)
                return

        query_params = {
            "folder": folder,
            "recursive": call.data.get("recursive", True),
            "file_type": call.data.get("file_type"),
            "date_from": call.data.get("date_from"),
            "date_to": call.data.get("date_to"),
            "favorites_only": call.data.get("favorites_only", False),
            "anniversary_month": call.data.get("anniversary_month"),
            "anniversary_day": call.data.get("anniversary_day"),
            "anniversary_window_days": call.data.get("anniversary_window_days", 0),
            "priority_new_files": call.data.get("priority_new_files", False),
        }

        # Add media_source_uri to items so cast.py can resolve them
        base_folder_path = config.get(CONF_BASE_FOLDER)
        media_source_prefix = config.get(CONF_MEDIA_SOURCE_URI, "")

        # Wrap cache_manager with a proxy that adds URIs automatically
        class _CacheManagerProxy:
            async def get_random_files(self, **kwargs):
                items = await cache_manager.get_random_files(**kwargs)
                if media_source_prefix and base_folder_path:
                    for item in items:
                        try:
                            item["media_source_uri"] = _convert_path_to_uri(
                                item["path"], base_folder_path, media_source_prefix
                            )
                        except (ValueError, KeyError):
                            item.setdefault("media_source_uri", "")
                return items

        # Auto-select transport: Roku ECP for Roku devices, media_player for others
        roku_host = _get_roku_host(hass, target_entity_id)
        if roku_host:
            transport = RokuEcpTransport(hass, roku_host)
            _LOGGER.info(
                "start_cast_slideshow: Roku ECP transport selected for %s (host=%s)",
                target_entity_id, roku_host,
            )
        else:
            transport = HaMediaPlayerTransport()
        coro = run_cast_slideshow(
            hass=hass,
            cache_manager=_CacheManagerProxy(),
            entity_id=target_entity_id,
            transport=transport,
            query_params=query_params,
            interval=interval,
            video_overlap=video_overlap,
            sync_group=sync_group,
            also_write_sync=also_write_sync,
        )
        # Stop any existing session for this target before starting the new one.
        # session_manager.start() also does this, but being explicit here gives
        # a clear INFO log and ensures the old task is cancelled before we create
        # the new coroutine context.
        if session_manager.is_active(target_entity_id):
            _LOGGER.info(
                "start_cast_slideshow: stopping existing session for %s before starting new one",
                target_entity_id,
            )
            session_manager.stop(target_entity_id)
        session_manager.start(target_entity_id, hass, coro)
        _LOGGER.info(
            "start_cast_slideshow: started for %s (interval=%ds)", target_entity_id, interval
        )

    async def handle_mirror_to_cast(call):
        """Mirror a media-card sync group to a media_player entity in real-time."""
        instance = _get_instance_data(hass, call)
        session_manager = instance.get("cast_session_manager")
        if session_manager is None:
            raise HomeAssistantError("Cast session manager not initialised")

        cache_manager = instance["cache_manager"]
        config = instance["config"]
        target_entity_id = call.data["media_player_entity_id"]
        sync_group = call.data["sync_group"]
        pre_end_pause = call.data.get("pre_end_pause", True)
        video_overlap = call.data.get("video_overlap", 2)

        # Stop any existing session for this target before mirroring.
        if session_manager.is_active(target_entity_id):
            _LOGGER.info(
                "mirror_to_cast: stopping existing session for %s before starting mirror",
                target_entity_id,
            )
            session_manager.stop(target_entity_id)

        # Auto-select transport: Roku ECP for Roku devices, media_player for others
        roku_host = _get_roku_host(hass, target_entity_id)
        if roku_host:
            transport = RokuEcpTransport(hass, roku_host)
            _LOGGER.info(
                "mirror_to_cast: Roku ECP transport selected for %s (host=%s)",
                target_entity_id, roku_host,
            )
        else:
            transport = HaMediaPlayerTransport()
        coro = run_mirror_cast(
            hass=hass,
            entity_id=target_entity_id,
            transport=transport,
            sync_group=sync_group,
            pre_end_pause=pre_end_pause,
            video_overlap=video_overlap,
            cache_manager=cache_manager if roku_host else None,
            media_source_prefix=config.get(CONF_MEDIA_SOURCE_URI, "") if roku_host else "",
            base_folder=config.get(CONF_BASE_FOLDER, "") if roku_host else "",
        )
        session_manager.start(target_entity_id, hass, coro)
        _LOGGER.info(
            "mirror_to_cast: started for %s (sync_group=%s)", target_entity_id, sync_group
        )

    async def handle_stop_cast_slideshow(call):
        """Stop one or all cast sessions, and dismiss xcast on Roku devices."""
        from homeassistant.helpers.aiohttp_client import async_get_clientsession
        from yarl import URL as YarlURL

        domain_data = hass.data.get(DOMAIN, {})
        target_entity_id = call.data.get("media_player_entity_id")

        # Search ALL instances for the matching active session (not just the first),
        # so this works regardless of which media_index instance started the slideshow.
        stopped = False
        for entry_data in domain_data.values():
            if not isinstance(entry_data, dict):
                continue
            session_manager = entry_data.get("cast_session_manager")
            if session_manager is None:
                continue
            if target_entity_id:
                if session_manager.is_active(target_entity_id):
                    session_manager.stop(target_entity_id)
                    stopped = True
                    break
            else:
                session_manager.stop_all()
                stopped = True

        if not stopped:
            _LOGGER.warning(
                "stop_cast_slideshow: no active cast session found for %s",
                target_entity_id or "any",
            )

        # For Roku targets, send a Home keypress to dismiss xcast from the TV.
        # Cancelling the HA task only stops new pushes — the Roku's xcast app
        # keeps playing the last item until we explicitly navigate away.
        # Only press Home if XCast Receiver is currently the active app; if the
        # user has already switched to Netflix / YouTube / etc. we must not
        # interrupt them.
        if target_entity_id:
            roku_host = _get_roku_host(hass, target_entity_id)
            if roku_host:
                entity_state = hass.states.get(target_entity_id)
                xcast_active = bool(
                    entity_state
                    and entity_state.attributes.get("app_name") == "XCast Receiver"
                )
                if xcast_active:
                    ecp_url = YarlURL(f"http://{roku_host}:8060/keypress/Home")
                    session = async_get_clientsession(hass)
                    try:
                        async with session.post(ecp_url, data=b"") as resp:
                            _LOGGER.info(
                                "stop_cast_slideshow: Home keypress → %s (%s) HTTP %s",
                                target_entity_id, roku_host, resp.status,
                            )
                    except Exception as exc:  # noqa: BLE001
                        _LOGGER.warning(
                            "stop_cast_slideshow: Home keypress failed for %s: %s",
                            target_entity_id, exc,
                        )
                else:
                    _LOGGER.debug(
                        "stop_cast_slideshow: skipping Home keypress for %s"
                        " — XCast Receiver is not the active app (app_name=%r)",
                        target_entity_id,
                        entity_state.attributes.get("app_name") if entity_state else None,
                    )

    # Register all services
    hass.services.async_register(
        DOMAIN,
        SERVICE_CHECK_FILE_EXISTS,
        handle_check_file_exists,
        schema=vol.Schema({
            vol.Optional("file_path"): cv.string,
            vol.Optional("media_source_uri"): cv.string,
        }, extra=vol.ALLOW_EXTRA),
        supports_response=SupportsResponse.ONLY,
    )
    
    hass.services.async_register(
        DOMAIN,
        SERVICE_INSTALL_LIBMEDIAINFO,
        handle_install_libmediainfo,
        supports_response=SupportsResponse.ONLY,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_STREAM_URL,
        handle_get_stream_url,
        schema=vol.Schema({
            vol.Optional("file_id"): vol.Coerce(int),
            vol.Optional("path_contains"): cv.string,
            vol.Optional("ttl", default=3600): vol.All(vol.Coerce(int), vol.Range(min=60, max=86400)),
        }, extra=vol.ALLOW_EXTRA),
        supports_response=SupportsResponse.ONLY,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_ROKU_ECP_CAST,
        handle_roku_ecp_cast,
        schema=vol.Schema({
            vol.Required("roku_entity_id"): cv.entity_id,
            vol.Optional("file_id"): vol.Coerce(int),
            vol.Optional("file_path"): cv.string,
            vol.Optional("media_source_uri"): cv.string,
            vol.Optional("path_contains"): cv.string,
            vol.Optional("ttl", default=3600): vol.All(vol.Coerce(int), vol.Range(min=60, max=86400)),
            vol.Optional("start_position_seconds"): vol.Coerce(float),
        }, extra=vol.ALLOW_EXTRA),
        supports_response=SupportsResponse.ONLY,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_STOP_CAST,
        handle_stop_cast,
        schema=vol.Schema({
            vol.Required("roku_entity_id"): cv.entity_id,
        }),
        supports_response=SupportsResponse.OPTIONAL,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_ROKU_ECP_QUERY,
        handle_roku_ecp_query,
        schema=vol.Schema({
            vol.Required("roku_entity_id"): cv.entity_id,
        }, extra=vol.ALLOW_EXTRA),
        supports_response=SupportsResponse.ONLY,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_ROKU_ECP_KEYPRESS,
        handle_roku_ecp_keypress,
        schema=vol.Schema({
            vol.Required("roku_entity_id"): cv.entity_id,
            vol.Required("keyname"): cv.string,
        }, extra=vol.ALLOW_EXTRA),
        supports_response=SupportsResponse.OPTIONAL,
    )

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
        SERVICE_GET_RELATED_FILES,
        handle_get_related_files,
        schema=SERVICE_GET_RELATED_FILES_SCHEMA,
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

    hass.services.async_register(
        DOMAIN,
        SERVICE_RESTORE_DELETED_FILES,
        handle_restore_deleted_files,
        schema=SERVICE_RESTORE_DELETED_FILES_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_CLEANUP_DATABASE,
        handle_cleanup_database,
        schema=vol.Schema({
            vol.Optional("dry_run", default=True): cv.boolean,
        }, extra=vol.ALLOW_EXTRA),
        supports_response=SupportsResponse.ONLY,
    )
    
    hass.services.async_register(
        DOMAIN,
        SERVICE_UPDATE_BURST_METADATA,
        handle_update_burst_metadata,
        schema=vol.Schema({
            vol.Required("burst_files"): vol.All(cv.ensure_list, [cv.string]),
            vol.Required("favorited_files"): vol.All(cv.ensure_list, [cv.string]),
        }, extra=vol.ALLOW_EXTRA),
        supports_response=SupportsResponse.ONLY,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_INDEX_BURST_GROUPS,
        handle_index_burst_groups,
        schema=vol.Schema({
            vol.Optional("folder"): cv.string,
            vol.Optional("time_window_seconds", default=10): vol.All(vol.Coerce(int), vol.Range(min=1, max=300)),
            vol.Optional("location_tolerance_meters", default=50): vol.All(vol.Coerce(int), vol.Range(min=0, max=1000)),
            vol.Optional("min_group_size", default=2): vol.All(cv.positive_int, vol.Range(min=2)),
            vol.Optional("overwrite_existing", default=True): cv.boolean,
        }, extra=vol.ALLOW_EXTRA),
        supports_response=SupportsResponse.ONLY,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_FIND_DUPLICATE_FILES,
        handle_find_duplicate_files,
        schema=vol.Schema({
            vol.Optional("folder"): cv.string,
            vol.Optional("prefer_folders"): cv.string,
            vol.Optional("dry_run", default=True): cv.boolean,
            vol.Optional("auto_delete", default=False): cv.boolean,
        }, extra=vol.ALLOW_EXTRA),
        supports_response=SupportsResponse.ONLY,
    )

    # --- Sync state services (cross-device queue sharing) ---

    async def handle_update_sync_state(call):
        """Write sync state for a shared queue group and fire a sync event."""
        import json as _json
        cache_manager = _get_instance_data(hass, call)["cache_manager"]

        sync_group = call.data["sync_group"]
        queue = call.data["queue"]
        current_index = call.data["current_index"]

        # Parse session_override and config_fields from JSON strings (sent by the card).
        raw_session_override = call.data.get("session_override")
        raw_config_fields = call.data.get("config_fields")
        try:
            session_override = _json.loads(raw_session_override) if raw_session_override else None
        except (ValueError, TypeError):
            session_override = None
        try:
            config_fields = _json.loads(raw_config_fields) if raw_config_fields else None
        except (ValueError, TypeError):
            config_fields = None

        # Store and broadcast the full queue so current_index always lines up with the
        # correct item. A previous 20-item tail-trim (queue[-20:]) caused current_index
        # to point to a completely different item (e.g. the 2nd-to-last photo instead
        # of the 2nd photo), making followers show wrong content after a filter change.
        await cache_manager.upsert_sync_state(
            sync_group, queue, current_index,
            session_override=session_override,
            config_fields=config_fields,
        )

        # Fire event on the HA bus so all subscribed cards/followers receive it immediately.
        # Services are callable by any authenticated user; the integration fires the event
        # as the system so non-admin users can participate in sync sessions.
        hass.bus.async_fire(
            EVENT_SYNC_UPDATED,
            {
                "sync_group": sync_group,
                "queue": queue,
                "current_index": current_index,
                "source_card_id": call.data.get("source_card_id", ""),
                "is_paused": call.data.get("is_paused"),
                "pause_intent": call.data.get("pause_intent", False),
                "cast_seek_position": call.data.get("cast_seek_position"),
                "current_metadata": call.data.get("current_metadata"),
                "written_at": call.data.get("written_at", 0),
                "session_override": raw_session_override,
                "config_fields": raw_config_fields,
            },
        )
        _LOGGER.debug("Sync state updated for group '%s', index %d", sync_group, current_index)
        return {"sync_group": sync_group, "current_index": current_index, "queue_size": len(queue)}

    async def handle_get_sync_state(call):
        """Return the current sync state for a shared queue group."""
        import json as _json
        cache_manager = _get_instance_data(hass, call)["cache_manager"]

        sync_group = call.data["sync_group"]
        state = await cache_manager.get_sync_state(sync_group)
        if state is None:
            return {"sync_group": sync_group, "found": False, "queue": [], "current_index": 0}
        # Re-serialize session_override and config_fields as JSON strings so the card
        # can parse them the same way it parses HA bus event payloads.
        return {
            **state,
            "found": True,
            "session_override": _json.dumps(state["session_override"]) if state.get("session_override") is not None else None,
            "config_fields": _json.dumps(state["config_fields"]) if state.get("config_fields") is not None else None,
        }

    hass.services.async_register(
        DOMAIN,
        SERVICE_UPDATE_SYNC_STATE,
        handle_update_sync_state,
        schema=vol.Schema({
            vol.Required("sync_group"): cv.string,
            vol.Required("queue"): vol.All(cv.ensure_list, [cv.string]),
            vol.Required("current_index"): vol.All(int, vol.Range(min=0)),
        }, extra=vol.ALLOW_EXTRA),
        supports_response=SupportsResponse.OPTIONAL,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_SYNC_STATE,
        handle_get_sync_state,
        schema=vol.Schema({
            vol.Required("sync_group"): cv.string,
        }, extra=vol.ALLOW_EXTRA),
        supports_response=SupportsResponse.OPTIONAL,
    )

    # ── WebSocket command: subscribe to sync events for a group ─────────────────
    # Non-admin dashboard users cannot use the generic subscribe_events WebSocket
    # command for custom integration events. We register our own command so that
    # any authenticated user (admin or not) can subscribe to sync updates for a
    # specific group.
    from homeassistant.components import websocket_api

    @websocket_api.websocket_command({
        "type": "media_index/subscribe_sync",
        "sync_group": str,
    })
    @websocket_api.async_response
    async def handle_ws_subscribe_sync(hass, connection, msg):
        """Stream sync-state updates for one shared-queue group to this connection."""
        from homeassistant.core import callback as ha_callback

        sync_group = msg["sync_group"]

        @ha_callback
        def forward_event(event):
            if event.data.get("sync_group") != sync_group:
                return
            connection.send_message(
                websocket_api.event_message(msg["id"], event.data)
            )

        unsubscribe = hass.bus.async_listen(EVENT_SYNC_UPDATED, forward_event)
        connection.subscriptions[msg["id"]] = unsubscribe
        connection.send_result(msg["id"])

    websocket_api.async_register_command(hass, handle_ws_subscribe_sync)

    hass.services.async_register(
        DOMAIN,
        SERVICE_START_CAST_SLIDESHOW,
        handle_start_cast_slideshow,
        schema=SERVICE_START_CAST_SLIDESHOW_SCHEMA,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_STOP_CAST_SLIDESHOW,
        handle_stop_cast_slideshow,
        schema=SERVICE_STOP_CAST_SLIDESHOW_SCHEMA,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_MIRROR_TO_CAST,
        handle_mirror_to_cast,
        schema=SERVICE_MIRROR_TO_CAST_SCHEMA,
    )

    _LOGGER.info("Media Index services registered")


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.info("Unloading Media Index integration")
    
    # Stop file watcher if running
    watcher = hass.data[DOMAIN][entry.entry_id].get("watcher")
    if watcher:
        watcher.stop_watching()

    # Stop any active cast sessions
    cast_session_manager = hass.data[DOMAIN][entry.entry_id].get("cast_session_manager")
    if cast_session_manager:
        cast_session_manager.stop_all()
    
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


