import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta

import psycopg2
from dotenv import load_dotenv

from funnel_goals import CHAINS, OTHER, REASONS

load_dotenv()

SCHEMA = os.environ["PG_SCHEMA"]
PREFIX = os.environ["TABLE_PREFIX"]
VISITS = f"{SCHEMA}.{PREFIX}visits"
GOALS = f"{SCHEMA}.{PREFIX}goals_dict"

# Routes found in data but not represented in the report.
WANTED = [
    "/loan/active", "/loan/approved", "/loan/pending", "/loan/new",
    "/loan/repay", "/oauth2/callback", "/forgot-password/confirm-code",
    "/forgot-password/default-questions", "/forgot-password/change-password",
    "/payment/unsubscribe", "/documents", "/proposal",
]

USED = set()
for lst in CHAINS:
    for n, g in lst:
        USED.add(g)
for rw, evs in REASONS.items():
    for n, g in evs:
        USED.add(g)
for n, g in OTHER:
    USED.add(g)


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def connect():
    return psycopg2.connect(
        host=os.environ["PG_HOST"], port=int(os.environ["PG_PORT"]),
        dbname=os.environ["PG_DB"], user=os.environ["PG_USER"],
        password=os.environ["PG_PASSWORD"], connect_timeout=30,
    )


def load_goals(cur):
    cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema=%s AND table_name=%s ORDER BY ordinal_position",
        (SCHEMA, f"{PREFIX}goals_dict"),
    )
    cols = [r[0] for r in cur.fetchall()]
    print(f"  колонки справочника: {cols}")
    idc = "goal_id" if "goal_id" in cols else cols[0]
    nmc = "name" if "name" in cols else cols[1]
    rawc = "raw" if "raw" in cols else None
    sel = f"{idc}::text, {nmc}" + (f", {rawc}::text" if rawc else ", ''")
    cur.execute(f"SELECT {sel} FROM {GOALS}")
    return [(r[0], r[1] or "", r[2] or "") for r in cur.fetchall()]


def conditions(raw):
    """Pull condition values out of the goal definition JSON."""
    out = []
    try:
        obj = json.loads(raw) if raw.strip().startswith("{") else None
    except Exception:
        obj = None
    if obj is None:
        return out

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k in ("url", "value", "pattern") and isinstance(v, str):
                    out.append(v)
                else:
                    walk(v)
        elif isinstance(node, list):
            for x in node:
                walk(x)

    walk(obj)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    args = ap.parse_args()

    d2 = (date.today() - timedelta(days=1)).isoformat()
    d1 = (date.today() - timedelta(days=args.days)).isoformat()
    p = {"d1": d1, "d2": d2}
    log(f"window {d1}..{d2}")

    conn = connect()
    cur = conn.cursor()
    print("\n[0] Справочник целей")
    goals = load_goals(cur)
    print(f"  целей: {len(goals)}, используется в отчёте: {len(USED)}")

    cur.execute(
        "SELECT g.gid, count(*) FROM "
        f"(SELECT nullif(trim(both '[]' from coalesce(ym_s_goalsid,'')),'') AS gs "
        f"FROM {VISITS} WHERE load_date BETWEEN %(d1)s AND %(d2)s) v "
        "CROSS JOIN LATERAL unnest(string_to_array(v.gs, ',')) AS g(gid) "
        "WHERE v.gs IS NOT NULL GROUP BY 1", p,
    )
    vol = {r[0].strip(): r[1] for r in cur.fetchall()}
    print(f"  целей со срабатываниями за период: {len(vol)}")

    print("\n[1] Цели, чьё условие ссылается на неразмеченные маршруты")
    found_any = False
    for route in WANTED:
        hits = []
        for gid, name, raw in goals:
            conds = conditions(raw)
            blob = " ".join(conds) if conds else raw
            if route in blob:
                hits.append((gid, name, vol.get(gid, 0), gid in USED))
        print(f"\n  {route}")
        if not hits:
            print("    цели не найдено")
            continue
        found_any = True
        for gid, name, n, used in hits:
            state = "используется" if used else "НЕ ПОДКЛЮЧЕНА"
            print(f"    {name[:44]:<44} {gid:<12} {n:>8} виз.  {state}")

    print("\n[2] Все цели со срабатываниями, не подключённые к отчёту")
    unused = [(g, n) for g, n in vol.items() if g not in USED]
    unused.sort(key=lambda x: -x[1])
    names = {g: n for g, n, _ in goals}
    print(f"  всего: {len(unused)}")
    for gid, n in unused[:40]:
        print(f"    {names.get(gid, '(нет в справочнике)')[:50]:<50} "
              f"{gid:<12} {n:>9} виз.")

    print("\n[3] Условия целей, упоминающие loan или oauth")
    for gid, name, raw in goals:
        conds = conditions(raw)
        blob = " ".join(conds) if conds else raw
        low = blob.lower()
        if "loan" in low or "oauth" in low:
            state = "используется" if gid in USED else "не подключена"
            short = (conds[:3] if conds else [raw[:80]])
            print(f"  {name[:40]:<40} {gid:<12} {vol.get(gid, 0):>8} виз. "
                  f"[{state}]")
            for c in short:
                print(f"      {str(c)[:90]}")

    cur.close()
    conn.close()
    return 0 if found_any else 0


if __name__ == "__main__":
    sys.exit(main())