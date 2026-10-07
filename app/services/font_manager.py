import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

from PIL import ImageFont

from app.errors import PosterError

_NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]+\.(ttf|otf)$")


@lru_cache(maxsize=512)
def _load_font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


class FontManager:
    """Resolves bundled fonts by file name; a future template can select a font by name."""

    def __init__(self, fonts_dir: Path, default_file: str):
        self.fonts_dir = Path(fonts_dir).resolve()
        self.default_file = default_file
        self.resolve(None)

    def resolve(self, name: Optional[str]) -> Path:
        file_name = name or self.default_file
        if not _NAME_RE.match(file_name):
            raise PosterError("INVALID_FONT", f"Font '{file_name}' is not a valid font file name.", 422)
        path = (self.fonts_dir / file_name).resolve()
        if self.fonts_dir not in path.parents or not path.is_file():
            raise PosterError("INVALID_FONT", f"Font '{file_name}' is not available.", 422)
        return path

    def get(self, name: Optional[str], size: int) -> ImageFont.FreeTypeFont:
        return _load_font(str(self.resolve(name)), max(1, int(size)))
