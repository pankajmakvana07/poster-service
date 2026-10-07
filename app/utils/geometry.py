import math
from dataclasses import dataclass
from typing import List, Tuple

from app.errors import PosterError


@dataclass(frozen=True)
class NormRect:
    """Rectangle in normalized template coordinates (0..1, origin top-left)."""

    x: float
    y: float
    width: float
    height: float

    def as_list(self) -> List[float]:
        return [self.x, self.y, self.width, self.height]


@dataclass(frozen=True)
class PixelRect:
    """Rectangle in whole pixels (origin top-left)."""

    x: int
    y: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height

    @property
    def box(self) -> Tuple[int, int, int, int]:
        return self.x, self.y, self.right, self.bottom

    @property
    def area(self) -> int:
        return self.width * self.height

    def as_list(self) -> List[int]:
        return [self.x, self.y, self.width, self.height]


def validate_norm_rect(rect: NormRect, label: str, eps: float = 1e-6) -> None:
    values = rect.as_list()
    if any(not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) for v in values):
        raise PosterError("INVALID_COORDINATES", f"{label}: coordinates must be finite numbers.", 422)
    if rect.x < -eps or rect.y < -eps or rect.width <= 0 or rect.height <= 0:
        raise PosterError("INVALID_COORDINATES", f"{label}: x and y must be >= 0 and width and height > 0.", 422)
    if rect.x + rect.width > 1 + eps or rect.y + rect.height > 1 + eps:
        raise PosterError("INVALID_COORDINATES", f"{label}: the rectangle extends beyond the template (values are 0..1).", 422)


def norm_to_pixel(rect: NormRect, width: int, height: int) -> PixelRect:
    x0 = min(max(round(rect.x * width), 0), width - 1)
    y0 = min(max(round(rect.y * height), 0), height - 1)
    x1 = min(max(round((rect.x + rect.width) * width), x0 + 1), width)
    y1 = min(max(round((rect.y + rect.height) * height), y0 + 1), height)
    return PixelRect(x0, y0, x1 - x0, y1 - y0)


def pixel_to_norm(rect: PixelRect, width: int, height: int) -> NormRect:
    return NormRect(rect.x / width, rect.y / height, rect.width / width, rect.height / height)


def intersection_area(a: PixelRect, b: PixelRect) -> int:
    w = min(a.right, b.right) - max(a.x, b.x)
    h = min(a.bottom, b.bottom) - max(a.y, b.y)
    return max(0, w) * max(0, h)


def iou(a: PixelRect, b: PixelRect) -> float:
    inter = intersection_area(a, b)
    union = a.area + b.area - inter
    return inter / union if union else 0.0


def expand_rect(rect: PixelRect, margin_x: int, margin_y: int, width: int, height: int) -> PixelRect:
    x0 = max(0, rect.x - margin_x)
    y0 = max(0, rect.y - margin_y)
    x1 = min(width, rect.right + margin_x)
    y1 = min(height, rect.bottom + margin_y)
    return PixelRect(x0, y0, x1 - x0, y1 - y0)
