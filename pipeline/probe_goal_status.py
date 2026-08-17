import os
import sys
from datetime import date, datetime, timedelta

import psycopg2
from dotenv import load_dotenv

load_dotenv()

SCHEMA = os.environ["PG_SCHEMA"]
PREFIX = os.environ["TABLE_PREFIX"]
VISITS = f"{SCHEMA}.{PREFIX}visits"
GOALS = f"{SCHEMA}.{PREFIX}goals_dict"

TARGETS = [
    ("308399526", "LK : APPROVED"),
    ("308400589", "LK : LOAN active"),
    ("308399520", "LK : PENDING"),
    ("308399341", "LK : REJECTED"),
    ("326553691", "Первый займ"),
    ("326553796", "Повторный займ"),
    ("334592789", "LK : STEP 7"),
]


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def connect():
    return psycopg2.connect(
        host=os.environ["PG_HOST"], port=int(os.environ["PG_PORT"]),
        dbname=os.environ["PG_DB"], user=os.environ["PG_USER"],
        password=os.environ["PG_PASSWORD"], connect_timeout=30,
    )


def main():
    d2 = (date.today() - timedelta(days=1)).isoformat()
    d1 = (date.today() - timedelta(days=30)).isoformat()
    p = {"d1": d1, "d2": d2}

    conn = connect()
    cur = conn.cursor()

    print("\n[1] Статус и тип целей по займам")
    cur.execute(
        f"SELECT goal_id::text, name, goal_type, status, raw::text "
        f"FROM {GOALS} WHERE goal_id::text = ANY(%s)",
        ([g for g, _ in TARGETS],),
    )
    for gid, name, gtype, status, raw in cur.fetchall():
        print(f"\n  {gid}  {name}")
        print(f"    тип: {gtype}   статус: {status}")
        print(f"    условие: {raw[:400]}")

    print("\n[2] Сводка статусов по всем 215 целям")
    cur.execute(
        f"SELECT status, goal_type, count(*) FROM {GOALS} "
        "GROUP BY 1,2 ORDER BY 3 DESC"
    )
    for status, gtype, n in cur.fetchall():
        print(f"  статус={status!r:<12} тип={gtype!r:<14} {n:>4}")

    print("\n[3] Работающие vs неработающие по статусу")
    cur.execute(
        "SELECT DISTINCT g.gid FROM "
        f"(SELECT nullif(trim(both '[]' from coalesce(ym_s_goalsid,'')),'') AS gs "
        f"FROM {VISITS} WHERE load_date BETWEEN %(d1)s AND %(d2)s) v "
        "CROSS JOIN LATERAL unnest(string_to_array(v.gs, ',')) AS g(gid) "
        "WHERE v.gs IS NOT NULL", p,
    )
    seen = {r[0].strip() for r in cur.fetchall()}
    cur.execute(f"SELECT goal_id::text, status FROM {GOALS}")
    rows = cur.fetchall()
    agg = {}
    for gid, status in rows:
        key = (status, gid in seen)
        agg[key] = agg.get(key, 0) + 1
    for (status, works), n in sorted(agg.items(), key=lambda x: -x[1]):
        print(f"  статус={status!r:<12} "
              f"{'срабатывает' if works else 'молчит':<12} {n:>4}")

    print("\n[4] Маршруты с /app/ в данных")
    cur.execute(
        "SELECT substring(ym_s_starturl from "
        "'^[a-zA-Z]+://[^/]+(/[^?#]*)') AS pth, count(*) "
        f"FROM {VISITS} WHERE load_date BETWEEN %(d1)s AND %(d2)s "
        "AND ym_s_starturl LIKE '%%/app/%%' "
        "GROUP BY 1 ORDER BY 2 DESC LIMIT 15", p,
    )
    for pth, n in cur.fetchall():
        print(f"  {str(pth)[:60]:<60} {n:>8}")

    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())