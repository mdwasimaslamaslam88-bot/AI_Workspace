# app/main.py

from fastapi import FastAPI
from app.core.config import settings


app = FastAPI(title=settings.APP_TITLE, version=settings.APP_VERSION)

@app.get("/")
def read_root():
    return {
        "name": settings.APP_TITLE,
        "version": settings.APP_VERSION,
        "status": "running"
    }

@app.get("/health")
def read_health():
    return {
        "status": "healthy"
    }