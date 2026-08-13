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

# Периоды и горизонты наблюдения: считаем период так, как он выглядел
# на каждую из дат — карта связи строится только по данным до этой даты.
CASES = [
    ("2026-05", "2026-05-01", "2026-05-31",
     ["2026-05-31", "2026-06-07", "2026-06-30", "2026-07-31", "2026-08-12"]),
    ("2026-06", "2026-06-01", "2026-06-30",
     ["2026-06-30", "2026-07-07", "2026-07-31", "2026-08-12"]),
    ("2026-07", "2026-07-01", "2026-07-31",
     ["2026-07-31", "2026-08-07", "2026-08-12"]),
]

WATCH = ["MAIN_PAGE_LOADED", "REGISTRATION_PAGE", "REGISTRATION_PAGE_OK",
         "MOBILE_VERIFICATION_PAGE", "CONFIRM_PAGE_OK"]

SQL = f"""
WITH raw AS (
  SELECT ym_s_clientid AS cid, load_date AS d,
         string_to_array(regexp_replace(coalesce(ym_s_parsedparamskey1,''),
           '[\\[\\]'']', '', 'g'), ',') AS k1,
         string_to_array(regexp_replace(coalesce(ym_s_parsedparamskey2,''),
           '[\\[\\]'']', '', 'g'), ',') AS k2,
         string_to_array(regexp_replace(coalesce(ym_s_parsedparamskey3,''),
           '[\\[\\]'']', '', 'g'), ',') AS k3
  FROM {VISITS} WHERE load_date <= %(horizon)s
),
w AS (
  SELECT raw.cid,
         (SELECT min(CASE
            WHEN trim(raw.k1[i]) = 'UserID' AND trim(raw.k2[i]) ~ '^[0-9]+$'
                 THEN trim(raw.k2[i])
            WHEN trim(raw.k1[i]) = 'params' AND trim(raw.k2[i]) = 'UserID'
                 AND trim(raw.k3[i]) ~ '^[0-9]+$' THEN trim(raw.k3[i])
          END)
          FROM generate_subscripts(raw.k1, 1) AS i) AS uid
  FROM raw
),
map AS (
  SELECT cid, min(uid) AS uid FROM w GROUP BY 1
),
v AS (
  SELECT coalesce(m.uid, 'br:' || t.ym_s_clientid) AS pkey,
         nullif(trim(both '[]' from coalesce(t.ym_s_goalsid,'')),'') AS gs
  FROM {VISITS} t LEFT JOIN map m ON m.cid = t.ym_s_clientid
  WHERE t.load_date BETWEEN %(d1)s AND %(d2)s
),
e AS (
  SELECT v.pkey, g.gid FROM v
  CROSS JOIN LATERAL unnest(string_to_array(v.gs, ',')) AS g(gid)
  WHERE v.gs IS NOT NULL
)
SELECT gid, count(distinct pkey) FROM e
WHERE gid = ANY(%(ids)s) GROUP BY 1
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
    gid_of = dict(FUNNEL)
    ids = [gid_of[n] for n in WATCH]
    conn = connect()
    cur = conn.cursor()

    for label, d1, d2, horizons in CASES:
        print(f"\n{'=' * 78}")
        print(f"ПЕРИОД {label} ({d1}..{d2})")
        print("=" * 78)
        series = {}
        for h in horizons:
            log(f"  горизонт {h}...")
            cur.execute(SQL, {"d1": d1, "d2": d2, "horizon": h, "ids": ids})
            series[h] = dict(cur.fetchall())

        head = f"{'шаг':<30}" + "".join(f"{h[5:]:>12}" for h in horizons)
        print("\n" + head)
        print("-" * len(head))
        for nm in WATCH:
            g = gid_of[nm]
            row = f"{nm:<30}"
            for h in horizons:
                row += f"{series[h].get(g, 0):>12}"
            print(row)

        print(f"\n{'шаг':<30}" + "".join(f"{h[5:]:>12}" for h in horizons[1:]))
        print("-" * len(head))
        for nm in WATCH:
            g = gid_of[nm]
            base = series[horizons[0]].get(g, 0)
            row = f"{nm:<30}"
            for h in horizons[1:]:
                cur_v = series[h].get(g, 0)
                d = 100.0 * (cur_v - base) / base if base else 0
                row += f"{d:>11.2f}%"
            print(row)

    cur.close()
    conn.close()
    print("\nЧитать: первая таблица — абсолютные числа на каждый горизонт.")
    print("Вторая — отклонение от значения на момент закрытия периода.")
    return 0


if __name__ == "__main__":
    sys.exit(main())