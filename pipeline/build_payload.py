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

# cid здесь — КЛЮЧ ЧЕЛОВЕКА (pkey), а не идентификатор браузера.
# pkey = UserID из parsedParams, если он есть, иначе 'br:' + clientID.
# Имя оставлено прежним, чтобы не менять остальную логику.
BASE = f"""
WITH v AS (
  SELECT coalesce(pm.pkey, 'br:' || t.ym_s_clientid) AS cid,
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
),
nu AS (SELECT DISTINCT cid FROM v WHERE newflag),
e AS (
  SELECT v.cid, v.vid, v.d, v.dt, v.devcat, v.osname, v.brengine, v.src,
         g.gid, (nu.cid IS NOT NULL) AS is_new
  FROM v CROSS JOIN LATERAL unnest(string_to_array(v.gs, ',')) AS g(gid)
  LEFT JOIN nu ON nu.cid = v.cid WHERE v.gs IS NOT NULL
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
                "Run: python pipeline\\ingest_metrica.py"
            )
        cur.execute("SELECT to_regclass(%s)", (PMAP,))
        if cur.fetchone()[0] is None:
            raise RuntimeError(
                f"preflight: table {PMAP} does not exist. Run: "
                "python pipeline\\apply_sql.py pipeline\\sql\\"
                "02_create_person_map.sql && python pipeline\\build_person_map.py"
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
            f"Run: python pipeline\\run_pipeline.py  "
            f"(or inspect existing: build_payload.py --date2 {mx})"
        )
    if str(mn) > d1:
        raise RuntimeError(
            f"preflight: earliest loaded day is {mn}, but period starts {d1}."
        )
    if unmapped:
        raise RuntimeError(
            f"preflight: {unmapped} visits have no entry in {PMAP}. "
            "Run: python pipeline\\build_person_map.py"
        )
    log(f"preflight ok: loaded {mn}..{mx}, {ndays} days, target {d1}..{d2}, "
        "person map complete")


def build(conn, d1, d2):
    p = {"d1": d1, "d2": d2}
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
        bucket = {}
        cur.execute(
            BASE + f"SELECT {expr} AS k, gid, count(distinct cid), "
            "count(distinct cid) FILTER (WHERE is_new) "
            "FROM e WHERE gid = ANY(%(ids)s) GROUP BY 1,2",
            {**p, "ids": STEP_IDS},
        )
        for k, gid, a, n in cur.fetchall():
            bucket.setdefault(k, {})[gid] = [a, n or 0]
        if level != "hour":
            cur.execute(
                BASE + f"SELECT {expr} AS k, gid, count(distinct cid), "
                "count(distinct cid) FILTER (WHERE is_new), "
                "count(distinct vid), "
                "count(distinct vid) FILTER (WHERE is_new) "
                "FROM e WHERE gid = ANY(%(ids)s) GROUP BY 1,2",
                {**p, "ids": EVENT_IDS},
            )
            for k, gid, a, n, va, vn in cur.fetchall():
                bucket.setdefault(k, {})[gid] = [a, n or 0, va, vn or 0]
        payload[level] = bucket
        log(f"  level {level}: {len(bucket)} keys")

    bits = ",".join(f"('{g}',{1 << i})" for i, (_, g) in enumerate(FUNNEL))
    strict = {}
    for level in ("month", "week", "day"):
        expr = KEYS[level]
        cur.execute(
            BASE + f"SELECT k, mask, isn, count(*) FROM ("
            f"SELECT {expr} AS k, cid, bool_or(is_new) AS isn, "
            f"bit_or(s.b) AS mask FROM e JOIN (VALUES {bits}) AS s(gid,b) "
            "ON s.gid = e.gid GROUP BY 1,2) t GROUP BY 1,2,3",
            p,
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
    for dname, dexpr in DIMS.items():
        cur.execute(
            BASE + "SELECT to_char(d,'YYYY-MM') AS k, gid, "
            f"{dexpr} AS val, count(distinct cid) AS c "
            "FROM e WHERE gid = ANY(%(ids)s) GROUP BY 1,2,3",
            {**p, "ids": STEP_IDS},
        )
        tmp = {}
        for k, gid, val, c in cur.fetchall():
            tmp.setdefault(k, {}).setdefault(gid, []).append([val, c])
        for k, goals in tmp.items():
            for gid, vals in goals.items():
                vals.sort(key=lambda x: -x[1])
                dim.setdefault(k, {}).setdefault(gid, {})[dname] = vals[:7]
        log(f"  dim {dname}: ok")
    payload["dim"] = dim

    cur.execute(
        BASE + "SELECT gid, min(d)::text, max(d)::text, count(distinct d) "
        "FROM e GROUP BY 1",
        p,
    )
    payload["cov"] = {g: [a, b, c] for g, a, b, c in cur.fetchall()}

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
    try:
        log(f"building payload {d1}..{d2} (unit: person)")
        payload = build(conn, d1, d2)
        conn.close()
        quality_checks(payload, d2)
        os.makedirs("state", exist_ok=True)
        with open(OUT, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
        size = os.path.getsize(OUT)
        journal_end(run_id, "ok", size)
        log(f"DONE build_payload: {OUT} ({size / 1024 / 1024:.2f} MB)")
        return 0
    except Exception as e:
        journal_end(run_id, "fail", None, e)
        log(f"FAIL build_payload: {type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())