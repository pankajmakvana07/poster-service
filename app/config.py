import tempfile
from functools import lru_cache
from pathlib import Path
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent


class Settings(BaseSettings):
    """Environment driven configuration (see .env.example)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    secret_key: str = ""
    render_context_ttl_seconds: int = 86400

    allowed_template_hosts: str = "*"
    allow_http_templates: bool = False
    allow_private_hosts: bool = False
    allow_custom_ports: bool = True
    download_timeout_seconds: float = 10.0
    max_redirects: int = 3
    max_template_bytes: int = 15 * 1024 * 1024
    max_template_pixels: int = 25_000_000

    max_photo_bytes: int = 12 * 1024 * 1024
    max_photo_pixels: int = 40_000_000
    min_photo_side: int = 16
    max_request_bytes: int = 20 * 1024 * 1024

    template_cache_backend: str = "disk"
    template_cache_dir: str = ""
    template_cache_ttl_seconds: int = 1800
    template_cache_max_entries: int = 32
    template_cache_max_memory_mb: int = 128

    fonts_dir: Path = BASE_DIR / "fonts"
    models_dir: Path = BASE_DIR.parent / "models"
    segmentation_model: str = "u2netp"
    default_font_file: str = "default-font.ttf"
    min_font_size_px: int = 10
    max_font_size_px: int = 400
    line_spacing: float = 1.15

    default_placeholder_tolerance: int = 24
    placeholder_search_margin: float = 0.5
    placeholder_edge_expand_px: int = 2

    min_fields: int = 0
    max_fields: int = 9
    max_text_length: int = 500

    output_jpeg_quality: int = 92
    preview_max_width: int = 720
    cors_origins: str = "*"

    @property
    def allowed_hosts(self) -> List[str]:
        return [h.strip().lower() for h in self.allowed_template_hosts.split(",") if h.strip()]

    @property
    def cors_origin_list(self) -> List[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def cache_directory(self) -> Path:
        if self.template_cache_dir:
            return Path(self.template_cache_dir)
        return Path(tempfile.gettempdir()) / "poster-engine-cache"

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
