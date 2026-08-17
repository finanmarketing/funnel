import os
import sys
from datetime import datetime

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

AI_OK = dict(FUNNEL)["ADDITIONAL_INFO_PAGE_OK"]
ID_PAGE = dict(FUNNEL)["IDENTIFICATION_PAGE"]


def log(m):
    print(f"[{datetime.now():%H:%M:%S}] {m}", flush=True)


def conn():
    return psycopg2.connect(
        host=os.environ["PG_HOST"], port=int(os.environ["PG_PORT"]),
        dbname=os.environ["PG_DB"], user=os.environ["PG_USER"],
        password=os.environ["PG_PASSWORD"], connect_timeout=60)


def main():
    print("START", flush=True)
    c = conn()
    c.autocommit = True
    cur = c.cursor()

    log("building cohort...")
    cur.execute("DROP TABLE IF EXISTS jul")
    cur.execute(
        f"""CREATE TEMP TABLE jul AS
        WITH v AS (
          SELECT coalesce(m.pkey,'br:'||t.ym_s_clientid) AS pkey,
                 nullif(trim(both '[]' from coalesce(t.ym_s_goalsid,'')),'')
                   AS gs
          FROM {V} t LEFT JOIN {M} m ON m.cid=t.ym_s_clientid
          WHERE t.load_date BETWEEN %s AND %s)
        SELECT DISTINCT v.pkey, g.gid FROM v
        CROSS JOIN LATERAL unnest(string_to_array(v.gs,',')) AS g(gid)
        WHERE v.gs IS NOT NULL AND g.gid = ANY(%s)""",
        (D1, D2, [AI_OK, ID_PAGE]))
    cur.execute("CREATE INDEX ON jul (gid)")
    cur.execute("CREATE INDEX ON jul (pkey)")
    cur.execute("ANALYZE jul")

    cur.execute("DROP TABLE IF EXISTS coh")
    cur.execute(
        "CREATE TEMP TABLE coh AS "
        "SELECT a.pkey, (i.pkey IS NOT NULL) AS saw_id "
        "FROM (SELECT DISTINCT pkey FROM jul WHERE gid=%s) a "
        "LEFT JOIN (SELECT DISTINCT pkey FROM jul WHERE gid=%s) i "
        "ON i.pkey=a.pkey", (AI_OK, ID_PAGE))
    cur.execute("CREATE INDEX ON coh (pkey)")
    cur.execute("ANALYZE coh")
    cur.execute("SELECT saw_id, count(*) FROM coh GROUP BY 1 ORDER BY 1")
    print("\n[0] Когорта")
    for f, n in cur.fetchall():
        print(f"  {'видевшие' if f else 'пропустившие':<14} {n}")

    print("\n[1] Максимальная глубина заявки в июле")
    cur.execute(
        "SELECT co.saw_id, s.mx, count(*) FROM coh co "
        "JOIN public.clients cl ON cl.client_number::text=co.pkey "
        f"JOIN {APPS} la ON la.client_id=cl.id "
        f"JOIN LATERAL (SELECT max(finished_details_step) AS mx FROM {STEPS} r "
        "WHERE r.loan_application_id=la.id) s ON true "
        "WHERE la.entity_created::date BETWEEN %s AND %s "
        "AND la.is_additional_amount_application='f' "
        "GROUP BY 1,2 ORDER BY 1,2", (D1, D2))
    rows = cur.fetchall()
    agg = {}
    for f, mx, n in rows:
        agg.setdefault(f, {})[mx] = n
    for f in (False, True):
        d = agg.get(f, {})
        tot = sum(d.values()) or 1
        lbl = "видевшие" if f else "пропустившие"
        print(f"\n  {lbl} (заявок {tot})")
        for mx in sorted(d):
            print(f"    шаг {str(mx):>4}: {d[mx]:>7} "
                  f"({100.0*d[mx]/tot:>5.1f}%)")

    print("\n[2] Исход заявок")
    cur.execute(
        "SELECT co.saw_id, la.resolution, count(*) FROM coh co "
        "JOIN public.clients cl ON cl.client_number::text=co.pkey "
        f"JOIN {APPS} la ON la.client_id=cl.id "
        "WHERE la.entity_created::date BETWEEN %s AND %s "
        "AND la.is_additional_amount_application='f' "
        "GROUP BY 1,2 ORDER BY 1,3 DESC", (D1, D2))
    res = {}
    for f, r, n in cur.fetchall():
        res.setdefault(f, []).append((r, n))
    for f in (False, True):
        lbl = "видевшие" if f else "пропустившие"
        tot = sum(n for _, n in res.get(f, [])) or 1
        print(f"\n  {lbl} (заявок {tot})")
        for r, n in res.get(f, [])[:6]:
            print(f"    {str(r):<14} {n:>7} ({100.0*n/tot:>5.1f}%)")

    print("\n[3] Сколько заявок на человека")
    cur.execute(
        "SELECT co.saw_id, count(DISTINCT co.pkey), count(la.id) FROM coh co "
        "JOIN public.clients cl ON cl.client_number::text=co.pkey "
        f"LEFT JOIN {APPS} la ON la.client_id=cl.id "
        "AND la.entity_created::date BETWEEN %s AND %s "
        "AND la.is_additional_amount_application='f' "
        "GROUP BY 1 ORDER BY 1", (D1, D2))
    for f, ppl, apps in cur.fetchall():
        lbl = "видевшие" if f else "пропустившие"
        print(f"  {lbl:<14} людей {ppl:>7}, заявок {apps:>8}, "
              f"на человека {apps/ppl if ppl else 0:.2f}")

    cur.close()
    c.close()
    print("\nDONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())