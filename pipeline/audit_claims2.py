import os
import sys

import psycopg2
from dotenv import load_dotenv

from funnel_goals import APPLY, FUNNEL

load_dotenv()

S = os.environ["PG_SCHEMA"]
P = os.environ["TABLE_PREFIX"]
V = f"{S}.{P}visits"
M = f"{S}.{P}person_map"
APPS = "public.loan_applications"
STEPS = "public.risk_finished_detail_steps"
D1, D2 = "2026-07-01", "2026-07-31"

REJECT = dict(APPLY)["REJECT_PAGE"]
PAYMENT = dict(APPLY)["PAYMENT_PAGE"]
CONF_OK = dict(FUNNEL)["CONFIRM_PAGE_OK"]

WEB = f"""
WITH v AS (
  SELECT coalesce(m.pkey,'br:'||t.ym_s_clientid) AS pkey,
         nullif(trim(both '[]' from coalesce(t.ym_s_goalsid,'')),'') AS gs
  FROM {V} t LEFT JOIN {M} m ON m.cid=t.ym_s_clientid
  WHERE t.load_date BETWEEN %(d1)s AND %(d2)s),
e AS (
  SELECT v.pkey, g.gid FROM v
  CROSS JOIN LATERAL unnest(string_to_array(v.gs,',')) AS g(gid)
  WHERE v.gs IS NOT NULL)
"""


def conn():
    return psycopg2.connect(
        host=os.environ["PG_HOST"], port=int(os.environ["PG_PORT"]),
        dbname=os.environ["PG_DB"], user=os.environ["PG_USER"],
        password=os.environ["PG_PASSWORD"], connect_timeout=60)


def main():
    print("START", flush=True)
    c = conn()
    cur = c.cursor()
    p = {"d1": D1, "d2": D2}

    print("\n[1] Застревание на седьмом шаге")
    cur.execute(
        f"SELECT s.mx, count(DISTINCT la.client_id) FROM {APPS} la "
        f"JOIN LATERAL (SELECT max(finished_details_step) AS mx FROM {STEPS} r "
        "WHERE r.loan_application_id=la.id) s ON true "
        "WHERE la.entity_created::date BETWEEN %(d1)s AND %(d2)s "
        "AND la.is_additional_amount_application='f' "
        "GROUP BY 1 ORDER BY 1", p)
    rows = cur.fetchall()
    total = sum(n for _, n in rows)
    print(f"  {'шаг':>5} {'клиентов':>10} {'дошли до него':>15}")
    reach = {}
    acc = total
    for st, n in rows:
        reach[st] = acc
        print(f"  {st:>5} {n:>10} {acc:>15}")
        acc -= n
    stuck7 = dict(rows).get(7, 0)
    r7 = reach.get(7, 0)
    print(f"\n  дошли до шага 7 и дальше: {r7}")
    print(f"  остановились ровно на 7:  {stuck7} "
          f"({100.0*stuck7/r7 if r7 else 0:.1f}%)")

    print("\n[2] Из застрявших на 7: сколько видели страницу отказа")
    cur.execute(
        WEB + f"SELECT count(DISTINCT cl.client_number::text) FROM {APPS} la "
        f"JOIN LATERAL (SELECT max(finished_details_step) AS mx FROM {STEPS} r "
        "WHERE r.loan_application_id=la.id) s ON true "
        "JOIN public.clients cl ON cl.id=la.client_id "
        "WHERE la.entity_created::date BETWEEN %(d1)s AND %(d2)s "
        "AND la.is_additional_amount_application='f' AND s.mx=7", p)
    stuck_ident = cur.fetchone()[0]
    for nm, gid in (("REJECT_PAGE", REJECT), ("PAYMENT_PAGE", PAYMENT),
                    ("CONFIRM_PAGE_OK", CONF_OK)):
        cur.execute(
            WEB + f"SELECT count(DISTINCT cl.client_number::text) "
            f"FROM {APPS} la JOIN LATERAL (SELECT max(finished_details_step) "
            f"AS mx FROM {STEPS} r WHERE r.loan_application_id=la.id) s ON true "
            "JOIN public.clients cl ON cl.id=la.client_id "
            "WHERE la.entity_created::date BETWEEN %(d1)s AND %(d2)s "
            "AND la.is_additional_amount_application='f' AND s.mx=7 "
            "AND EXISTS (SELECT 1 FROM e WHERE e.pkey=cl.client_number::text "
            "AND e.gid=%(g)s)", {**p, "g": gid})
        n = cur.fetchone()[0]
        print(f"  {nm:<18} {n:>8} из {stuck_ident} "
              f"({100.0*n/stuck_ident if stuck_ident else 0:.1f}%)")

    print("\n[3] Исход заявок застрявших на 7")
    cur.execute(
        f"SELECT la.status, la.resolution, count(*) FROM {APPS} la "
        f"JOIN LATERAL (SELECT max(finished_details_step) AS mx FROM {STEPS} r "
        "WHERE r.loan_application_id=la.id) s ON true "
        "WHERE la.entity_created::date BETWEEN %(d1)s AND %(d2)s "
        "AND la.is_additional_amount_application='f' AND s.mx=7 "
        "GROUP BY 1,2 ORDER BY 3 DESC LIMIT 8", p)
    for st, res, n in cur.fetchall():
        print(f"  status={str(st)[:16]:<16} resolution={str(res)[:20]:<20} "
              f"{n:>8}")

    print("\n[4] Люди без единой цели")
    cur.execute(
        f"SELECT count(*) FROM (SELECT coalesce(m.pkey,'br:'||t.ym_s_clientid) "
        f"AS pkey FROM {V} t LEFT JOIN {M} m ON m.cid=t.ym_s_clientid "
        "WHERE t.load_date BETWEEN %(d1)s AND %(d2)s GROUP BY 1 "
        "HAVING bool_and(nullif(trim(both '[]' from "
        "coalesce(t.ym_s_goalsid,'')),'') IS NULL)) x", p)
    nogoal = cur.fetchone()[0]
    cur.execute(
        f"SELECT count(DISTINCT coalesce(m.pkey,'br:'||t.ym_s_clientid)) "
        f"FROM {V} t LEFT JOIN {M} m ON m.cid=t.ym_s_clientid "
        "WHERE t.load_date BETWEEN %(d1)s AND %(d2)s", p)
    allp = cur.fetchone()[0]
    print(f"  людей всего {allp}, без единой цели {nogoal} "
          f"({100.0*nogoal/allp:.2f}%)")

    cur.close()
    c.close()
    print("\nDONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())