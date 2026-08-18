# Jeen AI Solution Engineer — Take-Home Assignment

This repository is a submission for the Jeen AI Solution Engineer take-home assignment.
It's a monorepo containing three independent parts: a Hebrew-speaking outbound voice AI
agent built on Vapi, a Python RAG (retrieval-augmented generation) CLI tool, and a
Langflow multi-agent customer support system.

## Parts

| Directory | Part | Description |
| --- | --- | --- |
| [`voice-agent/`](voice-agent/) | Part 1 | Outbound Hebrew voice agent for El Al flight-disruption recovery (Vapi + FastAPI + Airtable) |
| [`rag/`](rag/) | Part 2 | Text/PDF embedding and retrieval CLI tool, tested against Israel's Aviation Services Law |
| [`langflow/`](langflow/) | Part 3 | 3-agent customer support system (Orchestrator, Analysis, Response) with SQL and Gmail tools |

Each directory has its own README with full setup, architecture, and usage details.

### [`voice-agent/`](voice-agent/README.md) — Part 1

A FastAPI service that bridges Airtable, Vapi, and Vapi's webhooks to place outbound
Hebrew phone calls informing passengers of a flight delay and recording their decision
(alternative flight, refund, or human agent) back to Airtable. See
[voice-agent/README.md](voice-agent/README.md) for architecture, API reference, and
Hebrew voice engineering notes.

Submission artifacts:
- [`voice-agent/submission/elal_voice_agent.pdf`](voice-agent/submission/) — presentation (use cases, model comparison, architecture, business value)
- [`voice-agent/submission/outbound_calls_log.csv`](voice-agent/submission/) — Airtable export of the three test call records
- [`voice-agent/recordings/`](voice-agent/recordings/) — audio recordings of the three real outbound test calls (one per decision outcome)

### [`rag/`](rag/README.md) — Part 2

A standalone CLI that extracts text from a `.txt`/`.pdf` file, chunks it, embeds it with
OpenAI, and answers natural-language questions by cosine-similarity search over the
stored chunks. Tested against a Hebrew sample document, Israel's Aviation Services Law.
See [rag/README.md](rag/README.md) for the chunking strategy and example
index/search output.

### [`langflow/`](langflow/README.md) — Part 3

A 3-agent Langflow flow (Orchestrator → Analysis → Response) for customer support,
backed by a SQLite `support_requests` database queried through a SQL Tool, plus a
custom Gmail-sending Tool. See [langflow/README.md](langflow/README.md) for the
architecture, setup steps, and known issues/engineering notes.

Submission artifacts:
- [Demo video](https://www.youtube.com/watch?v=81BE5rDK_BU)
- [`langflow/Customer Support Multi-Agent System.json`](langflow/Customer%20Support%20Multi-Agent%20System.json) — exported flow

## Shared conventions

All three parts were built with the same feature-branch-per-component workflow: each
change was made on its own short-lived branch, committed using
[Conventional Commits](https://www.conventionalcommits.org/), and merged into `main`
only after tests passed. Run `git log --oneline --graph` to see the full branch/merge
history behind the current code.

Each part is fully self-contained — its own dependencies, its own tests, no code shared
between `voice-agent/`, `rag/`, and `langflow/`.
