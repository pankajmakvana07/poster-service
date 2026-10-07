from typing import Any, List, Optional


class PosterError(Exception):
    """Structured application error rendered as JSON by the API layer."""

    def __init__(self, code: str, message: str, status_code: int = 422, details: Optional[List[Any]] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details
