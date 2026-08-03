import os

import psycopg2
from dotenv import load_dotenv

load_dotenv()

conn = psycopg2.connect(
    host=os.environ["PG_HOST"],
    port=int(os.environ["PG_PORT"]),
    dbname=os.environ["PG_DB"],
    user=os.environ["PG_USER"],
    password=os.environ["PG_PASSWORD"],
)
cur = conn.cursor()

cur.execute("SELECT current_user, session_user, version()")
print("USER:", cur.fetchone()[:2])

cur.execute("SELECT r.rolname FROM pg_roles r JOIN pg_auth_members m ON m.roleid=r.oid JOIN pg_roles u ON u.oid=m.member WHERE u.rolname=current_user")
print("ROLES:", [r[0] for r in cur.fetchall()])

cur.execute("SELECT nspname, pg_get_userbyid(nspowner) AS owner, has_schema_privilege(current_user,nspname,'CREATE') AS can_create, has_schema_privilege(current_user,nspname,'USAGE') AS can_use FROM pg_namespace WHERE nspname NOT LIKE 'pg_%' AND nspname <> 'information_schema' ORDER BY 3 DESC, 1")
print("\nSCHEMAS (can_create first):")
for n, o, c, u in cur.fetchall():
    print(f"  {'WRITE' if c else '     '} {n:<30} owner={o:<15} usage={u}")

cur.execute("SELECT has_database_privilege(current_database(),'CREATE'), has_database_privilege(current_database(),'TEMP')")
db_create, db_temp = cur.fetchone()
print(f"\nDB CREATE={db_create}  TEMP={db_temp}")

try:
    cur.execute("CREATE TEMP TABLE _probe(x int)")
    cur.execute("DROP TABLE _probe")
    print("TEMP TABLE: ok")
except Exception as e:
    conn.rollback()
    print("TEMP TABLE: fail —", type(e).__name__)

conn.rollback()
conn.close()