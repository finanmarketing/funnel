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
APPS = "public.loan_applications"
STEPS = "public.risk_finished_detail_steps"

D1, D2 = "2026-07-01", "2026-07-31"
CONFIRM_OK = dict(FUNNEL)["CONFIRM_PAGE_OK"]

WEB = f"""
WITH v AS (
  SELECT coalesce(pm.pkey, 'br:' || t.ym_s_clientid) AS pkey,
         t.load_date AS d,
         nullif(trim(both '[]' from coalesce(t.ym_s_goalsid,'')),'') AS gs
  FROM {VISITS} t
  LEFT JOIN {PMAP} pm ON pm.cid = t.ym_s_clientid
  WHERE t.load_date BETWEEN %(d1)s AND %(d2)s
),
e AS (
  SELECT v.pkey, v.d, g.gid FROM v
  CROSS JOIN LATERAL unnest(string_to_array(v.gs, ',')) AS g(gid)
  WHERE v.gs IS NOT NULL
),
conf AS (
  SELECT pkey, min(d) AS web_day FROM e WHERE gid = %(g)s GROUP BY 1
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
    p = {"d1": D1, "d2": D2, "g": CONFIRM_OK}
    conn = connect()
    cur = conn.cursor()

    print("\n[0] Наличие таблицы шагов рисков")
    cur.execute("SELECT to_regclass(%s)", (STEPS,))
    has_steps = cur.fetchone()[0] is not None
    print(f"  {STEPS}: {'доступна' if has_steps else 'НЕДОСТУПНА'}")

    print(f"\n[1] Заявки за {D1}..{D2} по глубине шага")
    if has_steps:
        cur.execute(
            f"SELECT s.mx, count(*), count(distinct la.client_id) FROM {APPS} la "
            f"LEFT JOIN LATERAL (SELECT max(finished_details_step) AS mx "
            f"FROM {STEPS} r WHERE r.loan_application_id = la.id) s ON true "
            "WHERE la.entity_created::date BETWEEN %(d1)s AND %(d2)s "
            "AND la.is_additional_amount_application = 'f' "
            "GROUP BY 1 ORDER BY 1 NULLS FIRST", p,
        )
        print(f"  {'шаг':>6} {'заявок':>9} {'клиентов':>10}")
        rows = cur.fetchall()
        for mx, n, c in rows:
            print(f"  {str(mx):>6} {n:>9} {c:>10}")
        deep = sum(n for mx, n, _ in rows if mx is not None and mx >= 9)
        print(f"\n  заявок со step >= 9: {deep}")
    else:
        print("  пропускаю разбивку по шагам")

    print("\n[2] Веб: CONFIRM_PAGE_OK")
    cur.execute(WEB + "SELECT count(*) FROM conf", p)
    web_people = cur.fetchone()[0]
    print(f"  людей: {web_people}")

    print("\n[3] Сопоставление с полными заявками (step >= 9)")
    if has_steps:
        cur.execute(
            WEB + f"SELECT count(distinct la.client_id) FROM {APPS} la "
            f"JOIN LATERAL (SELECT max(finished_details_step) AS mx "
            f"FROM {STEPS} r WHERE r.loan_application_id = la.id) s ON true "
            "JOIN public.clients cl ON cl.id = la.client_id "
            "WHERE la.entity_created::date BETWEEN %(d1)s AND %(d2)s "
            "AND la.is_additional_amount_application = 'f' AND s.mx >= 9", p,
        )
        full_clients = cur.fetchone()[0]
        cur.execute(
            WEB + f"SELECT count(distinct la.client_id) FROM {APPS} la "
            f"JOIN LATERAL (SELECT max(finished_details_step) AS mx "
            f"FROM {STEPS} r WHERE r.loan_application_id = la.id) s ON true "
            "JOIN public.clients cl ON cl.id = la.client_id "
            "JOIN conf c ON c.pkey = cl.client_number::text "
            "WHERE la.entity_created::date BETWEEN %(d1)s AND %(d2)s "
            "AND la.is_additional_amount_application = 'f' AND s.mx >= 9", p,
        )
        both = cur.fetchone()[0]
        print(f"  клиентов с полной заявкой: {full_clients}")
        print(f"  из них есть веб-след:      {both} "
              f"({100.0 * both / full_clients if full_clients else 0:.1f}%)")
        print(f"  полных заявок без веба:    {full_clients - both} "
              f"({100.0 * (full_clients - both) / full_clients if full_clients else 0:.1f}%)")
        print(f"  веб без полной заявки:     {web_people - both} "
              f"({100.0 * (web_people - both) / web_people:.1f}%)")

    print("\n[4] Лаг: ПЕРВАЯ заявка не раньше веб-шага")
    cur.execute(
        WEB + f"SELECT lag_days, count(*) FROM ("
        "SELECT c.pkey, min(la.entity_created::date - c.web_day) AS lag_days "
        "FROM conf c JOIN public.clients cl "
        "ON cl.client_number::text = c.pkey "
        f"JOIN {APPS} la ON la.client_id = cl.id "
        "WHERE la.is_additional_amount_application = 'f' "
        "AND la.entity_created::date >= c.web_day "
        "AND la.entity_created::date <= c.web_day + 30 "
        "GROUP BY 1) t GROUP BY 1 ORDER BY 1 LIMIT 15", p,
    )
    rows = cur.fetchall()
    tot = sum(n for _, n in rows) or 1
    print(f"  {'дней':>6} {'людей':>9} {'доля':>7} {'накопл':>8}")
    acc = 0
    for lag, n in rows:
        acc += n
        print(f"  {lag:>6} {n:>9} {100.0 * n / tot:>6.1f}% "
              f"{100.0 * acc / tot:>7.1f}%")

    print("\n[5] Исход заявок людей с веб-следом")
    cur.execute(
        WEB + "SELECT la.status, la.resolution, count(*) "
        f"FROM conf c JOIN public.clients cl "
        "ON cl.client_number::text = c.pkey "
        f"JOIN {APPS} la ON la.client_id = cl.id "
        "WHERE la.is_additional_amount_application = 'f' "
        "AND la.entity_created::date BETWEEN %(d1)s AND (%(d2)s::date + 7) "
        "AND la.entity_created::date >= c.web_day "
        "GROUP BY 1,2 ORDER BY 3 DESC LIMIT 15", p,
    )
    for st, res, n in cur.fetchall():
        print(f"  status={str(st)[:18]:<18} resolution={str(res)[:18]:<18} "
              f"{n:>8}")

    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())