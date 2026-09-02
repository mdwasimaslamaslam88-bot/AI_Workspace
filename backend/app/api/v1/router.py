# app/api/v1/router.py

from fastapi import APIRouter

from app.api.v1.agent_os import router as agent_os_router
from app.api.v1.ai import router as ai_router
from app.api.v1.assets import router as assets_router
from app.api.v1.conversations import router as conversations_router
from app.api.v1.communications import router as communications_router
from app.api.v1.connectors import router as connectors_router
from app.api.v1.creative import router as creative_router
from app.api.v1.documents import router as documents_router
from app.api.v1.external_ai import router as external_ai_router
from app.api.v1.finance import router as finance_router
from app.api.v1.features import router as features_router
from app.api.v1.self_update import router as self_update_router
from app.api.v1.diagnostics import router as diagnostics_router
from app.api.v1.health import router as health_router
from app.api.v1.images import router as images_router
from app.api.v1.learning import router as learning_router
from app.api.v1.memories import router as memories_router
from app.api.v1.marketing import router as marketing_router
from app.api.v1.tools import router as tools_router
from app.api.v1.users import router as users_router
from app.api.v1.voice import router as voice_router
from app.api.v1.workflows import router as workflows_router

router = APIRouter()

router.include_router(health_router)
router.include_router(agent_os_router)
router.include_router(external_ai_router)
router.include_router(finance_router)
router.include_router(features_router)
router.include_router(self_update_router)
router.include_router(users_router)
router.include_router(communications_router)
router.include_router(connectors_router)
router.include_router(creative_router)
router.include_router(voice_router)
router.include_router(images_router)
router.include_router(learning_router)
router.include_router(memories_router)
router.include_router(marketing_router)
router.include_router(tools_router)
router.include_router(workflows_router)
router.include_router(documents_router)
router.include_router(diagnostics_router)
router.include_router(assets_router)
router.include_router(conversations_router)
router.include_router(ai_router)
