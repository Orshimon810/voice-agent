# Langflow Multi-Agent System (Part 3)

This is Part 3 of a multi-part assignment. It provides the local SQLite
database that backs the SQL Tool used by the Langflow multi-agent flow.

## What it is

A single `support_requests` table with sample customer support tickets,
so an agent in the Langflow flow can query it via a SQL Tool.

## Build the database

```bash
python db/build_db.py
```

This deletes any existing `db/support.db`, creates a fresh one, and runs
`schema.sql` followed by `seed_data.sql` against it. `support.db` is
generated/gitignored — regenerate it locally with the command above.

## Schema

```sql
CREATE TABLE support_requests (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  customer_name TEXT,
  email TEXT,
  category TEXT,
  priority TEXT,
  status TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

## Sample data

```sql
INSERT INTO support_requests
(customer_name, email, category, priority, status)
VALUES
('John Smith', 'john@example.com', 'Login Issue', 'High', 'Open'),
('Sarah Cohen', 'sarah@example.com', 'Billing', 'Medium', 'In Progress'),
('David Levi', 'david@example.com', 'Technical Support', 'Low', 'Closed'),
('Emma Johnson', 'emma@example.com', 'Account Access', 'High', 'Open'),
('Michael Brown', 'michael@example.com', 'Subscription', 'Medium', 'Open');
```
