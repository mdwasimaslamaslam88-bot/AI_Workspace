# app/api/v1/router.py

from fastapi import APIRouter

from app.api.v1.ai import router as ai_router
from app.api.v1.assets import router as assets_router
from app.api.v1.conversations import router as conversations_router
from app.api.v1.documents import router as documents_router
from app.api.v1.health import router as health_router
from app.api.v1.images import router as images_router
from app.api.v1.memories import router as memories_router
from app.api.v1.tools import router as tools_router
from app.api.v1.users import router as users_router
from app.api.v1.voice import router as voice_router
from app.api.v1.workflows import router as workflows_router

router = APIRouter()

router.include_router(health_router)
router.include_router(users_router)
router.include_router(voice_router)
router.include_router(images_router)
router.include_router(memories_router)
router.include_router(tools_router)
router.include_router(workflows_router)
router.include_router(documents_router)
router.include_router(assets_router)
router.include_router(conversations_router)
router.include_router(ai_router)
