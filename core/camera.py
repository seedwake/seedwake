"""Camera frame capture from MJPEG stream."""

import base64
import logging
import time
from urllib import error, request

from core.common_types import elapsed_ms

logger = logging.getLogger(__name__)
MJPEG_READ_CHUNK = 4096
MJPEG_MAX_BYTES = 512 * 1024
JPEG_START = b"\xff\xd8"
JPEG_END = b"\xff\xd9"


def capture_camera_frame(
    stream_url: str,
    *,
    timeout: float = 5.0,
) -> str | None:
    """Grab one JPEG frame from an MJPEG stream, return as base64 string."""
    if not stream_url:
        return None
    started_at = time.perf_counter()
    try:
        jpeg_bytes = _grab_jpeg_frame(stream_url, timeout=timeout)
    except (error.URLError, OSError, TimeoutError, ValueError) as exc:
        logger.warning(
            "camera frame capture failed in %.1f ms: %s",
            elapsed_ms(started_at),
            exc,
        )
        return None
    if jpeg_bytes is None:
        logger.warning(
            "camera frame capture returned no frame in %.1f ms",
            elapsed_ms(started_at),
        )
        return None
    encoded = base64.b64encode(jpeg_bytes).decode("ascii")
    logger.info(
        "camera frame captured in %.1f ms (jpeg_bytes=%d, base64_chars=%d)",
        elapsed_ms(started_at),
        len(jpeg_bytes),
        len(encoded),
    )
    return encoded


def _grab_jpeg_frame(stream_url: str, *, timeout: float) -> bytes | None:
    req = request.Request(stream_url)
    with request.urlopen(req, timeout=timeout) as resp:
        buf = b""
        while len(buf) < MJPEG_MAX_BYTES:
            chunk = resp.read(MJPEG_READ_CHUNK)
            if not chunk:
                return None
            buf += chunk
            start = buf.find(JPEG_START)
            if start < 0:
                continue
            end = buf.find(JPEG_END, start + 2)
            if end >= 0:
                return buf[start:end + 2]
    return None
