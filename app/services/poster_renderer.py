from dataclasses import dataclass
from typing import List, Optional, Tuple

from PIL import Image, ImageDraw

from app.services.photo_renderer import PhotoConfig, PhotoRenderer
from app.services.template_analyzer import PhotoRegion
from app.services.text_renderer import TextRenderer, TextStyle
from app.utils.geometry import PixelRect


@dataclass
class FieldRender:
    key: str
    rect: PixelRect
    text: str
    style: TextStyle


class PosterRenderer:
    """Orchestrates template + photo + fields into the final poster (photo first, then text)."""

    def __init__(self, photo_renderer: PhotoRenderer, text_renderer: TextRenderer):
        self.photos = photo_renderer
        self.texts = text_renderer

    def render(
        self,
        template: Image.Image,
        region: Optional[PhotoRegion],
        photo: Optional[Image.Image],
        photo_config: Optional[PhotoConfig],
        fields: List[FieldRender],
        photo_bbox: Optional[PixelRect] = None,
    ) -> Tuple[Image.Image, List[str]]:
        warnings: List[str] = []
        if photo is not None and photo_config is not None:
            canvas, photo_warnings = self.photos.compose(template, region, photo, photo_config, bbox=photo_bbox)
            warnings.extend(photo_warnings)
        else:
            canvas = template.copy()

        for item in fields:
            if item.text and item.text.strip():
                layout = self.texts.draw_field(canvas, item.rect, item.text, item.style, item.key)
                if layout.truncated:
                    warnings.append(f"{item.key}: text was shortened with an ellipsis to fit.")
        return canvas, warnings

    @staticmethod
    def preview(template: Image.Image, region: PhotoRegion, field_rects: List[Tuple[str, PixelRect]], guides: bool, max_width: int) -> Image.Image:
        canvas = template.copy()
        if guides:
            draw = ImageDraw.Draw(canvas)
            line = max(2, canvas.width // 400)
            draw.rectangle(region.bbox.box, outline=(255, 0, 200), width=line)
            for _, rect in field_rects:
                draw.rectangle(rect.box, outline=(0, 200, 255), width=line)
        if canvas.width > max_width:
            ratio = max_width / canvas.width
            canvas = canvas.resize((max_width, max(1, round(canvas.height * ratio))), Image.LANCZOS)
        return canvas
