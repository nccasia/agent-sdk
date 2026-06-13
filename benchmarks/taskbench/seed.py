"""A realistic, deterministic e-commerce dataset in stdlib SQLite — no mocks, no network.

Seeded with a fixed RNG so the data (and therefore every task's ground truth) is
reproducible run-to-run. The agent runs *real* SQL against this DB; the bench
computes each task's correct answer by running reference SQL against the same DB,
so grading needs no hand-entered expected values.
"""

from __future__ import annotations

import random
import sqlite3

SCHEMA = """
CREATE TABLE customers (
  id INTEGER PRIMARY KEY, name TEXT, email TEXT, country TEXT, signup_date TEXT);
CREATE TABLE products (
  id INTEGER PRIMARY KEY, name TEXT, category TEXT, price REAL);
CREATE TABLE orders (
  id INTEGER PRIMARY KEY, customer_id INTEGER, order_date TEXT, status TEXT);
CREATE TABLE order_items (
  order_id INTEGER, product_id INTEGER, quantity INTEGER, unit_price REAL);
"""

_FIRST = ["Anna", "Ben", "Carla", "David", "Elena", "Frank", "Grace", "Hugo", "Ivy",
          "Jack", "Kira", "Leo", "Mia", "Noah", "Olive", "Paul", "Quinn", "Rosa",
          "Sam", "Tara", "Uma", "Victor", "Wendy", "Xander", "Yara", "Zane"]
_LAST = ["Adler", "Brooks", "Cruz", "Diaz", "Evans", "Ford", "Gupta", "Hale", "Ito",
         "Jones", "Khan", "Lopez", "Meyer", "Novak", "Owens", "Park", "Reyes", "Singh"]
_COUNTRIES = ["US", "US", "US", "DE", "DE", "VN", "VN", "FR", "GB", "JP"]  # weighted: US most
# (name, category, price)
_PRODUCTS = [
    ("Wireless Mouse", "Accessories", 24.99), ("Mechanical Keyboard", "Accessories", 89.00),
    ("USB-C Cable 2m", "Accessories", 12.50), ("Laptop Stand", "Accessories", 39.95),
    ("Webcam 1080p", "Accessories", 54.00), ("Noise-Cancel Headphones", "Audio", 199.00),
    ("Bluetooth Speaker", "Audio", 79.00), ("Wired Earbuds", "Audio", 19.99),
    ("27in 4K Monitor", "Displays", 329.00), ("24in FHD Monitor", "Displays", 159.00),
    ("Ultrawide Monitor", "Displays", 499.00), ("Laptop Pro 14", "Computers", 1799.00),
    ("Laptop Air 13", "Computers", 999.00), ("Mini Desktop", "Computers", 649.00),
    ("Tablet 10", "Computers", 449.00), ("Office Chair", "Furniture", 219.00),
    ("Standing Desk", "Furniture", 379.00), ("Desk Lamp LED", "Furniture", 34.50),
    ("Power Bank 20k", "Mobile", 45.00), ("Phone Case", "Mobile", 14.99),
]
_STATUSES = ["completed", "completed", "completed", "completed", "shipped", "cancelled"]


def build_db() -> sqlite3.Connection:
    """An in-memory SQLite DB seeded deterministically (same data every run)."""
    rng = random.Random(2024)
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA)

    customers = []
    for i in range(1, 61):
        name = f"{rng.choice(_FIRST)} {rng.choice(_LAST)}"
        month, day = rng.randint(1, 12), rng.randint(1, 28)
        customers.append((i, name, f"user{i}@example.com", rng.choice(_COUNTRIES),
                          f"2023-{month:02d}-{day:02d}"))
    conn.executemany("INSERT INTO customers VALUES (?,?,?,?,?)", customers)

    products = [(i + 1, n, c, p) for i, (n, c, p) in enumerate(_PRODUCTS)]
    conn.executemany("INSERT INTO products VALUES (?,?,?,?)", products)

    orders, items = [], []
    for oid in range(1, 401):
        cust = rng.randint(1, 60)
        month, day = rng.randint(1, 12), rng.randint(1, 28)
        orders.append((oid, cust, f"2023-{month:02d}-{day:02d}", rng.choice(_STATUSES)))
        for _ in range(rng.randint(1, 4)):  # 1–4 line items per order
            pid = rng.randint(1, len(_PRODUCTS))
            qty = rng.randint(1, 3)
            unit = products[pid - 1][3]
            items.append((oid, pid, qty, unit))
    conn.executemany("INSERT INTO orders VALUES (?,?,?,?)", orders)
    conn.executemany("INSERT INTO order_items VALUES (?,?,?,?)", items)
    conn.commit()
    return conn


def schema_text() -> str:
    """Human/agent-readable schema description (what db.schema returns)."""
    return (
        "Tables (SQLite):\n"
        "- customers(id, name, email, country, signup_date)  -- country e.g. US/DE/VN/FR/GB/JP\n"
        "- products(id, name, category, price)               -- category e.g. Accessories/Audio/Displays/Computers/Furniture/Mobile\n"
        "- orders(id, customer_id, order_date, status)       -- status e.g. completed/shipped/cancelled; order_date 'YYYY-MM-DD'\n"
        "- order_items(order_id, product_id, quantity, unit_price)\n"
        "Revenue of a line item = quantity * unit_price. Join order_items→orders for dates/status, "
        "order_items→products for names/categories, orders→customers for customer info."
    )
