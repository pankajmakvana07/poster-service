"""
Background remover for dynamic-posters.
Uses rembg with local u2netp.onnx (no internet download).
Mirrors static-poster RembgRemover approach.
"""
import logging
import os
import threading
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image

logger = logging.getLogger("poster-engine.bg_remover")


def _refine_mask(mask: Image.Image) -> Image.Image:
    """Clean up the raw rembg mask: kill weak noise, soft-blur edges."""
    try:
        import cv2
        array = np.array(mask.convert("L"), dtype=np.uint8).copy()
        array[array < 30] = 0
        array = cv2.GaussianBlur(array, (3, 3), 0)
        return Image.fromarray(array)
    except ImportError:
        return mask


class BackgroundRemover:
    """
    Removes image background using rembg with local u2netp.onnx model.
    Gracefully falls back (returns original image) if rembg is not installed.
    """

    def __init__(self, model_name: str = "u2netp", models_dir: Optional[Path] = None):
        self.model_name = model_name
        self.models_dir = models_dir or (Path(__file__).resolve().parent.parent.parent / "models")
        self._session = None
        self._lock = threading.Lock()
        self._available: Optional[bool] = None

    def is_available(self) -> bool:
        if self._available is None:
            try:
                import rembg  # noqa: F401
                self._available = True
            except ImportError:
                logger.info("rembg not installed. Background removal disabled.")
                self._available = False
        return self._available

    def _get_session(self):
        if self._session is None and self.is_available():
            with self._lock:
                if self._session is None:
                    if self.models_dir and Path(self.models_dir).exists():
                        os.environ.setdefault("U2NET_HOME", str(self.models_dir))
                    from rembg import new_session
                    try:
                        self._session = new_session(self.model_name)
                        logger.info("Background removal model loaded: %s from %s", self.model_name, self.models_dir)
                    except Exception as e:
                        logger.warning("Failed to load model %s: %s; falling back to u2netp", self.model_name, e)
                        self._session = new_session("u2netp")
        return self._session

    def _get_foreground_mask(self, image: Image.Image) -> Image.Image:
        """
        Returns a grayscale (L) mask where 255 = foreground, 0 = background.
        Mirrors static-poster foreground_mask() API.
        """
        from rembg import remove
        session = self._get_session()
        with self._lock:
            raw_mask = remove(
                image.convert("RGB"),
                session=session,
                only_mask=True,
                post_process_mask=False,
            )
        return _refine_mask(raw_mask)

    def remove_background(self, image: Image.Image) -> Image.Image:
        """
        Returns an RGBA image with background removed.
        Falls back to original RGB image if rembg is unavailable.
        """
        if not self.is_available():
            return image

        try:
            from rembg import remove
            session = self._get_session()
            with self._lock:
                result = remove(
                    image.convert("RGB"),
                    session=session,
                    only_mask=False,
                    post_process_mask=True,
                )
            return result
        except Exception as e:
            logger.warning("Background removal failed, using original: %s", e)
            return image
