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
PMAP = f"{SCHEMA}.{PREFIX}person_map"

MONTHS = [("2026-05-01", "2026-05-31"), ("2026-06-01", "2026-06-30"),
          ("2026-07-01", "2026-07-31")]

MAIN = dict(FUNNEL)["MAIN_PAGE_LOADED"]
CONF = dict(FUNNEL)["CONFIRM_PAGE_OK"]
REG = dict(FUNNEL)["REGISTRATION_PAGE_OK"]


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def connect():
    return psycopg2.connect(
        host=os.environ["PG_HOST"], port=int(os.environ["PG_PORT"]),
        dbname=os.environ["PG_DB"], user=os.environ["PG_USER"],
        password=os.environ["PG_PASSWORD"], connect_timeout=60,
    )


def main():
    conn = connect()
    cur = conn.cursor()

    print("\n[1] Три версии знаменателя трафика")
    print(f"  {'месяц':<10} {'все визиты':>12} {'видели главную':>15} "
          f"{'доля':>7}")
    print("  " + "-" * 50)
    data = {}
    for d1, d2 in MONTHS:
        p = {"d1": d1, "d2": d2}
        cur.execute(
            f"SELECT count(distinct coalesce(pm.pkey, 'br:' || t.ym_s_clientid)) "
            f"FROM {VISITS} t LEFT JOIN {PMAP} pm ON pm.cid = t.ym_s_clientid "
            "WHERE t.load_date BETWEEN %(d1)s AND %(d2)s", p,
        )
        all_people = cur.fetchone()[0]
        cur.execute(
            f"""
            WITH v AS (
              SELECT coalesce(pm.pkey, 'br:' || t.ym_s_clientid) AS pkey,
                     nullif(trim(both '[]' from coalesce(t.ym_s_goalsid,'')),'')
                       AS gs
              FROM {VISITS} t
              LEFT JOIN {PMAP} pm ON pm.cid = t.ym_s_clientid
              WHERE t.load_date BETWEEN %(d1)s AND %(d2)s
            )
            SELECT count(distinct v.pkey) FROM v
            CROSS JOIN LATERAL unnest(string_to_array(v.gs, ',')) AS g(gid)
            WHERE v.gs IS NOT NULL AND g.gid = %(g)s
            """,
            {**p, "g": MAIN},
        )
        main_people = cur.fetchone()[0]
        data[d1[:7]] = {"all": all_people, "main": main_people}
        print(f"  {d1[:7]:<10} {all_people:>12} {main_people:>15} "
              f"{100.0 * main_people / all_people:>6.1f}%")

    print("\n[2] CR при разных знаменателях")
    print(f"  {'месяц':<10} {'заявка':>9} {'CR от всех':>11} "
          f"{'CR от главной':>14}")
    print("  " + "-" * 48)
    for d1, d2 in MONTHS:
        p = {"d1": d1, "d2": d2}
        cur.execute(
            f"""
            WITH v AS (
              SELECT coalesce(pm.pkey, 'br:' || t.ym_s_clientid) AS pkey,
                     nullif(trim(both '[]' from coalesce(t.ym_s_goalsid,'')),'')
                       AS gs
              FROM {VISITS} t
              LEFT JOIN {PMAP} pm ON pm.cid = t.ym_s_clientid
              WHERE t.load_date BETWEEN %(d1)s AND %(d2)s
            )
            SELECT count(distinct v.pkey) FROM v
            CROSS JOIN LATERAL unnest(string_to_array(v.gs, ',')) AS g(gid)
            WHERE v.gs IS NOT NULL AND g.gid = %(g)s
            """,
            {**p, "g": CONF},
        )
        conf = cur.fetchone()[0]
        mo = d1[:7]
        data[mo]["conf"] = conf
        print(f"  {mo:<10} {conf:>9} "
              f"{100.0 * conf / data[mo]['all']:>10.2f}% "
              f"{100.0 * conf / data[mo]['main']:>13.2f}%")

    print("\n[3] Люди без единой цели (техническая проверка)")
    for d1, d2 in MONTHS[-1:]:
        p = {"d1": d1, "d2": d2}
        cur.execute(
            f"""
            WITH v AS (
              SELECT coalesce(pm.pkey, 'br:' || t.ym_s_clientid) AS pkey,
                     nullif(trim(both '[]' from coalesce(t.ym_s_goalsid,'')),'')
                       AS gs
              FROM {VISITS} t
              LEFT JOIN {PMAP} pm ON pm.cid = t.ym_s_clientid
              WHERE t.load_date BETWEEN %(d1)s AND %(d2)s
            )
            SELECT count(*) FROM (
              SELECT pkey FROM v GROUP BY 1
              HAVING bool_and(gs IS NULL)) t
            """, p,
        )
        nogoal = cur.fetchone()[0]
        mo = d1[:7]
        print(f"  {mo}: {nogoal} человек "
              f"({100.0 * nogoal / data[mo]['all']:.1f}% от всех) "
              "не имеют ни одной цели")

    print("\n[4] Актуальные числа цепочки заявки (для документа)")
    for d1, d2 in MONTHS[:1]:
        p = {"d1": d1, "d2": d2}
        for nm, gid in (("PAYMENT_PAGE", "398982288"),
                        ("CONFIRM_PAGE", "398984331"),
                        ("REJECT_PAGE", "398983728")):
            cur.execute(
                f"""
                WITH v AS (
                  SELECT coalesce(pm.pkey, 'br:' || t.ym_s_clientid) AS pkey,
                         nullif(trim(both '[]' from
                           coalesce(t.ym_s_goalsid,'')),'') AS gs
                  FROM {VISITS} t
                  LEFT JOIN {PMAP} pm ON pm.cid = t.ym_s_clientid
                  WHERE t.load_date BETWEEN %(d1)s AND %(d2)s
                )
                SELECT count(distinct v.pkey) FROM v
                CROSS JOIN LATERAL unnest(string_to_array(v.gs, ',')) AS g(gid)
                WHERE v.gs IS NOT NULL AND g.gid = %(g)s
                """,
                {**p, "g": gid},
            )
            print(f"  {d1[:7]} {nm:<16} {cur.fetchone()[0]}")

    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())