"""Config flow for Media Index integration."""
import logging
import os
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .const import (
    DOMAIN,
    CONF_BASE_FOLDER,
    CONF_MEDIA_SOURCE_URI,
    CONF_WATCHED_FOLDERS,
    CONF_SCAN_ON_STARTUP,
    CONF_SCAN_SCHEDULE,
    CONF_EXTRACT_EXIF,
    CONF_GEOCODE_ENABLED,
    CONF_GEOCODE_PRECISION,
    CONF_GEOCODE_NATIVE_LANGUAGE,
    CONF_MAX_STARTUP_TIME,
    CONF_CONCURRENT_SCANS,
    CONF_BATCH_SIZE,
    CONF_CACHE_MAX_AGE,
    CONF_AUTO_INSTALL_LIBMEDIAINFO,
    DEFAULT_BASE_FOLDER,
    DEFAULT_SCAN_ON_STARTUP,
    DEFAULT_SCAN_SCHEDULE,
    DEFAULT_EXTRACT_EXIF,
    DEFAULT_GEOCODE_ENABLED,
    DEFAULT_GEOCODE_PRECISION,
    DEFAULT_GEOCODE_NATIVE_LANGUAGE,
    DEFAULT_MAX_STARTUP_TIME,
    DEFAULT_CONCURRENT_SCANS,
    DEFAULT_BATCH_SIZE,
    DEFAULT_CACHE_MAX_AGE,
    DEFAULT_AUTO_INSTALL_LIBMEDIAINFO,
    SCAN_SCHEDULES,
)

_LOGGER = logging.getLogger(__name__)


class MediaIndexConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Media Index."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Validate base folder exists
            base_folder = user_input[CONF_BASE_FOLDER]

            if not await self.hass.async_add_executor_job(os.path.isdir, base_folder):
                errors["base"] = "folder_not_found"
            else:
                # Parse watched folders from comma-separated string
                watched_folders_str = user_input.get(CONF_WATCHED_FOLDERS, "")
                if isinstance(watched_folders_str, str):
                    watched_folders = [f.strip() for f in watched_folders_str.split(",") if f.strip()]
                else:
                    watched_folders = watched_folders_str
                
                user_input[CONF_WATCHED_FOLDERS] = watched_folders

                # Create entry
                await self.async_set_unique_id(f"media_index_{base_folder}")
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=f"Media Index ({base_folder})",
                    data=user_input,
                )

        # Show form
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_BASE_FOLDER, default=DEFAULT_BASE_FOLDER
                    ): str,
                    vol.Optional(
                        CONF_MEDIA_SOURCE_URI, default=""
                    ): str,
                    vol.Optional(
                        CONF_WATCHED_FOLDERS, default=""
                    ): str,
                    vol.Optional(
                        CONF_SCAN_ON_STARTUP, default=DEFAULT_SCAN_ON_STARTUP
                    ): bool,
                    vol.Optional(
                        CONF_SCAN_SCHEDULE, default=DEFAULT_SCAN_SCHEDULE
                    ): vol.In(SCAN_SCHEDULES),
                    vol.Optional(
                        CONF_EXTRACT_EXIF, default=DEFAULT_EXTRACT_EXIF
                    ): bool,
                    vol.Optional(
                        CONF_GEOCODE_ENABLED, default=DEFAULT_GEOCODE_ENABLED
                    ): bool,
                    vol.Optional(
                        CONF_GEOCODE_PRECISION, default=DEFAULT_GEOCODE_PRECISION
                    ): vol.All(vol.Coerce(int), vol.Range(min=2, max=6)),
                    vol.Optional(
                        CONF_GEOCODE_NATIVE_LANGUAGE, default=DEFAULT_GEOCODE_NATIVE_LANGUAGE
                    ): bool,
                    vol.Optional(
                        CONF_MAX_STARTUP_TIME, default=DEFAULT_MAX_STARTUP_TIME
                    ): vol.All(vol.Coerce(int), vol.Range(min=5, max=300)),
                    vol.Optional(
                        CONF_CONCURRENT_SCANS, default=DEFAULT_CONCURRENT_SCANS
                    ): vol.All(vol.Coerce(int), vol.Range(min=1, max=10)),
                    vol.Optional(
                        CONF_AUTO_INSTALL_LIBMEDIAINFO, default=DEFAULT_AUTO_INSTALL_LIBMEDIAINFO
                    ): bool,
                    vol.Optional(
                        CONF_BATCH_SIZE, default=DEFAULT_BATCH_SIZE
                    ): vol.All(vol.Coerce(int), vol.Range(min=10, max=1000)),
                    vol.Optional(
                        CONF_CACHE_MAX_AGE, default=DEFAULT_CACHE_MAX_AGE
                    ): vol.All(vol.Coerce(int), vol.Range(min=1, max=365)),
                }
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Get the options flow for this handler."""
        return MediaIndexOptionsFlow()


class MediaIndexOptionsFlow(config_entries.OptionsFlow):
    """Handle options flow for Media Index."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            # Parse watched folders from comma-separated string
            watched_folders_str = user_input.get(CONF_WATCHED_FOLDERS, "")
            if isinstance(watched_folders_str, str):
                watched_folders = [f.strip() for f in watched_folders_str.split(",") if f.strip()]
            else:
                watched_folders = watched_folders_str
            
            user_input[CONF_WATCHED_FOLDERS] = watched_folders

            # Update config entry options
            return self.async_create_entry(title="", data=user_input)

        # Get current values (options take precedence over data)
        current_media_source_uri = self.config_entry.options.get(
            CONF_MEDIA_SOURCE_URI,
            self.config_entry.data.get(CONF_MEDIA_SOURCE_URI, ""),
        )
        current_watched = self.config_entry.options.get(
            CONF_WATCHED_FOLDERS,
            self.config_entry.data.get(CONF_WATCHED_FOLDERS, []),
        )
        # Convert list to comma-separated string for display
        if isinstance(current_watched, list):
            current_watched_str = ", ".join(current_watched)
        else:
            current_watched_str = current_watched

        current_schedule = self.config_entry.options.get(
            CONF_SCAN_SCHEDULE,
            self.config_entry.data.get(CONF_SCAN_SCHEDULE, DEFAULT_SCAN_SCHEDULE),
        )
        current_exif = self.config_entry.options.get(
            CONF_EXTRACT_EXIF,
            self.config_entry.data.get(CONF_EXTRACT_EXIF, DEFAULT_EXTRACT_EXIF),
        )
        current_geocode = self.config_entry.options.get(
            CONF_GEOCODE_ENABLED,
            self.config_entry.data.get(CONF_GEOCODE_ENABLED, DEFAULT_GEOCODE_ENABLED),
        )
        current_precision = self.config_entry.options.get(
            CONF_GEOCODE_PRECISION,
            self.config_entry.data.get(CONF_GEOCODE_PRECISION, DEFAULT_GEOCODE_PRECISION),
        )
        current_native_language = self.config_entry.options.get(
            CONF_GEOCODE_NATIVE_LANGUAGE,
            self.config_entry.data.get(CONF_GEOCODE_NATIVE_LANGUAGE, DEFAULT_GEOCODE_NATIVE_LANGUAGE),
        )
        current_max_startup = self.config_entry.options.get(
            CONF_MAX_STARTUP_TIME,
            self.config_entry.data.get(CONF_MAX_STARTUP_TIME, DEFAULT_MAX_STARTUP_TIME),
        )
        current_concurrent = self.config_entry.options.get(
            CONF_CONCURRENT_SCANS,
            self.config_entry.data.get(CONF_CONCURRENT_SCANS, DEFAULT_CONCURRENT_SCANS),
        )
        current_batch = self.config_entry.options.get(
            CONF_BATCH_SIZE,
            self.config_entry.data.get(CONF_BATCH_SIZE, DEFAULT_BATCH_SIZE),
        )
        current_cache_age = self.config_entry.options.get(
            CONF_CACHE_MAX_AGE,
            self.config_entry.data.get(CONF_CACHE_MAX_AGE, DEFAULT_CACHE_MAX_AGE),
        )
        current_auto_install = self.config_entry.options.get(
            CONF_AUTO_INSTALL_LIBMEDIAINFO,
            self.config_entry.data.get(CONF_AUTO_INSTALL_LIBMEDIAINFO, DEFAULT_AUTO_INSTALL_LIBMEDIAINFO),
        )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_MEDIA_SOURCE_URI, default=current_media_source_uri
                    ): str,
                    vol.Optional(
                        CONF_WATCHED_FOLDERS, default=current_watched_str
                    ): str,
                    vol.Optional(
                        CONF_SCAN_SCHEDULE, default=current_schedule
                    ): vol.In(SCAN_SCHEDULES),
                    vol.Optional(
                        CONF_EXTRACT_EXIF, default=current_exif
                    ): bool,
                    vol.Optional(
                        CONF_GEOCODE_ENABLED, default=current_geocode
                    ): bool,
                    vol.Optional(
                        CONF_GEOCODE_PRECISION, default=current_precision
                    ): vol.All(vol.Coerce(int), vol.Range(min=2, max=6)),
                    vol.Optional(
                        CONF_GEOCODE_NATIVE_LANGUAGE, default=current_native_language
                    ): bool,
                    vol.Optional(
                        CONF_MAX_STARTUP_TIME, default=current_max_startup
                    ): vol.All(vol.Coerce(int), vol.Range(min=5, max=300)),
                    vol.Optional(
                        CONF_CONCURRENT_SCANS, default=current_concurrent
                    ): vol.All(vol.Coerce(int), vol.Range(min=1, max=10)),
                    vol.Optional(
                        CONF_BATCH_SIZE, default=current_batch
                    ): vol.All(vol.Coerce(int), vol.Range(min=10, max=1000)),
                    vol.Optional(
                        CONF_CACHE_MAX_AGE, default=current_cache_age
                    ): vol.All(vol.Coerce(int), vol.Range(min=1, max=365)),
                    vol.Optional(
                        CONF_AUTO_INSTALL_LIBMEDIAINFO, default=current_auto_install
                    ): bool,
                }
            ),
        )

