import httpx
import pytest
import respx

from app.airtable import AirtableClient, AirtableError
from app.config import Settings


@pytest.fixture
def settings() -> Settings:
    return Settings(
        airtable_token="test-token",
        airtable_base_id="appTEST",
        airtable_table_id="tblTEST",
        vapi_api_key="vapi-key",
        vapi_assistant_id="assistant-id",
        vapi_phone_number_id="phone-id",
        webhook_secret="secret",
    )


RECORD = {
    "id": "recABC123",
    "fields": {
        "passenger_name": "Yossi Cohen",
        "phone": "+972501234567",
        "flight_number": "LY315",
        "destination": "JFK",
        "original_departure": "06:00",
        "delay_hours": 3,
        "alt_flight_time": "09:30",
        "refund_amount": 850,
        "call_status": "pending",
    },
}


@pytest.mark.asyncio
async def test_get_record_success(settings: Settings) -> None:
    async with httpx.AsyncClient() as http_client:
        client = AirtableClient(settings, client=http_client)
        with respx.mock:
            respx.get(f"{client._base_url}/recABC123").mock(
                return_value=httpx.Response(200, json=RECORD)
            )
            record = await client.get_record("recABC123")
    assert record is not None
    assert record.record_id == "recABC123"
    assert record.passenger_name == "Yossi Cohen"
    assert record.flight_number == "LY315"
    assert record.call_status == "pending"


@pytest.mark.asyncio
async def test_get_record_not_found(settings: Settings) -> None:
    async with httpx.AsyncClient() as http_client:
        client = AirtableClient(settings, client=http_client)
        with respx.mock:
            respx.get(f"{client._base_url}/recMISSING").mock(return_value=httpx.Response(404))
            record = await client.get_record("recMISSING")
    assert record is None


@pytest.mark.asyncio
async def test_get_record_persistent_server_error_raises(settings: Settings) -> None:
    async with httpx.AsyncClient() as http_client:
        client = AirtableClient(settings, client=http_client)
        with respx.mock:
            respx.get(f"{client._base_url}/recABC123").mock(return_value=httpx.Response(500))
            with pytest.raises(AirtableError):
                await client.get_record("recABC123")


@pytest.mark.asyncio
async def test_get_record_client_error_not_retried(settings: Settings) -> None:
    async with httpx.AsyncClient() as http_client:
        client = AirtableClient(settings, client=http_client)
        with respx.mock:
            route = respx.get(f"{client._base_url}/recBAD").mock(
                return_value=httpx.Response(422)
            )
            with pytest.raises(AirtableError):
                await client.get_record("recBAD")
    assert route.call_count == 1


@pytest.mark.asyncio
async def test_find_record_by_call_id_found(settings: Settings) -> None:
    async with httpx.AsyncClient() as http_client:
        client = AirtableClient(settings, client=http_client)
        with respx.mock:
            respx.get(client._base_url).mock(
                return_value=httpx.Response(200, json={"records": [RECORD]})
            )
            record = await client.find_record_by_call_id("call_xyz")
    assert record is not None
    assert record.record_id == "recABC123"


@pytest.mark.asyncio
async def test_find_record_by_call_id_not_found(settings: Settings) -> None:
    async with httpx.AsyncClient() as http_client:
        client = AirtableClient(settings, client=http_client)
        with respx.mock:
            respx.get(client._base_url).mock(
                return_value=httpx.Response(200, json={"records": []})
            )
            record = await client.find_record_by_call_id("call_xyz")
    assert record is None


@pytest.mark.asyncio
async def test_update_record_success(settings: Settings) -> None:
    async with httpx.AsyncClient() as http_client:
        client = AirtableClient(settings, client=http_client)
        with respx.mock:
            route = respx.patch(f"{client._base_url}/recABC123").mock(
                return_value=httpx.Response(200, json=RECORD)
            )
            await client.update_record("recABC123", {"call_status": "completed"})
    assert route.called
    assert route.calls.last.request.headers["Authorization"] == "Bearer test-token"


@pytest.mark.asyncio
async def test_update_record_retries_transient_error_then_succeeds(settings: Settings) -> None:
    async with httpx.AsyncClient() as http_client:
        client = AirtableClient(settings, client=http_client)
        with respx.mock:
            route = respx.patch(f"{client._base_url}/recABC123").mock(
                side_effect=[httpx.Response(503), httpx.Response(200, json=RECORD)]
            )
            await client.update_record("recABC123", {"call_status": "completed"})
    assert route.call_count == 2
