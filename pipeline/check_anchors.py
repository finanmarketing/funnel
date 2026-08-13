import argparse
import os
import sys
from datetime import date, datetime, timedelta

import psycopg2
from dotenv import load_dotenv

from funnel_goals import APPLY, FUNNEL, LOGIN, REASONS

load_dotenv()

SCHEMA = os.environ["PG_SCHEMA"]
PREFIX = os.environ["TABLE_PREFIX"]
VISITS = f"{SCHEMA}.{PREFIX}visits"
GOALS = f"{SCHEMA}.{PREFIX}goals_dict"

TOPN = 5

STEPS = []
_seen = set()
for _lst in (FUNNEL, LOGIN, APPLY):
    for _n, _g in _lst:
        if _g not in _seen:
            _seen.add(_g)
            STEPS.append((_n, _g))
STEP_NAME = {g: n for n, g in STEPS}
STEP_IDS = [g for _, g in STEPS]

EVENTS = []
for rw, evs in REASONS.items():
    for nm, g in evs:
        EVENTS.append((rw, nm, g))
EVENT_IDS = sorted({g for _, _, g in EVENTS})

DUP = {}
for rw, nm, g in EVENTS:
    DUP.setdefault(g, []).append(f"{nm}@{rw}")

BASE = f"""
WITH v AS (
  SELECT ym_s_visitid AS vid,
         nullif(trim(both '[]' from coalesce(ym_s_goalsid,'')),'') AS gs
  FROM {VISITS} WHERE load_date BETWEEN %(d1)s AND %(d2)s
),
e AS (
  SELECT DISTINCT v.vid, g.gid
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


def db_names(cur):
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
        nmc = "name" if "name" in cols else (cols[1] if len(cols) > 1 else idc)
        cur.execute(f"SELECT {idc}::text, {nmc} FROM {GOALS}")
        return {a: (b or "") for a, b in cur.fetchall()}
    except Exception as e:
        log(f"goals_dict unavailable: {type(e).__name__}")
        return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    args = ap.parse_args()

    d2 = (date.today() - timedelta(days=1)).isoformat()
    d1 = (date.today() - timedelta(days=args.days)).isoformat()
    p = {"d1": d1, "d2": d2}
    log(f"window {d1}..{d2} ({args.days} days)")
    log(f"events: {len(EVENT_IDS)}, screens: {len(STEP_IDS)}")

    conn = connect()
    cur = conn.cursor()
    names = db_names(cur)

    cur.execute(
        BASE + "SELECT gid, count(*) FROM e WHERE gid = ANY(%(ids)s) GROUP BY 1",
        {**p, "ids": EVENT_IDS + STEP_IDS},
    )
    total = dict(cur.fetchall())
    log("totals done, building matrix...")

    cur.execute(
        BASE + ", ev AS (SELECT vid, gid FROM e WHERE gid = ANY(%(ev)s)), "
        "st AS (SELECT vid, gid FROM e WHERE gid = ANY(%(st)s)) "
        "SELECT ev.gid, st.gid, count(*) FROM ev JOIN st USING (vid) GROUP BY 1,2",
        {**p, "ev": EVENT_IDS, "st": STEP_IDS},
    )
    co = {}
    for eg, sg, c in cur.fetchall():
        co.setdefault(eg, {})[sg] = c
    cur.close()
    conn.close()

    dups = {g: v for g, v in DUP.items() if len(v) > 1}
    if dups:
        print("\nДУБЛИРУЮЩИЕСЯ ЦЕЛИ (один id в нескольких местах):")
        for g, v in dups.items():
            print(f"  {g} [{names.get(g, '?')}]: {', '.join(v)}")

    for rw, nm, g in EVENTS:
        tot = total.get(g, 0)
        print("\n" + "-" * 84)
        print(f"{nm}  ({g})  словарь: {names.get(g, '?')}")
        print(f"  привязано к: {rw}   визитов события: {tot}")
        if tot == 0:
            print("  НЕ СРАБАТЫВАЛО за период")
            continue
        rows = []
        for sg, c in co.get(g, {}).items():
            rows.append((100.0 * c / tot, sg, c, total.get(sg, 0)))
        rows.sort(reverse=True)
        anchor = dict(FUNNEL + LOGIN + APPLY).get(rw)
        print(f"  {'экран':<32} {'покр.':>7} {'вместе':>8} {'объём экрана':>13}")
        for covp, sg, c, st in rows[:TOPN]:
            mark = "  <-- текущая привязка" if sg == anchor else ""
            wide = "" if st >= tot else "  (уже события)"
            print(f"  {STEP_NAME[sg][:32]:<32} {covp:>6.1f}% {c:>8} "
                  f"{st:>13}{wide}{mark}")
        if anchor and all(r[1] != anchor for r in rows[:TOPN]):
            cur_cov = 100.0 * co.get(g, {}).get(anchor, 0) / tot
            print(f"  {STEP_NAME.get(anchor, anchor)[:32]:<32} "
                  f"{cur_cov:>6.1f}% {co.get(g, {}).get(anchor, 0):>8} "
                  f"{total.get(anchor, 0):>13}  <-- текущая привязка")
    return 0


if __name__ == "__main__":
    sys.exit(main())