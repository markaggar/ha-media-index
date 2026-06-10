"""Cast session management and transport for Media Index.

Primary mode: mirror_to_cast — the media card drives navigation and the TV
follows in real-time via media_index.sync_updated events.

Secondary mode: start_cast_slideshow — autonomous random-batch loop with
configurable interval, no card required.

Current transport: HaMediaPlayerTransport (media_player.play_media).
Works for LG WebOS and any other HA media_player entity.

"""
import asyncio
import logging

_LOGGER = logging.getLogger(__name__)

# Event fired by update_sync_state service — mirrors const.EVENT_SYNC_UPDATED
# Imported here directly to avoid a circular import with __init__.py
_EVENT_SYNC_UPDATED = "media_index.sync_updated"


def _get_roku_host(hass, entity_id: str) -> str | None:
    """Return the Roku device host IP for *entity_id*, or None if not a Roku.

    Walks entity → device → config_entry to find a 'roku' integration entry
    and returns the 'host' value from its config data.
    """
    from homeassistant.helpers import entity_registry as er, device_registry as dr

    entity_reg = er.async_get(hass)
    device_reg = dr.async_get(hass)
    entity_entry = entity_reg.async_get(entity_id)
    if entity_entry and entity_entry.device_id:
        device = device_reg.async_get(entity_entry.device_id)
        if device:
            for ceid in device.config_entries:
                ce = hass.config_entries.async_get_entry(ceid)
                if ce and ce.domain == "roku":
                    return ce.data.get("host")
    return None


class CastSessionManager:
    """Manages in-memory asyncio Tasks for active cast sessions.

    One session per target entity_id — starting a second session on the same
    target cancels the first, preventing orphaned tasks.

    Sessions are fire-and-forget: they do NOT survive HA restarts.
    Call stop_all() from async_unload_entry to clean up on integration unload.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, asyncio.Task] = {}
        self._update_callbacks: list = []

    def register_update_callback(self, callback) -> None:
        """Register a callable to be invoked whenever session state changes."""
        self._update_callbacks.append(callback)

    def _notify_update(self) -> None:
        """Invoke all registered callbacks (e.g. to push sensor state update)."""
        for cb in self._update_callbacks:
            try:
                cb()
            except Exception:  # noqa: BLE001
                pass

    def active_targets(self) -> list[str]:
        """Return entity_ids that currently have a running cast session."""
        return [t for t, task in self._sessions.items() if not task.done()]

    def start(self, target: str, hass, coro) -> None:
        """Cancel any prior session for *target* and start a new one."""
        existing = self._sessions.get(target)
        if existing and not existing.done():
            _LOGGER.info("Cast session for %s already active — cancelling before starting new one", target)
            existing.cancel()
        task = hass.async_create_task(coro, name=f"media_index_cast_{target}")
        self._sessions[target] = task
        _LOGGER.info("Cast session started for %s", target)
        self._notify_update()

    def stop(self, target: str | None = None) -> None:
        """Cancel the session for *target*, or all sessions if target is None."""
        if target is not None:
            task = self._sessions.pop(target, None)
            if task and not task.done():
                task.cancel()
                _LOGGER.info("Cast session stopped for %s", target)
            else:
                _LOGGER.debug("No active cast session found for %s", target)
        else:
            self.stop_all()
        self._notify_update()

    def stop_all(self) -> None:
        """Cancel all active sessions. Called on integration unload."""
        targets = list(self._sessions.keys())
        for target in targets:
            task = self._sessions.pop(target)
            if task and not task.done():
                task.cancel()
        if targets:
            _LOGGER.info("All cast sessions stopped (%d)", len(targets))
        self._notify_update()

    def is_active(self, target: str) -> bool:
        """Return True if there is a running session for *target*."""
        task = self._sessions.get(target)
        return task is not None and not task.done()


class HaMediaPlayerTransport:
    """Transport that pushes media to any HA media_player entity via play_media.

    Works for integrations whose media_player entity accepts arbitrary HTTP URLs
    via media_player.play_media: Chromecast (Google Cast), DLNA DMR devices,
    and Roku (via XCast — see RokuEcpTransport for the preferred Roku path).

    Does NOT work for LG WebOS, Samsung SmartThings, or other TV integrations
    whose media_player entity does not accept direct HTTP stream URLs.
    """

    async def push(self, hass, entity_id: str, media_url: str, file_type: str, item: dict | None = None) -> None:
        """Send one media item to the player. The *item* parameter is accepted
        for interface compatibility but is not used by this transport."""
        media_content_type = "image" if file_type == "image" else "video"
        _LOGGER.debug(
            "Pushing %s (%s) to %s", media_url, media_content_type, entity_id
        )
        await hass.services.async_call(
            "media_player",
            "play_media",
            {
                "entity_id": entity_id,
                "media_content_id": media_url,
                "media_content_type": media_content_type,
            },
            blocking=False,
        )


class RokuEcpTransport:
    """Transport that casts media to a Roku device via the xcast app (ECP app ID 687485).

    Generates a HMAC-signed streaming URL and POSTs directly to the Roku ECP
    endpoint, providing correct orientation/dimension handling and native format
    support. Falls back to the resolved HTTP URL when no DB item is available.
    """

    _XCAST_APP_NAME = "XCast Receiver"
    _XCAST_APP_ID = "687485"       # Roku channel ID for xcast
    _XCAST_LAUNCH_TIMEOUT = 10.0  # seconds to wait for xcast to start
    _XCAST_READY_PAUSE = 3.0      # pause after xcast appears active before resending;
                                  # xcast's media pipeline needs time to init after
                                  # the app is foregrounded (~0.3s is too soon)
    _XCAST_ECP_POLL_INTERVAL = 0.3  # how often to poll ECP /query/active-app

    def __init__(self, hass, roku_host: str) -> None:
        self._hass = hass
        self._roku_host = roku_host

    def _is_xcast_active(self, hass, entity_id: str) -> bool:
        """Return True if the Roku entity currently has xcast in the foreground.

        Uses HA entity state (may lag by up to the Roku polling interval).
        Prefer _is_xcast_active_ecp() for time-sensitive checks.
        """
        state = hass.states.get(entity_id)
        return bool(state and state.attributes.get("app_name") == self._XCAST_APP_NAME)

    async def _is_xcast_active_ecp(self, session) -> bool:
        """Query the Roku ECP /query/active-app endpoint directly.

        Returns True immediately based on the Roku's live state, without
        waiting for HA's entity polling cycle to catch up.
        """
        from yarl import URL as YarlURL
        try:
            url = YarlURL(f"http://{self._roku_host}:8060/query/active-app")
            async with session.get(url) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    active = self._XCAST_APP_ID in text
                    _LOGGER.debug(
                        "Roku ECP /query/active-app → xcast_active=%s (%s)",
                        active, text[:120].replace("\n", " "),
                    )
                    return active
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("Roku ECP /query/active-app failed: %s", exc)
        return False

    async def _wait_for_xcast(self, session) -> bool:
        """Poll Roku ECP directly until xcast is in the foreground or timeout.

        Uses /query/active-app rather than HA entity state so we see the
        real app switch within ~300 ms instead of waiting for HA's slow
        Roku polling cycle (up to 10-30 s).

        Returns True if xcast became active within the timeout window.
        """
        deadline = asyncio.get_running_loop().time() + self._XCAST_LAUNCH_TIMEOUT
        while asyncio.get_running_loop().time() < deadline:
            if await self._is_xcast_active_ecp(session):
                return True
            await asyncio.sleep(self._XCAST_ECP_POLL_INTERVAL)
        return False

    async def push(self, hass, entity_id: str, media_url: str, file_type: str, item: dict | None = None) -> None:
        """Send one media item to the Roku via xcast ECP.

        When *item* is a full DB row dict (with at least 'id' and 'path'), a
        HMAC-signed stream URL is generated and correct orientation/dimension
        params are applied. Otherwise falls back to *media_url* with minimal
        metadata.

        If xcast is not already running on the Roku, the first push will launch
        it.  We then wait for the entity's app_name to become "XCast Receiver"
        and resend the push so the media is actually displayed once xcast has
        fully initialised.
        """
        import os as _os
        import urllib.parse
        from yarl import URL as YarlURL
        from homeassistant.helpers.aiohttp_client import async_get_clientsession

        roku_host = self._roku_host

        # DB item's file_type is authoritative; the card's sync-event metadata
        # does not currently propagate file_type, so caller may pass "image" for
        # a video.  Override here as a safety net.
        if item is not None and item.get("file_type"):
            file_type = item["file_type"]

        if item is not None and item.get("id"):
            # Full DB row available — generate a HMAC-signed stream URL
            from .stream import generate_stream_url, get_display_dimensions

            fid = item["id"]
            file_path = item.get("path", "")
            orientation = item.get("orientation")
            ori = orientation or "normal"
            width = item.get("width")
            height = item.get("height")
            ext = _os.path.splitext(file_path)[1].lower()
            filename_hint = ("video" if file_type == "video" else "photo") + ext if ext else ""

            stream_url = generate_stream_url(hass, fid, 3600, filename=filename_hint)

            if file_type != "video":
                try:
                    ecp_w, ecp_h = await hass.async_add_executor_job(
                        get_display_dimensions, file_path
                    )
                except Exception:  # noqa: BLE001
                    ecp_w, ecp_h = None, None
            else:
                _SWAP = {"90_cw", "90_ccw"}
                ecp_w, ecp_h = (height, width) if ori in _SWAP else (width, height)
        else:
            # No DB row — use the already-resolved URL with minimal metadata
            stream_url = media_url
            file_path = ""
            ori = "normal"
            ext = _os.path.splitext(urllib.parse.urlparse(media_url).path)[1].lower()
            ecp_w, ecp_h = None, None

        _FORMAT_MAP = {
            ".jpg": "jpeg", ".jpeg": "jpeg", ".png": "png", ".gif": "gif",
            ".webp": "webp", ".bmp": "bmp", ".heic": "heic", ".tiff": "tiff",
            ".mp4": "mp4", ".mov": "mov", ".avi": "avi", ".mkv": "mkv",
            ".m4v": "m4v", ".webm": "webm", ".mpg": "mpeg", ".mpeg": "mpeg",
        }
        fmt = _FORMAT_MAP.get(ext, ext.lstrip(".") or "jpeg")

        parsed = urllib.parse.urlparse(stream_url)
        ha_host = parsed.hostname or "localhost"
        ha_port = parsed.port or 8123
        enc_url = urllib.parse.quote(stream_url, safe="")
        title = _os.path.basename(file_path) or "media"
        enc_title = urllib.parse.quote(title, safe="")

        if file_type == "video":
            _VIDEO_ROT = {"90_cw": "90.0", "90_ccw": "270.0", "180": "180.0"}
            r_val = _VIDEO_ROT.get(ori, "0.0")
            params = (
                f"title={enc_title}&mediaType=video&format={fmt}"
                f"&url={enc_url}&host={ha_host}&port={ha_port}&r={r_val}"
            )
            if ecp_w and ecp_h:
                params += f"&w={ecp_w}&h={ecp_h}"
        else:
            params = f"title={enc_title}&mediaType=image&format={fmt}&url={enc_url}"
            if ecp_w and ecp_h:
                params += f"&w={ecp_w}&h={ecp_h}"
            params += "&r=0.0&ri=0.0"  # rotation already baked in by exif_transpose

        ecp_url = YarlURL(f"http://{roku_host}:8060/input/687485?{params}", encoded=True)
        _LOGGER.debug(
            "Roku ECP push → %s  type=%s  file_id=%s  title=%s",
            roku_host,
            file_type,
            item.get("id") if item else "n/a",
            title,
        )

        # Capture whether xcast was already in the foreground BEFORE we push.
        # Use ECP directly rather than HA entity state — the entity's app_name
        # lags by the Roku polling interval (up to 10-30 s), whereas ECP
        # reflects live Roku state within ~100 ms.
        session = async_get_clientsession(hass)
        xcast_was_active = await self._is_xcast_active_ecp(session)

        try:
            async with session.post(ecp_url, data=b"") as resp:
                if resp.status != 200:
                    try:
                        body = await resp.text()
                    except Exception:  # noqa: BLE001
                        body = "(unreadable)"
                    _LOGGER.warning(
                        "Roku ECP push: HTTP %s for '%s': %s", resp.status, title, body
                    )
                else:
                    _LOGGER.debug("Roku ECP push: sent %s → HTTP 200 OK", ecp_url)
        except Exception as e:  # noqa: BLE001
            _LOGGER.error("Roku ECP push failed for %s: %s", roku_host, e)
            return

        if not xcast_was_active:
            _LOGGER.info(
                "Roku ECP: xcast was not running on %s — waiting for it to initialise",
                entity_id,
            )
            started = await self._wait_for_xcast(session)
            if started:
                # Brief pause to let xcast fully render its input pipeline.
                await asyncio.sleep(self._XCAST_READY_PAUSE)
                _LOGGER.info(
                    "Roku ECP: xcast ready on %s — resending media push", entity_id
                )
                try:
                    async with session.post(ecp_url, data=b"") as resp2:
                        _LOGGER.info(
                            "Roku ECP resend → HTTP %s for '%s'", resp2.status, title
                        )
                except Exception as e:  # noqa: BLE001
                    _LOGGER.error(
                        "Roku ECP resend failed for %s: %s", roku_host, e
                    )
            else:
                _LOGGER.warning(
                    "Roku ECP: xcast did not start within %.0fs on %s — "
                    "first item may not display",
                    self._XCAST_LAUNCH_TIMEOUT,
                    entity_id,
                )


async def _resolve_media_url(hass, media_content_id: str) -> str:
    """Resolve a media-source:// URI to a plain HTTP URL.

    Synology and other providers embed short-lived auth tokens in their URLs.
    Calling async_resolve_media fresh on every push ensures tokens are valid.

    Falls back to returning *media_content_id* unchanged if it is already an
    HTTP URL or if the media_source component is unavailable.
    """
    if not media_content_id.startswith("media-source://"):
        return media_content_id
    try:
        from homeassistant.components.media_source import async_resolve_media
        from homeassistant.components.media_player import BrowseMedia

        resolved = await async_resolve_media(hass, media_content_id, None)
        return resolved.url
    except Exception as err:  # noqa: BLE001
        _LOGGER.error(
            "Failed to resolve media URL for '%s': %s — using URI directly",
            media_content_id,
            err,
        )
        return media_content_id


async def run_cast_slideshow(
    hass,
    cache_manager,
    entity_id: str,
    transport: HaMediaPlayerTransport,
    query_params: dict,
    interval: int,
    video_overlap: int,
    sync_group: str | None,
    also_write_sync: bool,
) -> None:
    """Unattended slideshow loop — fetches random batches and pushes to TV.

    Runs until cancelled (CastSessionManager.stop() or HA unload).

    Sleeping strategy:
    - Images: sleep *interval* seconds.
    - Videos with a known duration: sleep max(1, duration - video_overlap) so
      the next item arrives before the player exits at end-of-video.
    - Videos without duration: fall back to *interval*.

    *query_params* keys match cache_manager.get_random_files() kwargs:
    folder, recursive, file_type, date_from, date_to, favorites_only,
    anniversary_month, anniversary_day, anniversary_window_days,
    priority_new_files.
    """
    _LOGGER.info(
        "Cast slideshow started → %s (interval=%ds, overlap=%ds, sync_group=%s)",
        entity_id, interval, video_overlap, sync_group,
    )
    try:
        while True:
            # Fetch a fresh 100-item batch each loop (matches card queue size)
            items = await cache_manager.get_random_files(count=100, **query_params)
            if not items:
                _LOGGER.warning(
                    "Cast slideshow: no items returned for %s — retrying in %ds",
                    entity_id, interval,
                )
                await asyncio.sleep(interval)
                continue

            for item in items:
                media_uri = item.get("media_source_uri") or item.get("path", "")
                if not media_uri:
                    _LOGGER.debug("Cast slideshow: skipping item with no URI")
                    continue

                # Resolve to a real URL (handles expiring auth tokens)
                url = await _resolve_media_url(hass, media_uri)

                # Push to TV
                file_type = item.get("file_type", "image")
                await transport.push(hass, entity_id, url, file_type, item=item)
                _LOGGER.debug("Cast slideshow → %s: sent %s", entity_id, item.get("filename", url))

                # Optionally write sync state so wall-mounted cards follow along
                if also_write_sync and sync_group:
                    try:
                        await hass.services.async_call(
                            "media_index",
                            "update_sync_state",
                            {
                                "sync_group": sync_group,
                                "queue": [media_uri],
                                "current_index": 0,
                                "is_paused": False,
                                "source_card_id": "cast_slideshow",
                                "written_at": int(__import__("time").time() * 1000),
                            },
                            blocking=False,
                        )
                    except Exception as err:  # noqa: BLE001
                        _LOGGER.warning("Cast slideshow: failed to write sync state: %s", err)

                # Calculate sleep duration
                duration = item.get("duration")
                if file_type == "video" and duration and duration > 0:
                    sleep_time = max(1, duration - video_overlap)
                    _LOGGER.debug(
                        "Cast slideshow: video duration=%.1fs, sleeping %.1fs (overlap=%ds)",
                        duration, sleep_time, video_overlap,
                    )
                else:
                    sleep_time = interval

                await asyncio.sleep(sleep_time)

    except asyncio.CancelledError:
        _LOGGER.info("Cast slideshow stopped for %s", entity_id)
        raise


async def run_mirror_cast(
    hass,
    entity_id: str,
    transport,
    sync_group: str,
    pre_end_pause: bool,
    video_overlap: int,
    cache_manager=None,
    media_source_prefix: str = "",
    base_folder: str = "",
) -> None:
    """Attended cast — TV mirrors the card's navigation in real-time.

    Listens to media_index.sync_updated events for *sync_group* and pushes
    the current item to the TV whenever the card advances/jumps.

    If *pre_end_pause* is True, schedules media_player.media_pause before a
    video ends so the TV doesn't snap back to the home screen.
    """
    _LOGGER.info(
        "Mirror cast started → %s (sync_group=%s, pre_end_pause=%s, overlap=%ds)",
        entity_id, sync_group, pre_end_pause, video_overlap,
    )

    pending_pause_task: asyncio.Task | None = None

    async def _pause_after(delay: float) -> None:
        """Sleep then pause the player."""
        await asyncio.sleep(delay)
        _LOGGER.debug("Pre-end pause: pausing %s", entity_id)
        await hass.services.async_call(
            "media_player",
            "media_pause",
            {"entity_id": entity_id},
            blocking=False,
        )

    async def _on_sync_event(event) -> None:
        nonlocal pending_pause_task

        # Cancel any pending pre-end-pause from the previous item
        if pending_pause_task and not pending_pause_task.done():
            pending_pause_task.cancel()
            pending_pause_task = None

        data = event.data
        if data.get("sync_group") != sync_group:
            return

        queue = data.get("queue", [])
        current_index = data.get("current_index", 0)
        if not queue or current_index >= len(queue):
            return

        # The queue entries are media_source_uri strings
        media_uri = queue[current_index]
        if not media_uri:
            return

        url = await _resolve_media_url(hass, media_uri)
        # Determine file type from metadata carried in the event
        current_metadata = data.get("current_metadata")
        file_type = "image"
        if current_metadata:
            try:
                import json
                meta = json.loads(current_metadata) if isinstance(current_metadata, str) else current_metadata
                if meta and meta.get("file_type"):
                    file_type = meta["file_type"]
            except Exception:  # noqa: BLE001
                pass

        # Attempt DB lookup to get full item metadata (orientation, dimensions)
        # for transports that need it (e.g. RokuEcpTransport).
        db_item = None
        if cache_manager is not None and media_source_prefix and base_folder:
            try:
                if media_uri.startswith(media_source_prefix):
                    rel = media_uri[len(media_source_prefix):].lstrip("/")
                    file_path = base_folder.rstrip("/") + "/" + rel
                    db_item = await cache_manager.get_file_by_path(file_path)
            except Exception as _db_err:  # noqa: BLE001
                _LOGGER.debug("Mirror cast: DB lookup failed for %s: %s", media_uri, _db_err)

        # The card's metadata objects don't include file_type, so current_metadata
        # rarely carries it.  Prefer the DB-authoritative value when available;
        # fall back to extension-based detection so videos aren't mis-typed as images.
        if db_item and db_item.get("file_type"):
            file_type = db_item["file_type"]
        elif file_type == "image":
            import os as _os_ft
            _VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".m4v", ".wmv", ".webm", ".mts", ".m2ts"}
            _uri_ext = _os_ft.path.splitext(media_uri.lower())[1]
            if _uri_ext in _VIDEO_EXTS:
                file_type = "video"

        await transport.push(hass, entity_id, url, file_type, item=db_item)
        _LOGGER.debug("Mirror cast → %s: sent %s", entity_id, media_uri)

        # Schedule pre-end pause for videos
        if pre_end_pause and file_type == "video" and current_metadata:
            try:
                import json
                meta = json.loads(current_metadata) if isinstance(current_metadata, str) else current_metadata
                duration = meta.get("duration") if meta else None
                if duration and float(duration) > video_overlap:
                    delay = max(1.0, float(duration) - video_overlap)
                    pending_pause_task = hass.async_create_task(
                        _pause_after(delay),
                        name=f"media_index_cast_pause_{entity_id}",
                    )
                    _LOGGER.debug(
                        "Mirror cast: scheduled pre-end pause in %.1fs for %s",
                        delay, entity_id,
                    )
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("Mirror cast: could not schedule pre-end pause: %s", err)

    unsub = hass.bus.async_listen(_EVENT_SYNC_UPDATED, _on_sync_event)
    try:
        # Keep the coroutine alive until cancelled
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        # Clean up pending pause task and event listener
        if pending_pause_task and not pending_pause_task.done():
            pending_pause_task.cancel()
        unsub()
        _LOGGER.info("Mirror cast stopped for %s (sync_group=%s)", entity_id, sync_group)
        raise
