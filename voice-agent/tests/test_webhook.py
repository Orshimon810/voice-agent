import re

from fastapi.testclient import TestClient

from app.airtable import AirtableError
from app.main import (
    _DECISION_HEBREW_LABELS,
    _extract_call_timestamp,
    _extract_decision,
    _extract_decision_from_transcript,
    _extract_summary_from_transcript,
    _find_structured_result,
    app,
    get_airtable_client,
)
from app.models import (
    PassengerDecision,
    PassengerRecord,
    VapiAnalysis,
    VapiArtifact,
    VapiArtifactMessage,
    VapiCall,
    VapiMessage,
)

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


def _end_of_call_report(
    *,
    call_id: str = "call_999",
    ended_reason: str = "customer-ended-call",
    structured_outputs: dict | None = None,
    structured_data: dict | None = None,
    transcript_turns: list[tuple[str, str]] | None = None,
    summary: str | None = "Passenger chose the alternative flight.",
    recording_url: str = "https://recordings.vapi.ai/call_999.mp3",
) -> dict:
    call: dict = {"id": call_id}
    message: dict = {
        "type": "end-of-call-report",
        "endedReason": ended_reason,
        "call": call,
        "summary": summary,
        "recordingUrl": recording_url,
    }
    if structured_outputs is not None:
        message["structuredOutputs"] = structured_outputs
    if structured_data is not None:
        message["analysis"] = {"structuredData": structured_data}
    if transcript_turns is not None:
        message["artifact"] = {
            "messages": [{"role": role, "message": text} for role, text in transcript_turns]
        }
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


def _transcript_message(
    turns: list[tuple[str, str]], call: VapiCall | None = None
) -> VapiMessage:
    """Build a VapiMessage with message.artifact.messages populated from an
    ordered list of (role, text) turns, e.g. [("assistant", "..."), ("user", "...")]."""
    return VapiMessage(
        artifact=VapiArtifact(
            messages=[VapiArtifactMessage(role=role, message=text) for role, text in turns]
        ),
        call=call,
    )


def _fallback_summary(name: str | None = None, flight: str | None = None) -> str:
    """Expected deterministic fallback summary for an undecided outcome
    with no transcript signal, matching _extract_summary_from_transcript's
    template exactly (kept independent of the implementation on purpose, so
    a regression in the template shows up here rather than only visually)."""
    subject = f"הנוסע {name}" if name else "הנוסע"
    flight_clause = f" בטיסה {flight}" if flight else ""
    label = _DECISION_HEBREW_LABELS[PassengerDecision.undecided.value]
    return f"{subject} התבקש לבחור בין טיסה חלופית להחזר כספי בעקבות עיכוב{flight_clause}, ובחר ב{label}."


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


def test_webhook_unrecognized_structured_decision_falls_back_to_transcript(
    client: TestClient,
) -> None:
    """An invalid/unrecognized structuredOutputs decision must not block
    the transcript fallback — it should be treated the same as absent."""
    fake_airtable = FakeAirtableClient(SAMPLE_RECORD)
    app.dependency_overrides[get_airtable_client] = lambda: fake_airtable

    response = client.post(
        "/webhooks/vapi",
        json=_end_of_call_report(
            structured_outputs=_structured_outputs(decision="not_a_real_choice"),
            transcript_turns=[("user", "אני רוצה לדבר עם נציג בבקשה")],
        ),
        headers={"x-vapi-secret": "secret"},
    )

    assert response.status_code == 200
    assert fake_airtable.updates[0][1]["passenger_decision"] == "human_agent"


# --- _find_structured_result -------------------------------------------------


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


# --- _extract_decision (structuredOutputs-based bonus override) -------------


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
    """The old analysis.structuredData path is not read by _extract_decision
    — decisions come from message.structuredOutputs instead. analysis is
    empty in production because analysisPlan.summaryPlan is disabled."""
    message = VapiMessage(
        analysis=VapiAnalysis(
            structuredData={
                "3fa85f64-5717-4562-b3fc-2c963f66afa6": {"name": "decision", "result": "refund"}
            }
        )
    )
    assert _extract_decision(message) == PassengerDecision.undecided.value


# --- _extract_decision_from_transcript ---------------------------------------


def test_extract_decision_from_transcript_alternative_flight() -> None:
    for text in [
        "אני חושב שאני רוצה את הטיסה החלופית",
        "החלופית מתאימה לי הכי טוב",
        "אני מעדיף טיסה אחרת בבקשה",
    ]:
        message = _transcript_message([("user", text)])
        assert _extract_decision_from_transcript(message) == "alternative_flight"


def test_extract_decision_from_transcript_refund() -> None:
    for text in [
        "אני מעדיף לקבל החזר כספי בבקשה",
        "פשוט תעשו לי החזר",
        "אני רוצה את הכסף בחזרה",
        "אני בהחלט רוצה את הכסף",
    ]:
        message = _transcript_message([("user", text)])
        assert _extract_decision_from_transcript(message) == "refund"


def test_extract_decision_from_transcript_human_agent() -> None:
    for text in [
        "אני רוצה לדבר עם נציג בבקשה",
        "אפשר להעביר אותי לאדם אמיתי?",
        "אני צריך לדבר עם מישהו על זה",
    ]:
        message = _transcript_message([("user", text)])
        assert _extract_decision_from_transcript(message) == "human_agent"


def test_extract_decision_from_transcript_callback_requested() -> None:
    for text in [
        "אני צריך לחשוב על זה עוד קצת",
        "תתקשרו אליי אחר כך בבקשה",
        "אין לי זמן כרגע",
        "לא כרגע, אולי מחר",
    ]:
        message = _transcript_message([("user", text)])
        assert _extract_decision_from_transcript(message) == "callback_requested"


def test_extract_decision_from_transcript_undecided_when_no_phrase_matches() -> None:
    message = _transcript_message([("assistant", "איך אפשר לעזור?"), ("user", "שלום, מה שלומך?")])
    assert _extract_decision_from_transcript(message) == PassengerDecision.undecided.value


def test_extract_decision_from_transcript_undecided_when_artifact_missing() -> None:
    assert _extract_decision_from_transcript(VapiMessage()) == PassengerDecision.undecided.value


def test_extract_decision_from_transcript_undecided_when_messages_empty() -> None:
    message = _transcript_message([])
    assert _extract_decision_from_transcript(message) == PassengerDecision.undecided.value


def test_extract_decision_from_transcript_undecided_when_only_assistant_turns() -> None:
    message = _transcript_message([("assistant", "אני יכול להציע לך טיסה חלופית או החזר כספי")])
    assert _extract_decision_from_transcript(message) == PassengerDecision.undecided.value


def test_extract_decision_from_transcript_later_signal_wins_refund_then_alternative() -> None:
    """Passenger asks about a refund first, then settles on the alternative
    flight — the later, more recent statement should win even though its
    category comes earlier in the phrase dict than "refund"."""
    message = _transcript_message(
        [
            ("user", "בהתחלה חשבתי שאני רוצה החזר כספי"),
            ("assistant", "בסדר, ומה לגבי הטיסה החלופית?"),
            ("user", "אבל בסוף אני רוצה את הטיסה החלופית"),
        ]
    )
    assert _extract_decision_from_transcript(message) == "alternative_flight"


def test_extract_decision_from_transcript_later_signal_wins_alternative_then_refund() -> None:
    """Same scenario reversed, to confirm it's genuinely position-based and
    not just picking whichever category happens to be declared first."""
    message = _transcript_message(
        [
            ("user", "טיסה חלופית זה מעניין"),
            ("user", "אבל בסוף אני רוצה את הכסף בחזרה"),
        ]
    )
    assert _extract_decision_from_transcript(message) == "refund"


def test_extract_decision_from_transcript_handles_punctuation_variations() -> None:
    message = _transcript_message([("user", 'אני, למעשה, רוצה "טיסה חלופית".')])
    assert _extract_decision_from_transcript(message) == "alternative_flight"


def test_extract_decision_from_transcript_case_insensitive_for_latin_text() -> None:
    message = _transcript_message([("user", "OK, אני רוצה את הכסף בחזרה")])
    assert _extract_decision_from_transcript(message) == "refund"


# --- definite article (ה-) normalization -------------------------------------
# Regression coverage for a real production miss: "הטיסה החלופית" (article
# prefixed to both words) didn't match the phrase "טיסה חלופית" at all,
# since the phrase list has no article and substring matching is literal.
# These stand alone (no other matching phrase in the sentence) specifically
# so they can only pass via the article-stripping normalization, not by
# accidentally matching some other phrase already in the list.


def test_extract_decision_from_transcript_matches_article_prefixed_alternative_flight() -> None:
    """The exact real transcript phrase that missed in production."""
    message = _transcript_message([("user", "הטיסה החלופית")])
    assert _extract_decision_from_transcript(message) == "alternative_flight"


def test_extract_decision_from_transcript_matches_article_prefixed_refund() -> None:
    message = _transcript_message([("user", "אני רוצה את ההחזר הכספי")])
    assert _extract_decision_from_transcript(message) == "refund"


def test_extract_decision_from_transcript_matches_article_prefixed_human_agent() -> None:
    message = _transcript_message([("user", "תעבירו אותי בבקשה אל הנציג")])
    assert _extract_decision_from_transcript(message) == "human_agent"


def test_extract_decision_from_transcript_matches_article_prefixed_callback_requested() -> None:
    message = _transcript_message([("user", "אין לי הזמן כרגע")])
    assert _extract_decision_from_transcript(message) == "callback_requested"


# --- _extract_summary_from_transcript ----------------------------------------


def test_extract_summary_from_transcript_includes_name_flight_and_decision() -> None:
    call = VapiCall(
        assistantOverrides={
            "variableValues": {"passenger_name": "יוסי כהן", "flight_number": "LY315"}
        }
    )
    message = _transcript_message([("user", "אני רוצה החזר כספי")], call=call)

    summary = _extract_summary_from_transcript(message)

    assert summary.startswith("הנוסע יוסי כהן")
    assert "LY315" in summary
    assert summary.endswith("ובחר בהחזר כספי.")


def test_extract_summary_from_transcript_falls_back_to_generic_phrasing_when_no_context() -> None:
    summary = _extract_summary_from_transcript(_transcript_message([]))
    assert summary == _fallback_summary()
    assert "הנוסע הנוסע" not in summary


def test_extract_summary_from_transcript_reflects_undecided_when_no_signal() -> None:
    summary = _extract_summary_from_transcript(_transcript_message([("user", "שלום")]))
    assert summary.endswith(f"ובחר ב{_DECISION_HEBREW_LABELS['undecided']}.")


# --- webhook-level: transcript is now the reliable primary path -------------


def test_webhook_extracts_decision_and_summary_from_transcript(client: TestClient) -> None:
    fake_airtable = FakeAirtableClient(SAMPLE_RECORD)
    app.dependency_overrides[get_airtable_client] = lambda: fake_airtable

    response = client.post(
        "/webhooks/vapi",
        json=_end_of_call_report(
            summary=None,
            transcript_turns=[
                ("assistant", "האם תרצה טיסה חלופית או החזר כספי?"),
                ("user", "אני מעדיף לקבל החזר כספי בבקשה"),
            ],
        ),
        headers={"x-vapi-secret": "secret"},
    )

    assert response.status_code == 200
    fields = fake_airtable.updates[0][1]
    assert fields["passenger_decision"] == "refund"
    assert fields["call_summary"].endswith("ובחר בהחזר כספי.")


def test_webhook_structured_outputs_bonus_override_wins_over_conflicting_transcript(
    client: TestClient,
) -> None:
    """When structuredOutputs is present and valid, it takes priority over
    transcript analysis — even if the transcript would classify
    differently."""
    fake_airtable = FakeAirtableClient(SAMPLE_RECORD)
    app.dependency_overrides[get_airtable_client] = lambda: fake_airtable

    response = client.post(
        "/webhooks/vapi",
        json=_end_of_call_report(
            summary=None,
            structured_outputs=_structured_outputs(decision="human_agent"),
            transcript_turns=[("user", "אני מעדיף לקבל החזר כספי בבקשה")],
        ),
        headers={"x-vapi-secret": "secret"},
    )

    assert response.status_code == 200
    assert fake_airtable.updates[0][1]["passenger_decision"] == "human_agent"


def test_webhook_structured_outputs_summary_bonus_override_wins_over_transcript(
    client: TestClient,
) -> None:
    fake_airtable = FakeAirtableClient(SAMPLE_RECORD)
    app.dependency_overrides[get_airtable_client] = lambda: fake_airtable

    response = client.post(
        "/webhooks/vapi",
        json=_end_of_call_report(
            summary=None,
            structured_outputs=_structured_outputs(summary="Structured summary wins."),
            transcript_turns=[("user", "אני מעדיף לקבל החזר כספי בבקשה")],
        ),
        headers={"x-vapi-secret": "secret"},
    )

    assert response.status_code == 200
    assert fake_airtable.updates[0][1]["call_summary"] == "Structured summary wins."


def test_webhook_missing_structured_outputs_and_transcript_falls_back_to_deterministic_summary(
    client: TestClient,
) -> None:
    """No structuredOutputs, no transcript: decision stays undecided, and
    call_summary is now the deterministic template sentence rather than an
    empty string."""
    fake_airtable = FakeAirtableClient(SAMPLE_RECORD)
    app.dependency_overrides[get_airtable_client] = lambda: fake_airtable

    response = client.post(
        "/webhooks/vapi",
        json=_end_of_call_report(summary=None, structured_outputs=None),
        headers={"x-vapi-secret": "secret"},
    )

    assert response.status_code == 200
    fields = fake_airtable.updates[0][1]
    assert fields["passenger_decision"] == "undecided"
    assert fields["call_summary"] == _fallback_summary()


def test_webhook_empty_structured_outputs_falls_back_to_transcript(client: TestClient) -> None:
    fake_airtable = FakeAirtableClient(SAMPLE_RECORD)
    app.dependency_overrides[get_airtable_client] = lambda: fake_airtable

    response = client.post(
        "/webhooks/vapi",
        json=_end_of_call_report(
            summary=None,
            structured_outputs={},
            transcript_turns=[("user", "אני רוצה לדבר עם נציג בבקשה")],
        ),
        headers={"x-vapi-secret": "secret"},
    )

    assert response.status_code == 200
    fields = fake_airtable.updates[0][1]
    assert fields["passenger_decision"] == "human_agent"


def test_webhook_legacy_analysis_structured_data_no_longer_used(client: TestClient) -> None:
    """Confirms the old analysis.structuredData path is still not read: a
    payload that only populates it (no structuredOutputs, no transcript)
    must not extract a decision or summary from it — it falls through to
    the deterministic transcript-fallback summary, same as if it were
    entirely absent."""
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
    assert fields["call_summary"] == _fallback_summary()


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


# --- _extract_call_timestamp (unaffected by this change) --------------------


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
