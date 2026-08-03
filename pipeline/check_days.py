import os
from datetime import date, timedelta

import psycopg2
from dotenv import load_dotenv

load_dotenv()

SCHEMA = os.environ["PG_SCHEMA"]
PREFIX = os.environ["TABLE_PREFIX"]
VISITS = f"{SCHEMA}.{PREFIX}visits"

conn = psycopg2.connect(
    host=os.environ["PG_HOST"],
    port=int(os.environ["PG_PORT"]),
    dbname=os.environ["PG_DB"],
    user=os.environ["PG_USER"],
    password=os.environ["PG_PASSWORD"],
)
cur = conn.cursor()
cur.execute(f"SELECT load_date, count(*) FROM {VISITS} GROUP BY 1 ORDER BY 1")
rows = cur.fetchall()
if not rows:
    print("[FAIL] table is empty")
    raise SystemExit(1)

have = {d: n for d, n in rows}
first, last = min(have), max(have)
print(f"range: {first} .. {last} | days: {len(have)} | rows: {sum(have.values())}")

missing = []
d = first
while d <= last:
    if d not in have:
        missing.append(str(d))
    d += timedelta(days=1)
print("gaps inside range:", missing if missing else "none")

yesterday = date.today() - timedelta(days=1)
tail = []
d = last + timedelta(days=1)
while d <= yesterday:
    tail.append(str(d))
    d += timedelta(days=1)
print("missing up to yesterday:", tail if tail else "none")

print(f"daily min/max: {min(have.values())} / {max(have.values())}")
odd = [str(d) for d, n in sorted(have.items()) if n < 5000 or n > 40000]
print("suspicious days:", odd if odd else "none")
conn.close()