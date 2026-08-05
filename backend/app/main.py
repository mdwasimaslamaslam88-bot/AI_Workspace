from fastapi import FastAPI

app = FastAPI(title="AI Workspace Backend", version="0.1.0")

@app.get("/")
def read_root():
    return {
        "name": "AI Workspace Backend",
        "version": "0.1.0",
        "status": "running"
    }

@app.get("/health")
def read_health():
    return {
        "status": "healthy"
    }