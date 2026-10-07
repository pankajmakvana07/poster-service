from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np
from PIL import Image, ImageFilter

from app.config import Settings
from app.errors import PosterError
from app.utils.color_utils import RGB, to_hex
from app.utils.geometry import PixelRect, expand_rect, iou

MODES = ("detect", "auto", "coordinates")


@dataclass(frozen=True)
class PlaceholderOptions:
    color: Optional[RGB]
    tolerance: int
    mode: str = "detect"


@dataclass
class PhotoRegion:
    """Where the photo goes: bounding box, optional exact pixel mask and detection details."""

    bbox: PixelRect
    mask: Optional[Image.Image]
    detected: bool
    color: Optional[RGB]
    tolerance: int
    candidates: int = 0
    warnings: List[str] = field(default_factory=list)


@dataclass
class _Components:
    rows: np.ndarray
    x0: np.ndarray
    x1: np.ndarray
    labels: np.ndarray
    count: int
    area: np.ndarray
    min_x: np.ndarray
    max_x: np.ndarray
    min_y: np.ndarray
    max_y: np.ndarray


def _label_components(mask: np.ndarray) -> _Components:
    height, width = mask.shape
    padded = np.zeros((height, width + 2), dtype=np.int8)
    padded[:, 1:-1] = mask
    diff = np.diff(padded, axis=1)
    start_rows, start_x = np.nonzero(diff == 1)
    _, end_x = np.nonzero(diff == -1)
    rows = start_rows.astype(np.int64)
    x0 = start_x.astype(np.int64)
    x1 = end_x.astype(np.int64)
    count_runs = len(rows)
    if count_runs == 0:
        empty = np.zeros(0, dtype=np.int64)
        return _Components(rows, x0, x1, empty, 0, empty, empty, empty, empty, empty)

    parent = list(range(count_runs))

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    row_starts = np.searchsorted(rows, np.arange(height + 1)).tolist()
    x0_list = x0.tolist()
    x1_list = x1.tolist()
    for r in range(height - 1):
        a0, a1 = row_starts[r], row_starts[r + 1]
        b0, b1 = row_starts[r + 1], row_starts[r + 2]
        if a0 == a1 or b0 == b1:
            continue
        i, j = a0, b0
        while i < a1 and j < b1:
            if x0_list[i] < x1_list[j] and x0_list[j] < x1_list[i]:
                ra, rb = find(i), find(j)
                if ra != rb:
                    parent[rb] = ra
            if x1_list[i] <= x1_list[j]:
                i += 1
            else:
                j += 1

    roots = np.fromiter((find(i) for i in range(count_runs)), dtype=np.int64, count=count_runs)
    _, labels = np.unique(roots, return_inverse=True)
    labels = labels.astype(np.int64)
    count = int(labels.max()) + 1
    lengths = (x1 - x0).astype(np.float64)
    area = np.bincount(labels, weights=lengths, minlength=count).astype(np.int64)
    min_x = np.full(count, np.iinfo(np.int64).max, dtype=np.int64)
    max_x = np.zeros(count, dtype=np.int64)
    min_y = np.full(count, np.iinfo(np.int64).max, dtype=np.int64)
    max_y = np.zeros(count, dtype=np.int64)
    np.minimum.at(min_x, labels, x0)
    np.maximum.at(max_x, labels, x1)
    np.minimum.at(min_y, labels, rows)
    np.maximum.at(max_y, labels, rows + 1)
    return _Components(rows, x0, x1, labels, count, area, min_x, max_x, min_y, max_y)


def _paint_component(comps: _Components, label: int, shape: Tuple[int, int]) -> np.ndarray:
    canvas = np.zeros(shape, dtype=bool)
    indices = np.nonzero(comps.labels == label)[0]
    for idx in indices.tolist():
        canvas[comps.rows[idx], comps.x0[idx] : comps.x1[idx]] = True
    return canvas


def _fill_holes(mask: np.ndarray) -> np.ndarray:
    comps = _label_components(~mask)
    height, width = mask.shape
    filled = mask.copy()
    for label in range(comps.count):
        touches = (
            comps.min_x[label] <= 0 or comps.max_x[label] >= width or comps.min_y[label] <= 0 or comps.max_y[label] >= height
        )
        if not touches:
            filled |= _paint_component(comps, label, mask.shape)
    return filled


class TemplateAnalyzer:
    """Finds the photo placeholder in a template by colour (with tolerance) and connected components."""

    def __init__(self, settings: Settings):
        self.settings = settings

    def detect_color(self, image: Image.Image, hint: PixelRect) -> RGB:
        """Return the dominant flat colour inside the middle of the hinted photo area."""
        inner = PixelRect(
            hint.x + hint.width // 5, hint.y + hint.height // 5, max(1, hint.width * 3 // 5), max(1, hint.height * 3 // 5)
        )
        sample = np.asarray(image.convert("RGB").crop(inner.box), dtype=np.uint8)
        pixels = sample.reshape(-1, 3)
        codes = (
            (pixels[:, 0].astype(np.int64) >> 3) << 10 | (pixels[:, 1].astype(np.int64) >> 3) << 5 | (pixels[:, 2].astype(np.int64) >> 3)
        )
        values, counts = np.unique(codes, return_counts=True)
        best = int(values[np.argmax(counts)])
        share = counts.max() / len(codes)
        if share < 0.2:
            raise PosterError(
                "PLACEHOLDER_COLOR_NOT_FOUND",
                "No single flat colour was found inside photoArea. Set photoArea.placeholderColor explicitly.",
                422,
            )
        selected = pixels[codes == best]
        mean = selected.mean(axis=0)
        return int(round(mean[0])), int(round(mean[1])), int(round(mean[2]))

    def analyze(self, image: Image.Image, hint: PixelRect, options: PlaceholderOptions) -> PhotoRegion:
        if options.mode == "coordinates":
            return PhotoRegion(bbox=hint, mask=None, detected=False, color=options.color, tolerance=options.tolerance)
        try:
            return self._detect(image, hint, options)
        except PosterError as exc:
            if options.mode == "auto" and exc.code in ("PLACEHOLDER_NOT_FOUND", "PLACEHOLDER_COLOR_NOT_FOUND"):
                return PhotoRegion(
                    bbox=hint,
                    mask=None,
                    detected=False,
                    color=options.color,
                    tolerance=options.tolerance,
                    warnings=[f"Placeholder detection failed ({exc.code}); configured coordinates were used."],
                )
            raise

    def _detect(self, image: Image.Image, hint: PixelRect, options: PlaceholderOptions) -> PhotoRegion:
        width, height = image.size
        color = options.color or self.detect_color(image, hint)
        margin = self.settings.placeholder_search_margin
        window = expand_rect(hint, int(hint.width * margin), int(hint.height * margin), width, height)
        array = np.asarray(image.convert("RGB").crop(window.box), dtype=np.int16)
        target = np.array(color, dtype=np.int16)
        raw = (np.abs(array - target) <= options.tolerance).all(axis=2)
        if not raw.any():
            raise self._not_found(color, options.tolerance)

        cleaned = Image.fromarray((raw * 255).astype(np.uint8), "L")
        cleaned = cleaned.filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.MinFilter(5))
        cleaned = cleaned.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.MaxFilter(3))
        mask = np.asarray(cleaned) > 127
        comps = _label_components(mask)
        if comps.count == 0:
            raise self._not_found(color, options.tolerance)

        local_hint = PixelRect(hint.x - window.x, hint.y - window.y, hint.width, hint.height)
        overlap = self._hint_overlap(comps, local_hint)
        min_area = max(100, int(hint.area * 0.01))
        viable = [k for k in range(comps.count) if comps.area[k] >= min_area and overlap[k] > 0]
        if not viable:
            raise self._not_found(color, options.tolerance)
        viable.sort(key=lambda k: (overlap[k], comps.area[k]), reverse=True)
        best = viable[0]
        best_bbox = PixelRect(
            int(comps.min_x[best]),
            int(comps.min_y[best]),
            int(comps.max_x[best] - comps.min_x[best]),
            int(comps.max_y[best] - comps.min_y[best]),
        )
        if overlap[best] < 0.15 * hint.area and iou(best_bbox, local_hint) < 0.25:
            raise self._not_found(color, options.tolerance)

        warnings: List[str] = []
        if len(viable) > 1 and overlap[viable[1]] >= 0.8 * overlap[best]:
            warnings.append("Several placeholder regions overlap photoArea; the one with the largest overlap was used.")

        full = _paint_component(comps, best, mask.shape)
        crop = full[best_bbox.y : best_bbox.bottom, best_bbox.x : best_bbox.right]
        crop = _fill_holes(crop)
        region_mask = Image.fromarray((crop * 255).astype(np.uint8), "L")
        bbox = PixelRect(window.x + best_bbox.x, window.y + best_bbox.y, best_bbox.width, best_bbox.height)
        return PhotoRegion(
            bbox=bbox,
            mask=region_mask,
            detected=True,
            color=color,
            tolerance=options.tolerance,
            candidates=len(viable),
            warnings=warnings,
        )

    @staticmethod
    def _hint_overlap(comps: _Components, hint: PixelRect) -> np.ndarray:
        in_rows = (comps.rows >= hint.y) & (comps.rows < hint.bottom)
        length = np.minimum(comps.x1, hint.right) - np.maximum(comps.x0, hint.x)
        length = np.where(in_rows, np.maximum(length, 0), 0).astype(np.float64)
        return np.bincount(comps.labels, weights=length, minlength=comps.count).astype(np.int64)

    @staticmethod
    def _not_found(color: RGB, tolerance: int) -> PosterError:
        return PosterError(
            "PLACEHOLDER_NOT_FOUND",
            f"No photo placeholder matching {to_hex(color + (255,))} (tolerance {tolerance}) was found inside photoArea.",
            422,
        )
