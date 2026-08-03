import argparse
import gzip
import json
import os
import sys
import tempfile
import time
from datetime import date, datetime, timedelta

import psycopg2
import requests
from dotenv import load_dotenv

load_dotenv()

SCHEMA = os.environ["PG_SCHEMA"]
PREFIX = os.environ["TABLE_PREFIX"]
COUNTER = os.environ["METRICA_COUNTER_ID"]
TOKEN = os.environ["METRICA_TOKEN"]
RELOAD_WINDOW = int(os.environ.get("RELOAD_WINDOW_DAYS", "3"))
BACKFILL_START = os.environ.get("BACKFILL_START", "2026-05-01")

VISITS = f"{SCHEMA}.{PREFIX}visits"
RUNS = f"{SCHEMA}.{PREFIX}pipeline_runs"
FIELDS_STATE = os.path.join("state", "visit_fields.json")
API = "https://api-metrika.yandex.ru"
HEAD = {"Authorization": f"OAuth {TOKEN}"}


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def connect():
    return psycopg2.connect(
        host=os.environ["PG_HOST"],
        port=int(os.environ["PG_PORT"]),
        dbname=os.environ["PG_DB"],
        user=os.environ["PG_USER"],
        password=os.environ["PG_PASSWORD"],
        connect_timeout=15,
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


def norm(col):
    return col.strip().replace(":", "_").lower()


def ensure_table(conn, header):
    cols = [norm(c) for c in header]
    with conn.cursor() as cur:
        cur.execute(
            "SELECT to_regclass(%s)", (VISITS,)
        )
        exists = cur.fetchone()[0] is not None
        if not exists:
            body = ",\n  ".join(f'"{c}" text' for c in cols)
            cur.execute(
                f"CREATE TABLE {VISITS} (\n  {body},\n"
                "  load_date date NOT NULL,\n"
                "  loaded_at timestamptz DEFAULT now()\n)"
            )
            cur.execute(
                f"COMMENT ON TABLE {VISITS} IS "
                "'Raw Metrica Logs API visits. Owner: analytics (E.Rybakov). "
                "Funnel pipeline v1.0.'"
            )
            log(f"table {VISITS} created with {len(cols)} data columns")
        else:
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema=%s AND table_name=%s",
                (SCHEMA, f"{PREFIX}visits"),
            )
            have = {r[0] for r in cur.fetchall()}
            missing = [c for c in cols if c not in have]
            for c in missing:
                cur.execute(f'ALTER TABLE {VISITS} ADD COLUMN "{c}" text')
            if missing:
                log(f"added {len(missing)} new column(s): {missing}")
        cur.execute(
            f"CREATE INDEX IF NOT EXISTS ix_{PREFIX}visits_load_date "
            f"ON {VISITS}(load_date)"
        )
        if "ym_s_clientid" in cols:
            cur.execute(
                f"CREATE INDEX IF NOT EXISTS ix_{PREFIX}visits_clientid "
                f"ON {VISITS}(ym_s_clientid)"
            )
    conn.commit()
    return cols


def open_tsv(path):
    if path.endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", newline="")
    return open(path, "r", encoding="utf-8", newline="")


def split_by_day(path, tmpdir):
    """Split TSV into per-day files. Returns (header, {day: filepath, ...})."""
    with open_tsv(path) as f:
        header_line = f.readline().rstrip("\n").rstrip("\r")
        header = header_line.split("\t")
        try:
            didx = header.index("ym:s:date")
        except ValueError:
            raise RuntimeError("column ym:s:date not found in header")
        handles, paths, counts = {}, {}, {}
        bad = 0
        for line in f:
            line = line.rstrip("\n").rstrip("\r")
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) != len(header):
                bad += 1
                continue
            day = parts[didx]
            if day not in handles:
                p = os.path.join(tmpdir, f"day_{day}.tsv")
                paths[day] = p
                handles[day] = open(p, "w", encoding="utf-8", newline="")
                counts[day] = 0
            handles[day].write(line + "\t" + day + "\n")
            counts[day] += 1
        for h in handles.values():
            h.close()
    if bad:
        log(f"WARNING: skipped {bad} malformed row(s)")
    for d in sorted(counts):
        log(f"  {d}: {counts[d]} rows")
    return header, paths


def copy_day(conn, day, filepath, cols):
    collist = ", ".join(f'"{c}"' for c in cols) + ", load_date"
    sql = (
        f"COPY {VISITS} ({collist}) FROM STDIN WITH "
        "(FORMAT csv, DELIMITER E'\\t', QUOTE E'\\x01', NULL '')"
    )
    with conn.cursor() as cur:
        cur.execute(f"DELETE FROM {VISITS} WHERE load_date = %s", (day,))
        deleted = cur.rowcount
        with open(filepath, "r", encoding="utf-8", newline="") as f:
            cur.copy_expert(sql, f)
        cur.execute(f"SELECT count(*) FROM {VISITS} WHERE load_date = %s", (day,))
        loaded = cur.fetchone()[0]
    conn.commit()
    log(f"  {day}: deleted {deleted}, loaded {loaded}")
    return loaded


def load_tsv(path):
    log(f"loading file: {path}")
    conn = connect()
    total = 0
    with tempfile.TemporaryDirectory(prefix="metrica_") as tmp:
        header, paths = split_by_day(path, tmp)
        cols = ensure_table(conn, header)
        os.makedirs("state", exist_ok=True)
        with open(FIELDS_STATE, "w", encoding="utf-8") as f:
            json.dump(header, f, ensure_ascii=False, indent=1)
        for day in sorted(paths):
            total += copy_day(conn, day, paths[day], cols)
    conn.close()
    return total


def req(method, url, **kw):
    last = None
    for attempt in range(5):
        try:
            r = requests.request(method, url, headers=HEAD, timeout=120, **kw)
            if r.status_code >= 500:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
            return r
        except Exception as e:
            last = e
            wait = 2 ** attempt
            log(f"  retry {attempt + 1}/5 in {wait}s — {type(e).__name__}")
            time.sleep(wait)
    raise RuntimeError(f"network failed after 5 attempts: {last}")


def load_fields():
    if not os.path.exists(FIELDS_STATE):
        raise RuntimeError(
            f"{FIELDS_STATE} not found. Run --from-file backfill first "
            "to capture the accepted field list."
        )
    with open(FIELDS_STATE, "r", encoding="utf-8") as f:
        return json.load(f)


def create_logrequest(d1, d2, fields):
    fields = list(fields)
    for _ in range(20):
        r = req(
            "POST",
            f"{API}/management/v1/counter/{COUNTER}/logrequests",
            params={
                "date1": d1,
                "date2": d2,
                "source": "visits",
                "fields": ",".join(fields),
            },
        )
        if r.status_code == 200:
            return r.json()["log_request"]["request_id"], fields
        text = r.text
        rejected = [f for f in fields if f in text]
        if not rejected:
            raise RuntimeError(f"logrequest failed: HTTP {r.status_code} {text[:500]}")
        for f in rejected:
            log(f"  API rejected field {f} — removing")
            fields.remove(f)
    raise RuntimeError("too many rejected fields")


def api_pull(d1, d2):
    fields = load_fields()
    log(f"API pull {d1}..{d2}, fields={len(fields)}")
    request_id, fields = create_logrequest(d1, d2, fields)
    log(f"  request_id={request_id}")
    parts = None
    t0 = time.time()
    while True:
        r = req("GET", f"{API}/management/v1/counter/{COUNTER}/logrequest/{request_id}")
        info = r.json()["log_request"]
        status = info["status"]
        if status == "processed":
            parts = len(info.get("parts", []))
            log(f"  [{int(time.time() - t0)}s] processed, parts: {parts}")
            break
        if status in ("processing_failed", "canceled"):
            raise RuntimeError(f"logrequest status={status}")
        log(f"  [{int(time.time() - t0)}s] {status} ...")
        time.sleep(11)
    fd, tmp_path = tempfile.mkstemp(suffix=".tsv", prefix="metrica_api_")
    os.close(fd)
    with open(tmp_path, "w", encoding="utf-8", newline="") as out:
        for n in range(parts):
            r = req(
                "GET",
                f"{API}/management/v1/counter/{COUNTER}/logrequest/"
                f"{request_id}/part/{n}/download",
            )
            text = r.content.decode("utf-8")
            if n > 0:
                text = text.split("\n", 1)[1] if "\n" in text else ""
            out.write(text)
            if not text.endswith("\n"):
                out.write("\n")
            log(f"  part {n + 1}/{parts} downloaded")
    try:
        total = load_tsv(tmp_path)
    finally:
        os.remove(tmp_path)
        req(
            "POST",
            f"{API}/management/v1/counter/{COUNTER}/logrequest/{request_id}/clean",
        )
    return total


def days_to_load(conn):
    yesterday = date.today() - timedelta(days=1)
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s)", (VISITS,))
        if cur.fetchone()[0] is None:
            return [date.fromisoformat(BACKFILL_START), yesterday]
        cur.execute(f"SELECT max(load_date) FROM {VISITS}")
        mx = cur.fetchone()[0]
    if mx is None:
        return [date.fromisoformat(BACKFILL_START), yesterday]
    start = min(mx - timedelta(days=RELOAD_WINDOW - 1), yesterday)
    return [start, yesterday]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-file")
    ap.add_argument("--date1")
    ap.add_argument("--date2")
    args = ap.parse_args()

    if args.from_file:
        stage, run_id = "ingest_file", journal_start("ingest_file")
        try:
            total = load_tsv(args.from_file)
            journal_end(run_id, "ok", total)
            log(f"DONE {stage}: {total} rows")
            return 0
        except Exception as e:
            journal_end(run_id, "fail", None, e)
            log(f"FAIL {stage}: {type(e).__name__}: {e}")
            return 1

    if args.date1 and args.date2:
        d1, d2 = args.date1, args.date2
    else:
        conn = connect()
        rng = days_to_load(conn)
        conn.close()
        d1, d2 = rng[0].isoformat(), rng[1].isoformat()
        if rng[0] > rng[1]:
            log("nothing to load")
            return 0

    run_id = journal_start("ingest_api")
    try:
        total = api_pull(d1, d2)
        journal_end(run_id, "ok", total)
        log(f"DONE ingest_api {d1}..{d2}: {total} rows")
        return 0
    except Exception as e:
        journal_end(run_id, "fail", None, e)
        log(f"FAIL ingest_api: {type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())