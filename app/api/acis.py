from typing import Optional
from fastapi import APIRouter, Header, HTTPException

from app.config import SCRAPER_API_KEY, SCRAPER_HEADLESS
from app.schemas.acis import CaseRequest, CaseResponse, HealthResponse
from app.services.acis_service import fetch_acis_case

router = APIRouter(prefix="/acis", tags=["ACIS"])


@router.get("/health", response_model=HealthResponse)
async def health():
    return {
        "status": "ok",
        "profile_path": "tempfile (fresh per run)",
        "headless": SCRAPER_HEADLESS,
    }


@router.post("/case", response_model=CaseResponse)
async def get_acis_case(
    payload: CaseRequest,
    x_api_key: Optional[str] = Header(default=None)
):
    if x_api_key != SCRAPER_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")

    try:
        result = await fetch_acis_case(
            a_number=payload.a_number,
            nationality=payload.nationality
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))