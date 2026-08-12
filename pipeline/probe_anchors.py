import argparse
import os
import sys
from datetime import date, datetime, timedelta

import psycopg2
from dotenv import load_dotenv

from funnel_goals import FUNNEL, LOGIN, OTHER, REASONS

load_dotenv()

SCHEMA = os.environ["PG_SCHEMA"]
PREFIX = os.environ["TABLE_PREFIX"]
VISITS = f"{SCHEMA}.{PREFIX}visits"
GOALS = f"{SCHEMA}.{PREFIX}goals_dict"

TARGETS = [
    ("398982288", "экран-выплаты"),
    ("398982605", "выплата-СБП"),
    ("398983728", "страница-отказа"),
    ("465920058", "заход-браузер"),
    ("398982473", "выплата-карта"),
]

NAMES = {}
for nm, g in FUNNEL:
    NAMES[g] = f"[ШАГ] {nm}"
for nm, g in LOGIN:
    NAMES[g] = f"[ЛОГИН] {nm}"
for nm, g in OTHER:
    NAMES[g] = f"[ПРОЧЕЕ] {nm}"
for rw, evs in REASONS.items():
    for nm, g in evs:
        NAMES.setdefault(g, f"{nm} (при {rw})")

STEP_SET = {g for _, g in FUNNEL}

BASE = f"""
WITH v AS (
  SELECT ym_s_visitid AS vid, ym_s_clientid AS cid,
         nullif(trim(both '[]' from coalesce(ym_s_goalsid,'')),'') AS gs
  FROM {VISITS} WHERE load_date BETWEEN %(d1)s AND %(d2)s
),
e AS (
  SELECT v.vid, v.cid, g.gid
  FROM v CROSS JOIN LATERAL unnest(string_to_array(v.gs, ',')) AS g(gid)
  WHERE v.gs IS NOT NULL
)
"""


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def connect():
    return psycopg2.connect(
        host=os.environ["PG_HOST"], port=int(os.environ["PG_PORT"]),
        dbname=os.environ["PG_DB"], user=os.environ["PG_USER"],
        password=os.environ["PG_PASSWORD"], connect_timeout=15,
    )


def goal_names_from_db(cur):
    try:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema=%s AND table_name=%s ORDER BY ordinal_position",
            (SCHEMA, f"{PREFIX}goals_dict"),
        )
        cols = [r[0] for r in cur.fetchall()]
        if not cols:
            return {}
        idc = "goal_id" if "goal_id" in cols else cols[0]
        namec = "name" if "name" in cols else (cols[1] if len(cols) > 1 else idc)
        cur.execute(f"SELECT {idc}::text, {namec} FROM {GOALS}")
        return {a: b for a, b in cur.fetchall()}
    except Exception as e:
        log(f"  goals_dict unavailable: {type(e).__name__}")
        return {}


def label(gid, dbnames):
    a = NAMES.get(gid)
    b = dbnames.get(gid)
    if a and b:
        return f"{a} / {b}"
    return a or b or gid


def probe(cur, gid, name, p, dbnames):
    print("\n" + "=" * 74)
    print(f"ЦЕЛЬ {gid} — {name}")
    print("=" * 74)

    cur.execute(
        BASE + "SELECT count(distinct vid), count(distinct cid) "
        "FROM e WHERE gid = %(t)s",
        {**p, "t": gid},
    )
    tv, tc = cur.fetchone()
    if tv == 0:
        print("  не сработала ни разу за период — привязка невозможна")
        return
    print(f"  визитов: {tv},  уникальных клиентов: {tc}")

    cur.execute(
        BASE + ", tgt AS (SELECT DISTINCT vid FROM e WHERE gid = %(t)s) "
        "SELECT e.gid, count(distinct e.vid) FROM e JOIN tgt USING (vid) "
        "GROUP BY 1 ORDER BY 2 DESC LIMIT 25",
        {**p, "t": gid},
    )
    rows = cur.fetchall()

    cur.execute(
        BASE + "SELECT gid, count(distinct vid) FROM e GROUP BY 1",
        p,
    )
    totals = dict(cur.fetchall())

    print(f"\n  {'цель':<52} {'вместе':>8} {'покр.':>7} {'обратно':>8}")
    print("  " + "-" * 78)
    for g, cnt in rows:
        if g == gid:
            continue
        cover = 100.0 * cnt / tv
        back = 100.0 * cnt / totals.get(g, 1)
        mark = ""
        if cover >= 97 and totals.get(g, 0) >= tv:
            mark = "  <== ЯКОРЬ"
        elif cover >= 97:
            mark = "  <== спутник"
        print(f"  {label(g, dbnames)[:52]:<52} {cnt:>8} "
              f"{cover:>6.1f}% {back:>7.1f}%{mark}")

    cur.execute(
        BASE + ", tgt AS (SELECT DISTINCT vid FROM e WHERE gid = %(t)s) "
        "SELECT count(*) FROM tgt WHERE NOT EXISTS ("
        "SELECT 1 FROM e WHERE e.vid = tgt.vid AND e.gid = ANY(%(steps)s))",
        {**p, "t": gid, "steps": list(STEP_SET)},
    )
    orphan = cur.fetchone()[0]
    print(f"\n  визитов вообще без шагов воронки: {orphan} "
          f"({100.0 * orphan / tv:.1f}%)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14)
    args = ap.parse_args()

    d2 = (date.today() - timedelta(days=1)).isoformat()
    d1 = (date.today() - timedelta(days=args.days)).isoformat()
    p = {"d1": d1, "d2": d2}
    log(f"probe window {d1}..{d2} ({args.days} days)")

    conn = connect()
    cur = conn.cursor()
    dbnames = goal_names_from_db(cur)
    log(f"goal names from dict: {len(dbnames)}")

    print("\n### Названия целей из справочника Метрики ###")
    for gid, nm in TARGETS:
        print(f"  {gid}  {nm:<18} -> {dbnames.get(gid, '(нет в справочнике)')}")

    for gid, nm in TARGETS:
        probe(cur, gid, nm, p, dbnames)

    cur.close()
    conn.close()
    print("\nЧитать так:")
    print("  покр.   — доля визитов цели, где встретилась и вторая цель")
    print("  обратно — доля визитов второй цели, где встретилась наша")
    print("  ЯКОРЬ   — экран покрывает почти все визиты цели и шире её")
    return 0


if __name__ == "__main__":
    sys.exit(main())