import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

from app.config import Settings
from app.errors import PosterError
from app.services.font_manager import FontManager
from app.utils.color_utils import RGBA
from app.utils.geometry import PixelRect

ALIGNMENTS = ("left", "center", "right")
VERTICAL_ALIGNMENTS = ("top", "center", "bottom")
OVERFLOW_MODES = ("error", "ellipsis")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


@dataclass(frozen=True)
class TextStyle:
    text_color: RGBA
    background_color: RGBA
    align: str = "center"
    vertical_align: str = "center"
    padding: float = 0.1
    max_font_size: Optional[int] = None
    min_font_size: Optional[int] = None
    wrap: bool = True
    max_lines: Optional[int] = None
    overflow: str = "error"
    font_name: Optional[str] = None


@dataclass
class TextLayout:
    lines: List[str]
    font: ImageFont.FreeTypeFont
    font_size: int
    positions: List[Tuple[float, float]]
    truncated: bool = False


def sanitize_text(value: str) -> str:
    text = (value or "").replace("\r\n", "\n").replace("\r", "\n")
    text = _CONTROL_RE.sub("", text).replace("\t", " ")
    paragraphs = [" ".join(p.split()) for p in text.split("\n")]
    return "\n".join(paragraphs).strip("\n")


class TextRenderer:
    """Fits text into a box (font size search, wrapping, alignment) and draws background plus text."""

    def __init__(self, settings: Settings, fonts: FontManager):
        self.settings = settings
        self.fonts = fonts

    @staticmethod
    def _split_word(word: str, font: ImageFont.FreeTypeFont, max_width: float) -> List[str]:
        chunks: List[str] = []
        current = ""
        for ch in word:
            if not current or font.getlength(current + ch) <= max_width:
                current += ch
            else:
                chunks.append(current)
                current = ch
        chunks.append(current)
        return chunks

    def wrap(self, text: str, font: ImageFont.FreeTypeFont, max_width: float, wrap: bool) -> List[str]:
        lines: List[str] = []
        for paragraph in text.split("\n"):
            if not wrap or not paragraph:
                lines.append(paragraph)
                continue
            current = ""
            for word in paragraph.split(" "):
                candidate = word if not current else f"{current} {word}"
                if font.getlength(candidate) <= max_width:
                    current = candidate
                    continue
                if current:
                    lines.append(current)
                    current = ""
                if font.getlength(word) <= max_width:
                    current = word
                else:
                    chunks = self._split_word(word, font, max_width)
                    lines.extend(chunks[:-1])
                    current = chunks[-1]
            lines.append(current)
        return lines

    def _place(
        self,
        lines: List[str],
        font: ImageFont.FreeTypeFont,
        size: int,
        avail: Tuple[float, float, float, float],
        style: TextStyle,
    ) -> Optional[List[Tuple[float, float]]]:
        ax, ay, aw, ah = avail
        spacing = size * self.settings.line_spacing
        boxes = [font.getbbox(line, anchor="ls") if line else None for line in lines]
        filled = [(i, b) for i, b in enumerate(boxes) if b is not None]
        if not filled:
            return []
        if max(b[2] - b[0] for _, b in filled) > aw + 0.5:
            return None
        ink_top = min(b[1] + i * spacing for i, b in filled)
        ink_bottom = max(b[3] + i * spacing for i, b in filled)
        if ink_bottom - ink_top > ah + 0.5:
            return None
        if style.vertical_align == "top":
            first = ay - ink_top
        elif style.vertical_align == "bottom":
            first = ay + ah - ink_bottom
        else:
            cap = -font.getbbox("H", anchor="ls")[1]
            block = cap + (len(lines) - 1) * spacing
            first = ay + (ah - block) / 2 + cap
            if first + ink_top < ay - 0.5 or first + ink_bottom > ay + ah + 0.5:
                first = ay + (ah - (ink_bottom - ink_top)) / 2 - ink_top
        positions: List[Tuple[float, float]] = []
        for i, line in enumerate(lines):
            box = boxes[i]
            if box is None:
                positions.append((ax, first + i * spacing))
                continue
            ink_width = box[2] - box[0]
            if style.align == "left":
                x = ax - box[0]
            elif style.align == "right":
                x = ax + aw - ink_width - box[0]
            else:
                x = ax + (aw - ink_width) / 2 - box[0]
            positions.append((x, first + i * spacing))
        return positions

    def _try_size(self, text: str, size: int, avail: Tuple[float, float, float, float], style: TextStyle) -> Optional[TextLayout]:
        font = self.fonts.get(style.font_name, size)
        lines = self.wrap(text, font, avail[2], style.wrap)
        if style.max_lines is not None and len(lines) > style.max_lines:
            return None
        positions = self._place(lines, font, size, avail, style)
        if positions is None:
            return None
        return TextLayout(lines, font, size, positions)

    def _ellipsize(self, text: str, size: int, avail: Tuple[float, float, float, float], style: TextStyle) -> Optional[TextLayout]:
        font = self.fonts.get(style.font_name, size)
        lines = self.wrap(text, font, avail[2], style.wrap)
        spacing = size * self.settings.line_spacing
        limit = max(1, int((avail[3] - size * 0.95) // spacing) + 1)
        if style.max_lines is not None:
            limit = min(limit, style.max_lines)
        while limit >= 1:
            kept = lines[:limit]
            last = kept[-1].rstrip()
            while last and font.getlength(last + "…") > avail[2]:
                last = last[:-1].rstrip()
            kept[-1] = (last + "…") if last else "…"
            positions = self._place(kept, font, size, avail, style)
            if positions is not None:
                return TextLayout(kept, font, size, positions, truncated=True)
            limit -= 1
        return None

    def fit(self, text: str, rect: PixelRect, style: TextStyle, label: str = "field") -> TextLayout:
        pad = style.padding * min(rect.width, rect.height)
        avail = (pad, pad, rect.width - 2 * pad, rect.height - 2 * pad)
        if avail[2] < 1 or avail[3] < 1:
            raise PosterError("TEXT_DOES_NOT_FIT", f"{label}: the text box is too small.", 422)
        settings = self.settings
        low = max(1, style.min_font_size or settings.min_font_size_px)
        high = min(style.max_font_size or settings.max_font_size_px, settings.max_font_size_px, int(avail[3] * 1.15) + 1)
        low = min(low, max(1, high))
        high = max(high, low)

        best = self._try_size(text, high, avail, style)
        if best is not None:
            return best
        best = self._try_size(text, low, avail, style)
        if best is None:
            if style.overflow == "ellipsis":
                truncated = self._ellipsize(text, low, avail, style)
                if truncated is not None:
                    return truncated
            raise PosterError(
                "TEXT_DOES_NOT_FIT",
                f"{label}: the text does not fit in its box even at the minimum font size ({low}px).",
                422,
            )
        lo, hi = low, high - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            candidate = self._try_size(text, mid, avail, style)
            if candidate is not None:
                best, lo = candidate, mid
            else:
                hi = mid - 1
        return best

    def draw_field(self, canvas: Image.Image, rect: PixelRect, text: str, style: TextStyle, label: str = "field") -> TextLayout:
        layout = self.fit(text, rect, style, label)
        region = canvas.crop(rect.box).convert("RGB")
        background = style.background_color
        if background[3] > 0:
            if background[3] == 255:
                region.paste(background[:3], (0, 0, region.width, region.height))
            else:
                region.paste(background[:3], (0, 0), Image.new("L", region.size, background[3]))
        mask = Image.new("L", region.size, 0)
        draw = ImageDraw.Draw(mask)
        for line, (x, y) in zip(layout.lines, layout.positions):
            if line:
                draw.text((x, y), line, font=layout.font, fill=255, anchor="ls")
        if style.text_color[3] < 255:
            mask = mask.point(lambda v: v * style.text_color[3] // 255)
        region.paste(style.text_color[:3], (0, 0), mask)
        canvas.paste(region, (rect.x, rect.y))
        return layout
