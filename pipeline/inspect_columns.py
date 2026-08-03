import os

import psycopg2
from dotenv import load_dotenv

load_dotenv()

SCHEMA = os.environ["PG_SCHEMA"]
PREFIX = os.environ["TABLE_PREFIX"]
VISITS = f"{SCHEMA}.{PREFIX}visits"
DAY = "2026-08-02"

conn = psycopg2.connect(
    host=os.environ["PG_HOST"],
    port=int(os.environ["PG_PORT"]),
    dbname=os.environ["PG_DB"],
    user=os.environ["PG_USER"],
    password=os.environ["PG_PASSWORD"],
)
cur = conn.cursor()
cur.execute(
    "SELECT column_name FROM information_schema.columns "
    "WHERE table_schema=%s AND table_name=%s ORDER BY ordinal_position",
    (SCHEMA, f"{PREFIX}visits"),
)
cols = [r[0] for r in cur.fetchall()]
print("=== ALL COLUMNS ===")
for i in range(0, len(cols), 3):
    print("  " + "  |  ".join(f"{c:<34}" for c in cols[i:i + 3]))

print("\n=== SAMPLE: goalsid (non-empty) ===")
cur.execute(
    f"SELECT ym_s_goalsid FROM {VISITS} WHERE load_date=%s "
    "AND ym_s_goalsid <> '[]' AND ym_s_goalsid <> '' LIMIT 3",
    (DAY,),
)
for r in cur.fetchall():
    print("  ", r[0][:200])

print("\n=== deviceCategory distinct ===")
cur.execute(
    f"SELECT ym_s_devicecategory, count(*) FROM {VISITS} "
    "WHERE load_date=%s GROUP BY 1 ORDER BY 2 DESC",
    (DAY,),
)
print("  ", cur.fetchall())

print("\n=== isNewUser distinct ===")
cur.execute(
    f"SELECT ym_s_isnewuser, count(*) FROM {VISITS} "
    "WHERE load_date=%s GROUP BY 1 ORDER BY 2 DESC",
    (DAY,),
)
print("  ", cur.fetchall())

print("\n=== lastTrafficSource distinct ===")
cur.execute(
    f"SELECT ym_s_lasttrafficsource, count(*) FROM {VISITS} "
    "WHERE load_date=%s GROUP BY 1 ORDER BY 2 DESC LIMIT 12",
    (DAY,),
)
print("  ", cur.fetchall())

print("\n=== datetime sample ===")
cur.execute(f"SELECT ym_s_datetime FROM {VISITS} WHERE load_date=%s LIMIT 3", (DAY,))
print("  ", [r[0] for r in cur.fetchall()])

print("\n=== browser-like columns, filled counts ===")
for c in cols:
    if any(k in c for k in ("browser", "operatingsystem", "os")):
        cur.execute(
            f"SELECT count(nullif(\"{c}\",'')) FROM {VISITS} WHERE load_date=%s",
            (DAY,),
        )
        print(f"  {c:<34} filled={cur.fetchone()[0]}")

print("\n=== dbreg check: public.clients ===")
cur.execute(
    "SELECT min(entity_created::date), max(entity_created::date), count(*) "
    "FROM public.clients WHERE entity_created >= '2026-05-01'"
)
print("  ", cur.fetchone())
conn.close()