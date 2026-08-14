import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta

import psycopg2
from dotenv import load_dotenv

from funnel_goals import (APPLY, CHAINS, EVENT_IDS, FUNNEL, LOGIN, OTHER, PWA,
                          REASONS, RECOVERY, STEP_IDS)

load_dotenv()

SCHEMA = os.environ["PG_SCHEMA"]
PREFIX = os.environ["TABLE_PREFIX"]
VISITS = f"{SCHEMA}.{PREFIX}visits"
PMAP = f"{SCHEMA}.{PREFIX}person_map"
RUNS = f"{SCHEMA}.{PREFIX}pipeline_runs"
START = os.environ.get("BACKFILL_START", "2026-05-01")
OUT = os.path.join("state", "payload.json")

NEEDED_IDS = list(dict.fromkeys(STEP_IDS + EVENT_IDS))

KEYS = {
    "month": "to_char(d,'YYYY-MM')",
    "week": "to_char(d,'IYYY-\"W\"IW')",
    "day": "d::text",
    "hour": "substr(dt,1,13)",
}
DIMS = {
    "device": "coalesce(nullif(devcat,''),'-')",
    "os": "coalesce(nullif(osname,''),'-')",
    "browser": "coalesce(nullif(brengine,''),'-')",
    "source": "coalesce(nullif(src,''),'-')",
}

# Step 1: one pass over visits, attach the person key.
VIS = f"""
CREATE TEMP TABLE vis AS
SELECT coalesce(pm.pkey, 'br:' || t.ym_s_clientid) AS pkey,
       t.ym_s_visitid AS vid, t.load_date AS d,
       t.ym_s_datetime AS dt, (t.ym_s_isnewuser = '1') AS newflag,
       t.ym_s_devicecategory AS devcat, t.ym_s_operatingsystem AS osname,
       t.ym_s_browserengine AS brengine,
       CASE WHEN t.ym_s_lasttrafficsource = 'undefined' THEN '-'
            ELSE t.ym_s_lasttrafficsource END AS src,
       nullif(trim(both '[]' from coalesce(t.ym_s_goalsid,'')),'') AS gs
FROM {VISITS} t
LEFT JOIN {PMAP} pm ON pm.cid = t.ym_s_clientid
WHERE t.load_date BETWEEN %(d1)s AND %(d2)s
"""

# Step 2: is_new is derived from ALL visits of the person, including visits
# that carry no tracked goals. Deriving it from goal rows would silently
# drop people whose first visit had no tracked goal.
PERSONS = """
CREATE TEMP TABLE pers AS
SELECT pkey, row_number() OVER (ORDER BY pkey)::int AS cid,
       bool_or(newflag) AS is_new
FROM vis GROUP BY pkey
"""

# Step 3: explode goals, keep only the ones the report uses.
EMAT = """
CREATE TEMP TABLE emat AS
SELECT p.cid, v.vid, v.d, v.dt, v.devcat, v.osname, v.brengine, v.src,
       g.gid, p.is_new
FROM vis v JOIN pers p ON p.pkey = v.pkey
CROSS JOIN LATERAL unnest(string_to_array(v.gs, ',')) AS g(gid)
WHERE v.gs IS NOT NULL AND g.gid = ANY(%(ids)s)
"""


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def connect():
    return psycopg2.connect(
        host=os.environ["PG_HOST"], port=int(os.environ["PG_PORT"]),
        dbname=os.environ["PG_DB"], user=os.environ["PG_USER"],
        password=os.environ["PG_PASSWORD"], connect_timeout=15,
    )


def journal_start(stage):
    conn = connect()
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO {RUNS}(stage,status) VALUES (%s,'start') RETURNING run_id",
            (stage,),
        )
        rid = cur.fetchone()[0]
    conn.close()
    return rid


def journal_end(run_id, status, rows=None, err=None):
    conn = connect()
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(
            f"UPDATE {RUNS} SET finished_at=now(), status=%s, rows_loaded=%s, "
            "error_text=%s WHERE run_id=%s",
            (status, rows, (str(err)[:4000] if err else None), run_id),
        )
    conn.close()


def preflight(conn, d1, d2):
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s)", (VISITS,))
        if cur.fetchone()[0] is None:
            raise RuntimeError(
                f"preflight: table {VISITS} does not exist. "
                "Run: run pipeline\\ingest_metrica.py"
            )
        cur.execute("SELECT to_regclass(%s)", (PMAP,))
        if cur.fetchone()[0] is None:
            raise RuntimeError(
                f"preflight: table {PMAP} does not exist. "
                "Run: run pipeline\\build_person_map.py"
            )
        cur.execute(
            f"SELECT min(load_date), max(load_date), count(distinct load_date) "
            f"FROM {VISITS}"
        )
        mn, mx, ndays = cur.fetchone()
        cur.execute(
            f"SELECT count(*) FROM {VISITS} t LEFT JOIN {PMAP} pm "
            "ON pm.cid = t.ym_s_clientid WHERE pm.cid IS NULL "
            "AND t.load_date BETWEEN %s AND %s", (d1, d2),
        )
        unmapped = cur.fetchone()[0]
    if mx is None:
        raise RuntimeError(f"preflight: {VISITS} is empty.")
    if str(mx) < d2:
        raise RuntimeError(
            f"preflight: data loaded up to {mx}, but {d2} requested. "
            f"Run: run pipeline\\run_pipeline.py  "
            f"(or inspect existing: build_payload.py --date2 {mx})"
        )
    if str(mn) > d1:
        raise RuntimeError(
            f"preflight: earliest loaded day is {mn}, but period starts {d1}."
        )
    if unmapped:
        raise RuntimeError(
            f"preflight: {unmapped} visits missing from {PMAP}. "
            "Run: run pipeline\\build_person_map.py"
        )
    log(f"preflight ok: loaded {mn}..{mx}, {ndays} days, target {d1}..{d2}, "
        "person map complete")


def materialize(conn, d1, d2):
    cur = conn.cursor()
    for stmt in ("SET temp_buffers = '512MB'", "SET work_mem = '256MB'"):
        try:
            cur.execute(stmt)
        except Exception:
            conn.rollback()
            cur = conn.cursor()
            log(f"  cannot apply: {stmt}")

    t0 = datetime.now()
    for t in ("emat", "pers", "vis"):
        cur.execute(f"DROP TABLE IF EXISTS {t}")
    conn.commit()

    cur.execute(VIS, {"d1": d1, "d2": d2})
    conn.commit()
    cur.execute("SELECT count(*) FROM vis")
    nvis = cur.fetchone()[0]
    log(f"  vis: {nvis} visits ({(datetime.now() - t0).seconds}s)")

    cur.execute(PERSONS)
    cur.execute("CREATE INDEX ON pers (pkey)")
    cur.execute("ANALYZE pers")
    conn.commit()
    cur.execute("SELECT count(*), count(*) FILTER (WHERE is_new) FROM pers")
    npers, nnew = cur.fetchone()
    log(f"  persons: {npers} (new {nnew})")

    cur.execute(EMAT, {"ids": NEEDED_IDS})
    cur.execute("DROP TABLE vis")
    cur.execute("CREATE INDEX ON emat (gid)")
    cur.execute("ANALYZE emat")
    conn.commit()
    cur.execute("SELECT count(*), count(distinct vid) FROM emat")
    rows, visits = cur.fetchone()
    log(f"  materialized: {rows} goal-rows, {visits} visits "
        f"(total {(datetime.now() - t0).seconds}s)")
    cur.close()
    return rows


def build(conn, d1, d2):
    cur = conn.cursor()
    payload = {
        "funnel": [[n, g] for n, g in FUNNEL],
        "login": [[n, g] for n, g in LOGIN],
        "apply": [[n, g] for n, g in APPLY],
        "recovery": [[n, g] for n, g in RECOVERY],
        "pwa": [[n, g] for n, g in PWA],
        "other": [[n, g] for n, g in OTHER],
        "reasons": {k: [[n, g] for n, g in v] for k, v in REASONS.items()},
        "unit": "person",
    }

    for level, expr in KEYS.items():
        t0 = datetime.now()
        bucket = {}
        cur.execute(
            f"SELECT {expr} AS k, gid, count(distinct cid), "
            "count(distinct cid) FILTER (WHERE is_new) "
            "FROM emat WHERE gid = ANY(%(ids)s) GROUP BY 1,2",
            {"ids": STEP_IDS},
        )
        for k, gid, a, n in cur.fetchall():
            bucket.setdefault(k, {})[gid] = [a, n or 0]
        if level != "hour":
            cur.execute(
                f"SELECT {expr} AS k, gid, count(distinct cid), "
                "count(distinct cid) FILTER (WHERE is_new), "
                "count(distinct vid), "
                "count(distinct vid) FILTER (WHERE is_new) "
                "FROM emat WHERE gid = ANY(%(ids)s) GROUP BY 1,2",
                {"ids": EVENT_IDS},
            )
            for k, gid, a, n, va, vn in cur.fetchall():
                bucket.setdefault(k, {})[gid] = [a, n or 0, va, vn or 0]
        payload[level] = bucket
        log(f"  level {level}: {len(bucket)} keys "
            f"({(datetime.now() - t0).seconds}s)")

    bits = ",".join(f"('{g}',{1 << i})" for i, (_, g) in enumerate(FUNNEL))
    strict = {}
    for level in ("month", "week", "day"):
        expr = KEYS[level]
        cur.execute(
            f"SELECT k, mask, isn, count(*) FROM ("
            f"SELECT {expr} AS k, cid, bool_or(is_new) AS isn, "
            f"bit_or(s.b) AS mask FROM emat JOIN (VALUES {bits}) AS s(gid,b) "
            "ON s.gid = emat.gid GROUP BY 1,2) t GROUP BY 1,2,3"
        )
        acc = {}
        for k, mask, isn, cnt in cur.fetchall():
            depth = 0
            while depth < len(FUNNEL) and (mask >> depth) & 1:
                depth += 1
            arr = acc.setdefault(k, [[0, 0] for _ in FUNNEL])
            for i in range(depth):
                arr[i][0] += cnt
                if isn:
                    arr[i][1] += cnt
        strict[level] = acc
        log(f"  strict {level}: {len(acc)} keys")
    payload["strict"] = strict

    dim = {}
    t0 = datetime.now()
    for dname, dexpr in DIMS.items():
        cur.execute(
            "SELECT to_char(d,'YYYY-MM') AS k, gid, "
            f"{dexpr} AS val, count(distinct cid) AS c "
            "FROM emat WHERE gid = ANY(%(ids)s) GROUP BY 1,2,3",
            {"ids": STEP_IDS},
        )
        tmp = {}
        for k, gid, val, c in cur.fetchall():
            tmp.setdefault(k, {}).setdefault(gid, []).append([val, c])
        for k, goals in tmp.items():
            for gid, vals in goals.items():
                vals.sort(key=lambda x: -x[1])
                dim.setdefault(k, {}).setdefault(gid, {})[dname] = vals[:7]
    payload["dim"] = dim
    log(f"  dims: 4 ok ({(datetime.now() - t0).seconds}s)")

    t0 = datetime.now()
    cur.execute(
        "SELECT gid, min(d)::text, max(d)::text, count(distinct d) "
        "FROM emat GROUP BY 1"
    )
    payload["cov"] = {g: [a, b, c] for g, a, b, c in cur.fetchall()}
    log(f"  coverage: {len(payload['cov'])} goals "
        f"({(datetime.now() - t0).seconds}s)")

    cur.execute(
        "SELECT entity_created::date::text, count(*) FROM public.clients "
        "WHERE entity_created >= %s AND entity_created::date <= %s "
        "GROUP BY 1 ORDER BY 1",
        (d1, d2),
    )
    payload["dbreg"] = {d: n for d, n in cur.fetchall()}

    days = sorted(payload["day"].keys())
    payload["period"] = [days[0], days[-1], len(days)]

    months, weeks, dmap = [], {}, {}
    for ds in days:
        dt = date.fromisoformat(ds)
        m = ds[:7]
        iso = dt.isocalendar()
        w = f"{iso[0]}-W{iso[1]:02d}"
        if m not in months:
            months.append(m)
        wl = weeks.setdefault(m, [])
        if w not in wl:
            wl.append(w)
        dl = dmap.setdefault(w, [])
        if ds not in dl:
            dl.append(ds)
    payload["nav"] = {"months": months, "weeks": weeks, "days": dmap}
    cur.close()
    return payload


def quality_checks(payload, d2):
    reg_id = dict(FUNNEL)["REGISTRATION_PAGE_OK"]
    day = payload["day"].get(d2)
    if not day:
        raise RuntimeError(f"QC: no data for {d2}")
    metrica = day.get(reg_id, [0, 0])[0]
    if metrica <= 0:
        raise RuntimeError(f"QC: Metrica registrations on {d2} = 0")
    dbreg = payload["dbreg"].get(d2, 0)
    if dbreg <= 0:
        raise RuntimeError(f"QC: DB registrations on {d2} = 0")
    gap = (dbreg - metrica) / dbreg * 100
    if not (0 <= gap <= 15):
        raise RuntimeError(
            f"QC: gap on {d2} = {gap:.1f}% (metrica={metrica}, db={dbreg})"
        )
    missing = [n for n, g in FUNNEL if g not in day]
    if missing:
        raise RuntimeError(f"QC: steps missing on {d2}: {missing}")

    step_of = {}
    for lst in CHAINS:
        for n, g in lst:
            step_of.setdefault(n, g)

    bad = []
    for level in ("month", "week", "day"):
        for k, goals in payload[level].items():
            for rw, evs in REASONS.items():
                gid = step_of.get(rw)
                if not gid or gid not in goals:
                    continue
                den = goals[gid][0]
                if den <= 0:
                    continue
                for nm, g in evs:
                    val = goals.get(g)
                    if val and val[0] > den * 1.01:
                        bad.append(f"{level}/{k}/{rw}/{g}: {val[0]}>{den}")
    if bad:
        log(f"  QC WARNING: {len(bad)} event(s) exceed screen population by >1%")
        for b in bad[:5]:
            log(f"    {b}")
    else:
        log("  QC: no event exceeds its screen population")
    log(f"  QC ok: metrica={metrica} db={dbreg} gap={gap:.1f}%")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date1", default=START)
    ap.add_argument("--date2")
    args = ap.parse_args()

    d1 = args.date1
    d2 = args.date2 or (date.today() - timedelta(days=1)).isoformat()

    conn = connect()
    try:
        preflight(conn, d1, d2)
    except Exception as e:
        conn.close()
        log(f"FAIL build_payload: {type(e).__name__}: {e}")
        return 1

    run_id = journal_start("build_payload")
    t_all = datetime.now()
    try:
        log(f"building payload {d1}..{d2} (unit: person)")
        materialize(conn, d1, d2)
        payload = build(conn, d1, d2)
        conn.close()
        quality_checks(payload, d2)
        os.makedirs("state", exist_ok=True)
        with open(OUT, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
        size = os.path.getsize(OUT)
        journal_end(run_id, "ok", size)
        log(f"DONE build_payload: {OUT} ({size / 1024 / 1024:.2f} MB), "
            f"total {(datetime.now() - t_all).seconds}s")
        return 0
    except Exception as e:
        try:
            conn.close()
        except Exception:
            pass
        journal_end(run_id, "fail", None, e)
        log(f"FAIL build_payload: {type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())