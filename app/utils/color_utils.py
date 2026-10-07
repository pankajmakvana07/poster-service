import re
from typing import Optional, Tuple

from app.errors import PosterError

RGB = Tuple[int, int, int]
RGBA = Tuple[int, int, int, int]

_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
TRANSPARENT: RGBA = (0, 0, 0, 0)


def parse_color(value: Optional[str], label: str = "color", default: Optional[RGBA] = None) -> RGBA:
    """Parse #RGB, #RGBA, #RRGGBB, #RRGGBBAA or 'transparent' into an RGBA tuple."""
    if value is None or (isinstance(value, str) and not value.strip()):
        if default is not None:
            return default
        raise PosterError("INVALID_COLOR", f"{label} is required.", 422)
    if not isinstance(value, str):
        raise PosterError("INVALID_COLOR", f"{label} must be a hex string such as #C99A32.", 422)
    text = value.strip()
    if text.lower() == "transparent":
        return TRANSPARENT
    match = _HEX_RE.match(text)
    if not match:
        raise PosterError("INVALID_COLOR", f"{label} must be a hex colour such as #FFFFFF or #C99A32 ('{value}' is invalid).", 422)
    digits = match.group(1)
    if len(digits) in (3, 4):
        digits = "".join(ch * 2 for ch in digits)
    if len(digits) == 6:
        digits += "ff"
    return tuple(int(digits[i : i + 2], 16) for i in range(0, 8, 2))  # type: ignore[return-value]


def parse_rgb(value: Optional[str], label: str = "color") -> RGB:
    rgba = parse_color(value, label)
    if rgba[3] != 255:
        raise PosterError("INVALID_COLOR", f"{label} must be an opaque colour.", 422)
    return rgba[0], rgba[1], rgba[2]


def to_hex(color: RGBA) -> str:
    r, g, b, a = color
    return f"#{r:02X}{g:02X}{b:02X}" if a == 255 else f"#{r:02X}{g:02X}{b:02X}{a:02X}"
