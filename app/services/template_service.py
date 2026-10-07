import hashlib
import json
import math
import re
import time
from io import BytesIO
from typing import Any, Callable, Dict, List, Optional, Tuple

from PIL import Image

from app.config import Settings
from app.errors import PosterError
from app.schemas.poster import (
    FieldDef,
    PhotoAreaDef,
    RenderRequest,
    RenderResponse,
)
from app.services.image_loader import TemplateFetcher, decode_image
from app.services.photo_renderer import FITS, HEX_ORIENTATIONS, SHAPES, PhotoConfig, PhotoRenderer
from app.services.poster_renderer import FieldRender, PosterRenderer
from app.services.template_analyzer import MODES, PhotoRegion, PlaceholderOptions, TemplateAnalyzer
from app.services.template_cache import TemplateCache
from app.services.text_renderer import ALIGNMENTS, OVERFLOW_MODES, VERTICAL_ALIGNMENTS, TextStyle, sanitize_text
from app.utils.base64_utils import decode_image_base64, encode_base64
from app.utils.color_utils import TRANSPARENT, parse_color, parse_rgb, to_hex
from app.utils.geometry import NormRect, PixelRect, norm_to_pixel, validate_norm_rect
from app.utils.signing import ContextSigner

_ID_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")
_KEY_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")
_OUTPUT_FORMATS = {"jpeg": ("JPEG", "image/jpeg"), "jpg": ("JPEG", "image/jpeg"), "png": ("PNG", "image/png"), "webp": ("WEBP", "image/webp")}


class TemplateService:
    """Unified single-API service to render dynamic posters in a single request."""

    def __init__(
        self,
        settings: Settings,
        fetcher: TemplateFetcher,
        cache: TemplateCache,
        signer: ContextSigner,
        analyzer: TemplateAnalyzer,
        renderer: PosterRenderer,
        clock: Callable[[], float] = time.time,
    ):
        self.settings = settings
        self.fetcher = fetcher
        self.cache = cache
        self.signer = signer
        self.analyzer = analyzer
        self.renderer = renderer
        self.clock = clock

    def render(self, request: RenderRequest) -> RenderResponse:
        self._validate_template_id(request.templateId)
        mode = self._choice(request.coordinateMode, ("normalized", "pixel"), "coordinateMode", "INVALID_COORDINATES")
        output_format, mime = self._output_format(request.outputFormat)

        # 1. Load template image (from base64 or URL)
        image, image_bytes = self._load_template_image(request)
        width, height = image.size

        # 2. Process Photo Area (if supplied)
        photo = None
        photo_cfg = None
        region = None
        photo_bbox = None
        warnings: List[str] = []

        if request.photoArea is not None:
            photo_cfg = self._photo_config(request.photoArea)
            # Determine photo coordinates
            if mode == "normalized":
                photo_norm = self._rect(request.photoArea.x, request.photoArea.y, request.photoArea.width, request.photoArea.height)
                validate_norm_rect(photo_norm, "photoArea")
                photo_bbox = norm_to_pixel(photo_norm, width, height)
            else:
                photo_bbox = PixelRect(
                    round(request.photoArea.x),
                    round(request.photoArea.y),
                    round(request.photoArea.width),
                    round(request.photoArea.height),
                )

            # Check if placeholder detection is needed
            shape_norm = (photo_cfg.shape or "").lower()
            placeholder_mode = (request.photoArea.placeholderMode or "detect").lower()
            if shape_norm == "placeholder" and placeholder_mode == "detect":
                tolerance = request.photoArea.placeholderColorTolerance or self.settings.default_placeholder_tolerance
                explicit_color = parse_rgb(request.photoArea.placeholderColor, "placeholderColor") if request.photoArea.placeholderColor else None
                options = PlaceholderOptions(explicit_color, tolerance, placeholder_mode)
                region = self.analyzer.analyze(image, photo_bbox, options)
                photo_bbox = region.bbox
                warnings.extend(list(region.warnings))

            # Decode Photo Image
            photo_source = request.photoArea.fieldvalue or request.photoBase64
            if photo_source and photo_source.strip():
                photo_source = photo_source.strip()
                if photo_source.startswith(("http://", "https://")):
                    photo_url = self.fetcher.policy.validate(photo_source)
                    photo_bytes = self.fetcher.fetch(photo_url)
                    photo = decode_image(
                        photo_bytes,
                        max_pixels=self.settings.max_photo_pixels,
                        code_invalid="INVALID_PHOTO",
                        code_format="UNSUPPORTED_IMAGE_FORMAT",
                        label="photo",
                        min_side=self.settings.min_photo_side,
                        apply_exif=True,
                    )
                else:
                    photo = self.renderer.photos.decode(photo_source, (photo_bbox.width, photo_bbox.height))

        # 3. Process Text Fields
        field_renders = []
        for field in request.fields:
            if not isinstance(field.fieldkey, str) or not _KEY_RE.match(field.fieldkey):
                raise PosterError("INVALID_FIELD_KEY", f"Invalid fieldkey '{field.fieldkey}'.", 422)

            text = sanitize_text(field.fieldvalue or "")
            if mode == "normalized":
                field_norm = self._rect(field.x, field.y, field.width, field.height)
                validate_norm_rect(field_norm, f"field '{field.fieldkey}'")
                field_rect = norm_to_pixel(field_norm, width, height)
            else:
                field_rect = PixelRect(round(field.x), round(field.y), round(field.width), round(field.height))

            if not text:
                continue

            if len(text) > self.settings.max_text_length:
                raise PosterError("TEXT_TOO_LONG", f"Field '{field.fieldkey}' exceeds {self.settings.max_text_length} characters.", 422)

            text_color = parse_color(field.textColor, f"{field.fieldkey}.textColor", default=(0, 0, 0, 255))
            bg_color = parse_color(field.backgroundColor, f"{field.fieldkey}.backgroundColor", default=TRANSPARENT) if field.backgroundColor else TRANSPARENT
            align = self._choice(field.align, ALIGNMENTS, "align", "INVALID_FIELD_STYLE")
            valign = self._choice(field.verticalAlign, VERTICAL_ALIGNMENTS, "verticalAlign", "INVALID_FIELD_STYLE")
            overflow = self._choice(field.overflow, OVERFLOW_MODES, "overflow", "INVALID_FIELD_STYLE")

            if not math.isfinite(field.padding) or not 0 <= field.padding <= 0.4:
                raise PosterError("INVALID_FIELD_STYLE", "padding must be between 0 and 0.4.", 422)
            for name, value in (("maxFontSize", field.maxFontSize), ("minFontSize", field.minFontSize), ("maxLines", field.maxLines)):
                if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value < 1 or value > 1000):
                    raise PosterError("INVALID_FIELD_STYLE", f"{name} must be a positive integer.", 422)
            if field.maxFontSize and field.minFontSize and field.minFontSize > field.maxFontSize:
                raise PosterError("INVALID_FIELD_STYLE", "minFontSize must not exceed maxFontSize.", 422)

            if field.fontName is not None:
                self.renderer.texts.fonts.resolve(field.fontName)

            style = TextStyle(
                text_color=text_color,
                background_color=bg_color,
                align=align,
                vertical_align=valign,
                padding=round(float(field.padding), 4),
                max_font_size=field.maxFontSize,
                min_font_size=field.minFontSize,
                wrap=bool(field.wrap),
                max_lines=field.maxLines,
                overflow=overflow,
                font_name=field.fontName,
            )
            field_renders.append(FieldRender(field.fieldkey, field_rect, text, style))

        # 4. Render final poster
        poster, render_warnings = self.renderer.render(image, region, photo, photo_cfg, field_renders, photo_bbox=photo_bbox)
        warnings.extend(render_warnings)

        # 5. Output encoding
        buffer = BytesIO()
        quality = request.quality if request.quality is not None else self.settings.output_jpeg_quality
        if not 30 <= quality <= 100:
            raise PosterError("INVALID_OUTPUT_OPTIONS", "quality must be between 30 and 100.", 422)
        if output_format == "JPEG":
            poster.save(buffer, "JPEG", quality=quality, optimize=True, subsampling=0)
        elif output_format == "WEBP":
            poster.save(buffer, "WEBP", quality=quality)
        else:
            poster.save(buffer, "PNG", optimize=True)

        return RenderResponse(
            templateId=request.templateId,
            templateName=request.templateName,
            categoryId=request.categoryId,
            userId=request.userId,
            imageFormat=output_format.lower(),
            mimeType=mime,
            width=poster.width,
            height=poster.height,
            imageBase64=encode_base64(buffer.getvalue()),
            warnings=warnings,
        )

    def _load_template_image(self, request: RenderRequest) -> Tuple[Image.Image, bytes]:
        source = request.templateImageBase64 or request.templateImageUrl
        if not source or not source.strip():
            raise PosterError("INVALID_TEMPLATE_IMAGE", "Either templateImageBase64 or templateImageUrl must be provided.", 422)

        source = source.strip()
        if source.startswith(("http://", "https://")):
            url = self.fetcher.policy.validate(source)
            data = self.fetcher.fetch(url)
        else:
            data = decode_image_base64(source, self.settings.max_template_bytes)

        image = decode_image(
            data,
            max_pixels=self.settings.max_template_pixels,
            code_invalid="INVALID_TEMPLATE_IMAGE",
            code_format="UNSUPPORTED_IMAGE_FORMAT",
            label="template image",
            min_side=32,
        )
        return image, data

    @staticmethod
    def _validate_template_id(template_id: Any) -> None:
        if isinstance(template_id, bool) or not isinstance(template_id, (str, int)) or not _ID_RE.match(str(template_id)):
            raise PosterError("INVALID_TEMPLATE_ID", "templateId must be 1-64 characters: letters, digits, '_', '-' or '.'.", 422)

    @staticmethod
    def _choice(value: str, options: Tuple[str, ...], label: str, code: str) -> str:
        normalized = (value or "").strip().lower()
        if normalized not in options:
            raise PosterError(code, f"{label} must be one of: {', '.join(options)}.", 422)
        return normalized

    @staticmethod
    def _rect(x: float, y: float, w: float, h: float) -> NormRect:
        return NormRect(float(x), float(y), float(w), float(h))

    def _photo_config(self, area: PhotoAreaDef) -> PhotoConfig:
        shape_input = (area.shape or "square").strip().lower()
        shape = self._choice(shape_input, SHAPES, "shape", "INVALID_PHOTO_SHAPE")
        fit = self._choice(area.fit, FITS, "fit", "INVALID_PHOTO_FIT")
        orientation = self._choice(area.hexagonOrientation, HEX_ORIENTATIONS, "hexagonOrientation", "INVALID_PHOTO_SHAPE")
        for name, value in (("focusX", area.focusX), ("focusY", area.focusY)):
            if not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
                raise PosterError("INVALID_PHOTO_FIT", f"{name} must be between 0 and 1.", 422)
        if not math.isfinite(area.cornerRadius) or not 0 <= area.cornerRadius <= 0.5:
            raise PosterError("INVALID_PHOTO_SHAPE", "cornerRadius must be between 0 and 0.5.", 422)
        fill = parse_rgb(area.backgroundColor or "#E0E0E0", "photoArea.backgroundColor")
        return PhotoConfig(
            shape=shape,
            fit=fit,
            focus=(float(area.focusX), float(area.focusY)),
            corner_radius=float(area.cornerRadius),
            hexagon_orientation=orientation,
            fill=fill,
            remove_background=bool(area.removeBackground),
            edge_expand=self.settings.placeholder_edge_expand_px,
        )

    def _output_format(self, value: str) -> Tuple[str, str]:
        key = (value or "").strip().lower()
        if key not in _OUTPUT_FORMATS:
            raise PosterError("INVALID_OUTPUT_OPTIONS", "outputFormat must be jpeg, png or webp.", 422)
        return _OUTPUT_FORMATS[key]
