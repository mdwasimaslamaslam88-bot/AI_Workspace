from fastapi.testclient import TestClient
from app.main import app

def test_lifespan_exposes_unconfigured_clients_and_cleans_up():
    with TestClient(app):
        assert app.state.postgres_engine is None
        assert app.state.redis_client is None
        assert app.state.ollama_client is None
    assert app.state.postgres_engine is None
    assert app.state.redis_client is None
    assert app.state.ollama_client is None
