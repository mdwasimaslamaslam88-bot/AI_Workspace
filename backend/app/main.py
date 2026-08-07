 # app/main.py

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.api.v1.router import router as api_v1_router
from app.exceptions.handlers import register_exception_handlers
from app.middleware.request_id import RequestIDMiddleware

app = FastAPI(
    title=settings.APP_TITLE,
    version=settings.APP_VERSION,
)

register_exception_handlers(app)
app.add_middleware(RequestIDMiddleware)

origins = settings.BACKEND_CORS_ORIGINS

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def read_root():
    return {
        "name": settings.APP_TITLE,
        "version": settings.APP_VERSION,
        "status": "running",
    }


app.include_router(api_v1_router, prefix="/api/v1")
