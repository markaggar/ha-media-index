"""HTTP streaming view for media files — PoC for Roku ECP cast testing.

Provides a short HMAC-signed URL (~80 chars) that the Roku can use as an
ECP contentId, bypassing the 255-char limit imposed by HA's authSig JWTs.

Typical URL (with filename hint for Roku MIME detection):
  http://10.0.0.26:8123/api/media_index/stream/1234/photo.jpg?t=abc1def2ghi3jkl4&exp=1746999999

Security model:
- requires_auth=False is intentional; the HMAC token is the auth mechanism.
- Token is HMAC-SHA256(stream_secret, "{file_id}:{exp}"), truncated to 16 hex chars.
- stream_secret is generated once per HA boot with os.urandom(32).
- Tokens expire after ttl seconds (default 3600).
"""
import hashlib
import hmac
import io
import logging
import mimetypes
import os
import time

from PIL import Image, ImageOps

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


# Maximum image dimensions served to Roku via xcast.
# The Roku's xcast JPEG decoder cannot handle images wider than ~4K; large
# Nikon D5100 files (4928×3264) render as grey.  The phone's xcast app
# re-encodes through Android's JPEG library which also normalises Huffman/
# quantization tables — both effects are reproduced here via Pillow.
_ROKU_MAX_W = 3840
_ROKU_MAX_H = 2160


def _transcode_jpeg_for_roku(file_path: str) -> bytes:
    """Transcode a JPEG for Roku xcast compatibility.

    - Applies EXIF orientation (exif_transpose) so pixel dimensions match the
      logical (DB-stored) w/h sent to Roku via ECP, preventing stretch distortion.
    - Re-encodes with standard Pillow quantization / Huffman tables that the
      Roku's hardware JPEG decoder handles reliably.
    - Downscales to 4K max when the original exceeds Roku's decoder limit.
    Returns raw JFIF JPEG bytes (no EXIF/XMP metadata).
    """
    with Image.open(file_path) as raw:
        # Apply EXIF orientation so the pixel data matches the logical
        # dimensions stored in the DB (which the caller passes as w/h to
        # Roku's ECP).  Without this, a phone photo physically stored as
        # 1920×2560 with EXIF "rotate 90°" is served in portrait while Roku
        # is told w=2560&h=1920 — causing horizontal stretch distortion.
        img = ImageOps.exif_transpose(raw)

    if img.mode in ('RGBA', 'P'):
        img = img.convert('RGB')
    elif img.mode not in ('RGB', 'L'):
        img = img.convert('RGB')
    # thumbnail() is a no-op when already within bounds
    img.thumbnail((_ROKU_MAX_W, _ROKU_MAX_H), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format='JPEG', quality=92, optimize=False)
    return buf.getvalue()


def get_display_dimensions(file_path: str) -> tuple[int, int]:
    """Return (width, height) of *file_path* after applying EXIF orientation.

    Uses the same ``exif_transpose`` + thumbnail pipeline as
    ``_transcode_jpeg_for_roku`` so the returned dimensions always match what
    Roku will receive in the served JPEG.  This is a blocking call; use
    ``run_in_executor`` when calling from an async context.
    """
    with Image.open(file_path) as raw:
        img = ImageOps.exif_transpose(raw)
    img.thumbnail((_ROKU_MAX_W, _ROKU_MAX_H), Image.LANCZOS)
    return img.size  # (width, height)


def _make_token(secret: bytes, file_id: int, exp: int) -> str:
    """Return a 16-char hex HMAC token for (file_id, exp)."""
    message = f"{file_id}:{exp}".encode()
    return hmac.new(secret, message, hashlib.sha256).hexdigest()[:16]


def generate_stream_url(hass: HomeAssistant, file_id: int, ttl: int = 3600, filename: str = "") -> str:
    """Return a signed stream URL for *file_id* that expires in *ttl* seconds.

    The URL is always well under 255 chars, making it suitable for use as a
    Roku ECP contentId parameter.

    *filename* is appended to the path (e.g. "photo.jpg", "video.mp4") so that
    Roku's URL-extension-based MIME detection works correctly.  The filename is
    purely cosmetic — the view ignores it and looks up the real path by file_id.
    """
    secret: bytes = hass.data.get(f"{DOMAIN}.stream_secret", b"")
    exp = int(time.time()) + ttl
    token = _make_token(secret, file_id, exp)

    # Determine base URL: prefer HA's configured internal_url, fall back to api config
    base_url: str = hass.config.internal_url or ""
    if not base_url:
        try:
            if hass.config.api:
                scheme = "https" if hass.config.api.use_ssl else "http"
                base_url = f"{scheme}://{hass.config.api.local_ip}:{hass.config.api.port}"
        except Exception:
            base_url = "http://localhost:8123"

    base_url = base_url.rstrip("/")
    path = f"/api/media_index/stream/{file_id}"
    if filename:
        path = f"{path}/{filename}"
    return f"{base_url}{path}?t={token}&exp={exp}"


class MediaIndexStreamView(HomeAssistantView):
    """Serve media files from their database filesystem paths.

    Authentication is a short-lived HMAC-signed token embedded in the URL.
    requires_auth=False is intentional — the Roku has no HA session cookie.
    Registered for both /stream/{file_id} and /stream/{file_id}/{filename} so
    Roku's URL-extension MIME detection works (filename is ignored by the handler).
    """

    url = "/api/media_index/stream/{file_id}"
    name = "api:media_index:stream"
    extra_urls = ["/api/media_index/stream/{file_id}/{filename}"]
    requires_auth = False

    async def get(self, request: web.Request, file_id: str, filename: str = "") -> web.Response:
        """Handle GET request for a media stream."""
        hass: HomeAssistant = request.app["hass"]

        # --- Validate file_id is an integer ---
        try:
            fid = int(file_id)
        except ValueError:
            return web.Response(status=400, text="Invalid file_id")

        # --- Validate token ---
        params = request.rel_url.query
        token = params.get("t", "")
        exp_str = params.get("exp", "0")

        try:
            exp = int(exp_str)
        except ValueError:
            return web.Response(status=400, text="Invalid exp parameter")

        _LOGGER.debug("Stream request: file_id=%s filename=%s from %s", file_id, filename, request.remote)

        if time.time() > exp:
            _LOGGER.warning("Stream request rejected: token expired (exp=%s)", exp_str)
            return web.Response(status=403, text="Token expired")

        secret: bytes = hass.data.get(f"{DOMAIN}.stream_secret", b"")
        expected = _make_token(secret, fid, exp)
        if not hmac.compare_digest(token, expected):
            _LOGGER.warning("Stream request rejected: invalid token for file_id=%s", fid)
            return web.Response(status=403, text="Invalid token")

        # --- Find file path from any registered CacheManager instance ---
        file_path: str | None = None
        for key, entry_data in hass.data.get(DOMAIN, {}).items():
            if not isinstance(entry_data, dict):
                continue
            cache_manager = entry_data.get("cache_manager")
            if cache_manager is None:
                continue
            try:
                row = await cache_manager.get_file_by_id(fid)
                if row:
                    file_path = row.get("path")
                    break
            except Exception as err:
                _LOGGER.debug("Error querying cache_manager for file %d: %s", fid, err)

        if not file_path:
            return web.Response(status=404, text="File not found in database")

        # --- Validate file exists on disk ---
        exists = await hass.async_add_executor_job(os.path.exists, file_path)
        if not exists:
            _LOGGER.warning("Stream requested but file not on disk: %s", file_path)
            return web.Response(status=404, text="File not found on disk")

        # --- Determine MIME type ---
        # mimetypes on Linux is case-sensitive; .JPG may not be in the DB
        _MIME_FALLBACK = {
            '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
            '.png': 'image/png',  '.gif': 'image/gif',
            '.webp': 'image/webp', '.bmp': 'image/bmp',
            '.mp4': 'video/mp4', '.mov': 'video/quicktime',
            '.avi': 'video/x-msvideo', '.mkv': 'video/x-matroska',
        }
        mime_type, _ = mimetypes.guess_type(file_path)
        if not mime_type:
            _ext = os.path.splitext(file_path)[1].lower()
            mime_type = _MIME_FALLBACK.get(_ext, 'application/octet-stream')

        _LOGGER.info("Streaming file_id=%d path=%s mime=%s", fid, file_path, mime_type)

        # For JPEG images: transcode via Pillow to ensure Roku xcast compatibility.
        # This re-encodes with standard Huffman/quantization tables, strips oversized
        # EXIF blocks (GPS-tagged files had ~48KB APP1 causing grey screens), and
        # downscales anything wider than 3840px (Nikon D5100 4928×3264 exceeded the
        # Roku decoder limit).  The phone's xcast app does the same via Android's
        # JPEG library; this matches that behaviour.
        if mime_type in ('image/jpeg', 'image/jpg'):
            try:
                clean = await hass.async_add_executor_job(
                    _transcode_jpeg_for_roku, file_path
                )
                _LOGGER.info(
                    "JPEG transcode: %s → %d bytes for file_id=%d",
                    file_path.rsplit('/', 1)[-1], len(clean), fid,
                )
                return web.Response(
                    body=clean,
                    content_type='image/jpeg',
                )
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning(
                    "JPEG transcode failed for file_id=%d (%s): %s — serving raw",
                    fid, file_path, err,
                )
                # Fall through to FileResponse below

        # aiohttp FileResponse handles Range headers (206 Partial Content) automatically,
        # which is required by Roku for video seeking.
        return web.FileResponse(
            path=file_path,
            headers={"Content-Type": mime_type},
        )
