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


class CastSessionManager:
    """Manages in-memory asyncio Tasks for active cast sessions.

    One session per target entity_id — starting a second session on the same
    target cancels the first, preventing orphaned tasks.

    Sessions are fire-and-forget: they do NOT survive HA restarts.
    Call stop_all() from async_unload_entry to clean up on integration unload.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, asyncio.Task] = {}

    def start(self, target: str, hass, coro) -> None:
        """Cancel any prior session for *target* and start a new one."""
        existing = self._sessions.get(target)
        if existing and not existing.done():
            _LOGGER.debug("Cancelling existing cast session for %s", target)
            existing.cancel()
        task = hass.async_create_task(coro, name=f"media_index_cast_{target}")
        self._sessions[target] = task
        _LOGGER.info("Cast session started for %s", target)

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

    def stop_all(self) -> None:
        """Cancel all active sessions. Called on integration unload."""
        targets = list(self._sessions.keys())
        for target in targets:
            task = self._sessions.pop(target)
            if task and not task.done():
                task.cancel()
        if targets:
            _LOGGER.info("All cast sessions stopped (%d)", len(targets))

    def is_active(self, target: str) -> bool:
        """Return True if there is a running session for *target*."""
        task = self._sessions.get(target)
        return task is not None and not task.done()


class HaMediaPlayerTransport:
    """Transport that pushes media to any HA media_player entity.

    Uses media_player.play_media — works for LG WebOS, Chromecast, and most
    other HA-integrated players.
    """

    async def push(self, hass, entity_id: str, media_url: str, file_type: str) -> None:
        """Send one media item to the player."""
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
                await transport.push(hass, entity_id, url, file_type)
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
    transport: HaMediaPlayerTransport,
    sync_group: str,
    pre_end_pause: bool,
    video_overlap: int,
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

        await transport.push(hass, entity_id, url, file_type)
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
