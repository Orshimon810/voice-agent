from __future__ import annotations

from datetime import datetime
from enum import Enum

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
