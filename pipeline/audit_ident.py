import os
import sys
from datetime import datetime

import psycopg2
from dotenv import load_dotenv

from funnel_goals import APPLY, FUNNEL

load_dotenv()

S = os.environ["PG_SCHEMA"]
P = os.environ["TABLE_PREFIX"]
V = f"{S}.{P}visits"
M = f"{S}.{P}person_map"
APPS = "public.loan_applications"

AI_OK = dict(FUNNEL)["ADDITIONAL_INFO_PAGE_OK"]
ID_PAGE = dict(FUNNEL)["IDENTIFICATION_PAGE"]
ID_OK = dict(FUNNEL)["IDENTIFICATION_PAGE_OK"]
PAY = dict(APPLY)["PAYMENT_PAGE"]
CONF_OK = dict(FUNNEL)["CONFIRM_PAGE_OK"]

IDS = [AI_OK, ID_PAGE, ID_OK, PAY, CONF_OK]


def log(m):
    print(f"[{datetime.now():%H:%M:%S}] {m}", flush=True)


def conn():
    return psycopg2.connect(
        host=os.environ["PG_HOST"], port=int(os.environ["PG_PORT"]),
        dbname=os.environ["PG_DB"], user=os.environ["PG_USER"],
        password=os.environ["PG_PASSWORD"], connect_timeout=60)


def build(cur, name, d1, d2, ids):
    cur.execute(f"DROP TABLE IF EXISTS {name}")
    cur.execute(
        f"""CREATE TEMP TABLE {name} AS
        WITH v AS (
          SELECT coalesce(m.pkey,'br:'||t.ym_s_clientid) AS pkey,
                 bool_or(t.ym_s_isnewuser='1') OVER (
                   PARTITION BY coalesce(m.pkey,'br:'||t.ym_s_clientid)
                 ) AS isnew,
                 nullif(trim(both '[]' from coalesce(t.ym_s_goalsid,'')),'')
                   AS gs
          FROM {V} t LEFT JOIN {M} m ON m.cid=t.ym_s_clientid
          WHERE t.load_date BETWEEN %s AND %s)
        SELECT DISTINCT v.pkey, v.isnew, g.gid FROM v
        CROSS JOIN LATERAL unnest(string_to_array(v.gs,',')) AS g(gid)
        WHERE v.gs IS NOT NULL AND g.gid = ANY(%s)""",
        (d1, d2, ids))
    cur.execute(f"CREATE INDEX ON {name} (gid)")
    cur.execute(f"CREATE INDEX ON {name} (pkey)")
    cur.execute(f"ANALYZE {name}")
    cur.execute(f"SELECT count(*) FROM {name}")
    log(f"  {name}: {cur.fetchone()[0]} rows")


def main():
    print("START", flush=True)
    c = conn()
    c.autocommit = True
    cur = c.cursor()

    log("building july goals...")
    build(cur, "jul", "2026-07-01", "2026-07-31", IDS)
    log("building may-june goals...")
    build(cur, "prev", "2026-05-01", "2026-06-30", [ID_OK])

    cur.execute(
        "DROP TABLE IF EXISTS coh; "
        "CREATE TEMP TABLE coh AS "
        "SELECT a.pkey, bool_or(a.isnew) AS isnew, "
        "bool_or(j.gid IS NOT NULL) AS saw_id "
        "FROM (SELECT DISTINCT pkey, isnew FROM jul WHERE gid=%s) a "
        "LEFT JOIN jul j ON j.pkey=a.pkey AND j.gid=%s "
        "GROUP BY a.pkey", (AI_OK, ID_PAGE))
    cur.execute("CREATE INDEX ON coh (pkey)")
    cur.execute("ANALYZE coh")

    cur.execute("SELECT saw_id, count(*) FROM coh GROUP BY 1")
    r = dict(cur.fetchall())
    sk, sw = r.get(False, 0), r.get(True, 0)
    tot = sk + sw
    print(f"\n[0] Отправили доп. сведения в июле: {tot}")
    print(f"  видели экран идентификации: {sw} ({100.0*sw/tot:.2f}%)")
    print(f"  ПРОПУСТИЛИ:                 {sk} ({100.0*sk/tot:.2f}%)")

    print("\n[1] Повторные клиенты (loans_count > 0 на заявке в июле)")
    cur.execute(
        "SELECT co.saw_id, count(*), count(DISTINCT cl.client_number) "
        "FROM coh co LEFT JOIN public.clients cl "
        "ON cl.client_number::text=co.pkey "
        f"LEFT JOIN {APPS} la ON la.client_id=cl.id "
        "AND la.entity_created::date BETWEEN '2026-07-01' AND '2026-07-31' "
        "AND la.loans_count > 0 AND la.id IS NOT NULL "
        "GROUP BY 1 ORDER BY 1")
    for flag, n, rep in cur.fetchall():
        lbl = "видевшие" if flag else "пропустившие"
        print(f"  {lbl:<14} {n:>7} чел., повторных {rep:>7} "
              f"({100.0*rep/n if n else 0:.1f}%)")

    print("\n[2] Идентифицировались раньше (май-июнь)")
    cur.execute(
        "SELECT co.saw_id, count(*), "
        "count(*) FILTER (WHERE p.pkey IS NOT NULL) "
        "FROM coh co LEFT JOIN prev p ON p.pkey=co.pkey AND p.gid=%s "
        "GROUP BY 1 ORDER BY 1", (ID_OK,))
    for flag, n, prev in cur.fetchall():
        lbl = "видевшие" if flag else "пропустившие"
        print(f"  {lbl:<14} {n:>7} чел., ранее идентифицированы {prev:>7} "
              f"({100.0*prev/n if n else 0:.1f}%)")

    print("\n[3] Куда попали дальше в июле")
    for gid, nm in ((PAY, "экран выплаты"), (CONF_OK, "заявка отправлена"),
                    (ID_OK, "идентификация готово")):
        cur.execute(
            "SELECT co.saw_id, count(*), "
            "count(*) FILTER (WHERE j.pkey IS NOT NULL) "
            "FROM coh co LEFT JOIN jul j ON j.pkey=co.pkey AND j.gid=%s "
            "GROUP BY 1 ORDER BY 1", (gid,))
        parts = []
        for flag, n, hit in cur.fetchall():
            lbl = "видевшие" if flag else "пропустившие"
            parts.append(f"{lbl} {100.0*hit/n if n else 0:.1f}%")
        print(f"  {nm:<24} {' | '.join(parts)}")

    print("\n[4] Признак Метрики «первый визит»")
    cur.execute(
        "SELECT saw_id, count(*), count(*) FILTER (WHERE isnew) "
        "FROM coh GROUP BY 1 ORDER BY 1")
    for flag, n, isnew in cur.fetchall():
        lbl = "видевшие" if flag else "пропустившие"
        print(f"  {lbl:<14} {n:>7} чел., новых {isnew:>7} "
              f"({100.0*isnew/n if n else 0:.1f}%)")

    cur.close()
    c.close()
    print("\nDONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())