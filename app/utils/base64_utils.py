import base64
import binascii
import re
from typing import Optional

from app.errors import PosterError

_DATA_URI_RE = re.compile(r"^data:(?P<mime>[a-zA-Z0-9.+/-]+)?(?P<params>;[^,]*)?,", re.DOTALL)
_ALLOWED_MIME = {"image/jpeg", "image/jpg", "image/png", "image/webp"}


def decode_image_base64(value: Optional[str], max_bytes: int) -> bytes:
    """Decode raw base64 or a data URI into bytes with strict validation and a size limit."""
    if value is None or not isinstance(value, str) or not value.strip():
        raise PosterError("MISSING_PHOTO", "A base64 encoded photo is required.", 422)
    text = value.strip()
    match = _DATA_URI_RE.match(text)
    if match:
        mime = (match.group("mime") or "").lower()
        params = (match.group("params") or "").lower()
        if mime not in _ALLOWED_MIME:
            raise PosterError("INVALID_PHOTO", "Unsupported data URI type. Use JPEG, PNG or WEBP.", 422)
        if "base64" not in params:
            raise PosterError("INVALID_BASE64", "The data URI must be base64 encoded.", 400)
        text = text[match.end():]
    text = "".join(text.split())
    if len(text) > (max_bytes * 4) // 3 + 8:
        raise PosterError("PHOTO_TOO_LARGE", "The photo payload is too large.", 413)
    if not text:
        raise PosterError("MISSING_PHOTO", "A base64 encoded photo is required.", 422)
    urlsafe = "-" in text or "_" in text
    text = text.rstrip("=")
    text += "=" * (-len(text) % 4)
    try:
        data = base64.urlsafe_b64decode(text) if urlsafe else base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        raise PosterError("INVALID_BASE64", "The photo is not valid base64.", 400)
    if not data:
        raise PosterError("INVALID_BASE64", "The photo is empty.", 400)
    if len(data) > max_bytes:
        raise PosterError("PHOTO_TOO_LARGE", f"The photo exceeds {max_bytes // (1024 * 1024)} MB.", 413)
    return data


def encode_base64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")
