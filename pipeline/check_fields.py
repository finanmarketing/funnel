import os

import psycopg2
from dotenv import load_dotenv

load_dotenv()

SCHEMA = os.environ["PG_SCHEMA"]
PREFIX = os.environ["TABLE_PREFIX"]
VISITS = f"{SCHEMA}.{PREFIX}visits"

REF_DAY = "2026-06-15"
NEW_DAY = "2026-08-02"

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
cols = [r[0] for r in cur.fetchall() if r[0] not in ("load_date", "loaded_at")]
print(f"total data columns: {len(cols)}")

sel_ref = ", ".join(
    f"count(nullif(\"{c}\",''))" for c in cols
)
cur.execute(f"SELECT count(*), {sel_ref} FROM {VISITS} WHERE load_date=%s", (REF_DAY,))
ref = cur.fetchone()
cur.execute(f"SELECT count(*), {sel_ref} FROM {VISITS} WHERE load_date=%s", (NEW_DAY,))
new = cur.fetchone()

print(f"rows {REF_DAY}={ref[0]}  {NEW_DAY}={new[0]}")
lost = []
for i, c in enumerate(cols, start=1):
    if ref[i] > 0 and new[i] == 0:
        lost.append(c)
print(f"\nEMPTY on {NEW_DAY} but filled on {REF_DAY}: {len(lost)}")
for c in lost:
    print("  -", c)

CRITICAL = [
    "ym_s_clientid",
    "ym_s_date",
    "ym_s_datetime",
    "ym_s_goalsid",
    "ym_s_isnewuser",
    "ym_s_devicecategory",
    "ym_s_operatingsystem",
    "ym_s_lasttrafficsource",
]
print("\nCRITICAL FIELDS:")
for c in CRITICAL:
    if c not in cols:
        print(f"  [MISSING COLUMN] {c}")
        continue
    i = cols.index(c) + 1
    print(f"  {c:<28} {REF_DAY}={ref[i]:<7} {NEW_DAY}={new[i]}")

print("\nPARAMS COLUMNS (UserID bridge):")
for c in cols:
    if "param" in c:
        i = cols.index(c) + 1
        print(f"  {c:<32} {REF_DAY}={ref[i]:<7} {NEW_DAY}={new[i]}")
conn.close()