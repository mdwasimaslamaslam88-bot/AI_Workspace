# app/api/v1/health.py

from fastapi import APIRouter

router = APIRouter()

@router.get("/health", tags=["Health"])
def read_health():
    return {"status": "healthy"}