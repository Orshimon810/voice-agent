import re
from typing import Any

from fastapi.testclient import TestClient

from app.airtable import AirtableError
from app.main import (
    _extract_call_timestamp,
    _extract_decision,
    _find_structured_result,
    app,
    get_airtable_client,
    get_vapi_client,
)
from app.models import PassengerDecision, PassengerRecord, VapiAnalysis, VapiCall, VapiMessage
from app.vapi import VapiError

_ISO8601_UTC_MS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")

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


class FakeVapiClient:
    """Mocked VapiClient.get_call: returns each entry in call_responses in
    order (or raises it, if it's a VapiError), one per poll attempt."""

    def __init__(self, call_responses: list[dict[str, Any] | Exception] | None = None) -> None:
        self._call_responses = list(call_responses or [])
        self.get_call_calls: list[str] = []

    async def get_call(self, call_id: str) -> dict[str, Any]:
        self.get_call_calls.append(call_id)
        if not self._call_responses:
            return {}
        response = self._call_responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _end_of_call_report(
    *,
    call_id: str = "call_999",
    ended_reason: str = "customer-ended-call",
    structured_outputs: dict | None = None,
    structured_data: dict | None = None,
    summary: str | None = "Passenger chose the alternative flight.",
    recording_url: str = "https://recordings.vapi.ai/call_999.mp3",
) -> dict:
    message: dict = {
        "type": "end-of-call-report",
        "endedReason": ended_reason,
        "call": {"id": call_id},
        "summary": summary,
        "recordingUrl": recording_url,
    }
    if structured_outputs is not None:
        message["structuredOutputs"] = structured_outputs
    if structured_data is not None:
        message["analysis"] = {"structuredData": structured_data}
    return {"message": message}


def _structured_outputs(*, decision: str | None = None, summary: str | None = None) -> dict:
    """Build Vapi's real structuredOutputs shape (captured from production):
    random-UUID keys, each value a {"name", "result", "compliancePlan"}
    object. This lives at message.structuredOutputs, a sibling of
    "analysis" — not nested inside it."""
    data: dict[str, dict] = {}
    if decision is not None:
        data["3fa85f64-5717-4562-b3fc-2c963f66afa6"] = {
            "name": "decision",
            "result": decision,
            "compliancePlan": None,
        }
    if summary is not None:
        data["9c858901-8a57-4791-81fe-4c455b099bc9"] = {
            "name": "summary",
            "result": summary,
            "compliancePlan": None,
        }
    return data


def _structured_data(*, decision: str | None = None, summary: str | None = None) -> dict:
    """Build the legacy (no longer read) analysis.structuredData shape, for
    tests confirming that path is no longer required."""
    data: dict[str, dict] = {}
    if decision is not None:
        data["3fa85f64-5717-4562-b3fc-2c963f66afa6"] = {"name": "decision", "result": decision}
    if summary is not None:
        data["9c858901-8a57-4791-81fe-4c455b099bc9"] = {"name": "summary", "result": summary}
    return data


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
            structured_outputs=_structured_outputs(decision="alternative_flight"),
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


def test_webhook_missing_structured_outputs_defaults_to_undecided(client: TestClient) -> None:
    fake_airtable = FakeAirtableClient(SAMPLE_RECORD)
    app.dependency_overrides[get_airtable_client] = lambda: fake_airtable

    response = client.post(
        "/webhooks/vapi",
        json=_end_of_call_report(structured_outputs=None),
        headers={"x-vapi-secret": "secret"},
    )

    assert response.status_code == 200
    assert fake_airtable.updates[0][1]["passenger_decision"] == "undecided"


def test_webhook_recognizes_callback_requested_decision(client: TestClient) -> None:
    fake_airtable = FakeAirtableClient(SAMPLE_RECORD)
    app.dependency_overrides[get_airtable_client] = lambda: fake_airtable

    response = client.post(
        "/webhooks/vapi",
        json=_end_of_call_report(
            structured_outputs=_structured_outputs(decision="callback_requested")
        ),
        headers={"x-vapi-secret": "secret"},
    )

    assert response.status_code == 200
    assert fake_airtable.updates[0][1]["passenger_decision"] == "callback_requested"


def test_find_structured_result_matches_uuid_keyed_entry() -> None:
    structured = {
        "3fa85f64-5717-4562-b3fc-2c963f66afa6": {
            "name": "decision",
            "result": "refund",
            "compliancePlan": None,
        },
        "9c858901-8a57-4791-81fe-4c455b099bc9": {
            "name": "summary",
            "result": "some summary",
            "compliancePlan": None,
        },
    }
    assert _find_structured_result(structured, "decision") == "refund"
    assert _find_structured_result(structured, "summary") == "some summary"


def test_find_structured_result_returns_none_when_name_absent() -> None:
    structured = {
        "3fa85f64-5717-4562-b3fc-2c963f66afa6": {
            "name": "decision",
            "result": "refund",
            "compliancePlan": None,
        }
    }
    assert _find_structured_result(structured, "summary") is None


def test_find_structured_result_handles_missing_or_empty_structured_data() -> None:
    assert _find_structured_result(None, "decision") is None
    assert _find_structured_result({}, "decision") is None


def test_find_structured_result_ignores_malformed_entries() -> None:
    structured = {
        "not-a-dict-entry": "oops",
        "missing-result": {"name": "decision"},
        "non-string-result": {"name": "decision", "result": 42},
    }
    assert _find_structured_result(structured, "decision") is None


def test_extract_decision_from_uuid_keyed_structured_outputs() -> None:
    """Exact shape captured from production: structuredOutputs is a
    top-level field on the message, sibling to "analysis"."""
    message = VapiMessage(
        structuredOutputs={
            "3fa85f64-5717-4562-b3fc-2c963f66afa6": {
                "name": "decision",
                "result": "refund",
                "compliancePlan": None,
            },
            "c3b2a1f0-1234-4abc-9def-0123456789ab": {
                "name": "some_other_field",
                "result": "irrelevant",
                "compliancePlan": None,
            },
        }
    )
    assert _extract_decision(message) == "refund"


def test_extract_decision_defaults_to_undecided_for_unknown_result() -> None:
    message = VapiMessage(
        structuredOutputs={
            "3fa85f64-5717-4562-b3fc-2c963f66afa6": {
                "name": "decision",
                "result": "not_a_real_choice",
                "compliancePlan": None,
            }
        }
    )
    assert _extract_decision(message) == PassengerDecision.undecided.value


def test_extract_decision_defaults_to_undecided_when_structured_outputs_missing() -> None:
    assert _extract_decision(VapiMessage()) == PassengerDecision.undecided.value


def test_extract_decision_defaults_to_undecided_when_structured_outputs_empty() -> None:
    message = VapiMessage(structuredOutputs={})
    assert _extract_decision(message) == PassengerDecision.undecided.value


def test_extract_decision_ignores_legacy_analysis_structured_data() -> None:
    """The old analysis.structuredData path is no longer read — decisions
    must come from message.structuredOutputs instead. analysis is empty in
    production because analysisPlan.summaryPlan is disabled."""
    message = VapiMessage(
        analysis=VapiAnalysis(
            structuredData={
                "3fa85f64-5717-4562-b3fc-2c963f66afa6": {"name": "decision", "result": "refund"}
            }
        )
    )
    assert _extract_decision(message) == PassengerDecision.undecided.value


def test_webhook_extracts_decision_from_uuid_keyed_structured_outputs(
    client: TestClient,
) -> None:
    fake_airtable = FakeAirtableClient(SAMPLE_RECORD)
    app.dependency_overrides[get_airtable_client] = lambda: fake_airtable

    response = client.post(
        "/webhooks/vapi",
        json=_end_of_call_report(structured_outputs=_structured_outputs(decision="human_agent")),
        headers={"x-vapi-secret": "secret"},
    )

    assert response.status_code == 200
    assert fake_airtable.updates[0][1]["passenger_decision"] == "human_agent"


def test_webhook_extracts_summary_from_uuid_keyed_structured_outputs(
    client: TestClient,
) -> None:
    fake_airtable = FakeAirtableClient(SAMPLE_RECORD)
    app.dependency_overrides[get_airtable_client] = lambda: fake_airtable

    response = client.post(
        "/webhooks/vapi",
        json=_end_of_call_report(
            summary=None,
            structured_outputs=_structured_outputs(summary="Passenger wants a callback."),
        ),
        headers={"x-vapi-secret": "secret"},
    )

    assert response.status_code == 200
    assert fake_airtable.updates[0][1]["call_summary"] == "Passenger wants a callback."


def test_webhook_summary_falls_back_to_top_level_when_no_structured_summary(
    client: TestClient,
) -> None:
    fake_airtable = FakeAirtableClient(SAMPLE_RECORD)
    app.dependency_overrides[get_airtable_client] = lambda: fake_airtable

    response = client.post(
        "/webhooks/vapi",
        json=_end_of_call_report(
            summary="Top-level summary.",
            structured_outputs=_structured_outputs(decision="refund"),
        ),
        headers={"x-vapi-secret": "secret"},
    )

    assert response.status_code == 200
    assert fake_airtable.updates[0][1]["call_summary"] == "Top-level summary."


def test_webhook_empty_structured_outputs_defaults_to_undecided_and_empty_summary(
    client: TestClient,
) -> None:
    fake_airtable = FakeAirtableClient(SAMPLE_RECORD)
    app.dependency_overrides[get_airtable_client] = lambda: fake_airtable

    response = client.post(
        "/webhooks/vapi",
        json=_end_of_call_report(summary=None, structured_outputs={}),
        headers={"x-vapi-secret": "secret"},
    )

    assert response.status_code == 200
    fields = fake_airtable.updates[0][1]
    assert fields["passenger_decision"] == "undecided"
    assert fields["call_summary"] == ""


def test_webhook_legacy_analysis_structured_data_no_longer_used(client: TestClient) -> None:
    """Confirms the old analysis.structuredData path is no longer required:
    a payload that only populates it (no structuredOutputs) must not
    extract a decision or summary from it."""
    fake_airtable = FakeAirtableClient(SAMPLE_RECORD)
    app.dependency_overrides[get_airtable_client] = lambda: fake_airtable

    response = client.post(
        "/webhooks/vapi",
        json=_end_of_call_report(
            summary=None,
            structured_data=_structured_data(decision="refund", summary="old shape summary"),
        ),
        headers={"x-vapi-secret": "secret"},
    )

    assert response.status_code == 200
    fields = fake_airtable.updates[0][1]
    assert fields["passenger_decision"] == "undecided"
    assert fields["call_summary"] == ""


def test_webhook_polls_vapi_and_recovers_structured_outputs_on_second_attempt(
    client: TestClient,
) -> None:
    """structuredOutputs is absent from the webhook payload itself (Vapi
    computes it asynchronously and never re-notifies), so the handler must
    poll GET /call/{id}. Here the first poll comes back empty and the
    second one has it."""
    fake_airtable = FakeAirtableClient(SAMPLE_RECORD)
    fake_vapi = FakeVapiClient(
        call_responses=[
            {"id": "call_999", "structuredOutputs": None},
            {
                "id": "call_999",
                "structuredOutputs": _structured_outputs(
                    decision="refund", summary="Passenger wants a refund."
                ),
            },
        ]
    )
    app.dependency_overrides[get_airtable_client] = lambda: fake_airtable
    app.dependency_overrides[get_vapi_client] = lambda: fake_vapi

    response = client.post(
        "/webhooks/vapi",
        json=_end_of_call_report(summary=None, structured_outputs=None),
        headers={"x-vapi-secret": "secret"},
    )

    assert response.status_code == 200
    assert len(fake_vapi.get_call_calls) == 2
    assert fake_vapi.get_call_calls == ["call_999", "call_999"]
    fields = fake_airtable.updates[0][1]
    assert fields["passenger_decision"] == "refund"
    assert fields["call_summary"] == "Passenger wants a refund."


def test_webhook_falls_back_to_undecided_when_poll_exhausted(client: TestClient) -> None:
    """All 5 poll attempts come back empty (or fail) — the handler must
    still update call_status/timestamp/recording_url from the original
    webhook payload, leaving decision as undecided rather than giving up."""
    fake_airtable = FakeAirtableClient(SAMPLE_RECORD)
    fake_vapi = FakeVapiClient(
        call_responses=[
            {"id": "call_999", "structuredOutputs": {}},
            VapiError("Vapi is unavailable"),
            {"id": "call_999", "structuredOutputs": None},
            {"id": "call_999", "structuredOutputs": {}},
            {"id": "call_999", "structuredOutputs": None},
        ]
    )
    app.dependency_overrides[get_airtable_client] = lambda: fake_airtable
    app.dependency_overrides[get_vapi_client] = lambda: fake_vapi

    response = client.post(
        "/webhooks/vapi",
        json=_end_of_call_report(
            summary=None,
            structured_outputs=None,
            ended_reason="customer-ended-call",
            recording_url="https://recordings.vapi.ai/call_999.mp3",
        ),
        headers={"x-vapi-secret": "secret"},
    )

    assert response.status_code == 200
    assert len(fake_vapi.get_call_calls) == 5
    fields = fake_airtable.updates[0][1]
    assert fields["passenger_decision"] == "undecided"
    assert fields["call_summary"] == ""
    assert fields["call_status"] == "completed"
    assert fields["recording_url"] == "https://recordings.vapi.ai/call_999.mp3"
    assert "call_timestamp" in fields


def test_extract_call_timestamp_prefers_top_level_ended_at() -> None:
    message = VapiMessage(
        endedAt="2026-08-17T12:03:00.000Z",
        call=VapiCall(endedAt="2026-08-17T13:00:00.000Z"),
    )
    assert _extract_call_timestamp(message) == "2026-08-17T12:03:00.000Z"


def test_extract_call_timestamp_falls_back_to_call_ended_at() -> None:
    message = VapiMessage(call=VapiCall(endedAt="2026-08-17T13:00:00.000Z"))
    assert _extract_call_timestamp(message) == "2026-08-17T13:00:00.000Z"


def test_extract_call_timestamp_falls_back_to_started_at() -> None:
    message = VapiMessage(call=VapiCall(startedAt="2026-08-17T11:00:00.000Z"))
    assert _extract_call_timestamp(message) == "2026-08-17T11:00:00.000Z"


def test_extract_call_timestamp_normalizes_offset_to_utc_z() -> None:
    message = VapiMessage(endedAt="2026-08-17T14:03:00+02:00")
    assert _extract_call_timestamp(message) == "2026-08-17T12:03:00.000Z"


def test_extract_call_timestamp_defaults_to_now_when_missing() -> None:
    assert _ISO8601_UTC_MS.match(_extract_call_timestamp(VapiMessage()))


def test_extract_call_timestamp_defaults_to_now_when_malformed() -> None:
    message = VapiMessage(endedAt="not-a-timestamp")
    assert _ISO8601_UTC_MS.match(_extract_call_timestamp(message))


def test_webhook_writes_iso8601_utc_call_timestamp_from_call_ended_at(
    client: TestClient,
) -> None:
    fake_airtable = FakeAirtableClient(SAMPLE_RECORD)
    app.dependency_overrides[get_airtable_client] = lambda: fake_airtable

    payload = _end_of_call_report()
    payload["message"]["call"] = {"id": "call_999", "endedAt": "2026-08-17T12:03:00.000Z"}

    response = client.post(
        "/webhooks/vapi", json=payload, headers={"x-vapi-secret": "secret"}
    )

    assert response.status_code == 200
    assert fake_airtable.updates[0][1]["call_timestamp"] == "2026-08-17T12:03:00.000Z"


def test_webhook_unrecognized_decision_defaults_to_undecided(client: TestClient) -> None:
    fake_airtable = FakeAirtableClient(SAMPLE_RECORD)
    app.dependency_overrides[get_airtable_client] = lambda: fake_airtable

    response = client.post(
        "/webhooks/vapi",
        json=_end_of_call_report(
            structured_outputs=_structured_outputs(decision="not_a_real_choice")
        ),
        headers={"x-vapi-secret": "secret"},
    )

    assert response.status_code == 200
    assert fake_airtable.updates[0][1]["passenger_decision"] == "undecided"
