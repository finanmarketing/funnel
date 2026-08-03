import os
import sys

import psycopg2
from dotenv import load_dotenv

load_dotenv()


def connect():
    return psycopg2.connect(
        host=os.environ["PG_HOST"],
        port=int(os.environ["PG_PORT"]),
        dbname=os.environ["PG_DB"],
        user=os.environ["PG_USER"],
        password=os.environ["PG_PASSWORD"],
        connect_timeout=15,
    )


def main(path):
    with open(path, "r", encoding="utf-8") as f:
        sql = f.read()
    if not sql.strip():
        print(f"[FAIL] file is empty: {path}")
        return 1
    print(f"[INFO] applying {path} ({len(sql)} bytes)")
    conn = connect()
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            schema = os.environ["PG_SCHEMA"]
            cur.execute(
                "SELECT current_user, current_database(), "
                "has_schema_privilege(current_user,%s,'CREATE')",
                (schema,),
            )
            user, db, can_create = cur.fetchone()
            print(f"[INFO] user={user} db={db} schema={schema} can_create={can_create}")
            if not can_create:
                print(f"[FAIL] no CREATE privilege on schema {schema}")
                conn.rollback()
                return 1
            cur.execute(sql)
            if cur.description:
                rows = cur.fetchall()
                print(f"[INFO] returned {len(rows)} row(s):")
                for r in rows:
                    print("   ", r)
        conn.commit()
        print("[ OK ] committed")
    except Exception as e:
        conn.rollback()
        print(f"[FAIL] rolled back: {type(e).__name__}: {e}")
        return 1
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))