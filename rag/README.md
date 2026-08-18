# RAG (Retrieval-Augmented Generation) CLI

A standalone Python script that builds a small local search index over a
text or PDF document using OpenAI embeddings, and answers natural-language
questions by retrieving the most relevant chunks of text. No web server,
no database — just a CLI and a JSON file.

> Part 2 of a multi-part take-home assignment. Independent of the
> `voice-agent/` component in this repo — different task, no shared code.

## What it does

1. **Extract** text from a `.txt` or `.pdf` file.
2. **Chunk** the text into overlapping pieces sized for embedding.
3. **Embed** each chunk with OpenAI's `text-embedding-3-small` model.
4. **Store** chunks + embeddings + source metadata in a local JSON file.
5. **Search**: embed a question, rank stored chunks by cosine similarity,
   and print the top matches with their scores.

## Setup

```bash
cd rag
python -m venv .venv
.venv\Scripts\activate      # Windows
# source .venv/bin/activate # macOS/Linux

pip install -r requirements.txt

copy .env.example .env      # Windows
# cp .env.example .env      # macOS/Linux
# then edit .env and set OPENAI_API_KEY=sk-...
```

`.env` is gitignored — never commit real API keys.

## Usage

### Index a document

```bash
python cli.py index data/chok_tivi_aviation_services_law.txt
```

```
Extracted 6360 characters, split into 20 chunks. Embedding...
Saved index with 20 chunks to rag/data/index.json
```

This writes `data/index.json` containing each chunk's text, embedding
vector, and source filename. Re-running `index` overwrites it.

### Search the index

```bash
python cli.py search "מה גובה הפיצוי הכספי במקרה של ביטול טיסה?"
```

```
#1  score=0.6297  source=chok_tivi_aviation_services_law.txt
ק"מ; עד ארבע שעות אם המרחק עולה על 4,500 ק"מ. (ג) נוסע שטיסתו בוטלה לא
יהיה זכאי לפיצוי כספי אם מפעיל הטיסה הוכיח כי הנוסע קיבל הודעה על
הביטול 14 ימים לפחות לפני מועד הטיסה...

#2  score=0.6022  source=chok_tivi_aviation_services_law.txt
לפי בחירת הנוסע; פיצוי כספי כאמור בתוספת הראשונה. (ב) מפעיל טיסה או
מארגן שהציע כרטיס טיסה חלופי והנוסע קיבל את ההצעה, רשאי להפחית במחצית
את סכום הפיצוי הכספי...

#3  score=0.5463  source=chok_tivi_aviation_services_law.txt
וכן לפי בחירתו — השבת תמורה או כרטיס טיסה חלופי. (ג) מפעיל טיסה או
מארגן שהציע כרטיס טיסה חלופי, והנוסע קיבל את ההצעה, רשאי להפחית במחצית
את סכום הפיצוי הכספי...
```

Optional flags:

```bash
python cli.py search "<question>" --top-n 5
python cli.py index <file_path> --index-path data/custom_index.json
python cli.py search "<question>" --index-path data/custom_index.json
```

## Chunking strategy

`chunk_text()` targets **~500 characters per chunk with ~50 characters of
overlap**:

- **500 chars** keeps each chunk small enough to stay topically focused
  (roughly one to a few sentences of legal/dense text) while still large
  enough to give the embedding model meaningful context — short enough for
  precise retrieval, long enough to avoid fragmenting a single clause
  across chunks.
- **50 chars (~10%) overlap** guards against relevant text being split
  right at a chunk boundary — a sentence beginning at the end of one chunk
  is repeated at the start of the next, so its full context can still be
  retrieved.
- Splitting prefers **paragraph boundaries**, then **sentence boundaries**
  (`.`, `!`, `?`), before falling back to a **hard character split** (only
  for a single token longer than the chunk size) — so chunks almost never
  cut a word in half, keeping retrieved text readable.

These parameters worked well on the sample legal document: its numbered
sections and compensation clauses are dense with commas and semicolons but
few short sentences, so ~500 chars typically captures one clause or a
tight cluster of related clauses per chunk (20 chunks from ~6,360
characters).

## Project structure

```
rag/
├── rag/
│   ├── extract.py   # PDF/TXT text extraction
│   ├── chunk.py     # overlapping text splitting
│   ├── embed.py     # OpenAI embeddings client
│   ├── store.py     # local JSON storage (chunks + vectors)
│   └── search.py    # cosine similarity ranking
├── cli.py           # command-line entry point (index / search)
├── data/
│   └── chok_tivi_aviation_services_law.txt  # sample document
├── tests/
│   ├── test_extract.py
│   ├── test_chunk.py
│   └── test_search.py
├── .env.example
└── requirements.txt
```

## Running tests

```bash
python -m pytest
```

`test_search.py` verifies the cosine-similarity ranking logic against
fake embedding vectors — no OpenAI API calls, no network access needed to
run the test suite. `test_extract.py` builds a minimal PDF from raw bytes
to exercise real PDF parsing without a PDF-writing dependency.

## Error handling

- Missing file → `FileNotFoundError` with the path that was checked.
- Empty file / no extractable text → `ValueError`.
- Unsupported extension → clear error listing supported extensions
  (`.txt`, `.pdf`).
- Missing `OPENAI_API_KEY` → error pointing to `.env.example`.
- OpenAI API failures (rate limits, network errors, etc.) → wrapped with
  context and re-raised.
- Searching before an index exists → error telling you to run
  `python cli.py index <file_path>` first.
