from fastapi.testclient import TestClient

from app.airtable import AirtableError
from app.main import app, get_airtable_client
from app.models import PassengerRecord

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
    call_id="call_999",
)


class FakeAirtableClient:
    def __init__(self, record: PassengerRecord | None = None) -> None:
        self.record = record
        self.updates: list[tuple[str, dict]] = []

    async def find_record_by_call_id(self, call_id: str) -> PassengerRecord | None:
        return self.record

    async def update_record(self, record_id: str, fields: dict) -> None:
        self.updates.append((record_id, fields))


class FailingLookupAirtableClient:
    async def find_record_by_call_id(self, call_id: str) -> PassengerRecord | None:
        raise AirtableError("boom")


def _end_of_call_report(
    *,
    call_id: str = "call_999",
    ended_reason: str = "customer-ended-call",
    structured_data: dict | None = None,
    summary: str = "Passenger chose the alternative flight.",
    recording_url: str = "https://recordings.vapi.ai/call_999.mp3",
) -> dict:
    return {
        "message": {
            "type": "end-of-call-report",
            "endedReason": ended_reason,
            "call": {"id": call_id},
            "summary": summary,
            "recordingUrl": recording_url,
            "analysis": {"structuredData": structured_data} if structured_data else None,
        }
    }


def test_webhook_wrong_secret_returns_401(client: TestClient) -> None:
    response = client.post(
        "/webhooks/vapi",
        json=_end_of_call_report(),
        headers={"x-vapi-secret": "wrong"},
    )
    assert response.status_code == 401


def test_webhook_missing_secret_returns_401(client: TestClient) -> None:
    response = client.post("/webhooks/vapi", json=_end_of_call_report())
    assert response.status_code == 401


def test_webhook_ignores_other_event_types(client: TestClient) -> None:
    app.dependency_overrides[get_airtable_client] = lambda: FakeAirtableClient(SAMPLE_RECORD)
    response = client.post(
        "/webhooks/vapi",
        json={"message": {"type": "status-update"}},
        headers={"x-vapi-secret": "secret"},
    )
    assert response.status_code == 200
    assert response.json() == {"status": "ignored"}


def test_webhook_malformed_json_returns_200(client: TestClient) -> None:
    response = client.post(
        "/webhooks/vapi",
        content=b"{not valid json",
        headers={"x-vapi-secret": "secret", "content-type": "application/json"},
    )
    assert response.status_code == 200
    assert response.json() == {"status": "ignored"}


def test_webhook_unexpected_shape_returns_200(client: TestClient) -> None:
    response = client.post(
        "/webhooks/vapi",
        json={"message": {"call": "not-an-object"}},
        headers={"x-vapi-secret": "secret"},
    )
    assert response.status_code == 200
    assert response.json() == {"status": "ignored"}


def test_webhook_record_not_found_returns_200_and_does_not_write(client: TestClient) -> None:
    fake_airtable = FakeAirtableClient(None)
    app.dependency_overrides[get_airtable_client] = lambda: fake_airtable

    response = client.post(
        "/webhooks/vapi", json=_end_of_call_report(), headers={"x-vapi-secret": "secret"}
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ignored"}
    assert fake_airtable.updates == []


def test_webhook_airtable_lookup_failure_returns_200_not_5xx(client: TestClient) -> None:
    app.dependency_overrides[get_airtable_client] = lambda: FailingLookupAirtableClient()

    response = client.post(
        "/webhooks/vapi", json=_end_of_call_report(), headers={"x-vapi-secret": "secret"}
    )

    assert response.status_code == 200
    assert response.json() == {"status": "error"}


def test_webhook_success_writes_completed_status_and_decision(client: TestClient) -> None:
    fake_airtable = FakeAirtableClient(SAMPLE_RECORD)
    app.dependency_overrides[get_airtable_client] = lambda: fake_airtable

    response = client.post(
        "/webhooks/vapi",
        json=_end_of_call_report(
            ended_reason="customer-ended-call",
            structured_data={"decision": "alternative_flight"},
        ),
        headers={"x-vapi-secret": "secret"},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert len(fake_airtable.updates) == 1
    record_id, fields = fake_airtable.updates[0]
    assert record_id == "recABC123"
    assert fields["call_status"] == "completed"
    assert fields["passenger_decision"] == "alternative_flight"
    assert fields["call_summary"] == "Passenger chose the alternative flight."
    assert fields["recording_url"] == "https://recordings.vapi.ai/call_999.mp3"
    assert "call_timestamp" in fields


def test_webhook_voicemail_maps_to_no_answer(client: TestClient) -> None:
    fake_airtable = FakeAirtableClient(SAMPLE_RECORD)
    app.dependency_overrides[get_airtable_client] = lambda: fake_airtable

    response = client.post(
        "/webhooks/vapi",
        json=_end_of_call_report(ended_reason="voicemail"),
        headers={"x-vapi-secret": "secret"},
    )

    assert response.status_code == 200
    assert fake_airtable.updates[0][1]["call_status"] == "no_answer"


def test_webhook_error_reason_maps_to_failed(client: TestClient) -> None:
    fake_airtable = FakeAirtableClient(SAMPLE_RECORD)
    app.dependency_overrides[get_airtable_client] = lambda: fake_airtable

    response = client.post(
        "/webhooks/vapi",
        json=_end_of_call_report(ended_reason="pipeline-error-openai-llm-failed"),
        headers={"x-vapi-secret": "secret"},
    )

    assert response.status_code == 200
    assert fake_airtable.updates[0][1]["call_status"] == "failed"


def test_webhook_missing_structured_data_defaults_to_undecided(client: TestClient) -> None:
    fake_airtable = FakeAirtableClient(SAMPLE_RECORD)
    app.dependency_overrides[get_airtable_client] = lambda: fake_airtable

    response = client.post(
        "/webhooks/vapi",
        json=_end_of_call_report(structured_data=None),
        headers={"x-vapi-secret": "secret"},
    )

    assert response.status_code == 200
    assert fake_airtable.updates[0][1]["passenger_decision"] == "undecided"


def test_webhook_unrecognized_decision_defaults_to_undecided(client: TestClient) -> None:
    fake_airtable = FakeAirtableClient(SAMPLE_RECORD)
    app.dependency_overrides[get_airtable_client] = lambda: fake_airtable

    response = client.post(
        "/webhooks/vapi",
        json=_end_of_call_report(structured_data={"decision": "not_a_real_choice"}),
        headers={"x-vapi-secret": "secret"},
    )

    assert response.status_code == 200
    assert fake_airtable.updates[0][1]["passenger_decision"] == "undecided"
