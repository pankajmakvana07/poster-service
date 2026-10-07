import ipaddress
import socket
from io import BytesIO
from typing import Callable, List, Optional, Tuple
from urllib.parse import urljoin, urlsplit

import httpx
from PIL import Image, ImageOps, UnidentifiedImageError

from app.config import Settings
from app.errors import PosterError

ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}
REDIRECT_STATUSES = {301, 302, 303, 307, 308}
BLOCKED_HOST_SUFFIXES = ("localhost", ".localhost", ".local", ".internal", ".localdomain")


def decode_image(
    data: bytes,
    *,
    max_pixels: int,
    code_invalid: str,
    code_format: str,
    label: str,
    min_side: int = 1,
    draft_size: Optional[Tuple[int, int]] = None,
    apply_exif: bool = False,
) -> Image.Image:
    """Validate and decode image bytes into an RGB image (alpha is flattened onto white)."""
    if not data:
        raise PosterError(code_invalid, f"The {label} is empty.", 422)
    try:
        probe = Image.open(BytesIO(data))
        image_format = probe.format
        if image_format not in ALLOWED_FORMATS:
            raise PosterError(code_format, f"Unsupported {label} format. Use JPEG, PNG or WEBP.", 415)
        width, height = probe.size
        if width * height > max_pixels:
            raise PosterError(code_invalid, f"The {label} dimensions are too large.", 422)
        if min(width, height) < min_side:
            raise PosterError(code_invalid, f"The {label} is too small (minimum side {min_side}px).", 422)
        probe.verify()
        image = Image.open(BytesIO(data))
        if draft_size and image_format == "JPEG":
            image.draft("RGB", draft_size)
        image.load()
    except PosterError:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError, SyntaxError, EOFError):
        raise PosterError(code_invalid, f"The {label} is not a valid image.", 422)
    if apply_exif:
        image = ImageOps.exif_transpose(image)
    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        canvas = Image.new("RGB", rgba.size, (255, 255, 255))
        canvas.paste(rgba, mask=rgba.getchannel("A"))
        return canvas
    return image.convert("RGB")


def system_resolver(host: str) -> List[str]:
    try:
        return sorted({info[4][0] for info in socket.getaddrinfo(host, None)})
    except socket.gaierror:
        return []


class TemplateUrlPolicy:
    """SSRF protection: scheme, credentials, port, host allowlist and public-IP checks."""

    def __init__(self, settings: Settings, resolver: Optional[Callable[[str], List[str]]] = None):
        self.settings = settings
        self.resolver = resolver or system_resolver

    def _host_allowed(self, host: str) -> bool:
        for pattern in self.settings.allowed_hosts:
            if pattern == "*":
                return True
            if pattern.startswith("*."):
                suffix = pattern[1:]
                if host.endswith(suffix) and len(host) > len(suffix):
                    return True
            elif host == pattern:
                return True
        return False

    def validate(self, url: str) -> str:
        if not isinstance(url, str) or not url.strip() or len(url) > 2048:
            raise PosterError("INVALID_TEMPLATE_URL", "templateImageUrl is missing or too long.", 400)
        url = url.strip()
        try:
            parts = urlsplit(url)
            port = parts.port
        except ValueError:
            raise PosterError("INVALID_TEMPLATE_URL", "templateImageUrl is not a valid URL.", 400)
        schemes = {"https"} | ({"http"} if self.settings.allow_http_templates else set())
        if parts.scheme.lower() not in schemes:
            raise PosterError("TEMPLATE_URL_NOT_ALLOWED", "Only https template URLs are allowed.", 400)
        if parts.username or parts.password:
            raise PosterError("TEMPLATE_URL_NOT_ALLOWED", "Credentials in the template URL are not allowed.", 400)
        host = (parts.hostname or "").lower().rstrip(".")
        if not host:
            raise PosterError("INVALID_TEMPLATE_URL", "templateImageUrl has no host.", 400)
        default_port = 443 if parts.scheme.lower() == "https" else 80
        if port is not None and port != default_port and not (self.settings.allow_private_hosts or self.settings.allow_custom_ports):
            raise PosterError("TEMPLATE_URL_NOT_ALLOWED", "Custom ports are not allowed for template URLs.", 400)
        if not self.settings.allow_private_hosts and (host in ("localhost",) or host.endswith(BLOCKED_HOST_SUFFIXES)):
            raise PosterError("TEMPLATE_URL_NOT_ALLOWED", "Local and internal host names are not allowed.", 400)
        if not self._host_allowed(host):
            raise PosterError("TEMPLATE_URL_NOT_ALLOWED", "The template host is not in the allowed list.", 400)
        if not self.settings.allow_private_hosts:
            self._assert_public(host)
        return url

    def _assert_public(self, host: str) -> None:
        try:
            addresses = [str(ipaddress.ip_address(host))]
        except ValueError:
            addresses = self.resolver(host)
        if not addresses:
            raise PosterError("TEMPLATE_DOWNLOAD_FAILED", "The template host could not be resolved.", 502)
        for raw in addresses:
            ip = ipaddress.ip_address(raw.split("%")[0])
            if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
                ip = ip.ipv4_mapped
            if not ip.is_global:
                raise PosterError("TEMPLATE_URL_NOT_ALLOWED", "The template host resolves to a private or internal address.", 400)


class TemplateFetcher:
    """Downloads template images with timeouts, a size cap and per-redirect SSRF validation."""

    def __init__(
        self,
        settings: Settings,
        policy: TemplateUrlPolicy,
        client_factory: Optional[Callable[[], httpx.Client]] = None,
    ):
        self.settings = settings
        self.policy = policy
        self._client_factory = client_factory or self._default_client

    def _default_client(self) -> httpx.Client:
        timeout = httpx.Timeout(self.settings.download_timeout_seconds)
        return httpx.Client(
            timeout=timeout,
            follow_redirects=False,
            headers={"User-Agent": "poster-engine/1.0", "Accept": "image/jpeg,image/png,image/webp,image/*"},
        )

    def fetch(self, url: str) -> bytes:
        current = url
        try:
            with self._client_factory() as client:
                for _ in range(self.settings.max_redirects + 1):
                    current = self.policy.validate(current)
                    with client.stream("GET", current) as response:
                        if response.status_code in REDIRECT_STATUSES:
                            location = response.headers.get("location")
                            if not location:
                                raise PosterError("TEMPLATE_DOWNLOAD_FAILED", "Redirect without a location.", 502)
                            current = urljoin(current, location)
                            continue
                        if response.status_code != 200:
                            raise PosterError(
                                "TEMPLATE_DOWNLOAD_FAILED", f"The template server answered HTTP {response.status_code}.", 502
                            )
                        return self._read_body(response)
        except PosterError:
            raise
        except httpx.TimeoutException:
            raise PosterError("TEMPLATE_DOWNLOAD_TIMEOUT", "Downloading the template timed out.", 504)
        except httpx.HTTPError:
            raise PosterError("TEMPLATE_DOWNLOAD_FAILED", "The template could not be downloaded.", 502)
        raise PosterError("TEMPLATE_DOWNLOAD_FAILED", "Too many redirects while downloading the template.", 502)

    def _read_body(self, response: httpx.Response) -> bytes:
        limit = self.settings.max_template_bytes
        content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
        if content_type and not (content_type.startswith("image/") or content_type == "application/octet-stream"):
            raise PosterError("INVALID_TEMPLATE_IMAGE", "The template URL did not return an image.", 422)
        declared = response.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > limit:
            raise PosterError("TEMPLATE_TOO_LARGE", "The template image is too large.", 422)
        chunks: List[bytes] = []
        total = 0
        for chunk in response.iter_bytes():
            total += len(chunk)
            if total > limit:
                raise PosterError("TEMPLATE_TOO_LARGE", "The template image is too large.", 422)
            chunks.append(chunk)
        return b"".join(chunks)
