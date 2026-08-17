import os
import sys
from datetime import datetime

import psycopg2
from dotenv import load_dotenv

from funnel_goals import APPLY, FUNNEL

load_dotenv()

SCHEMA = os.environ["PG_SCHEMA"]
PREFIX = os.environ["TABLE_PREFIX"]
VISITS = f"{SCHEMA}.{PREFIX}visits"
PMAP = f"{SCHEMA}.{PREFIX}person_map"
APPS = "public.loan_applications"
STEPS = "public.risk_finished_detail_steps"

D1, D2 = "2026-07-01", "2026-07-31"

WEB_STEPS = [(n, g) for n, g in FUNNEL] + \
            [(n, g) for n, g in APPLY if n in ("PAYMENT_PAGE", "REJECT_PAGE")]

BASE = f"""
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

    print("\n[0] Готовлю таблицу глубины шага по клиентам")
    cur.execute("DROP TABLE IF EXISTS depth")
    cur.execute(
        f"CREATE TEMP TABLE depth AS "
        f"SELECT cl.client_number::text AS pkey, max(s.mx) AS max_step "
        f"FROM {APPS} la "
        f"JOIN LATERAL (SELECT max(finished_details_step) AS mx "
        f"FROM {STEPS} r WHERE r.loan_application_id = la.id) s ON true "
        "JOIN public.clients cl ON cl.id = la.client_id "
        "WHERE la.entity_created::date BETWEEN %(d1)s AND %(d2)s "
        "AND la.is_additional_amount_application = 'f' "
        "GROUP BY 1", p,
    )
    conn.commit()
    cur.execute("CREATE INDEX ON depth (pkey)")
    cur.execute("ANALYZE depth")
    cur.execute("SELECT count(*) FROM depth")
    log(f"  клиентов с заявками: {cur.fetchone()[0]}")

    print("\n[1] Веб-шаг -> медиана и распределение finished_details_step")
    print(f"  {'веб-шаг':<30} {'людей':>8} {'с заявкой':>10} "
          f"{'медиана':>8} {'>=7':>7} {'>=9':>7}")
    print("  " + "-" * 78)
    for name, gid in WEB_STEPS:
        cur.execute(
            BASE + "SELECT count(*), count(d.pkey), "
            "percentile_disc(0.5) WITHIN GROUP (ORDER BY d.max_step), "
            "count(*) FILTER (WHERE d.max_step >= 7), "
            "count(*) FILTER (WHERE d.max_step >= 9) "
            "FROM (SELECT DISTINCT pkey FROM e WHERE gid = %(g)s) w "
            "LEFT JOIN depth d ON d.pkey = w.pkey",
            {**p, "g": gid},
        )
        total, withapp, med, ge7, ge9 = cur.fetchone()
        if total == 0:
            continue
        print(f"  {name:<30} {total:>8} {withapp:>10} "
              f"{str(med):>8} {100.0 * ge7 / total:>6.1f}% "
              f"{100.0 * ge9 / total:>6.1f}%")

    print("\n[2] Обратно: finished_details_step -> какой веб-шаг достигнут")
    for step in range(1, 11):
        cur.execute(
            "SELECT count(*) FROM depth WHERE max_step = %s", (step,)
        )
        n = cur.fetchone()[0]
        if n == 0:
            continue
        parts = []
        for name, gid in WEB_STEPS:
            cur.execute(
                BASE + "SELECT count(*) FROM depth d "
                "WHERE d.max_step = %(s)s AND EXISTS "
                "(SELECT 1 FROM e WHERE e.pkey = d.pkey AND e.gid = %(g)s)",
                {**p, "g": gid, "s": step},
            )
            c = cur.fetchone()[0]
            if c > 0:
                parts.append((name, 100.0 * c / n))
        top = ", ".join(f"{nm} {pc:.0f}%" for nm, pc in parts
                        if pc >= 50)
        print(f"  step {step:>2}: {n:>7} клиентов -> {top or 'нет веб-следа'}")

    print("\n[3] Сколько людей достигают каждого шага (для сверки объёмов)")
    cur.execute(
        "SELECT max_step, count(*) FROM depth GROUP BY 1 ORDER BY 1"
    )
    rows = cur.fetchall()
    tot = sum(n for _, n in rows)
    acc = tot
    print(f"  {'шаг':>5} {'ровно':>9} {'дошли до него и дальше':>24}")
    for st, n in rows:
        print(f"  {st:>5} {n:>9} {acc:>24}")
        acc -= n

    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())