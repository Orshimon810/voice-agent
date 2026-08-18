"""Build support.db from schema.sql and seed_data.sql."""

import sqlite3
from pathlib import Path

DB_DIR = Path(__file__).parent
DB_PATH = DB_DIR / "support.db"
SCHEMA_PATH = DB_DIR / "schema.sql"
SEED_PATH = DB_DIR / "seed_data.sql"


def main() -> None:
    if DB_PATH.exists():
        DB_PATH.unlink()

    conn = sqlite3.connect(DB_PATH)
    try:
        conn.executescript(SCHEMA_PATH.read_text())
        conn.executescript(SEED_PATH.read_text())
        conn.commit()

        row_count = conn.execute("SELECT COUNT(*) FROM support_requests").fetchone()[0]
    finally:
        conn.close()

    print(f"Built {DB_PATH} with {row_count} rows in support_requests.")


if __name__ == "__main__":
    main()
