import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.deps import get_template_service
from app.api.routes import poster
from app.config import Settings, get_settings
from app.errors import PosterError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("poster-engine")


def _body(code: str, message: str, details=None) -> dict:
    payload = {"success": False, "error": code, "message": message}
    if details:
        payload["details"] = details
    return payload


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        service = app.dependency_overrides.get(get_template_service, get_template_service)()
        yield
        service.cache.clear()

    app = FastAPI(title="Poster Engine", version="1.0.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def limit_body(request: Request, call_next):
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > settings.max_request_bytes:
            return JSONResponse(status_code=413, content=_body("REQUEST_TOO_LARGE", "The request body is too large."))
        return await call_next(request)

    @app.exception_handler(PosterError)
    async def poster_error_handler(request: Request, exc: PosterError):
        return JSONResponse(status_code=exc.status_code, content=_body(exc.code, exc.message, exc.details))

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        details = [{"field": ".".join(str(p) for p in e["loc"][1:]), "message": e["msg"]} for e in exc.errors()]
        return JSONResponse(status_code=422, content=_body("VALIDATION_ERROR", "The request body is invalid.", details))

    @app.exception_handler(StarletteHTTPException)
    async def http_handler(request: Request, exc: StarletteHTTPException):
        return JSONResponse(status_code=exc.status_code, content=_body("HTTP_ERROR", str(exc.detail)))

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception):
        logger.exception("Unhandled error on %s", request.url.path)
        message = "The poster could not be processed." if settings.is_production else f"Internal error: {exc}"
        return JSONResponse(status_code=500, content=_body("PROCESSING_FAILED", message))

    app.include_router(poster.router)

    @app.get("/health", tags=["health"])
    def health():
        return {"status": "ok"}

    return app


app = create_app()
