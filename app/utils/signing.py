import base64
import binascii
import hashlib
import hmac
import json
from typing import Any, Dict

from app.errors import PosterError


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class ContextSigner:
    """Creates and verifies tamper-proof render contexts (HMAC-SHA256)."""

    def __init__(self, secret: str):
        if not secret:
            raise ValueError("secret must not be empty")
        self._key = secret.encode("utf-8")

    def sign(self, payload: Dict[str, Any]) -> str:
        body = _b64url(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        signature = _b64url(hmac.new(self._key, body.encode("ascii"), hashlib.sha256).digest())
        return f"{body}.{signature}"

    def verify(self, token: str) -> Dict[str, Any]:
        try:
            body, signature = token.strip().split(".", 1)
            expected = _b64url(hmac.new(self._key, body.encode("ascii"), hashlib.sha256).digest())
        except (ValueError, AttributeError, UnicodeEncodeError):
            raise PosterError("INVALID_RENDER_CONTEXT", "The render context is malformed.", 400)
        if not hmac.compare_digest(signature, expected):
            raise PosterError("INVALID_RENDER_CONTEXT", "The render context signature is invalid.", 400)
        try:
            payload = json.loads(_b64url_decode(body))
        except (ValueError, binascii.Error):
            raise PosterError("INVALID_RENDER_CONTEXT", "The render context is malformed.", 400)
        if not isinstance(payload, dict):
            raise PosterError("INVALID_RENDER_CONTEXT", "The render context is malformed.", 400)
        return payload

