import os
import sys
from datetime import datetime

import psycopg2
from dotenv import load_dotenv

from funnel_goals import FUNNEL, LOGIN

load_dotenv()

SCHEMA = os.environ["PG_SCHEMA"]
PREFIX = os.environ["TABLE_PREFIX"]
VISITS = f"{SCHEMA}.{PREFIX}visits"
PMAP = f"{SCHEMA}.{PREFIX}person_map"

D1, D2 = "2026-08-06", "2026-08-14"

LOGIN_PAGE = dict(LOGIN)["LOGIN_PAGE"]
LOGIN_OK = dict(LOGIN)["LOGIN_PAGE_OK"]
MAIN = dict(FUNNEL)["MAIN_PAGE_LOADED"]

BASE = f"""
WITH v AS (
  SELECT t.ym_s_visitid AS vid,
         coalesce(pm.pkey, 'br:' || t.ym_s_clientid) AS pkey,
         pm.uid IS NOT NULL AS known,
         t.load_date AS d,
         substr(t.ym_s_datetime, 12, 2)::int AS hh,
         nullif(trim(both '[]' from coalesce(t.ym_s_goalsid,'')),'') AS gs
  FROM {VISITS} t
  LEFT JOIN {PMAP} pm ON pm.cid = t.ym_s_clientid
  WHERE t.load_date BETWEEN %(d1)s AND %(d2)s
),
e AS (
  SELECT v.vid, v.pkey, v.d, v.hh, g.gid FROM v
  CROSS JOIN LATERAL unnest(string_to_array(v.gs, ',')) AS g(gid)
  WHERE v.gs IS NOT NULL
)
"""


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def connect():
    return psycopg2.connect(
        host=os.environ["PG_HOST"], port=int(os.environ["PG_PORT"]),
        dbname=os.environ["PG_DB"], user=os.environ["PG_USER"],
        password=os.environ["PG_PASSWORD"], connect_timeout=60,
    )


def main():
    p = {"d1": D1, "d2": D2}
    conn = connect()
    cur = conn.cursor()

    print(f"\n[1] Успешность авторизации по дням ({D1}..{D2})")
    cur.execute(
        BASE + "SELECT d::text, "
        "count(distinct pkey) FILTER (WHERE gid = %(lp)s) AS pg, "
        "count(distinct pkey) FILTER (WHERE gid = %(lo)s) AS okp, "
        "count(distinct vid) FILTER (WHERE gid = %(lp)s) AS pv, "
        "count(distinct vid) FILTER (WHERE gid = %(lo)s) AS okv "
        "FROM e GROUP BY 1 ORDER BY 1",
        {**p, "lp": LOGIN_PAGE, "lo": LOGIN_OK},
    )
    print(f"  {'дата':<12} {'люди LP':>9} {'люди OK':>9} {'%':>7} "
          f"{'виз LP':>9} {'виз OK':>9} {'%':>7}")
    for d, pg, okp, pv, okv in cur.fetchall():
        rp = 100.0 * okp / pg if pg else 0
        rv = 100.0 * okv / pv if pv else 0
        print(f"  {d:<12} {pg:>9} {okp:>9} {rp:>6.1f}% "
              f"{pv:>9} {okv:>9} {rv:>6.1f}%")

    print("\n[2] Успешность авторизации по часам (среднее за период)")
    cur.execute(
        BASE + "SELECT hh, "
        "count(distinct vid) FILTER (WHERE gid = %(lp)s), "
        "count(distinct vid) FILTER (WHERE gid = %(lo)s) "
        "FROM e GROUP BY 1 ORDER BY 1",
        {**p, "lp": LOGIN_PAGE, "lo": LOGIN_OK},
    )
    print(f"  {'час':>4} {'виз LP':>9} {'виз OK':>9} {'%':>7}")
    for hh, pv, okv in cur.fetchall():
        r = 100.0 * okv / pv if pv else 0
        print(f"  {hh:>4} {pv:>9} {okv:>9} {r:>6.1f}%")

    print("\n[3] Доля визитов известных клиентов (есть UserID)")
    cur.execute(
        f"SELECT t.load_date::text, count(*), "
        "count(*) FILTER (WHERE pm.uid IS NOT NULL) "
        f"FROM {VISITS} t LEFT JOIN {PMAP} pm ON pm.cid = t.ym_s_clientid "
        "WHERE t.load_date BETWEEN %(d1)s AND %(d2)s "
        "GROUP BY 1 ORDER BY 1", p,
    )
    print(f"  {'дата':<12} {'визитов':>10} {'с UserID':>10} {'%':>7}")
    for d, tot, known in cur.fetchall():
        print(f"  {d:<12} {tot:>10} {known:>10} "
              f"{100.0 * known / tot if tot else 0:>6.1f}%")

    print("\n[4] Другие кандидаты на «% visit exist user»")
    cur.execute(
        BASE + "SELECT d::text, count(distinct vid) AS allv, "
        "count(distinct vid) FILTER (WHERE gid = %(lo)s) AS okv, "
        "count(distinct vid) FILTER (WHERE gid = %(mn)s) AS mainv "
        "FROM e GROUP BY 1 ORDER BY 1",
        {**p, "lo": LOGIN_OK, "mn": MAIN},
    )
    print(f"  {'дата':<12} {'виз с целями':>13} {'вход OK':>9} {'%':>7} "
          f"{'главная':>9}")
    for d, allv, okv, mainv in cur.fetchall():
        print(f"  {d:<12} {allv:>13} {okv:>9} "
              f"{100.0 * okv / allv if allv else 0:>6.1f}% {mainv:>9}")

    print("\n[5] Повторные авторизации: сколько раз входит один человек")
    cur.execute(
        BASE + "SELECT n, count(*) FROM (SELECT pkey, count(distinct vid) AS n "
        "FROM e WHERE gid = %(lo)s GROUP BY 1) t GROUP BY 1 ORDER BY 1 LIMIT 10",
        {**p, "lo": LOGIN_OK},
    )
    for n, c in cur.fetchall():
        print(f"  {n} вход(ов): {c} человек")

    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())