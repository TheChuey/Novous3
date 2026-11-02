"""Model-profiles doorway: FastAPI router for /api/model-profiles."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from modelProfiles.logic_modelProfiles import model_profiles

router = APIRouter()


class ModelProfileRequest(BaseModel):
    num_ctx: int
    reply_reserve: int
    max_tools: int | None = None


@router.get("/api/model-profiles")
def api_list_model_profiles():
    return model_profiles.list_models()


@router.put("/api/model-profiles/{model:path}")
def api_save_model_profile(model: str, req: ModelProfileRequest):
    try:
        return model_profiles.save(model, req.num_ctx, req.reply_reserve, req.max_tools)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/api/model-profiles/{model:path}")
def api_reset_model_profile(model: str):
    return model_profiles.reset(model)
