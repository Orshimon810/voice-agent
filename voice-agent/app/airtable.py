from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import Settings
from app.logging_config import log_event
from app.models import PassengerRecord
from app.retry import retry_transient

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=5.0)


class AirtableError(Exception):
    """Raised when Airtable cannot fulfil a request after retries."""


def parse_passenger_record(record: dict[str, Any]) -> PassengerRecord:
    fields = record.get("fields", {})
    return PassengerRecord(
        record_id=record["id"],
        passenger_name=fields.get("passenger_name", ""),
        phone=fields.get("phone", ""),
        flight_number=fields.get("flight_number", ""),
        destination=fields.get("destination", ""),
        original_departure=fields.get("original_departure", ""),
        delay_hours=fields.get("delay_hours", 0),
        alt_flight_time=fields.get("alt_flight_time", ""),
        refund_amount=fields.get("refund_amount", 0),
        call_status=fields.get("call_status"),
        passenger_decision=fields.get("passenger_decision"),
        call_summary=fields.get("call_summary"),
        call_id=fields.get("call_id"),
        call_timestamp=fields.get("call_timestamp"),
        recording_url=fields.get("recording_url"),
    )


class AirtableClient:
    """Async client for reading and writing passenger records in Airtable."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._base_url = (
            f"https://api.airtable.com/v0/{settings.airtable_base_id}/{settings.airtable_table_id}"
        )
        self._headers = {"Authorization": f"Bearer {settings.airtable_token}"}
        self._client = client or httpx.AsyncClient(timeout=_TIMEOUT)
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    @retry_transient
    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        response = await self._client.request(method, url, headers=self._headers, **kwargs)
        if response.status_code >= 500:
            response.raise_for_status()
        return response

    async def _safe_request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        try:
            return await self._request(method, url, **kwargs)
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            log_event(logger, logging.ERROR, "airtable_request_failed", method=method, url=url)
            raise AirtableError("Airtable is unavailable") from exc

    async def get_record(self, record_id: str) -> PassengerRecord | None:
        response = await self._safe_request("GET", f"{self._base_url}/{record_id}")
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            log_event(logger, logging.ERROR, "airtable_error", status_code=response.status_code)
            raise AirtableError(f"Airtable request failed with status {response.status_code}")
        return parse_passenger_record(response.json())

    async def find_record_by_call_id(self, call_id: str) -> PassengerRecord | None:
        escaped = call_id.replace("'", "\\'")
        formula = f"{{call_id}}='{escaped}'"
        response = await self._safe_request(
            "GET", self._base_url, params={"filterByFormula": formula, "maxRecords": 1}
        )
        if response.status_code >= 400:
            log_event(logger, logging.ERROR, "airtable_error", status_code=response.status_code)
            raise AirtableError(f"Airtable request failed with status {response.status_code}")

        records = response.json().get("records", [])
        return parse_passenger_record(records[0]) if records else None

    async def update_record(self, record_id: str, fields: dict[str, Any]) -> None:
        response = await self._safe_request(
            "PATCH", f"{self._base_url}/{record_id}", json={"fields": fields}
        )
        if response.status_code >= 400:
            # TODO(debug): temporary, remove after diagnosing update-record failures.
            log_event(
                logger,
                logging.ERROR,
                "airtable_update_error_response_body",
                status_code=response.status_code,
                response_body=response.text,
            )
            log_event(logger, logging.ERROR, "airtable_error", status_code=response.status_code)
            raise AirtableError(f"Airtable request failed with status {response.status_code}")
