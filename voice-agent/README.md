# elal-voice-agent

A FastAPI service that bridges Airtable, [Vapi](https://vapi.ai) (a Hebrew-speaking
voice agent), and Vapi's webhook callbacks, for an outbound flight-disruption-recovery
use case: when an El Al flight is delayed, this service triggers a phone call from a
Hebrew voice agent ("Dana") that informs the passenger and offers a choice between an
alternative flight or a full refund, then records the outcome back to Airtable once the
call ends.

## Architecture

```
 Airtable                    FastAPI (this service)                  Vapi
 ─────────                   ──────────────────────                  ────

 passenger + flight   ──►    GET /passengers/{id}
 records                     - fetch record from Airtable
                              - normalize numbers/times into
                                natural spoken Hebrew

                              POST /calls/trigger
                              - same normalization
                              - build variableValues            ──►  POST /call
                                                                      (place outbound
                                                                       call, run the
                                                                       Hebrew script)
                              - write call_status=pending,
                                call_id to Airtable          ◄──
                                                                      passenger talks to
                                                                      "Dana", conversation
                                                                      is transcribed

                              POST /webhooks/vapi              ◄──   end-of-call-report
                              - verify x-vapi-secret                 webhook (transcript,
                              - extract decision + summary           recording URL, etc.)
                                from the transcript
 call_status,          ◄──   - write outcome back to
 passenger_decision,          Airtable
 call_summary, ...
```

## Setup

**Prerequisites:** Python 3.11+, an Airtable base with the passenger/flight schema
described below, and a Vapi account with a Hebrew assistant configured (see
[`agent/`](#agent-directory)).

```bash
cd voice-agent
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

pip install -r requirements.txt
cp .env.example .env   # then fill in the values from the table below
uvicorn app.main:app --reload
```

The service starts on `http://127.0.0.1:8000`.

Run the tests with:

```bash
pytest
```

## Environment variables

| Variable | Purpose | Example format |
| --- | --- | --- |
| `AIRTABLE_TOKEN` | Airtable personal access token, with read/write scope on the base below. | `patXXXXXXXXXXXXXX.XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX` |
| `AIRTABLE_BASE_ID` | Airtable base id. Defaults to `app5XBvVamnrsToQQ`. | `appXXXXXXXXXXXXXX` |
| `AIRTABLE_TABLE_ID` | Airtable table id. Defaults to `tblr7Qkcp3dg55JWQ`. | `tblXXXXXXXXXXXXXX` |
| `VAPI_API_KEY` | Vapi private API key, used to create outbound calls. | `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx` |
| `VAPI_ASSISTANT_ID` | The Vapi assistant id running the Hebrew "Dana" flow. | `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx` |
| `VAPI_PHONE_NUMBER_ID` | The Vapi phone number id calls are placed from. | `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx` |
| `WEBHOOK_SECRET` | Shared secret Vapi must send back as the `x-vapi-secret` header on the webhook request. | a long random string you generate yourself |

`.env` is git-ignored; `.env.example` lists every key with an empty value. No secret
should ever be committed.

## API reference

### `GET /health`

```bash
curl http://localhost:8000/health
# {"status":"ok"}
```

### `GET /passengers/{record_id}`

Returns the raw Airtable fields alongside their normalized spoken-Hebrew equivalents.
`404` if the record doesn't exist, `502` if Airtable is unreachable.

```bash
curl http://localhost:8000/passengers/recABC123
```

```json
{
  "raw": {
    "record_id": "recABC123",
    "passenger_name": "ישראל כהן",
    "phone": "+972501234567",
    "flight_number": "LY315",
    "destination": "ניו יורק",
    "original_departure": "23:45",
    "delay_hours": 6,
    "alt_flight_time": "06:00",
    "refund_amount": 3670,
    "call_status": null,
    "passenger_decision": null,
    "call_summary": null,
    "call_id": null,
    "call_timestamp": null,
    "recording_url": null
  },
  "normalized": {
    "original_departure_spoken": "רבע לחצות",
    "delay_hours_spoken": "שש",
    "alt_flight_time_spoken": "שש בבוקר",
    "refund_amount_spoken": "שלושת אלפים שש מאות ושבעים"
  }
}
```

### `POST /calls/trigger`

Body: `{"record_id": "...", "mode": "web" | "phone"}` (any other `mode` value is
rejected with `422`). `404` if the record doesn't exist, `502` if Airtable or Vapi is
unreachable.

```bash
# mode=web: builds and returns the variables Vapi would receive, without
# placing a call or writing to Airtable — useful for manual inspection.
curl -X POST http://localhost:8000/calls/trigger \
  -H "Content-Type: application/json" \
  -d '{"record_id": "recABC123", "mode": "web"}'

# mode=phone: places a real call through Vapi and marks the Airtable
# record call_status=pending with the new call_id.
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

### `POST /webhooks/vapi`

Configure this URL as the assistant's server URL in Vapi, with `x-vapi-secret` set to
`WEBHOOK_SECRET` (see [`agent/assistant_export.json`](#agent-directory)'s `server`
block). This is the only route that returns a non-200 for a bad request — a secret
mismatch returns `401`. Every other failure mode (malformed body, unrecognized event
type, missing call id, record not found, an Airtable write failure) still returns `200`
so Vapi never retries and double-writes a record.

```bash
curl -X POST http://localhost:8000/webhooks/vapi \
  -H "Content-Type: application/json" \
  -H "x-vapi-secret: $WEBHOOK_SECRET" \
  -d '{
    "message": {
      "type": "end-of-call-report",
      "endedReason": "customer-ended-call",
      "call": {"id": "call_999"},
      "recordingUrl": "https://recordings.vapi.ai/call_999.mp3",
      "artifact": {
        "messages": [
          {"role": "assistant", "message": "יש לך שתי אפשרויות, טיסה חלופית או החזר כספי"},
          {"role": "user", "message": "אני מעדיף לקבל החזר כספי בבקשה"}
        ]
      }
    }
  }'
# {"status":"ok"}
```

On success, the handler writes these fields to the matching Airtable record (matched by
`call.id`):

- **`call_status`** — derived from `endedReason` by substring match: anything
  mentioning voicemail/no-answer/busy → `no_answer`, anything mentioning error/failed →
  `failed`, everything else → `completed`.
- **`passenger_decision`** — one of `alternative_flight`, `refund`, `human_agent`,
  `callback_requested`, or `undecided`. See
  [Hebrew Voice Engineering Notes](#hebrew-voice-engineering-notes) for how this is
  actually determined.
- **`call_summary`** — a one-sentence Hebrew summary of the outcome.
- **`call_timestamp`** — the call's end (or start) time, normalized to UTC
  milliseconds with a `Z` suffix; falls back to the current time if Vapi didn't send
  one.
- **`recording_url`** — the recording URL, or an empty string if absent.

## Hebrew Voice Engineering Notes

### Why numbers and times are pre-normalized (`app/hebrew.py`)

Azure's Hebrew text-to-speech, which Vapi uses for this assistant's voice, handles raw
digits inconsistently. Two failure modes were observed in testing: it sometimes falls
back to reading numbers in English instead of Hebrew, and when it does read them in
Hebrew it reads the literal digits ("twenty-three, forty-five") rather than the way a
Hebrew speaker would actually say a time ("quarter to midnight"). To avoid both, every
numeric field sent to Vapi — departure time, delay hours, alternative flight time,
refund amount — is pre-converted into natural spoken Hebrew words before the TTS engine
ever sees it, so it only ever reads text, never digits.

### Why the decision/summary come from the transcript, not Vapi's `structuredOutputs`

Vapi can compute structured fields (like a classified `decision`) from the call and
attach them to the webhook payload under `message.structuredOutputs`. In production,
that field arrived too late or not at all: Vapi computes it asynchronously after the
end-of-call-report webhook already fires, with no guaranteed timing, so relying on it
directly — or even polling Vapi's API for it afterward — proved unreliable (tried up to
15 seconds of polling before giving up on that approach entirely).

Instead, `passenger_decision` and `call_summary` are derived locally from
`message.artifact.messages`, the full conversation transcript, which *is* reliably
present the moment the webhook fires. `structuredOutputs` is still checked first as an
optional bonus — if Vapi happens to have it ready, it's used — but the transcript is
the path the system actually depends on.

This extraction is a **pragmatic substring-matching heuristic, not NLP**: there's no
lightweight, dependency-free Hebrew stemming library available, so decisions are
classified by scanning the passenger's turns for a curated list of Hebrew phrases per
category (e.g. `"טיסה חלופית"` for `alternative_flight`, `"החזר כספי"` for `refund`).
Both the transcript and the phrase list are normalized the same way before matching —
punctuation stripped, and a leading Hebrew definite article (`ה-`) stripped from each
word, so `"טיסה חלופית"` and `"הטיסה החלופית"` match identically without needing every
article variant spelled out. If more than one category's phrases appear in the
conversation, whichever one appears **latest** wins, since passengers sometimes ask
about an option before settling on a different one.

**Known limitation:** this heuristic doesn't understand negation. A sentence like "לא
טיסה חלופית" ("not the alternative flight") would still match `alternative_flight`,
because it's matching phrases as substrings, not parsing meaning. This is an accepted
trade-off given the constraints, not an oversight — worth keeping an eye on as more
real transcripts come in.

## Deployment (Railway)

The service is deployed via `Procfile`
(`web: uvicorn app.main:app --host 0.0.0.0 --port $PORT`) and `railway.toml`. Since this
repo is a monorepo, Railway's service must have its **root directory set to
`voice-agent`**, or it won't find `requirements.txt`/`Procfile` at all. Set the
environment variables from the table above in the Railway project settings, then deploy
the `main` branch.

The webhook URL Railway assigns
(`https://<your-app>.up.railway.app/webhooks/vapi`) and the `WEBHOOK_SECRET` value must
be kept in sync with the Vapi assistant's `server.url` / `server.headers.x-vapi-secret`
configuration (see `agent/assistant_export.json`'s `server` block). If either drifts —
a new Railway deployment URL, or a rotated `WEBHOOK_SECRET` — update the assistant's
server config in the Vapi dashboard/API too, or webhooks will fail with `401` or never
arrive.

## `agent/` directory

`agent/assistant_export.json` and `agent/system_prompt.md` contain the exported Vapi
assistant configuration and system prompt for this project, included for assignment
submission purposes. `assistant_export.json` has had its webhook secret redacted.

## `recordings/` directory

`recordings/` contains the audio recordings of the three real outbound test calls
referenced in the submission, one per passenger decision outcome: `Yonathan_Cohen.wav`
(`refund`), `Roni_Levi.wav` (`alternative_flight`), and `David_Avraham.wav`
(`human_agent`).

## `submission/` directory

`submission/` contains the assignment deliverables: `elal_voice_agent.pdf`, the
presentation covering the use cases, model comparison, architecture, and business
value, and `outbound_calls_log.csv`, a CSV export from Airtable showing the three
example call records from `recordings/` with their outcomes.

## Development Workflow

This project was built using a feature-branch-per-component workflow: each change
(a fix, a new endpoint, a refactor) was made on its own short-lived branch, committed
using [Conventional Commits](https://www.conventionalcommits.org/) style messages, and
merged into `main` only after the test suite passed. As a result, `main`'s history is
not a single flat commit — run `git log --oneline --graph` to see the full sequence of
branches and merges that produced the current code.

## Project structure

```
voice-agent/
├── agent/
│   ├── assistant_export.json   # exported Vapi assistant config (secret redacted)
│   └── system_prompt.md        # the assistant's Hebrew system prompt
├── app/
│   ├── main.py                 # FastAPI app + routes
│   ├── config.py                # env vars via pydantic-settings
│   ├── airtable.py              # Airtable read/write client
│   ├── vapi.py                   # Vapi call-creation client
│   ├── hebrew.py                # Hebrew text normalization
│   ├── models.py                # Pydantic models
│   ├── retry.py                  # shared exponential-backoff retry policy
│   └── logging_config.py         # structured (JSON) logging setup
├── recordings/
│   ├── Yonathan_Cohen.wav      # test call recording (refund)
│   ├── Roni_Levi.wav           # test call recording (alternative_flight)
│   └── David_Avraham.wav       # test call recording (human_agent)
├── submission/
│   ├── elal_voice_agent.pdf         # presentation: use cases, model comparison, architecture, business value
│   └── outbound_calls_log.csv       # Airtable export of the three call records with outcomes
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
