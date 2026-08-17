import argparse
import os
import re
import sys
from datetime import date, datetime, timedelta

import psycopg2
from dotenv import load_dotenv

from funnel_goals import CHAINS, REASONS

load_dotenv()

SCHEMA = os.environ["PG_SCHEMA"]
PREFIX = os.environ["TABLE_PREFIX"]
VISITS = f"{SCHEMA}.{PREFIX}visits"
GOALS = f"{SCHEMA}.{PREFIX}goals_dict"

MIN_VISITS = 200

TRACKED = {}
for lst in CHAINS:
    for n, g in lst:
        TRACKED.setdefault(g, n)
for rw, evs in REASONS.items():
    for n, g in evs:
        TRACKED.setdefault(g, f"{n} ({rw})")

# Routes that carry no funnel meaning.
NOISE = re.compile(
    r"^/(affiliate/|assets|static|api|favicon|robots|sitemap|\.well-known)",
    re.I,
)


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def connect():
    return psycopg2.connect(
        host=os.environ["PG_HOST"], port=int(os.environ["PG_PORT"]),
        dbname=os.environ["PG_DB"], user=os.environ["PG_USER"],
        password=os.environ["PG_PASSWORD"], connect_timeout=30,
    )


def norm(path):
    """Collapse numeric ids and hashes so routes group together."""
    p = (path or "/").split("?")[0].rstrip("/") or "/"
    p = re.sub(r"/\d{3,}", "/{id}", p)
    p = re.sub(r"/[0-9a-f]{16,}", "/{hash}", p, flags=re.I)
    p = re.sub(r"/[a-z0-9]{20,}", "/{token}", p, flags=re.I)
    return p


def goal_conditions(cur):
    """Pull goal names and raw definitions from the dictionary."""
    cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema=%s AND table_name=%s ORDER BY ordinal_position",
        (SCHEMA, f"{PREFIX}goals_dict"),
    )
    cols = [r[0] for r in cur.fetchall()]
    if not cols:
        return {}, {}
    idc = "goal_id" if "goal_id" in cols else cols[0]
    nmc = "name" if "name" in cols else cols[1]
    rawc = "raw" if "raw" in cols else None
    sel = f"{idc}::text, {nmc}" + (f", {rawc}::text" if rawc else "")
    cur.execute(f"SELECT {sel} FROM {GOALS}")
    names, raws = {}, {}
    for row in cur.fetchall():
        names[row[0]] = row[1] or ""
        if rawc and len(row) > 2:
            raws[row[0]] = row[2] or ""
    return names, raws


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    args = ap.parse_args()

    d2 = (date.today() - timedelta(days=1)).isoformat()
    d1 = (date.today() - timedelta(days=args.days)).isoformat()
    p = {"d1": d1, "d2": d2}
    log(f"window {d1}..{d2} ({args.days} days)")

    conn = connect()
    cur = conn.cursor()
    names, raws = goal_conditions(cur)
    log(f"goals in dictionary: {len(names)}, with raw definition: {len(raws)}")

    print("\n[1] Маршруты сайта против размеченных целей")
    routes = {}
    for col in ("ym_s_starturl", "ym_s_endurl"):
        cur.execute(
            f"SELECT substring({col} from "
            "'^[a-zA-Z]+://[^/]+(/[^?#]*)') AS pth, count(*) "
            f"FROM {VISITS} WHERE load_date BETWEEN %(d1)s AND %(d2)s "
            f"AND {col} LIKE '%%//ezaem.ru/%%' "
            "GROUP BY 1", p,
        )
        for pth, n in cur.fetchall():
            key = norm(pth)
            routes[key] = routes.get(key, 0) + n

    total = sum(routes.values())
    big = {k: v for k, v in routes.items()
           if v >= MIN_VISITS and not NOISE.match(k)}
    log(f"routes found: {len(routes)}, above {MIN_VISITS} visits: {len(big)}")

    # Which goal names / definitions mention this route
    def covered_by(route):
        tail = route.strip("/").split("/")[-1].replace("-", "").lower()
        hits = []
        for gid, nm in TRACKED.items():
            dn = names.get(gid, "").lower()
            rw = raws.get(gid, "").lower()
            if not tail:
                continue
            if tail in dn.replace("_", "") or route.lower() in rw:
                hits.append(f"{names.get(gid, gid)}")
        return hits

    print(f"\n  {'маршрут':<44} {'визитов':>9} {'доля':>7}  покрыт целью")
    print("  " + "-" * 92)
    uncovered = []
    for route, n in sorted(big.items(), key=lambda x: -x[1]):
        hits = covered_by(route)
        mark = ", ".join(hits[:2]) if hits else "— НЕТ ЦЕЛИ"
        if not hits:
            uncovered.append((route, n))
        print(f"  {route[:44]:<44} {n:>9} {100.0 * n / total:>6.2f}%  {mark}")

    print(f"\n[2] Маршруты без сопоставленной цели: {len(uncovered)}")
    for route, n in uncovered:
        print(f"  {route:<50} {n:>9} визитов")

    print("\n[3] Цели, размеченные но не встреченные в данных")
    cur.execute(
        "SELECT DISTINCT g.gid FROM "
        f"(SELECT nullif(trim(both '[]' from coalesce(ym_s_goalsid,'')),'') AS gs "
        f"FROM {VISITS} WHERE load_date BETWEEN %(d1)s AND %(d2)s) v "
        "CROSS JOIN LATERAL unnest(string_to_array(v.gs, ',')) AS g(gid) "
        "WHERE v.gs IS NOT NULL", p,
    )
    seen = {r[0].strip() for r in cur.fetchall()}
    dead = [(g, n) for g, n in TRACKED.items() if g not in seen]
    if dead:
        for g, n in dead:
            print(f"  {n} ({g}): {names.get(g, '?')}")
    else:
        print("  нет")

    print("\n[4] Цели счётчика, не используемые в отчёте")
    unused = []
    for gid in seen:
        if gid not in TRACKED and gid in names:
            unused.append(gid)
    cur.execute(
        f"SELECT g.gid, count(*) FROM "
        f"(SELECT nullif(trim(both '[]' from coalesce(ym_s_goalsid,'')),'') AS gs "
        f"FROM {VISITS} WHERE load_date BETWEEN %(d1)s AND %(d2)s) v "
        "CROSS JOIN LATERAL unnest(string_to_array(v.gs, ',')) AS g(gid) "
        "WHERE v.gs IS NOT NULL GROUP BY 1", p,
    )
    vol = {r[0].strip(): r[1] for r in cur.fetchall()}
    unused.sort(key=lambda g: -vol.get(g, 0))
    print(f"  всего неиспользуемых: {len(unused)}")
    for gid in unused[:25]:
        print(f"  {names.get(gid, '?')[:50]:<50} {gid:<12} "
              f"{vol.get(gid, 0):>9} визитов")

    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())