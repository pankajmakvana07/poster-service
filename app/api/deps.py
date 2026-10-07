import logging
import secrets
from functools import lru_cache

from app.config import Settings, get_settings
from app.services.font_manager import FontManager
from app.services.image_loader import TemplateFetcher, TemplateUrlPolicy
from app.services.photo_renderer import PhotoRenderer
from app.services.poster_renderer import PosterRenderer
from app.services.template_analyzer import TemplateAnalyzer
from app.services.template_cache import build_cache
from app.services.template_service import TemplateService
from app.services.text_renderer import TextRenderer
from app.utils.signing import ContextSigner

logger = logging.getLogger(__name__)


def build_service(settings: Settings, fetcher: "TemplateFetcher | None" = None, cache=None) -> TemplateService:
    secret = settings.secret_key
    if not secret:
        if settings.is_production:
            raise RuntimeError("SECRET_KEY must be set when APP_ENV=production")
        secret = secrets.token_urlsafe(32)
        logger.warning("SECRET_KEY is not set; using a random per-process key (render contexts will not survive restarts).")
    fonts = FontManager(settings.fonts_dir, settings.default_font_file)
    fetcher = fetcher or TemplateFetcher(settings, TemplateUrlPolicy(settings))
    photos = PhotoRenderer(settings)
    texts = TextRenderer(settings, fonts)
    return TemplateService(
        settings=settings,
        fetcher=fetcher,
        cache=cache if cache is not None else build_cache(settings),
        signer=ContextSigner(secret),
        analyzer=TemplateAnalyzer(settings),
        renderer=PosterRenderer(photos, texts),
    )


@lru_cache
def get_template_service() -> TemplateService:
    return build_service(get_settings())
