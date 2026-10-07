"""
Photo renderer for dynamic-posters.

Pipeline per photo:
  1. Detect face (YuNet → Haar → centre fallback)
  2. Smart-crop: face centred in upper region, zoom to fill shape bbox (no black/colour bars)
  3. Remove background (rembg u2netp) → RGBA
  4. Composite on fill colour
  5. Shape-mask (square/rectangle/circle/rounded_rectangle/hexagon/placeholder)
  6. Paste onto template at the given bbox
"""
import math
import logging
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter

from app.config import Settings
from app.errors import PosterError
from app.services.background_remover import BackgroundRemover
from app.services.face_detector import Face, FaceDetector
from app.services.image_loader import decode_image
from app.services.template_analyzer import PhotoRegion
from app.utils.base64_utils import decode_image_base64
from app.utils.color_utils import RGB
from app.utils.geometry import PixelRect

logger = logging.getLogger("poster-engine.photo_renderer")

SHAPES = ("placeholder", "rectangle", "square", "circle", "rounded_rectangle", "hexagon")
FITS = ("cover", "contain")
HEX_ORIENTATIONS = ("pointy", "flat")

# Face-crop tuning (mirrors static-poster CropConfig defaults)
_FACE_SCALE = 2.8          # how many face-widths wide the crop window is
_HEADROOM = 0.16           # fraction of crop height above the face top
_VERT_FACE_POS = 0.42      # where vertically the face centre sits in the crop (0=top)
_MAX_ZOOM_IN = 1.25        # maximum extra zoom allowed when image is small
_MIN_SRC_RATIO = 0.25      # minimum source width relative to target (quality guard)


@dataclass(frozen=True)
class PhotoConfig:
    shape: str = "square"
    fit: str = "cover"
    focus: Tuple[float, float] = (0.5, 0.5)          # used when no face detected
    corner_radius: float = 0.12
    hexagon_orientation: str = "pointy"
    fill: RGB = (224, 224, 224)                        # default gray background
    remove_background: bool = True
    edge_expand: int = 2


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(v, hi))


def _smooth_ramp(count: int) -> np.ndarray:
    t = np.linspace(0.0, 1.0, count, dtype=np.float32)
    return t * t * (3.0 - 2.0 * t)


def _feather_edges(alpha: np.ndarray, pads: Tuple[int, int, int, int], band: int, bottom_band: int) -> np.ndarray:
    left, top, right, bottom = pads
    height, width = alpha.shape
    if left > 0:
        n = min(band, width)
        alpha[:, :n] *= _smooth_ramp(n)[None, :]
    if right > 0:
        n = min(band, width)
        alpha[:, width - n:] *= _smooth_ramp(n)[::-1][None, :]
    if top > 0:
        n = min(band, height)
        alpha[:n, :] *= _smooth_ramp(n)[:, None]
    if bottom > 0:
        n = min(bottom_band, height // 2)
        if n > 1:
            alpha[height - n:, :] *= _smooth_ramp(n)[::-1][:, None]
    return alpha


def _compose_on_color(image_rgb: np.ndarray, alpha_l: np.ndarray, color: RGB) -> np.ndarray:
    a = (alpha_l.astype(np.float32) / 255.0)[..., None]
    bg = np.empty_like(image_rgb, dtype=np.float32)
    bg[:] = color
    fg = image_rgb.astype(np.float32)
    return np.clip(fg * a + bg * (1.0 - a) + 0.5, 0, 255).astype(np.uint8)


# ─────────────────────────────────────────────────────────────────────────────
# Face-aware crop
# ─────────────────────────────────────────────────────────────────────────────

def _face_crop_box(
    img_w: int, img_h: int,
    face: Face,
    target_w: int, target_h: int,
) -> Tuple[int, int, int, int]:
    """
    Return (x0, y0, x1, y1) crop box centred on the face with passport-style framing.
    The box is guaranteed to cover target_w/target_h aspect ratio.
    The face sits in the upper portion (_VERT_FACE_POS from top).
    """
    aspect = target_w / target_h
    cx = face.center_x
    cy = face.center_y

    def layout(crop_w: float) -> Tuple[float, float, float, float]:
        crop_h = crop_w / aspect
        top = min(cy - _VERT_FACE_POS * crop_h, face.y - _HEADROOM * crop_h)
        left = cx - crop_w / 2
        return crop_w, crop_h, left, top

    crop_w = face.width * _FACE_SCALE
    crop_w, crop_h, left, top = layout(crop_w)

    # If crop is taller than the image, zoom out
    if crop_h > img_h:
        needed_h = (img_h - cy) / max(1e-6, 1.0 - _VERT_FACE_POS)
        new_h = min(crop_h, max(needed_h, crop_h / _MAX_ZOOM_IN))
        crop_w = new_h * aspect
        crop_w, crop_h, left, top = layout(crop_w)

    # Clamp into image bounds
    if crop_w <= img_w:
        left = _clamp(left, 0, img_w - crop_w)
    if crop_h <= img_h:
        top = _clamp(top, 0, img_h - crop_h)

    x0, y0 = round(left), round(top)
    x1, y1 = round(left + crop_w), round(top + crop_h)
    return x0, y0, x1, y1


def _cover_crop_box(
    img_w: int, img_h: int,
    target_w: int, target_h: int,
    focus: Tuple[float, float],
) -> Tuple[int, int, int, int]:
    """Centre-weighted cover crop when no face detected."""
    scale = max(target_w / img_w, target_h / img_h)
    new_w = max(target_w, math.ceil(img_w * scale))
    new_h = max(target_h, math.ceil(img_h * scale))
    left = round((new_w - target_w) * focus[0])
    top = round((new_h - target_h) * focus[1])
    return 0, 0, img_w, img_h   # whole image; resize will cover


# ─────────────────────────────────────────────────────────────────────────────
# Shape masks
# ─────────────────────────────────────────────────────────────────────────────

def _supersampled(size: Tuple[int, int], draw_fn) -> Image.Image:
    w, h = size
    factor = 4 if w * h <= 4_000_000 else 2
    big = Image.new("L", (w * factor, h * factor), 0)
    draw_fn(ImageDraw.Draw(big), w * factor, h * factor, factor)
    return big.resize(size, Image.LANCZOS)


def build_shape_mask(
    shape: str,
    size: Tuple[int, int],
    cfg: PhotoConfig,
    placeholder_mask: Optional[Image.Image] = None,
) -> Image.Image:
    w, h = size
    s = shape.lower()
    if s == "placeholder":
        if placeholder_mask is None:
            return Image.new("L", size, 255)
        mask = placeholder_mask
        if cfg.edge_expand > 0:
            mask = mask.filter(ImageFilter.MaxFilter(cfg.edge_expand * 2 + 1))
        return mask.filter(ImageFilter.GaussianBlur(0.7))
    if s in ("rectangle", "square"):
        return Image.new("L", size, 255)
    if s == "circle":
        diameter = min(w, h)
        x0 = (w - diameter) // 2
        y0 = (h - diameter) // 2

        def draw_circle(draw, dw, dh, f):
            draw.ellipse([x0 * f, y0 * f, (x0 + diameter) * f - 1, (y0 + diameter) * f - 1], fill=255)

        return _supersampled(size, draw_circle)
    if s == "rounded_rectangle":
        radius = min(w, h) * min(max(cfg.corner_radius, 0.0), 0.5)

        def draw_rounded(draw, dw, dh, f):
            draw.rounded_rectangle([0, 0, dw - 1, dh - 1], radius=radius * f, fill=255)

        return _supersampled(size, draw_rounded)
    if s == "hexagon":
        if cfg.hexagon_orientation == "flat":
            pts = [(0.25, 0), (0.75, 0), (1, 0.5), (0.75, 1), (0.25, 1), (0, 0.5)]
        else:
            pts = [(0.5, 0), (1, 0.25), (1, 0.75), (0.5, 1), (0, 0.75), (0, 0.25)]

        def draw_hex(draw, dw, dh, f):
            draw.polygon([(px * (dw - 1), py * (dh - 1)) for px, py in pts], fill=255)

        return _supersampled(size, draw_hex)
    raise PosterError("INVALID_PHOTO_SHAPE", f"Unsupported photo shape '{shape}'.", 422)


# ─────────────────────────────────────────────────────────────────────────────
# Main renderer class
# ─────────────────────────────────────────────────────────────────────────────

class PhotoRenderer:
    """
    Face-aware photo renderer.

    1. Detect face → smart-crop so face is centred & fills the shape bbox
    2. Remove background (rembg u2netp) → RGBA
    3. Alpha-composite on fill colour
    4. Apply shape mask (hexagon / circle / square …)
    5. Paste onto template
    """

    def __init__(self, settings: Settings, bg_remover: Optional[BackgroundRemover] = None,
                 face_detector: Optional[FaceDetector] = None):
        self.settings = settings
        self.bg_remover = bg_remover or BackgroundRemover(
            model_name=settings.segmentation_model,
            models_dir=settings.models_dir,
        )
        yunet_path = settings.models_dir / "face_detection_yunet_2023mar.onnx"
        self.face_detector = face_detector or FaceDetector(yunet_model_path=yunet_path)

    # ── decode ────────────────────────────────────────────────────────────────

    def decode(self, data_base64: Optional[str], target: Tuple[int, int]) -> Image.Image:
        data = decode_image_base64(data_base64, self.settings.max_photo_bytes)
        side = max(target) * 2
        return decode_image(
            data,
            max_pixels=self.settings.max_photo_pixels,
            code_invalid="INVALID_PHOTO",
            code_format="UNSUPPORTED_IMAGE_FORMAT",
            label="photo",
            min_side=self.settings.min_photo_side,
            draft_size=(side, side),
            apply_exif=True,
        )

    # ── smart crop ────────────────────────────────────────────────────────────

    def _smart_crop(
        self,
        photo: Image.Image,
        target_w: int,
        target_h: int,
        cfg: PhotoConfig,
    ) -> Image.Image:
        """
        Detect face → crop so face is framed properly.
        Falls back to focus-based cover crop if no face detected.
        Result is EXACTLY target_w × target_h.
        """
        img_w, img_h = photo.size
        face: Optional[Face] = self.face_detector.detect_best(photo)

        if face is not None:
            x0, y0, x1, y1 = _face_crop_box(img_w, img_h, face, target_w, target_h)

            # Clamp to image bounds
            cx0 = max(0, x0)
            cy0 = max(0, y0)
            cx1 = min(img_w, x1)
            cy1 = min(img_h, y1)

            if cx1 - cx0 < 8 or cy1 - cy0 < 8:
                logger.warning("Face crop box too small; falling back to cover crop")
                face = None
            else:
                cropped = photo.crop((cx0, cy0, cx1, cy1))
                # Resize to target (slight stretch is acceptable if pads are small)
                result = cropped.resize((target_w, target_h), Image.LANCZOS)
                return result

        # No face – plain cover crop centred on focus
        scale = max(target_w / img_w, target_h / img_h)
        new_w = max(target_w, math.ceil(img_w * scale))
        new_h = max(target_h, math.ceil(img_h * scale))
        resized = photo.resize((new_w, new_h), Image.LANCZOS)
        left = round((new_w - target_w) * cfg.focus[0])
        top = round((new_h - target_h) * cfg.focus[1])
        left = max(0, min(left, new_w - target_w))
        top = max(0, min(top, new_h - target_h))
        return resized.crop((left, top, left + target_w, top + target_h))

    # ── background removal ────────────────────────────────────────────────────

    def _remove_bg(self, photo: Image.Image, fill: RGB) -> Image.Image:
        """
        Remove background via rembg (foreground_mask API, same as static-poster).
        Composites result on fill colour; returns RGB.
        """
        if not self.bg_remover.is_available():
            return photo.convert("RGB")

        try:
            # rembg returns foreground mask (L image)
            mask = self.bg_remover._get_foreground_mask(photo)
            rgb = np.asarray(photo.convert("RGB"))
            alpha_l = np.asarray(mask.convert("L"))
            return Image.fromarray(_compose_on_color(rgb, alpha_l, fill))
        except AttributeError:
            # Fallback: use remove_background which returns RGBA
            result = self.bg_remover.remove_background(photo)
            if result.mode == "RGBA":
                rgb = np.asarray(result.convert("RGB"))
                alpha_l = np.asarray(result.split()[3])
                return Image.fromarray(_compose_on_color(rgb, alpha_l, fill))
            return result.convert("RGB")
        except Exception as e:
            logger.warning("Background removal failed, using original: %s", e)
            return photo.convert("RGB")

    # ── surround colour (for replacing old placeholder) ───────────────────────

    @staticmethod
    def _surround_color(template: Image.Image, region: PhotoRegion) -> RGB:
        bbox = region.bbox
        pad = 12
        x0, y0 = max(0, bbox.x - pad), max(0, bbox.y - pad)
        x1, y1 = min(template.width, bbox.right + pad), min(template.height, bbox.bottom + pad)
        local = Image.new("L", (x1 - x0, y1 - y0), 0)
        local.paste(region.mask, (bbox.x - x0, bbox.y - y0))
        outer = local.filter(ImageFilter.MaxFilter(21))
        inner = local.filter(ImageFilter.MaxFilter(5))
        ring = np.asarray(ImageChops.subtract(outer, inner)) > 127
        pixels = np.asarray(template.crop((x0, y0, x1, y1)).convert("RGB"))
        if not ring.any():
            return 255, 255, 255
        mean = pixels[ring].mean(axis=0)
        return int(mean[0]), int(mean[1]), int(mean[2])

    # ── compose (main entry point) ────────────────────────────────────────────

    def compose(
        self,
        template: Image.Image,
        region: Optional[PhotoRegion],
        photo: Image.Image,
        cfg: PhotoConfig,
        bbox: Optional[PixelRect] = None,
    ) -> Tuple[Image.Image, List[str]]:
        warnings: List[str] = []

        # Determine target bbox
        if bbox is None:
            if region is None:
                raise PosterError("INVALID_PHOTO_SHAPE", "Photo position bbox is required.", 422)
            bbox = region.bbox
        target_w, target_h = bbox.width, bbox.height
        shape = (cfg.shape or "square").lower()
        if shape == "placeholder" and (region is None or region.mask is None):
            shape = "square"

        # 1. Smart crop – face centred, fills target size
        cropped = self._smart_crop(photo, target_w, target_h, cfg)

        # 2. Background removal + composite on fill colour
        if cfg.remove_background:
            processed = self._remove_bg(cropped, cfg.fill)
        else:
            processed = cropped.convert("RGB")

        # Ensure exact size after processing
        if processed.size != (target_w, target_h):
            processed = processed.resize((target_w, target_h), Image.LANCZOS)

        # 3. Shape mask
        shape_mask = build_shape_mask(shape, (target_w, target_h), cfg,
                                      region.mask if region else None)

        # 4. Paste onto template
        result = template.copy()
        # Clear old placeholder colour on template if it was there
        if shape != "placeholder" and region and region.mask is not None:
            expanded = region.mask.filter(ImageFilter.MaxFilter(5))
            leftover = ImageChops.subtract(expanded, shape_mask)
            if leftover.getbbox() is not None:
                result.paste(self._surround_color(template, region), (bbox.x, bbox.y), leftover)

        result.paste(processed, (bbox.x, bbox.y), shape_mask)
        return result, warnings
