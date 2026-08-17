import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app


@pytest.fixture
def env_settings(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AIRTABLE_TOKEN", "test-token")
    monkeypatch.setenv("VAPI_API_KEY", "vapi-key")
    monkeypatch.setenv("VAPI_ASSISTANT_ID", "assistant-id")
    monkeypatch.setenv("VAPI_PHONE_NUMBER_ID", "phone-id")
    monkeypatch.setenv("WEBHOOK_SECRET", "secret")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def client(env_settings: None):
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
