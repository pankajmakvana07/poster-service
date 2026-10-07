from fastapi import APIRouter, Depends

from app.api.deps import get_template_service
from app.schemas.poster import ErrorBody, RenderRequest, RenderResponse
from app.services.template_service import TemplateService

router = APIRouter(tags=["posters"])

_ERRORS = {code: {"model": ErrorBody} for code in (400, 409, 410, 413, 415, 422, 502, 504)}


@router.post("/api/v1/posters/render", response_model=RenderResponse, responses=_ERRORS)
@router.post("/posters/render", response_model=RenderResponse, responses=_ERRORS)
def render(request: RenderRequest, service: TemplateService = Depends(get_template_service)) -> RenderResponse:
    return service.render(request)
