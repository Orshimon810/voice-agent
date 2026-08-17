from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request

from app.airtable import AirtableClient, AirtableError
from app.config import Settings, get_settings
from app.hebrew import normalize_number, normalize_time
from app.logging_config import configure_logging, log_event
from app.models import (
    CallTriggerRequest,
    CallTriggerResponse,
    HealthResponse,
    NormalizedFields,
    PassengerRecord,
    PassengerResponse,
)
from app.vapi import VapiClient, VapiError

configure_logging()
logger = logging.getLogger(__name__)


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
