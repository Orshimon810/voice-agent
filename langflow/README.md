# Langflow Multi-Agent System (Part 3)

This is Part 3 of a multi-part assignment. It's a 3-agent Langflow flow
(Orchestrator, Analysis, Response) for a customer support use case, backed
by a SQLite database (`support_requests`) queried through a SQL Tool, plus
a custom Gmail-sending Tool built with SMTP and an App Password.

## Architecture

```
Chat Input → Orchestrator (Agent) → Response (Agent) → Chat Output
                   │                       │
                   ▼                       ▼
              Analysis (Tool)         Gmail Send Tool
                   │
                   ▼
              SQL Tool → support_requests (SQLite)
```

- **Orchestrator** reads the incoming chat message and, if it contains an
  identifying detail (a name, ticket detail, category, status word, etc.),
  calls **Analysis** as a Tool, passing the user's question verbatim.
  It then produces a short handoff message for Response.
- **Analysis** is wired to Orchestrator as an Agent-as-Tool. Its own tool is
  the SQL Tool, which it must call before answering — it looks up
  `support_requests` by partial name match and reports back exactly what it
  found (or didn't).
- **Response** receives Orchestrator's handoff as **direct input, not as a
  tool call** — Orchestrator's output feeds Response's input directly in the
  flow graph. This is a deliberate structural choice; see Known Issues (a)
  below for why. Response writes the final customer-facing reply and can
  call the **Gmail Send Tool** if the user asked for an email.

Prompts for all three agents live in [`agent/`](agent/):
[`orchestrator_prompt.md`](agent/orchestrator_prompt.md),
[`analysis_prompt.md`](agent/analysis_prompt.md),
[`response_prompt.md`](agent/response_prompt.md).

## Setup

1. **Build the SQLite database:**

   ```bash
   python db/build_db.py
   ```

   This deletes any existing `db/support.db`, creates a fresh one, and runs
   `schema.sql` followed by `seed_data.sql` against it. `support.db` is
   generated/gitignored — regenerate it locally with the command above.

2. **Import the flow into Langflow:**

   Import [`Customer Support Multi-Agent System.json`](Customer%20Support%20Multi-Agent%20System.json)
   into Langflow (Flows → Import).

3. **Re-enter credentials manually.** The exported flow JSON does **not**
   carry working credentials — connection strings and secrets are
   environment-specific and are not safe to ship in a shared export. After
   import, open the flow in the Langflow UI and set:
   - **SQL Database** component → **Database URL**: the absolute path to
     your local `db/support.db`, e.g. `sqlite:///<absolute-path-to>/langflow/db/support.db`.
   - **Gmail Send Tool** component → **Gmail Address** and **Gmail App
     Password** fields, filled in directly in the UI.

   Do this every time you import the flow on a new machine or a fresh
   Langflow instance.

## How to run

**Playground:** open the flow in Langflow and use the built-in Playground
to send messages and watch Orchestrator → Analysis → Response execute.

**HTTP API:** POST to `/api/v1/run/{flow_id}` with an API key header, e.g.:

```bash
curl -X POST "http://localhost:7860/api/v1/run/<flow_id>" \
  -H "Content-Type: application/json" \
  -H "x-api-key: <your-langflow-api-key>" \
  -d '{
    "input_value": "מה הסטטוס של הפנייה של Sarah Cohen?",
    "output_type": "chat",
    "input_type": "chat"
  }'
```

This matches what was actually tested end-to-end — asking for the status
of Sarah Cohen's ticket correctly round-tripped through Orchestrator →
Analysis (SQL lookup) → Response, returning her ticket's real status
("In Progress") in the same language as the question.

## Known Issues & Engineering Notes

**(a) Agent-as-Tool duplication bug.** Using one Agent component as a Tool
for another Agent (Langflow's Agent-as-Tool pattern) triggered a known
upstream bug where the final answer text got emitted twice and
concatenated together (see
[langflow-ai/langflow#5338](https://github.com/langflow-ai/langflow/issues/5338)
for the general pattern). This showed up when Response was wired as a Tool
of Orchestrator. The workaround was to restructure the flow so Response is
not a Tool at all — it's a direct downstream Agent, with Orchestrator's
output feeding Response's input directly. That also incidentally fixed the
duplication.

**(b) Ambiguous default tool actions on Agent-as-Tool.** When an Agent
component is exposed as a Tool (as Analysis is, to Orchestrator), Langflow
generates multiple generic actions by default (e.g. "Message Response" and
"Json Response"), and Orchestrator sometimes picked the wrong one. Fixed by
giving the intended action a clear name and description ("ALWAYS use this
tool when...", explicitly telling it to pass the user's full question) and
disabling the ambiguous alternative action in the component's Actions
settings.

**(c) Orchestrator forwarding vague text to Analysis.** Orchestrator
initially forwarded generic handoff text (e.g. "continue" or "look into
this") to Analysis instead of the user's actual question, which caused
failed database lookups. Fixed by explicitly requiring, in Orchestrator's
prompt, that the original question — including any names — be passed
verbatim as the tool input.

## Demo video

[Demo video](https://www.youtube.com/watch?v=81BE5rDK_BU)

## Project structure

```
langflow/
├── agent/
│   ├── orchestrator_prompt.md   # Orchestrator system prompt
│   ├── analysis_prompt.md       # Analysis system prompt
│   └── response_prompt.md       # Response system prompt
├── db/
│   ├── schema.sql                # support_requests table definition
│   ├── seed_data.sql             # sample support tickets
│   ├── build_db.py               # rebuilds support.db from schema + seed
│   └── support.db                # generated SQLite file (gitignored)
├── Customer Support Multi-Agent System.json   # exported Langflow flow
├── .gitignore
└── README.md
```
