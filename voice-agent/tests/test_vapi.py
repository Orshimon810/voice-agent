import httpx
import pytest
import respx

from app.config import Settings
from app.vapi import VapiClient, VapiError


@pytest.fixture
def settings() -> Settings:
    return Settings(
        airtable_token="test-token",
        airtable_base_id="appTEST",
        airtable_table_id="tblTEST",
        vapi_api_key="vapi-secret-key",
        vapi_assistant_id="assistant-id",
        vapi_phone_number_id="phone-id",
        webhook_secret="secret",
    )


@pytest.mark.asyncio
async def test_create_call_success(settings: Settings) -> None:
    async with httpx.AsyncClient() as http_client:
        client = VapiClient(settings, client=http_client)
        with respx.mock:
            route = respx.post("https://api.vapi.ai/call").mock(
                return_value=httpx.Response(201, json={"id": "call_123", "status": "queued"})
            )
            result = await client.create_call(
                phone_number_id="phone-id",
                assistant_id="assistant-id",
                customer_number="+972501234567",
                variable_values={"passenger_name": "Yossi"},
            )

    assert result == {"id": "call_123", "status": "queued"}
    request = route.calls.last.request
    assert request.headers["Authorization"] == "Bearer vapi-secret-key"

    import json

    body = json.loads(request.content)
    assert body["phoneNumberId"] == "phone-id"
    assert body["assistantId"] == "assistant-id"
    assert body["customer"] == {"number": "+972501234567"}
    assert body["assistantOverrides"]["variableValues"] == {"passenger_name": "Yossi"}


@pytest.mark.asyncio
async def test_create_call_client_error_not_retried(settings: Settings) -> None:
    async with httpx.AsyncClient() as http_client:
        client = VapiClient(settings, client=http_client)
        with respx.mock:
            route = respx.post("https://api.vapi.ai/call").mock(
                return_value=httpx.Response(400, json={"message": "invalid assistant"})
            )
            with pytest.raises(VapiError):
                await client.create_call(
                    phone_number_id="phone-id",
                    assistant_id="bad-assistant",
                    customer_number="+972501234567",
                    variable_values={},
                )
    assert route.call_count == 1


@pytest.mark.asyncio
async def test_create_call_retries_transient_error_then_succeeds(settings: Settings) -> None:
    async with httpx.AsyncClient() as http_client:
        client = VapiClient(settings, client=http_client)
        with respx.mock:
            route = respx.post("https://api.vapi.ai/call").mock(
                side_effect=[httpx.Response(502), httpx.Response(201, json={"id": "call_123"})]
            )
            result = await client.create_call(
                phone_number_id="phone-id",
                assistant_id="assistant-id",
                customer_number="+972501234567",
                variable_values={},
            )
    assert result == {"id": "call_123"}
    assert route.call_count == 2


@pytest.mark.asyncio
async def test_create_call_persistent_server_error_raises(settings: Settings) -> None:
    async with httpx.AsyncClient() as http_client:
        client = VapiClient(settings, client=http_client)
        with respx.mock:
            respx.post("https://api.vapi.ai/call").mock(return_value=httpx.Response(503))
            with pytest.raises(VapiError):
                await client.create_call(
                    phone_number_id="phone-id",
                    assistant_id="assistant-id",
                    customer_number="+972501234567",
                    variable_values={},
                )
