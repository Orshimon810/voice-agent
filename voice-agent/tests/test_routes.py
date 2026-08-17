import pytest
from fastapi.testclient import TestClient

from app.airtable import AirtableError
from app.config import get_settings
from app.main import app, get_airtable_client, get_vapi_client
from app.models import PassengerRecord
from app.vapi import VapiError

SAMPLE_RECORD = PassengerRecord(
    record_id="recABC123",
    passenger_name="Yossi Cohen",
    phone="+972501234567",
    flight_number="LY315",
    destination="JFK",
    original_departure="06:00",
    delay_hours=3,
    alt_flight_time="09:30",
    refund_amount=850,
)


class FakeAirtableClient:
    def __init__(self, record: PassengerRecord | None = None) -> None:
        self.record = record
        self.updates: list[tuple[str, dict]] = []

    async def get_record(self, record_id: str) -> PassengerRecord | None:
        return self.record

    async def update_record(self, record_id: str, fields: dict) -> None:
        self.updates.append((record_id, fields))


class FailingAirtableClient:
    async def get_record(self, record_id: str) -> PassengerRecord | None:
        raise AirtableError("boom")


class FakeVapiClient:
    def __init__(self, response: dict | None = None) -> None:
        self.response = response or {"id": "call_999"}
        self.calls: list[dict] = []

    async def create_call(self, **kwargs: object) -> dict:
        self.calls.append(kwargs)
        return self.response


class FailingVapiClient:
    async def create_call(self, **kwargs: object) -> dict:
        raise VapiError("boom")


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


def test_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_get_passenger_success(client: TestClient) -> None:
    app.dependency_overrides[get_airtable_client] = lambda: FakeAirtableClient(SAMPLE_RECORD)

    response = client.get("/passengers/recABC123")

    assert response.status_code == 200
    body = response.json()
    assert body["raw"]["passenger_name"] == "Yossi Cohen"
    assert body["normalized"]["original_departure_spoken"] == "שש בבוקר"
    assert body["normalized"]["delay_hours_spoken"] == "שלוש"
    assert body["normalized"]["alt_flight_time_spoken"] == "תשע וחצי בבוקר"
    assert body["normalized"]["refund_amount_spoken"] == "שמונה מאות וחמישים"


def test_get_passenger_not_found(client: TestClient) -> None:
    app.dependency_overrides[get_airtable_client] = lambda: FakeAirtableClient(None)

    response = client.get("/passengers/recMISSING")

    assert response.status_code == 404


def test_get_passenger_airtable_unavailable(client: TestClient) -> None:
    app.dependency_overrides[get_airtable_client] = lambda: FailingAirtableClient()

    response = client.get("/passengers/recABC123")

    assert response.status_code == 502


def test_trigger_call_web_mode_does_not_call_vapi(client: TestClient) -> None:
    fake_airtable = FakeAirtableClient(SAMPLE_RECORD)
    fake_vapi = FakeVapiClient()
    app.dependency_overrides[get_airtable_client] = lambda: fake_airtable
    app.dependency_overrides[get_vapi_client] = lambda: fake_vapi

    response = client.post("/calls/trigger", json={"record_id": "recABC123", "mode": "web"})

    assert response.status_code == 200
    body = response.json()
    assert body["call_id"] is None
    assert body["variable_values"]["passenger_name"] == "Yossi Cohen"
    assert body["variable_values"]["original_departure_spoken"] == "שש בבוקר"
    assert body["variable_values"]["delay_hours_spoken"] == "שלוש"
    assert body["variable_values"]["alt_flight_time_spoken"] == "תשע וחצי בבוקר"
    assert body["variable_values"]["refund_amount_spoken"] == "שמונה מאות וחמישים"
    assert fake_vapi.calls == []
    assert fake_airtable.updates == []


def test_trigger_call_phone_mode_calls_vapi_and_persists(client: TestClient) -> None:
    fake_airtable = FakeAirtableClient(SAMPLE_RECORD)
    fake_vapi = FakeVapiClient(response={"id": "call_999"})
    app.dependency_overrides[get_airtable_client] = lambda: fake_airtable
    app.dependency_overrides[get_vapi_client] = lambda: fake_vapi

    response = client.post("/calls/trigger", json={"record_id": "recABC123", "mode": "phone"})

    assert response.status_code == 200
    body = response.json()
    assert body["call_id"] == "call_999"
    assert len(fake_vapi.calls) == 1
    assert fake_vapi.calls[0]["customer_number"] == "+972501234567"
    assert fake_airtable.updates == [
        ("recABC123", {"call_status": "pending", "call_id": "call_999"})
    ]


def test_trigger_call_record_not_found(client: TestClient) -> None:
    app.dependency_overrides[get_airtable_client] = lambda: FakeAirtableClient(None)

    response = client.post("/calls/trigger", json={"record_id": "recMISSING", "mode": "web"})

    assert response.status_code == 404


def test_trigger_call_vapi_failure(client: TestClient) -> None:
    app.dependency_overrides[get_airtable_client] = lambda: FakeAirtableClient(SAMPLE_RECORD)
    app.dependency_overrides[get_vapi_client] = lambda: FailingVapiClient()

    response = client.post("/calls/trigger", json={"record_id": "recABC123", "mode": "phone"})

    assert response.status_code == 502


def test_trigger_call_invalid_mode(client: TestClient) -> None:
    app.dependency_overrides[get_airtable_client] = lambda: FakeAirtableClient(SAMPLE_RECORD)

    response = client.post("/calls/trigger", json={"record_id": "recABC123", "mode": "sms"})

    assert response.status_code == 422
