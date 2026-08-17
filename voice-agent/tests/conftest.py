import pytest
from fastapi.testclient import TestClient

from app import main as main_module
from app.config import get_settings
from app.main import app, get_vapi_client


class _StubVapiClient:
    """Default get_vapi_client override for tests that don't care about it —
    never hits the real network. Tests exercising structuredOutputs polling
    should override get_vapi_client themselves with a purpose-built fake."""

    async def get_call(self, call_id: str) -> dict:
        return {}


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


@pytest.fixture(autouse=True)
def _fast_structured_outputs_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    """The webhook's structuredOutputs poll normally sleeps between GET
    /call/{id} attempts; zero that out so tests stay fast regardless of
    whether they care about the polling behavior."""
    monkeypatch.setattr(main_module, "_STRUCTURED_OUTPUTS_POLL_DELAY_SECONDS", 0)


@pytest.fixture
def client(env_settings: None):
    app.dependency_overrides[get_vapi_client] = lambda: _StubVapiClient()
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
