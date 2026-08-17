import os
import sys

import psycopg2
from dotenv import load_dotenv

from funnel_goals import FUNNEL

load_dotenv()

S = os.environ["PG_SCHEMA"]
P = os.environ["TABLE_PREFIX"]
V = f"{S}.{P}visits"
M = f"{S}.{P}person_map"
APPS = "public.loan_applications"
STEPS = "public.risk_finished_detail_steps"
D1, D2 = "2026-07-01", "2026-07-31"
CONF = dict(FUNNEL)["CONFIRM_PAGE_OK"]

WEB = f"""
WITH v AS (
  SELECT coalesce(m.pkey,'br:'||t.ym_s_clientid) AS pkey, t.load_date AS d,
         nullif(trim(both '[]' from coalesce(t.ym_s_goalsid,'')),'') AS gs
  FROM {V} t LEFT JOIN {M} m ON m.cid=t.ym_s_clientid
  WHERE t.load_date BETWEEN %(d1)s AND %(d2)s),
e AS (
  SELECT v.pkey, v.d, g.gid FROM v
  CROSS JOIN LATERAL unnest(string_to_array(v.gs,',')) AS g(gid)
  WHERE v.gs IS NOT NULL),
conf AS (SELECT pkey, min(d) AS wd FROM e WHERE gid=%(g)s GROUP BY 1)
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
    p = {"d1": D1, "d2": D2, "g": CONF}

    cur.execute(WEB + "SELECT count(*) FROM conf", p)
    total = cur.fetchone()[0]
    print(f"\n[0] Людей с CONFIRM_PAGE_OK за июль: {total}")

    print("\n[1] Ближайшая заявка: распределение по разнице дней")
    cur.execute(
        WEB + "SELECT lag, count(*) FROM (SELECT c.pkey, "
        "(SELECT la.entity_created::date - c.wd "
        f" FROM {APPS} la JOIN public.clients cl ON cl.id=la.client_id "
        " WHERE cl.client_number::text=c.pkey "
        " AND la.is_additional_amount_application='f' "
        " ORDER BY abs(la.entity_created::date - c.wd) LIMIT 1) AS lag "
        "FROM conf c) t GROUP BY 1 ORDER BY 1 NULLS FIRST", p)
    rows = cur.fetchall()
    neg = sum(n for l, n in rows if l is not None and l < 0)
    zero = sum(n for l, n in rows if l == 0)
    pos = sum(n for l, n in rows if l is not None and l > 0)
    none = sum(n for l, n in rows if l is None)
    print(f"  {'разница':>9} {'людей':>8} {'доля':>7}")
    for lag, n in rows:
        if n < 30 and lag not in (0, -1, 1, None):
            continue
        label = "нет заявки" if lag is None else str(lag)
        print(f"  {label:>9} {n:>8} {100.0*n/total:>6.2f}%")
    print(f"\n  заявка раньше веб-шага: {neg} ({100.0*neg/total:.2f}%)")
    print(f"  в тот же день:          {zero} ({100.0*zero/total:.2f}%)")
    print(f"  позже веб-шага:         {pos} ({100.0*pos/total:.2f}%)")
    print(f"  заявки нет вообще:      {none} ({100.0*none/total:.2f}%)")

    print("\n[2] Покрытие при разных окнах допуска")
    for label, cond in (
            ("строго не раньше веб-шага", "la.entity_created::date >= c.wd"),
            ("допуск 1 день назад", "la.entity_created::date >= c.wd - 1"),
            ("допуск 3 дня назад", "la.entity_created::date >= c.wd - 3"),
            ("допуск 7 дней назад", "la.entity_created::date >= c.wd - 7"),
            ("любая заявка клиента", "TRUE")):
        cur.execute(
            WEB + "SELECT count(*) FROM conf c WHERE EXISTS ("
            f"SELECT 1 FROM {APPS} la JOIN public.clients cl "
            "ON cl.id=la.client_id WHERE cl.client_number::text=c.pkey "
            "AND la.is_additional_amount_application='f' "
            f"AND {cond} AND la.entity_created::date <= c.wd + 7)", p)
        n = cur.fetchone()[0]
        print(f"  {label:<28} {n:>7}/{total} = {100.0*n/total:.1f}%")

    print("\n[3] У кого заявки нет совсем: есть ли они в clients")
    cur.execute(
        WEB + "SELECT count(*), count(cl.client_number) FROM conf c "
        "LEFT JOIN public.clients cl ON cl.client_number::text=c.pkey "
        f"WHERE NOT EXISTS (SELECT 1 FROM {APPS} la "
        "JOIN public.clients c2 ON c2.id=la.client_id "
        "WHERE c2.client_number::text=c.pkey "
        "AND la.is_additional_amount_application='f')", p)
    noapp, inclients = cur.fetchone()
    print(f"  людей без заявок: {noapp}, из них есть в clients: {inclients}")

    print("\n[4] Глубина заявки у тех, чья заявка старше веб-шага")
    cur.execute(
        WEB + "SELECT s.mx, count(*) FROM conf c "
        "JOIN public.clients cl ON cl.client_number::text=c.pkey "
        f"JOIN {APPS} la ON la.client_id=cl.id "
        f"JOIN LATERAL (SELECT max(finished_details_step) AS mx FROM {STEPS} r "
        "WHERE r.loan_application_id=la.id) s ON true "
        "WHERE la.is_additional_amount_application='f' "
        "AND la.entity_created::date < c.wd "
        "AND la.entity_created::date >= c.wd - 30 "
        "GROUP BY 1 ORDER BY 1", p)
    for mx, n in cur.fetchall():
        print(f"  шаг {str(mx):>4}: {n:>7} заявок")

    cur.close()
    c.close()
    print("\nDONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())