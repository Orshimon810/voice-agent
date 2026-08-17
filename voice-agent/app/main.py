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
    decision = _find_structured_result(message.structuredOutputs, "decision")
    if decision is not None and decision in _VALID_DECISIONS:
        return decision
    return PassengerDecision.undecided.value


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

    summary = _find_structured_result(message.structuredOutputs, "summary") or message.summary or ""
    fields = {
        "call_status": _map_ended_reason(message.endedReason),
        "passenger_decision": _extract_decision(message),
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
