# elal-voice-agent

FastAPI service that sits between Airtable, the [Vapi](https://vapi.ai) voice-agent
platform, and Vapi's end-of-call webhook, for a Hebrew-speaking voice agent that calls
El Al passengers whose flight was delayed. The agent presents two options — an
alternative flight or a full refund — and this service feeds it the passenger's data
and persists the outcome of the call back to Airtable.

## What it does

1. **Reads passenger + flight data from Airtable** (`GET /passengers/{record_id}`) —
   name, phone, flight number, destination, original departure time, delay length,
   alternative flight time, and refund amount.
2. **Triggers a call** (`POST /calls/trigger`) — normalizes the numeric fields into
   natural spoken Hebrew (see below), builds the variables the voice agent needs, and
   either places a real call through Vapi (`mode=phone`) or just returns the built
   variables for manual inspection (`mode=web`).
3. **Receives the end-of-call webhook** (`POST /webhooks/vapi`) — verifies it's really
   from Vapi, extracts the call summary/recording/transcript/decision, and writes the
   outcome back to the matching Airtable record.

## Setup

```bash
cd voice-agent
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

pip install -r requirements.txt
cp .env.example .env   # then fill in the values below
uvicorn app.main:app --reload
```

The service starts on `http://127.0.0.1:8000`.

Run the tests with:

```bash
pytest
```

## Environment variables

| Variable                | Description                                                              |
| ------------------------ | ------------------------------------------------------------------------ |
| `AIRTABLE_TOKEN`         | Airtable personal access token with read/write scope on the base below.  |
| `AIRTABLE_BASE_ID`       | Airtable base id. Defaults to `app5XBvVamnrsToQQ`.                       |
| `AIRTABLE_TABLE_ID`      | Airtable table id. Defaults to `tblr7Qkcp3dg55JWQ`.                      |
| `VAPI_API_KEY`           | Vapi private API key, used to create outbound calls.                     |
| `VAPI_ASSISTANT_ID`      | The Vapi assistant configured to run this Hebrew flow.                   |
| `VAPI_PHONE_NUMBER_ID`   | The Vapi phone number id calls are placed from.                          |
| `WEBHOOK_SECRET`         | Shared secret Vapi sends back as `x-vapi-secret` on the webhook request. |

No secret ever appears in source; `.env` is git-ignored and `.env.example` lists every
key with an empty value.

## Routes

### `GET /health`

```bash
curl http://localhost:8000/health
# {"status":"ok"}
```

### `GET /passengers/{record_id}`

Returns the raw Airtable fields alongside their normalized spoken-Hebrew equivalents,
so the two can be compared during testing.

```bash
curl http://localhost:8000/passengers/recABC123
```

```json
{
  "raw": {
    "record_id": "recABC123",
    "passenger_name": "יוסי כהן",
    "phone": "+972501234567",
    "flight_number": "LY315",
    "destination": "JFK",
    "original_departure": "06:00",
    "delay_hours": 3,
    "alt_flight_time": "09:30",
    "refund_amount": 850,
    "call_status": null,
    "passenger_decision": null,
    "call_summary": null,
    "call_id": null,
    "call_timestamp": null,
    "recording_url": null
  },
  "normalized": {
    "original_departure_spoken": "שש בבוקר",
    "delay_hours_spoken": "שלוש",
    "alt_flight_time_spoken": "תשע וחצי בבוקר",
    "refund_amount_spoken": "שמונה מאות וחמישים"
  }
}
```

### `POST /calls/trigger`

```bash
# Build the variables without placing a call (manual inspection)
curl -X POST http://localhost:8000/calls/trigger \
  -H "Content-Type: application/json" \
  -d '{"record_id": "recABC123", "mode": "web"}'

# Place a real call through Vapi
curl -X POST http://localhost:8000/calls/trigger \
  -H "Content-Type: application/json" \
  -d '{"record_id": "recABC123", "mode": "phone"}'
```

```json
{
  "call_id": null,
  "variable_values": {
    "passenger_name": "יוסי כהן",
    "flight_number": "LY315",
    "destination": "JFK",
    "original_departure_spoken": "שש בבוקר",
    "delay_hours_spoken": "שלוש",
    "alt_flight_time_spoken": "תשע וחצי בבוקר",
    "refund_amount_spoken": "שמונה מאות וחמישים"
  }
}
```

In `mode=phone`, `call_id` is the id Vapi assigned to the created call, and the
Airtable record's `call_status` is set to `pending` with that `call_id` persisted.
`mode=web` never calls Vapi or writes to Airtable — it only returns the variables that
would have been sent, for manual review.

### `POST /webhooks/vapi`

Configure this URL as the assistant's end-of-call webhook in Vapi, with
`x-vapi-secret` set to `WEBHOOK_SECRET`.

```bash
curl -X POST http://localhost:8000/webhooks/vapi \
  -H "Content-Type: application/json" \
  -H "x-vapi-secret: $WEBHOOK_SECRET" \
  -d '{
    "message": {
      "type": "end-of-call-report",
      "endedReason": "customer-ended-call",
      "call": {"id": "call_999"},
      "summary": "Passenger chose the alternative flight.",
      "recordingUrl": "https://recordings.vapi.ai/call_999.mp3",
      "analysis": {"structuredData": {"decision": "alternative_flight"}}
    }
  }'
# {"status":"ok"}
```

The handler always responds `200` once past the secret check (even on a missing
record, malformed body, or an Airtable write failure), so Vapi never retries and
double-writes a record. A secret mismatch is the one case that returns a non-200
(`401`).

`passenger_decision` is read from the assistant's structured-data output
(`message.analysis.structuredData.decision`); this assumes the Vapi assistant is
configured to emit a `decision` field with one of
`alternative_flight` / `refund` / `human_agent` / `undecided`. Anything missing or
unrecognized defaults to `undecided`. `endedReason` is mapped to `call_status` by
substring: anything mentioning voicemail/no-answer/busy → `no_answer`, anything
mentioning error/failed → `failed`, everything else → `completed`.

## The Hebrew normalization layer (`app/hebrew.py`)

Azure's Hebrew text-to-speech, used by the voice agent, handles raw digits
inconsistently. Two distinct failure modes were observed in testing:

1. **English fallback** — `"06:00"` was sometimes spoken as "sikes" instead of Hebrew.
2. **Unnatural literal conversion** — `"23:45"` was spoken as
   *"esrim ve'shalosh arba'im ve'chamesh"* (literally "twenty-three, forty-five"),
   which is not how Hebrew speakers say the time — the idiomatic form is
   "quarter to midnight" (`רבע לחצות`).

To avoid both failure modes, every numeric field going to Vapi is pre-converted to
natural spoken Hebrew words before being sent, so the TTS engine only ever sees text,
never digits.

- **`normalize_time("HH:MM") -> str`** — converts 24-hour time to 12-hour spoken
  Hebrew with a part-of-day suffix (`בבוקר`/`בצהריים`/`בערב`/`בלילה`), feminine hour
  names (`אחת, שתיים, שלוש, ...`), and idiomatic minute handling: `:00` is dropped,
  `:15` → `ורבע`, `:30` → `וחצי`, `:45` → "quarter to" the next hour — with the
  special case `23:45` → `רבע לחצות` rather than the literal "quarter to twelve".
- **`normalize_number(n: int) -> str`** — converts integers (delay hours, refund
  amounts) into spoken Hebrew number words, e.g. `6` → `שש`,
  `3670` → `שלושת אלפים שש מאות ושבעים`.

Flight codes like `"LY315"` are left untouched — testing showed Azure's TTS reads
alphanumeric flight codes correctly already, so only `original_departure`,
`alt_flight_time`, `delay_hours`, and `refund_amount` are passed through this layer.

## Deployment (Railway)

The service is deployed via `Procfile` (`web: uvicorn app.main:app --host 0.0.0.0
--port $PORT`) and `railway.toml`. Railway auto-detects the Python app from
`requirements.txt`; set the environment variables from the table above in the
Railway project settings, then deploy the `main` branch.

## Project structure

```
voice-agent/
├── app/
│   ├── main.py            # FastAPI app + routes
│   ├── config.py           # env vars via pydantic-settings
│   ├── airtable.py         # Airtable read/write client
│   ├── vapi.py              # Vapi call-creation client
│   ├── hebrew.py           # Hebrew text normalization
│   ├── models.py           # Pydantic models
│   ├── retry.py             # shared exponential-backoff retry policy
│   └── logging_config.py    # structured (JSON) logging setup
├── tests/
│   ├── conftest.py
│   ├── test_hebrew.py
│   ├── test_airtable.py
│   ├── test_vapi.py
│   ├── test_routes.py
│   └── test_webhook.py
├── .env.example
├── requirements.txt
├── Procfile
├── railway.toml
└── README.md
```
