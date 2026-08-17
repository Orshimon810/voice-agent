from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pydantic import ValidationError

from app.airtable import AirtableClient, AirtableError
from app.config import Settings, get_settings
from app.hebrew import normalize_number, normalize_time
from app.logging_config import configure_logging, log_event
from app.models import (
    CallTriggerRequest,
    CallTriggerResponse,
    HealthResponse,
    NormalizedFields,
    PassengerDecision,
    PassengerRecord,
    PassengerResponse,
    VapiMessage,
    VapiWebhookPayload,
)
from app.vapi import VapiClient, VapiError

configure_logging()
logger = logging.getLogger(__name__)

_VALID_DECISIONS = {member.value for member in PassengerDecision}


def _find_structured_result(structured: dict[str, Any] | None, name: str) -> str | None:
    """Look up a value in one of Vapi's UUID-keyed structured field maps
    (e.g. message.structuredOutputs).

    Each entry is shaped like {"name": <field name>, "result": <value>, ...}
    rather than the field name being the dict key directly.
    """
    if not structured:
        return None
    for entry in structured.values():
        if isinstance(entry, dict) and entry.get("name") == name:
            result = entry.get("result")
            if isinstance(result, str):
                return result
    return None


def _extract_decision(message: VapiMessage) -> str:
    """structuredOutputs-based decision, when Vapi happens to have computed
    it in time. Computed asynchronously and frequently absent at webhook
    delivery time (even after polling GET /call/{id} for 15s in production)
    — treat this as a bonus override, not the primary source. See
    _extract_decision_from_transcript for the path that's actually reliable.
    """
    decision = _find_structured_result(message.structuredOutputs, "decision")
    if decision is not None and decision in _VALID_DECISIONS:
        return decision
    return PassengerDecision.undecided.value


# Pragmatic substring-matching heuristic, not NLP: Hebrew has no lightweight,
# dependency-free stemming library available here, and speech-to-text
# transcripts are short/conversational enough that a curated phrase list
# covers the realistic ways a passenger states each choice. Phrases are
# matched as substrings against normalized (punctuation-stripped,
# whitespace-collapsed, per-word definite-article-stripped — see
# _normalize_hebrew_text) text, so they don't need to be whole-word/sentence
# matches, and don't need every article variant spelled out separately —
# "אני רוצה את הכסף בחזרה" still matches "רוצה את הכסף", and "הטיסה
# החלופית" still matches "טיסה חלופית".
_DECISION_PHRASES: dict[str, list[str]] = {
    PassengerDecision.alternative_flight.value: [
        "טיסה חלופית",
        "אני רוצה את הטיסה",
        "החלופית מתאימה",
        "טיסה אחרת",
    ],
    PassengerDecision.refund.value: [
        "החזר כספי",
        "החזר",
        "כסף בחזרה",
        "רוצה את הכסף",
    ],
    PassengerDecision.human_agent.value: [
        "נציג",
        "לדבר עם מישהו",
        "אדם אמיתי",
        "העבירו אותי",
    ],
    PassengerDecision.callback_requested.value: [
        "לחשוב",
        "תתקשרו",
        "אחר כך",
        "לא כרגע",
        "אין לי זמן",
    ],
}

_DECISION_HEBREW_LABELS: dict[str, str] = {
    PassengerDecision.alternative_flight.value: "טיסה חלופית",
    PassengerDecision.refund.value: "החזר כספי",
    PassengerDecision.human_agent.value: "שיחה עם נציג",
    PassengerDecision.callback_requested.value: "בקשה לחזור אליו מאוחר יותר",
    PassengerDecision.undecided.value: "החלטה שלא הובעה בבירור",
}

_PUNCTUATION_TRANSLATION = str.maketrans("", "", "\".,!?;:'׳״()־-")


def _strip_leading_definite_article(word: str) -> str:
    """Strip a leading Hebrew definite article ("ה-") from a single word,
    e.g. "הטיסה" -> "טיסה", so "טיסה חלופית" and "הטיסה החלופית" normalize
    to the same thing. Only strips when at least 2 characters remain
    afterward, so short words where ה is part of the root rather than an
    article (e.g. "הוא") mostly survive intact — a reasonable heuristic,
    not a guarantee, consistent with the substring-matching approach here.
    """
    if word.startswith("ה") and len(word) - 1 >= 2:
        return word[1:]
    return word


def _normalize_hebrew_text(text: str) -> str:
    stripped = text.translate(_PUNCTUATION_TRANSLATION).lower()
    words = (_strip_leading_definite_article(word) for word in stripped.split())
    return " ".join(words)


def _user_transcript_turns(message: VapiMessage) -> list[str]:
    if not message.artifact or not message.artifact.messages:
        return []
    return [
        turn.message for turn in message.artifact.messages if turn.role == "user" and turn.message
    ]


def _extract_decision_from_transcript(message: VapiMessage) -> str:
    """Classify the passenger's decision from what they actually said,
    since the full transcript (message.artifact.messages) is reliably
    present at webhook delivery time, unlike structuredOutputs.

    Scans every candidate phrase across all categories and picks whichever
    one's last occurrence sits latest in the (normalized) user transcript —
    "most recent expressed intent wins" — so a passenger asking about an
    option before ultimately choosing something else still resolves
    correctly.
    """
    user_turns = _user_transcript_turns(message)
    if not user_turns:
        return PassengerDecision.undecided.value

    transcript_text = _normalize_hebrew_text(" ".join(user_turns))

    best_decision: str | None = None
    best_position = -1
    for decision, phrases in _DECISION_PHRASES.items():
        for phrase in phrases:
            position = transcript_text.rfind(_normalize_hebrew_text(phrase))
            if position > best_position:
                best_position = position
                best_decision = decision

    return best_decision if best_decision is not None else PassengerDecision.undecided.value


def _call_variable_values(message: VapiMessage) -> dict[str, Any]:
    if not message.call or not message.call.assistantOverrides:
        return {}
    variable_values = message.call.assistantOverrides.get("variableValues")
    return variable_values if isinstance(variable_values, dict) else {}


def _extract_summary_from_transcript(message: VapiMessage, decision: str | None = None) -> str:
    """Deterministic, template-based Hebrew summary — no external/AI calls.
    Passenger name and flight number come from call.assistantOverrides
    (Vapi echoes back the variableValues used to create the call; see
    _build_variable_values), falling back to generic phrasing when absent.

    `decision`, if given, is the already-resolved final decision (e.g. from
    a structuredOutputs bonus override) so the summary stays consistent
    with whatever was actually written to passenger_decision, rather than
    silently re-deriving a possibly different one from the transcript alone.
    """
    if decision is None:
        decision = _extract_decision_from_transcript(message)
    decision_label = _DECISION_HEBREW_LABELS.get(
        decision, _DECISION_HEBREW_LABELS[PassengerDecision.undecided.value]
    )

    variable_values = _call_variable_values(message)
    name = variable_values.get("passenger_name")
    flight = variable_values.get("flight_number")
    subject = f"הנוסע {name}" if name else "הנוסע"
    flight_clause = f" בטיסה {flight}" if flight else ""

    return (
        f"{subject} התבקש לבחור בין טיסה חלופית להחזר כספי בעקבות עיכוב"
        f"{flight_clause}, ובחר ב{decision_label}."
    )


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _format_call_timestamp(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def _extract_call_timestamp(message: VapiMessage) -> str:
    candidates = [
        message.endedAt,
        message.call.endedAt if message.call else None,
        message.startedAt,
        message.call.startedAt if message.call else None,
    ]
    for candidate in candidates:
        parsed = _parse_timestamp(candidate)
        if parsed is not None:
            return _format_call_timestamp(parsed)
    return _format_call_timestamp(datetime.now(timezone.utc))


def _map_ended_reason(ended_reason: str | None) -> str:
    if not ended_reason:
        return "completed"
    reason = ended_reason.lower()
    if "voicemail" in reason or "no-answer" in reason or "no_answer" in reason or "busy" in reason:
        return "no_answer"
    if "error" in reason or "failed" in reason:
        return "failed"
    return "completed"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    app.state.airtable_client = AirtableClient(settings)
    app.state.vapi_client = VapiClient(settings)
    try:
        yield
    finally:
        await app.state.airtable_client.aclose()
        await app.state.vapi_client.aclose()


app = FastAPI(title="elal-voice-agent", lifespan=lifespan)


def get_airtable_client(request: Request) -> AirtableClient:
    return request.app.state.airtable_client


def get_vapi_client(request: Request) -> VapiClient:
    return request.app.state.vapi_client


def _build_normalized_fields(record: PassengerRecord) -> NormalizedFields:
    return NormalizedFields(
        original_departure_spoken=normalize_time(record.original_departure),
        delay_hours_spoken=normalize_number(record.delay_hours),
        alt_flight_time_spoken=normalize_time(record.alt_flight_time),
        refund_amount_spoken=normalize_number(record.refund_amount),
    )


def _build_variable_values(record: PassengerRecord, normalized: NormalizedFields) -> dict[str, str]:
    return {
        "passenger_name": record.passenger_name,
        "flight_number": record.flight_number,
        "destination": record.destination,
        "original_departure_spoken": normalized.original_departure_spoken,
        "delay_hours_spoken": normalized.delay_hours_spoken,
        "alt_flight_time_spoken": normalized.alt_flight_time_spoken,
        "refund_amount_spoken": normalized.refund_amount_spoken,
    }


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok")


@app.get("/passengers/{record_id}", response_model=PassengerResponse)
async def get_passenger(
    record_id: str,
    airtable_client: AirtableClient = Depends(get_airtable_client),
) -> PassengerResponse:
    try:
        record = await airtable_client.get_record(record_id)
    except AirtableError as exc:
        log_event(logger, logging.ERROR, "passenger_lookup_failed", record_id=record_id)
        raise HTTPException(status_code=502, detail="Airtable is unavailable") from exc

    if record is None:
        raise HTTPException(status_code=404, detail="Passenger record not found")

    normalized = _build_normalized_fields(record)
    return PassengerResponse(raw=record, normalized=normalized)


@app.post("/calls/trigger", response_model=CallTriggerResponse)
async def trigger_call(
    body: CallTriggerRequest,
    airtable_client: AirtableClient = Depends(get_airtable_client),
    vapi_client: VapiClient = Depends(get_vapi_client),
    settings: Settings = Depends(get_settings),
) -> CallTriggerResponse:
    try:
        record = await airtable_client.get_record(body.record_id)
    except AirtableError as exc:
        log_event(logger, logging.ERROR, "call_trigger_lookup_failed", record_id=body.record_id)
        raise HTTPException(status_code=502, detail="Airtable is unavailable") from exc

    if record is None:
        raise HTTPException(status_code=404, detail="Passenger record not found")

    normalized = _build_normalized_fields(record)
    variable_values = _build_variable_values(record, normalized)

    call_id: str | None = None

    if body.mode == "phone":
        try:
            vapi_response = await vapi_client.create_call(
                phone_number_id=settings.vapi_phone_number_id,
                assistant_id=settings.vapi_assistant_id,
                customer_number=record.phone,
                variable_values=variable_values,
            )
        except VapiError as exc:
            log_event(logger, logging.ERROR, "call_trigger_vapi_failed", record_id=body.record_id)
            raise HTTPException(status_code=502, detail="Vapi is unavailable") from exc

        call_id = vapi_response.get("id")

        try:
            await airtable_client.update_record(
                body.record_id, {"call_status": "pending", "call_id": call_id}
            )
        except AirtableError as exc:
            log_event(
                logger, logging.ERROR, "call_trigger_persist_failed", record_id=body.record_id
            )
            raise HTTPException(status_code=502, detail="Airtable is unavailable") from exc

    log_event(
        logger,
        logging.INFO,
        "call_triggered",
        record_id=body.record_id,
        mode=body.mode,
        call_id=call_id,
    )
    return CallTriggerResponse(call_id=call_id, variable_values=variable_values)


@app.post("/webhooks/vapi")
async def vapi_webhook(
    request: Request,
    airtable_client: AirtableClient = Depends(get_airtable_client),
    settings: Settings = Depends(get_settings),
    x_vapi_secret: str | None = Header(default=None),
) -> dict[str, str]:
    if x_vapi_secret != settings.webhook_secret:
        raise HTTPException(status_code=401, detail="invalid webhook secret")

    try:
        raw_body: Any = await request.json()
        payload = VapiWebhookPayload.model_validate(raw_body)
    except (ValueError, ValidationError):
        # Malformed JSON or an unexpected shape. Never let Vapi see a 5xx (or
        # a validation 4xx) here — that would trigger retries and duplicate
        # writes once the underlying issue is fixed.
        log_event(logger, logging.WARNING, "webhook_malformed_payload")
        return {"status": "ignored"}

    message = payload.message
    if message is None or message.type != "end-of-call-report":
        return {"status": "ignored"}

    call_id = message.call.id if message.call else None
    if not call_id:
        log_event(logger, logging.WARNING, "webhook_missing_call_id")
        return {"status": "ignored"}

    try:
        record = await airtable_client.find_record_by_call_id(call_id)
    except AirtableError:
        log_event(logger, logging.ERROR, "webhook_airtable_lookup_failed", call_id=call_id)
        return {"status": "error"}

    if record is None:
        log_event(logger, logging.WARNING, "webhook_record_not_found", call_id=call_id)
        return {"status": "ignored"}

    # structuredOutputs, when Vapi happens to have computed it in time, is a
    # bonus override — transcript analysis is the reliable, always-available
    # path (see _extract_decision for why structuredOutputs can't be trusted
    # as primary).
    decision = _extract_decision(message)
    if decision == PassengerDecision.undecided.value:
        decision = _extract_decision_from_transcript(message)

    # Pass the already-resolved `decision` through so the generated summary
    # (if it comes to that) describes the same outcome that was just
    # written to passenger_decision, not a possibly different one re-derived
    # from the transcript alone.
    summary = (
        _find_structured_result(message.structuredOutputs, "summary")
        or message.summary
        or _extract_summary_from_transcript(message, decision=decision)
    )

    fields = {
        "call_status": _map_ended_reason(message.endedReason),
        "passenger_decision": decision,
        "call_summary": summary,
        "call_timestamp": _extract_call_timestamp(message),
        "recording_url": message.recordingUrl or "",
    }

    try:
        await airtable_client.update_record(record.record_id, fields)
    except AirtableError:
        log_event(logger, logging.ERROR, "webhook_airtable_update_failed", call_id=call_id)
        return {"status": "error"}

    return {"status": "ok"}
