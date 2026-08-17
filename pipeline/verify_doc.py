import os
import sys
from datetime import datetime

import psycopg2
from dotenv import load_dotenv

from funnel_goals import PWA, RECOVERY

load_dotenv()

SCHEMA = os.environ["PG_SCHEMA"]
PREFIX = os.environ["TABLE_PREFIX"]
VISITS = f"{SCHEMA}.{PREFIX}visits"
PMAP = f"{SCHEMA}.{PREFIX}person_map"
APPS = "public.loan_applications"
STEPS = "public.risk_finished_detail_steps"

D1, D2 = "2026-07-01", "2026-07-31"

SUSPECT = ("adm.", "test", "stage", "dev", "localhost", "moneza",
           "finlove", "preprod", "beta")

PEOPLE = f"""
WITH v AS (
  SELECT coalesce(pm.pkey, 'br:' || t.ym_s_clientid) AS pkey,
         t.ym_s_starturl AS url,
         nullif(trim(both '[]' from coalesce(t.ym_s_goalsid,'')),'') AS gs
  FROM {VISITS} t
  LEFT JOIN {PMAP} pm ON pm.cid = t.ym_s_clientid
  WHERE t.load_date BETWEEN %(d1)s AND %(d2)s
),
e AS (
  SELECT v.pkey, v.url, g.gid FROM v
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

    print("\n[1] Схлопывание браузеров: точная формулировка")
    cur.execute(f"SELECT count(*), count(distinct pkey) FROM {PMAP}")
    br, pe = cur.fetchone()
    print(f"  браузеров в карте: {br}")
    print(f"  из них получилось людей: {pe}")
    print(f"  избыток браузеров: {br - pe} ({100.0 * (br - pe) / br:.1f}%)")
    print(f"  на 100 человек приходится браузеров: {100.0 * br / pe:.1f}")

    print("\n[2] Заявки: весь поток против дошедших до решения")
    cur.execute(
        f"SELECT count(*), count(distinct client_id) FROM {APPS} "
        "WHERE entity_created::date BETWEEN %(d1)s AND %(d2)s "
        "AND is_additional_amount_application = 'f'", p,
    )
    a_all, c_all = cur.fetchone()
    print(f"  всего заявок: {a_all}, клиентов: {c_all}, "
          f"в среднем {a_all / c_all:.2f}")
    cur.execute(
        f"SELECT count(*), count(distinct la.client_id) FROM {APPS} la "
        f"JOIN LATERAL (SELECT max(finished_details_step) AS mx FROM {STEPS} r "
        "WHERE r.loan_application_id = la.id) s ON true "
        "WHERE la.entity_created::date BETWEEN %(d1)s AND %(d2)s "
        "AND la.is_additional_amount_application = 'f' AND s.mx >= 9", p,
    )
    a9, c9 = cur.fetchone()
    print(f"  из них дошли до шага 9: {a9} заявок, {c9} клиентов, "
          f"в среднем {a9 / c9:.2f}")

    print("\n[3] Админки и стенды в людях (не в браузерах)")
    cond = " OR ".join(f"lower(url) LIKE '%%{s}%%'" for s in SUSPECT)
    cur.execute(
        PEOPLE + "SELECT count(distinct pkey) FROM e", p,
    )
    all_people = cur.fetchone()[0]
    cur.execute(
        PEOPLE + f"SELECT count(distinct pkey) FROM e WHERE {cond}", p,
    )
    susp = cur.fetchone()[0]
    print(f"  людей с целями: {all_people}")
    print(f"  из них заходили с посторонних доменов: {susp} "
          f"({100.0 * susp / all_people:.2f}%)")

    print("\n[4] Покрытие целей внутри цепочек (одинаково ли)")
    for title, chain in (("Восстановление", RECOVERY), ("PWA", PWA)):
        print(f"\n  {title}")
        for nm, gid in chain:
            cur.execute(
                f"SELECT min(load_date)::text, max(load_date)::text, "
                f"count(distinct load_date) FROM {VISITS} "
                "WHERE string_to_array(trim(both '[]' from "
                "coalesce(ym_s_goalsid,'')), ',') @> ARRAY[%(g)s]",
                {"g": gid},
            )
            mn, mx, nd = cur.fetchone()
            cur.execute(
                PEOPLE + "SELECT count(distinct pkey) FROM e "
                "WHERE gid = %(g)s", {**p, "g": gid},
            )
            n = cur.fetchone()[0]
            print(f"    {nm:<24} {n:>8} чел.  покрытие {mn}..{mx} ({nd} дн.)")

    print("\n[5] Восстановление и PWA на общем окне покрытия")
    for title, chain in (("Восстановление", RECOVERY), ("PWA", PWA)):
        cur.execute(
            f"SELECT max(mn) FROM (SELECT min(load_date) AS mn FROM {VISITS} "
            "WHERE string_to_array(trim(both '[]' from "
            "coalesce(ym_s_goalsid,'')), ',') && %(ids)s::text[] "
            "GROUP BY 1) t",
            {"ids": [g for _, g in chain]},
        )
        cur.execute(
            "SELECT max(m) FROM (" + " UNION ALL ".join(
                f"SELECT min(load_date) AS m FROM {VISITS} WHERE "
                "string_to_array(trim(both '[]' from "
                f"coalesce(ym_s_goalsid,'')), ',') @> ARRAY['{g}']"
                for _, g in chain
            ) + ") t"
        )
        start = cur.fetchone()[0]
        d1 = max(str(start), D1)
        print(f"\n  {title}: общее окно с {d1} по {D2}")
        base_g = chain[0][1]
        cur.execute(
            PEOPLE + "SELECT count(distinct pkey) FROM e WHERE gid = %(g)s",
            {"d1": d1, "d2": D2, "g": base_g},
        )
        base = cur.fetchone()[0]
        for nm, gid in chain:
            cur.execute(
                PEOPLE + "SELECT count(distinct pkey) FROM e WHERE gid = %(g)s",
                {"d1": d1, "d2": D2, "g": gid},
            )
            n = cur.fetchone()[0]
            print(f"    {nm:<24} {n:>8} "
                  f"{100.0 * n / base if base else 0:>6.1f}%")

    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())