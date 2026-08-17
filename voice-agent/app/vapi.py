from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import Settings
from app.logging_config import log_event
from app.retry import retry_transient

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(connect=5.0, read=15.0, write=10.0, pool=5.0)
_BASE_URL = "https://api.vapi.ai"


class VapiError(Exception):
    """Raised when Vapi cannot fulfil a request after retries."""


class VapiClient:
    """Async client for creating outbound calls via Vapi."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._headers = {"Authorization": f"Bearer {settings.vapi_api_key}"}
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

    async def create_call(
        self,
        *,
        phone_number_id: str,
        assistant_id: str,
        customer_number: str,
        variable_values: dict[str, str],
    ) -> dict[str, Any]:
        payload = {
            "phoneNumberId": phone_number_id,
            "assistantId": assistant_id,
            "customer": {"number": customer_number},
            "assistantOverrides": {"variableValues": variable_values},
        }
        try:
            response = await self._request("POST", f"{_BASE_URL}/call", json=payload)
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            log_event(logger, logging.ERROR, "vapi_request_failed", url="/call")
            raise VapiError("Vapi is unavailable") from exc

        if response.status_code >= 400:
            log_event(logger, logging.ERROR, "vapi_error", status_code=response.status_code)
            raise VapiError(f"Vapi request failed with status {response.status_code}")

        return response.json()
