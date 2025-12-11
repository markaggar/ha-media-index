"""Constants for Media Index integration."""
from typing import Final

DOMAIN: Final = "media_index"

# Configuration constants
CONF_BASE_FOLDER: Final = "base_folder"
CONF_MEDIA_SOURCE_URI: Final = "media_source_uri"
CONF_WATCHED_FOLDERS: Final = "watched_folders"
CONF_SCAN_ON_STARTUP: Final = "scan_on_startup"
CONF_SCAN_SCHEDULE: Final = "scan_schedule"
CONF_EXTRACT_EXIF: Final = "extract_exif"
CONF_GEOCODE_ENABLED: Final = "geocode_enabled"
CONF_GEOCODE_PRECISION: Final = "geocode_precision"
CONF_GEOCODE_NATIVE_LANGUAGE: Final = "geocode_native_language"
CONF_MAX_STARTUP_TIME: Final = "max_startup_time"
CONF_CONCURRENT_SCANS: Final = "concurrent_scans"
CONF_BATCH_SIZE: Final = "batch_size"
CONF_CACHE_MAX_AGE: Final = "cache_max_age_days"
CONF_ENABLE_WATCHER: Final = "enable_watcher"

# Defaults
DEFAULT_BASE_FOLDER: Final = "/media"
DEFAULT_SCAN_ON_STARTUP: Final = True
DEFAULT_SCAN_SCHEDULE: Final = "hourly"
DEFAULT_EXTRACT_EXIF: Final = True
DEFAULT_GEOCODE_ENABLED: Final = True
DEFAULT_GEOCODE_PRECISION: Final = 4
DEFAULT_GEOCODE_NATIVE_LANGUAGE: Final = False
DEFAULT_MAX_STARTUP_TIME: Final = 30
DEFAULT_CONCURRENT_SCANS: Final = 3
DEFAULT_BATCH_SIZE: Final = 100
DEFAULT_CACHE_MAX_AGE: Final = 90
DEFAULT_ENABLE_WATCHER: Final = True

# Scan schedule options
SCAN_SCHEDULE_STARTUP_ONLY: Final = "startup_only"
SCAN_SCHEDULE_HOURLY: Final = "hourly"
SCAN_SCHEDULE_DAILY: Final = "daily"
SCAN_SCHEDULE_WEEKLY: Final = "weekly"

SCAN_SCHEDULES: Final = [
    SCAN_SCHEDULE_STARTUP_ONLY,
    SCAN_SCHEDULE_HOURLY,
    SCAN_SCHEDULE_DAILY,
    SCAN_SCHEDULE_WEEKLY,
]

# Scan status
SCAN_STATUS_IDLE: Final = "idle"
SCAN_STATUS_SCANNING: Final = "scanning"
SCAN_STATUS_WATCHING: Final = "watching"

# File types
SUPPORTED_IMAGE_EXTENSIONS: Final = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}
SUPPORTED_VIDEO_EXTENSIONS: Final = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
SUPPORTED_EXTENSIONS: Final = SUPPORTED_IMAGE_EXTENSIONS | SUPPORTED_VIDEO_EXTENSIONS

# Database
DB_NAME: Final = "media_index.db"

# Services
SERVICE_SCAN_FOLDER: Final = "scan_folder"
SERVICE_GET_RANDOM_ITEMS: Final = "get_random_items"
SERVICE_GET_ORDERED_FILES: Final = "get_ordered_files"
SERVICE_GET_FILE_METADATA: Final = "get_file_metadata"
SERVICE_GET_RELATED_FILES: Final = "get_related_files"
SERVICE_GEOCODE_FILE: Final = "geocode_file"
SERVICE_FAVORITE_FILE: Final = "favorite_file"
SERVICE_RATE_FILE: Final = "rate_file"
SERVICE_DELETE_FILE: Final = "delete_file"
SERVICE_MOVE_FILE: Final = "move_file"
SERVICE_MARK_FOR_EDIT: Final = "mark_for_edit"
SERVICE_RESTORE_EDITED_FILES: Final = "restore_edited_files"
SERVICE_CLEANUP_DATABASE: Final = "cleanup_database"
SERVICE_UPDATE_BURST_METADATA: Final = "update_burst_metadata"

# Attributes
ATTR_SCAN_STATUS: Final = "scan_status"
ATTR_LAST_SCAN_TIME: Final = "last_scan_time"
ATTR_TOTAL_FOLDERS: Final = "total_folders"
ATTR_TOTAL_IMAGES: Final = "total_images"
ATTR_TOTAL_VIDEOS: Final = "total_videos"
ATTR_WATCHED_FOLDERS: Final = "watched_folders"
ATTR_MEDIA_PATH: Final = "media_path"
ATTR_CACHE_SIZE_MB: Final = "cache_size_mb"
ATTR_GEOCODE_ENABLED: Final = "geocode_enabled"
ATTR_GEOCODE_CACHE_ENTRIES: Final = "geocode_cache_entries"
ATTR_GEOCODE_HIT_RATE: Final = "geocode_cache_hit_rate"
ATTR_FILES_WITH_LOCATION: Final = "files_with_location"
ATTR_GEOCODE_ATTRIBUTION: Final = "geocode_attribution"

# Geocoding attribution (required by Nominatim usage policy)
# See: https://operations.osmfoundation.org/policies/nominatim/
GEOCODE_ATTRIBUTION: Final = "Location data © OpenStreetMap contributors, ODbL 1.0. https://osm.org/copyright"

