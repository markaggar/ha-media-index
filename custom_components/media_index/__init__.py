"""Media Index integration for Home Assistant."""
import asyncio
import logging
import os
from datetime import timedelta
from pathlib import Path

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers.event import async_track_time_interval
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
    DEFAULT_ENABLE_WATCHER,
    DEFAULT_GEOCODE_ENABLED,
    DEFAULT_GEOCODE_NATIVE_LANGUAGE,
    DEFAULT_AUTO_INSTALL_LIBMEDIAINFO,
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
    SERVICE_CLEANUP_DATABASE,
    SERVICE_UPDATE_BURST_METADATA,
    SERVICE_INSTALL_LIBMEDIAINFO,
    SERVICE_CHECK_FILE_EXISTS,
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
    vol.Optional("recursive", default=True): cv.boolean,
    vol.Optional("file_type"): vol.In(["image", "video"]),
    vol.Optional("favorites_only", default=False): cv.boolean,
    vol.Optional("date_from"): cv.string,
    vol.Optional("date_to"): cv.string,
    vol.Optional("anniversary_month"): cv.string,  # "1"-"12" or "*"
    vol.Optional("anniversary_day"): cv.string,    # "1"-"31" or "*"
    vol.Optional("anniversary_window_days", default=0): cv.positive_int,
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

# Note: SERVICE_GET_FILE_METADATA_SCHEMA defined later after _validate_path_or_uri function

SERVICE_GET_RELATED_FILES_SCHEMA = vol.Schema({
    vol.Optional("reference_path"): cv.string,
    vol.Optional("media_source_uri"): cv.string,
    vol.Required("mode"): vol.In(["burst", "anniversary"]),
    
    # Burst mode parameters
    vol.Optional("time_window_seconds", default=120): vol.All(vol.Coerce(int), vol.Range(min=10, max=3600)),
    vol.Optional("prefer_same_location", default=True): cv.boolean,
    vol.Optional("location_tolerance_meters", default=50): vol.All(vol.Coerce(int), vol.Range(min=10, max=1000)),
    
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
    vol.Optional("entity_id"): cv.entity_ids,  # Target entity (from UI)
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
    return True


def _setup_scheduled_scan(
    hass: HomeAssistant,
    entry: ConfigEntry,
    scanner: MediaScanner,
    base_folder: str,
    watched_folders: list,
    scan_schedule: str,
) -> None:
    """Setup scheduled scanning based on config.
    
    Args:
        hass: Home Assistant instance
        entry: Config entry
        scanner: MediaScanner instance
        base_folder: Base folder path
        watched_folders: List of watched folders
        scan_schedule: Schedule type (hourly/daily/weekly)
    """
    async def _scheduled_scan_callback(now):
        """Run scheduled scan if not already running."""
        # Block if pymediainfo not available
        if not hass.data[DOMAIN][entry.entry_id].get("pymediainfo_available", False):
            _LOGGER.warning(
                "⏸️ Scheduled scan SKIPPED: pymediainfo not available. "
                "Call 'media_index.install_libmediainfo' to fix."
            )
            return
        
        # Check if scan already in progress
        if scanner.is_scanning:
            _LOGGER.info(
                "Scheduled scan skipped - scan already in progress. "
                "This prevents blocking watch folders and concurrent scans."
            )
            return
        
        _LOGGER.info("Starting scheduled scan (%s) of %s", scan_schedule, base_folder)
        await scanner.scan_folder(base_folder, watched_folders)
    
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
        _LOGGER.error(
            "❌ libmediainfo system library is NOT available - video metadata extraction DISABLED!\n"
            "This usually happens after Home Assistant Core upgrades.\n"
            "Error: %s\n"
            "Automatic scanning is BLOCKED to prevent metadata loss.\n"
            "To auto-fix: Call 'media_index.install_libmediainfo' service\n"
            "Or manually SSH into Home Assistant and run: apk add --no-cache libmediainfo",
            libmediainfo_error
        )
    except Exception as e:
        # Catch-all for unexpected errors
        libmediainfo_error = str(e)
        _LOGGER.warning(
            "⚠️ Unexpected error testing libmediainfo: %s\n"
            "Assuming library is not available - blocking scans.",
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
                    # Clear Python's import cache and reload the module to pick up the newly installed library
                    import sys
                    import importlib
                    if 'pymediainfo' in sys.modules:
                        importlib.reload(sys.modules['pymediainfo'])
                    from pymediainfo import MediaInfo  # noqa: F401
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
    watcher = MediaWatcher(scanner, cache_manager, hass)
    
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
    
    # Set up platforms BEFORE starting scan so sensor exists
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    
    # Trigger initial scan AFTER Home Assistant has fully started (not during setup)
    # Use config already constructed above
    watched_folders = config.get(CONF_WATCHED_FOLDERS, [])
    
    if config.get(CONF_SCAN_ON_STARTUP, DEFAULT_SCAN_ON_STARTUP):
        async def _trigger_startup_scan(_event=None):
            """Trigger scan after Home Assistant has fully started."""
            # Block if pymediainfo not available
            if not hass.data[DOMAIN][entry.entry_id].get("pymediainfo_available", False):
                _LOGGER.warning(
                    "⏸️ Startup scan SKIPPED: pymediainfo not available. "
                    "Call 'media_index.install_libmediainfo' to fix."
                )
                return
            
            _LOGGER.info("Home Assistant started - beginning initial scan of %s (watched: %s)", base_folder, watched_folders)
            hass.async_create_task(
                scanner.scan_folder(base_folder, watched_folders),
                name=f"media_index_scan_{entry.entry_id}"
            )
        
        # Listen for Home Assistant start event to trigger scan
        from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _trigger_startup_scan)
        _LOGGER.info("Startup scan scheduled to run after Home Assistant finishes starting")
    else:
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
    if scan_schedule != SCAN_SCHEDULE_STARTUP_ONLY:
        _setup_scheduled_scan(hass, entry, scanner, base_folder, watched_folders, scan_schedule)
    
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
        config = hass.data[DOMAIN][entry_id]["config"]
        
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
            anniversary_month=call.data.get("anniversary_month"),
            anniversary_day=call.data.get("anniversary_day"),
            anniversary_window_days=call.data.get("anniversary_window_days", 0),
            favorites_only=call.data.get("favorites_only", False),
            priority_new_files=call.data.get("priority_new_files", False),
            new_files_threshold_seconds=call.data.get("new_files_threshold_seconds", 3600),
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
        entry_id = _get_entry_id_from_call(hass, call)
        cache_manager = hass.data[DOMAIN][entry_id]["cache_manager"]
        config = hass.data[DOMAIN][entry_id]["config"]
        
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
        
        items = await cache_manager.get_ordered_files(
            count=call.data.get("count", 50),
            folder=folder,
            recursive=call.data.get("recursive", True),
            file_type=call.data.get("file_type"),
            order_by=call.data.get("order_by", "date_taken"),
            order_direction=call.data.get("order_direction", "desc"),
        )
        
        # Add media_source_uri to each item if configured
        _add_media_source_uris_to_items(items, config)
        
        result = {"items": items}
        # Debug: Retrieved X ordered items (logging removed)
        return result
    
    async def handle_get_file_metadata(call):
        """Handle get_file_metadata service call."""
        entry_id = _get_entry_id_from_call(hass, call)
        cache_manager = hass.data[DOMAIN][entry_id]["cache_manager"]
        config = hass.data[DOMAIN][entry_id]["config"]
        
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
        entry_id = _get_entry_id_from_call(hass, call)
        cache_manager = hass.data[DOMAIN][entry_id]["cache_manager"]
        config = hass.data[DOMAIN][entry_id]["config"]
        
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
                _LOGGER.info("Converted media_source_uri to path: %s -> %s", media_source_uri, reference_path)
            except ValueError as e:
                _LOGGER.error("Failed to convert URI to path: %s", e)
                return {"error": str(e), "items": []}
        
        if not reference_path:
            return {"error": "Either reference_path or media_source_uri required", "items": []}
        
        sort_order = call.data.get("sort_order", "time_asc")
        
        if mode == "burst":
            # Burst detection mode
            items = await cache_manager.get_burst_photos(
                reference_path=reference_path,
                time_window_seconds=call.data.get("time_window_seconds", 120),
                prefer_same_location=call.data.get("prefer_same_location", True),
                location_tolerance_meters=call.data.get("location_tolerance_meters", 50),
                sort_order=sort_order
            )
            _LOGGER.info("Found %d burst photos for %s", len(items), reference_path)
            
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
        entry_id = _get_entry_id_from_call(hass, call)
        cache_manager = hass.data[DOMAIN][entry_id]["cache_manager"]
        config = hass.data[DOMAIN][entry_id]["config"]
        geocode_service = hass.data[DOMAIN][entry_id].get("geocode_service")
        
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
        entry_id = _get_entry_id_from_call(hass, call)
        cache_manager = hass.data[DOMAIN][entry_id]["cache_manager"]
        config = hass.data[DOMAIN][entry_id]["config"]
        
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
                _LOGGER.info("✅ Wrote rating=%d to %s", rating, file_path)
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
        
        entry_id = _get_entry_id_from_call(hass, call)
        cache_manager = hass.data[DOMAIN][entry_id]["cache_manager"]
        config = hass.data[DOMAIN][entry_id]["config"]
        
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
        
        entry_id = _get_entry_id_from_call(hass, call)
        cache_manager = hass.data[DOMAIN][entry_id]["cache_manager"]
        config = hass.data[DOMAIN][entry_id]["config"]
        
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
        entry_id = _get_entry_id_from_call(hass, call)
        cache_manager = hass.data[DOMAIN][entry_id]["cache_manager"]
        
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
                        _LOGGER.info("Removed stale entry: %s", file_path)
                
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
    
    async def handle_update_burst_metadata(call):
        """Handle update_burst_metadata service call."""
        entry_id = _get_entry_id_from_call(hass, call)
        cache_manager = hass.data[DOMAIN][entry_id]["cache_manager"]
        config = hass.data[DOMAIN][entry_id]["config"]
        
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
    
    async def handle_scan_folder(call):
        """Handle scan_folder service call."""
        entry_id = _get_entry_id_from_call(hass, call)
        
        # Block scanning if pymediainfo is not available
        if not hass.data[DOMAIN][entry_id].get("pymediainfo_available", False):
            _LOGGER.error(
                "❌ Scan BLOCKED: pymediainfo/libmediainfo is not available!\n"
                "Scanning without video metadata support will wipe existing metadata.\n"
                "Call 'media_index.install_libmediainfo' service to fix and restart Home Assistant."
            )
            return {"status": "blocked", "reason": "pymediainfo_not_available"}
        
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
    
    async def handle_check_file_exists(call):
        """Handle check_file_exists service call - lightweight filesystem check."""
        entry_id = _get_entry_id_from_call(hass, call)
        config = hass.data[DOMAIN][entry_id]["config"]
        
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


