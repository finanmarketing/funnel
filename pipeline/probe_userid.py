import os
import sys
from datetime import datetime

import psycopg2
from dotenv import load_dotenv

load_dotenv()

SCHEMA = os.environ["PG_SCHEMA"]
PREFIX = os.environ["TABLE_PREFIX"]
VISITS = f"{SCHEMA}.{PREFIX}visits"

D1, D2 = "2026-07-01", "2026-07-31"

EXTRACT = f"""
WITH v AS (
  SELECT ym_s_clientid AS cid, ym_s_visitid AS vid, load_date AS d,
         string_to_array(regexp_replace(coalesce(ym_s_parsedparamskey1,''),
           '[\\[\\]'']', '', 'g'), ',') AS k1,
         string_to_array(regexp_replace(coalesce(ym_s_parsedparamskey2,''),
           '[\\[\\]'']', '', 'g'), ',') AS k2,
         string_to_array(regexp_replace(coalesce(ym_s_parsedparamskey3,''),
           '[\\[\\]'']', '', 'g'), ',') AS k3
  FROM {VISITS} WHERE load_date BETWEEN %(d1)s AND %(d2)s
),
uid AS (
  SELECT cid, vid, d,
         CASE WHEN trim(k1[i]) = 'UserID' THEN trim(k2[i])
              WHEN trim(k1[i]) = 'params' AND trim(k2[i]) = 'UserID'
                   THEN trim(k3[i])
         END AS uid
  FROM v, generate_subscripts(k1, 1) AS i
),
clean AS (
  SELECT cid, vid, d, uid FROM uid
  WHERE uid IS NOT NULL AND uid <> '' AND uid ~ '^[0-9]+$'
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
    p = {"d1": D1, "d2": D2}
    conn = connect()
    cur = conn.cursor()

    print(f"\n[1] Извлечение UserID за {D1}..{D2}")
    cur.execute(
        EXTRACT + "SELECT count(*), count(distinct uid), count(distinct cid), "
        "count(distinct vid) FROM clean", p,
    )
    rows, uids, cids, vids = cur.fetchone()
    print(f"  извлечено значений: {rows}")
    print(f"  уникальных UserID:  {uids}")
    print(f"  уникальных clientID с UserID: {cids}")
    print(f"  визитов с UserID:   {vids}")

    print("\n[2] Совпадение с clients.client_number")
    cur.execute(
        EXTRACT + "SELECT count(*) FROM (SELECT DISTINCT uid FROM clean) u "
        "JOIN public.clients c ON c.client_number::text = u.uid", p,
    )
    matched = cur.fetchone()[0]
    print(f"  найдено в clients: {matched} из {uids} "
          f"({100.0 * matched / uids if uids else 0:.1f}%)")

    print("\n[3] Примеры несовпадений")
    cur.execute(
        EXTRACT + "SELECT u.uid FROM (SELECT DISTINCT uid FROM clean) u "
        "LEFT JOIN public.clients c ON c.client_number::text = u.uid "
        "WHERE c.client_number IS NULL LIMIT 10", p,
    )
    miss = [r[0] for r in cur.fetchall()]
    print(f"  {miss if miss else 'нет'}")

    print("\n[4] Один ли UserID на clientID")
    cur.execute(
        EXTRACT + "SELECT n, count(*) FROM (SELECT cid, count(distinct uid) AS n "
        "FROM clean GROUP BY 1) t GROUP BY 1 ORDER BY 1 LIMIT 6", p,
    )
    for n, c in cur.fetchall():
        print(f"  {n} UserID на браузер: {c} браузеров")

    print("\n[5] Один ли clientID на UserID (клиент с нескольких устройств)")
    cur.execute(
        EXTRACT + "SELECT n, count(*) FROM (SELECT uid, count(distinct cid) AS n "
        "FROM clean GROUP BY 1) t GROUP BY 1 ORDER BY 1 LIMIT 6", p,
    )
    for n, c in cur.fetchall():
        print(f"  {n} браузеров на клиента: {c} клиентов")

    print("\n[6] Дата регистрации: до или внутри периода")
    cur.execute(
        EXTRACT + "SELECT CASE WHEN c.entity_created::date < %(d1)s THEN 'до периода' "
        "WHEN c.entity_created::date BETWEEN %(d1)s AND %(d2)s THEN 'в периоде' "
        "ELSE 'после' END AS grp, count(*) "
        "FROM (SELECT DISTINCT uid FROM clean) u "
        "JOIN public.clients c ON c.client_number::text = u.uid "
        "GROUP BY 1 ORDER BY 2 DESC", p,
    )
    for g, c in cur.fetchall():
        print(f"  {g}: {c}")

    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())