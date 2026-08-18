CREATE TABLE support_requests (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  customer_name TEXT,
  email TEXT,
  category TEXT,
  priority TEXT,
  status TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
