# app/api/v1/router.py

from fastapi import APIRouter
from app.api.v1.conversations import router as conversations_router
from app.api.v1.health import router as health_router
from app.api.v1.users import router as users_router

router = APIRouter()

router.include_router(health_router)
router.include_router(users_router)
router.include_router(conversations_router)
