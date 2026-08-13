import os
import sys
from datetime import date, datetime, timedelta

import psycopg2
from dotenv import load_dotenv

load_dotenv()

SCHEMA = os.environ["PG_SCHEMA"]
PREFIX = os.environ["TABLE_PREFIX"]
VISITS = f"{SCHEMA}.{PREFIX}visits"

D1, D2 = "2026-07-01", "2026-07-31"


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def connect():
    return psycopg2.connect(
        host=os.environ["PG_HOST"], port=int(os.environ["PG_PORT"]),
        dbname=os.environ["PG_DB"], user=os.environ["PG_USER"],
        password=os.environ["PG_PASSWORD"], connect_timeout=30,
    )


def main():
    p = {"d1": D1, "d2": D2}
    conn = connect()
    cur = conn.cursor()

    print("\n[1] Сопоставимость clientID и clients.client_number")
    cur.execute(
        f"SELECT count(distinct ym_s_clientid) FROM {VISITS} "
        "WHERE load_date BETWEEN %(d1)s AND %(d2)s",
        p,
    )
    total_cid = cur.fetchone()[0]
    print(f"  уникальных clientID за июль: {total_cid}")

    cur.execute(
        f"SELECT count(*) FROM (SELECT DISTINCT ym_s_clientid AS cid "
        f"FROM {VISITS} WHERE load_date BETWEEN %(d1)s AND %(d2)s) v "
        "JOIN public.clients c ON c.client_number::text = v.cid",
        p,
    )
    matched = cur.fetchone()[0]
    print(f"  нашлось в clients по client_number: {matched} "
          f"({100.0 * matched / total_cid:.1f}%)")

    print("\n[2] Диапазоны значений (сопоставимы ли вообще)")
    cur.execute(
        f"SELECT min(ym_s_clientid::numeric), max(ym_s_clientid::numeric) "
        f"FROM {VISITS} WHERE load_date BETWEEN %(d1)s AND %(d2)s "
        "AND ym_s_clientid ~ '^[0-9]+$'",
        p,
    )
    print(f"  clientID:      min={cur.fetchone()}")
    cur.execute("SELECT min(client_number), max(client_number) FROM public.clients")
    print(f"  client_number: min/max={cur.fetchone()}")

    print("\n[3] Есть ли в выгрузке параметр UserID")
    cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema=%s AND table_name=%s "
        "AND (column_name LIKE '%%param%%' OR column_name LIKE '%%userid%%') "
        "ORDER BY ordinal_position",
        (SCHEMA, f"{PREFIX}visits"),
    )
    cols = [r[0] for r in cur.fetchall()]
    print(f"  колонок с параметрами: {len(cols)}")
    for c in cols[:12]:
        print(f"    {c}")

    for c in cols[:4]:
        cur.execute(
            f"SELECT \"{c}\", count(*) FROM {VISITS} "
            "WHERE load_date BETWEEN %(d1)s AND %(d2)s "
            f"AND \"{c}\" IS NOT NULL AND \"{c}\" <> '' "
            "GROUP BY 1 ORDER BY 2 DESC LIMIT 5",
            p,
        )
        rows = cur.fetchall()
        if rows:
            print(f"\n  топ значений {c}:")
            for v, n in rows:
                print(f"    {str(v)[:60]:<60} {n}")

    print("\n[4] Сегмент по базе: сколько регистраций попадает в июль")
    cur.execute(
        "SELECT count(*) FROM public.clients "
        "WHERE entity_created::date BETWEEN %(d1)s AND %(d2)s",
        p,
    )
    print(f"  зарегистрировано в июле: {cur.fetchone()[0]}")

    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())