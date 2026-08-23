from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles


def mount_web_application(app: FastAPI, web_root: Path | None) -> None:
    """Mount only a previously compiled public frontend directory."""

    if web_root is None:
        return
    if not web_root.is_dir() or not (web_root / "index.html").is_file():
        raise RuntimeError(
            "WORK_STATION_WEB_ROOT must contain a compiled index.html"
        )
    app.mount("/", StaticFiles(directory=web_root, html=True), name="work-station-web")
