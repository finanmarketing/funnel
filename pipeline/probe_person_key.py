import os
import sys
from datetime import datetime

import psycopg2
from dotenv import load_dotenv

from funnel_goals import FUNNEL

load_dotenv()

SCHEMA = os.environ["PG_SCHEMA"]
PREFIX = os.environ["TABLE_PREFIX"]
VISITS = f"{SCHEMA}.{PREFIX}visits"

MONTHS = [("2026-05-01", "2026-05-31"), ("2026-06-01", "2026-06-30"),
          ("2026-07-01", "2026-07-31")]

BASE = f"""
WITH v AS (
  SELECT ym_s_clientid AS cid, load_date AS d,
         nullif(trim(both '[]' from coalesce(ym_s_goalsid,'')),'') AS gs,
         string_to_array(regexp_replace(coalesce(ym_s_parsedparamskey1,''),
           '[\\[\\]'']', '', 'g'), ',') AS k1,
         string_to_array(regexp_replace(coalesce(ym_s_parsedparamskey2,''),
           '[\\[\\]'']', '', 'g'), ',') AS k2,
         string_to_array(regexp_replace(coalesce(ym_s_parsedparamskey3,''),
           '[\\[\\]'']', '', 'g'), ',') AS k3
  FROM {VISITS} WHERE load_date BETWEEN %(d1)s AND %(d2)s
),
w AS (
  SELECT v.cid, v.d, v.gs,
         (SELECT min(CASE
            WHEN trim(v.k1[i]) = 'UserID' AND trim(v.k2[i]) ~ '^[0-9]+$'
                 THEN trim(v.k2[i])
            WHEN trim(v.k1[i]) = 'params' AND trim(v.k2[i]) = 'UserID'
                 AND trim(v.k3[i]) ~ '^[0-9]+$' THEN trim(v.k3[i])
          END)
          FROM generate_subscripts(v.k1, 1) AS i) AS uid
  FROM v
),
map AS (
  SELECT cid, max(uid) AS uid FROM w WHERE uid IS NOT NULL GROUP BY 1
),
e AS (
  SELECT w.cid, coalesce(m.uid, 'br:' || w.cid) AS pkey, g.gid
  FROM w LEFT JOIN map m ON m.cid = w.cid
  CROSS JOIN LATERAL unnest(string_to_array(w.gs, ',')) AS g(gid)
  WHERE w.gs IS NOT NULL
)
"""


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def connect():
    return psycopg2.connect(
        host=os.environ["PG_HOST"], port=int(os.environ["PG_PORT"]),
        dbname=os.environ["PG_DB"], user=os.environ["PG_USER"],
        password=os.environ["PG_PASSWORD"], connect_timeout=30,
    )


def main():
    conn = connect()
    cur = conn.cursor()

    for d1, d2 in MONTHS:
        log(f"считаю {d1}..{d2}")
        p = {"d1": d1, "d2": d2}

        cur.execute(
            BASE + "SELECT count(distinct cid), count(distinct pkey), "
            "count(distinct pkey) FILTER (WHERE pkey LIKE 'br:%%') "
            "FROM e", p,
        )
        tc, tp, anon = cur.fetchone()
        print(f"\n=== {d1[:7]} ===")
        print(f"  всего браузеров с целями: {tc}")
        print(f"  всего людей:              {tp}  (из них анонимных {anon})")

        cur.execute(
            BASE + "SELECT gid, count(distinct cid), count(distinct pkey) "
            "FROM e WHERE gid = ANY(%(ids)s) GROUP BY 1",
            {**p, "ids": [g for _, g in FUNNEL]},
        )
        res = {g: (a, b) for g, a, b in cur.fetchall()}

        print(f"\n{'шаг':<30} {'браузеры':>10} {'люди':>10} {'разница':>9}")
        print("-" * 62)
        for nm, g in FUNNEL:
            br, pp = res.get(g, (0, 0))
            diff = 100.0 * (pp - br) / br if br else 0
            print(f"{nm:<30} {br:>10} {pp:>10} {diff:>8.1f}%")

        cur.execute(
            "SELECT count(*) FROM public.clients "
            "WHERE entity_created::date BETWEEN %s AND %s", (d1, d2),
        )
        db = cur.fetchone()[0]
        reg = dict(FUNNEL)["REGISTRATION_PAGE_OK"]
        br, pp = res.get(reg, (0, 0))
        print(f"\n  База: {db}")
        print(f"  Разрыв по браузерам: {100.0 * (db - br) / db:.1f}%")
        print(f"  Разрыв по людям:     {100.0 * (db - pp) / db:.1f}%")

    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())