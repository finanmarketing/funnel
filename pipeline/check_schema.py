import os

import psycopg2
from dotenv import load_dotenv

load_dotenv()

SCHEMA = os.environ["PG_SCHEMA"]
PREFIX = os.environ["TABLE_PREFIX"]

conn = psycopg2.connect(
    host=os.environ["PG_HOST"],
    port=int(os.environ["PG_PORT"]),
    dbname=os.environ["PG_DB"],
    user=os.environ["PG_USER"],
    password=os.environ["PG_PASSWORD"],
)
cur = conn.cursor()
cur.execute(
    "SELECT t.table_name, (SELECT count(*) FROM information_schema.columns c "
    "WHERE c.table_schema=t.table_schema AND c.table_name=t.table_name) "
    "FROM information_schema.tables t "
    "WHERE t.table_schema=%s AND t.table_name LIKE %s ORDER BY 1",
    (SCHEMA, PREFIX + "%"),
)
rows = cur.fetchall()
for name, cols in rows:
    print(f"[ OK ] {SCHEMA}.{name} | columns: {cols}")
if not rows:
    print("[FAIL] no tables found")
runs = f"{SCHEMA}.{PREFIX}pipeline_runs"
cur.execute(
    f"INSERT INTO {runs}(stage,status,rows_loaded) "
    "VALUES ('smoke_test','ok',0) RETURNING run_id"
)
print(f"[ OK ] insert run_id: {cur.fetchone()[0]}")
cur.execute(f"DELETE FROM {runs} WHERE stage='smoke_test'")
print(f"[ OK ] delete rows: {cur.rowcount}")
conn.commit()
conn.close()