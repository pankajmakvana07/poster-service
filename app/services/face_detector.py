"""
Face detection for dynamic-posters.
Mirrors static-poster implementation: YuNet (ONNX) primary, Haar cascade fallback.
Uses face_detection_yunet_2023mar.onnx from the models/ folder.
"""
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import numpy as np

logger = logging.getLogger("poster-engine.face_detector")

EYE_DISTANCE_TO_FACE_WIDTH = 0.38
EYE_MIDPOINT_TO_FACE_CENTER = 0.10


@dataclass
class Face:
    x: int
    y: int
    width: int
    height: int
    confidence: float

    @property
    def area(self) -> int:
        return self.width * self.height

    @property
    def center_x(self) -> float:
        return self.x + self.width / 2

    @property
    def center_y(self) -> float:
        return self.y + self.height / 2


def _iou(a: Face, b: Face) -> float:
    x0, y0 = max(a.x, b.x), max(a.y, b.y)
    x1 = min(a.x + a.width, b.x + b.width)
    y1 = min(a.y + a.height, b.y + b.height)
    inter = max(0, x1 - x0) * max(0, y1 - y0)
    union = a.area + b.area - inter
    return inter / union if union else 0.0


def non_max_suppression(faces: List[Face], threshold: float = 0.3) -> List[Face]:
    ordered = sorted(faces, key=lambda f: f.confidence * f.area, reverse=True)
    kept: List[Face] = []
    for face in ordered:
        if all(_iou(face, other) < threshold for other in kept):
            kept.append(face)
    return kept


def face_from_landmarks(right_eye, left_eye, confidence: float) -> Optional[Face]:
    eye_distance = float(np.hypot(left_eye[0] - right_eye[0], left_eye[1] - right_eye[1]))
    if eye_distance < 4:
        return None
    face_width = eye_distance / EYE_DISTANCE_TO_FACE_WIDTH
    center_x = (left_eye[0] + right_eye[0]) / 2
    center_y = (left_eye[1] + right_eye[1]) / 2 + EYE_MIDPOINT_TO_FACE_CENTER * face_width
    return Face(
        x=int(round(center_x - face_width / 2)),
        y=int(round(center_y - face_width / 2)),
        width=int(round(face_width)),
        height=int(round(face_width)),
        confidence=confidence,
    )


def _silence_cv2_logs() -> None:
    try:
        import cv2
        cv2.setLogLevel(0)
    except Exception:
        pass


class YuNetDetector:
    name = "yunet"

    def __init__(self, model_path: Path, score_threshold: float = 0.6):
        self.model_path = Path(model_path)
        self.score_threshold = score_threshold
        self._usable: Optional[bool] = None

    @property
    def available(self) -> bool:
        if self._usable is None:
            self._usable = False
            if self.model_path.is_file():
                try:
                    import cv2
                    if hasattr(cv2, "FaceDetectorYN"):
                        self._run(np.zeros((320, 320, 3), np.uint8))
                        self._usable = True
                except Exception:
                    logger.warning("YuNet self-test failed; will use Haar cascade", exc_info=True)
        return self._usable

    def _run(self, image_bgr: np.ndarray):
        import cv2
        height, width = image_bgr.shape[:2]
        scale = min(1.0, 1280 / max(width, height))
        new_w = max(32, round(width * scale / 32) * 32)
        new_h = max(32, round(height * scale / 32) * 32)
        resized = cv2.resize(image_bgr, (new_w, new_h))
        detector = cv2.FaceDetectorYN.create(
            str(self.model_path), "", (new_w, new_h), self.score_threshold, 0.3, 5000
        )
        _, raw = detector.detect(resized)
        return raw, width / new_w, height / new_h

    def detect(self, image_bgr: np.ndarray) -> List[Face]:
        raw, sx, sy = self._run(image_bgr)
        faces: List[Face] = []
        if raw is None:
            return faces
        h_img, w_img = image_bgr.shape[:2]
        for row in raw:
            confidence = float(row[14])
            # Try landmark-based face rect first (more accurate)
            right_eye = (float(row[4]) * sx, float(row[5]) * sy)
            left_eye = (float(row[6]) * sx, float(row[7]) * sy)
            face = face_from_landmarks(right_eye, left_eye, confidence)
            if face is None:
                x0 = max(0, int(row[0] * sx))
                y0 = max(0, int(row[1] * sy))
                face = Face(
                    x=x0,
                    y=y0,
                    width=int(min(row[2] * sx, w_img - x0)),
                    height=int(min(row[3] * sy, h_img - y0)),
                    confidence=confidence,
                )
            faces.append(face)
        return non_max_suppression(faces)


class HaarDetector:
    name = "haar"

    def detect(self, image_bgr: np.ndarray) -> List[Face]:
        try:
            import cv2
            cascade_path = Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"
            cascade = cv2.CascadeClassifier(str(cascade_path))
            if cascade.empty():
                return []
            height, width = image_bgr.shape[:2]
            scale = min(1.0, 1000 / max(height, width))
            resized = cv2.resize(image_bgr, (max(1, int(width * scale)), max(1, int(height * scale))))
            gray = cv2.equalizeHist(cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY))
            min_size = max(24, int(min(gray.shape) * 0.06))
            rects, _, weights = cascade.detectMultiScale3(
                gray, scaleFactor=1.1, minNeighbors=6, minSize=(min_size, min_size), outputRejectLevels=True
            )
            if len(rects) == 0:
                return []
            faces = [
                Face(
                    x=int(x / scale), y=int(y / scale),
                    width=int(w / scale), height=int(h / scale),
                    confidence=float(min(1.0, max(0.0, float(wt) / 10.0))),
                )
                for (x, y, w, h), wt in zip(rects, np.ravel(weights))
            ]
            return non_max_suppression(faces)
        except Exception as e:
            logger.warning("Haar detection failed: %s", e)
            return []


class FaceDetector:
    """Composite detector: YuNet → Haar → None."""

    def __init__(self, yunet_model_path: Optional[Path] = None):
        _silence_cv2_logs()
        self._yunet: Optional[YuNetDetector] = None
        self._haar = HaarDetector()
        self._cv2_ok: Optional[bool] = None
        if yunet_model_path and Path(yunet_model_path).is_file():
            self._yunet = YuNetDetector(Path(yunet_model_path))

    def _check_cv2(self) -> bool:
        if self._cv2_ok is None:
            try:
                import cv2  # noqa: F401
                self._cv2_ok = True
            except ImportError:
                logger.info("opencv not installed; face detection disabled.")
                self._cv2_ok = False
        return self._cv2_ok

    def _to_bgr(self, image) -> np.ndarray:
        import cv2
        rgb = np.asarray(image.convert("RGB"))
        return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)

    def detect_best(self, image) -> Optional[Face]:
        """Returns the best detected face from a PIL Image, or None."""
        if not self._check_cv2():
            return None
        image_bgr = self._to_bgr(image)
        faces: List[Face] = []

        # 1. Try YuNet
        if self._yunet and self._yunet.available:
            try:
                faces = self._yunet.detect(image_bgr)
            except Exception as e:
                logger.warning("YuNet failed: %s", e)

        # 2. Fallback to Haar
        if not faces:
            try:
                faces = self._haar.detect(image_bgr)
            except Exception as e:
                logger.warning("Haar failed: %s", e)

        if not faces:
            return None

        # Pick largest confident face
        confident = [f for f in faces if f.confidence >= 0.4]
        pool = confident if confident else faces
        return max(pool, key=lambda f: f.area)
