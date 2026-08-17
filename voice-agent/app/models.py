from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel


class CallStatus(str, Enum):
    pending = "pending"
    completed = "completed"
    no_answer = "no_answer"
    failed = "failed"


class PassengerDecision(str, Enum):
    alternative_flight = "alternative_flight"
    refund = "refund"
    human_agent = "human_agent"
    callback_requested = "callback_requested"
    undecided = "undecided"


class PassengerRecord(BaseModel):
    """Raw Airtable fields for a passenger."""

    record_id: str
    passenger_name: str
    phone: str
    flight_number: str
    destination: str
    original_departure: str
    delay_hours: int
    alt_flight_time: str
    refund_amount: int

    call_status: CallStatus | None = None
    passenger_decision: PassengerDecision | None = None
    call_summary: str | None = None
    call_id: str | None = None
    call_timestamp: datetime | None = None
    recording_url: str | None = None


class NormalizedFields(BaseModel):
    original_departure_spoken: str
    delay_hours_spoken: str
    alt_flight_time_spoken: str
    refund_amount_spoken: str


class PassengerResponse(BaseModel):
    """Raw + normalized fields, so they can be compared during testing."""

    raw: PassengerRecord
    normalized: NormalizedFields


class CallTriggerRequest(BaseModel):
    record_id: str
    mode: Literal["web", "phone"]


class CallTriggerResponse(BaseModel):
    call_id: str | None
    variable_values: dict[str, str]


class HealthResponse(BaseModel):
    status: str


# Vapi's end-of-call-report webhook payload. Fields are optional/lenient so a
# partial or slightly-off payload doesn't fail validation and turn into a 5xx
# (which would trigger Vapi retries) — the handler ignores anything it can't
# make sense of and always returns 200.
class VapiCall(BaseModel):
    id: str | None = None
    startedAt: str | None = None
    endedAt: str | None = None


class VapiAnalysis(BaseModel):
    summary: str | None = None
    structuredData: dict[str, Any] | None = None


class VapiMessage(BaseModel):
    type: str | None = None
    endedReason: str | None = None
    call: VapiCall | None = None
    summary: str | None = None
    transcript: str | None = None
    recordingUrl: str | None = None
    analysis: VapiAnalysis | None = None
    startedAt: str | None = None
    endedAt: str | None = None


class VapiWebhookPayload(BaseModel):
    message: VapiMessage | None = None
