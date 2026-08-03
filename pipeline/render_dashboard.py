import json
import os
import shutil
import sys
from datetime import date, datetime, timedelta

import psycopg2
from dotenv import load_dotenv

load_dotenv()

SCHEMA = os.environ["PG_SCHEMA"]
PREFIX = os.environ["TABLE_PREFIX"]
RUNS = f"{SCHEMA}.{PREFIX}pipeline_runs"

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE = os.path.join(BASE_DIR, "pipeline", "dashboard_template.html")
PAYLOAD = os.path.join(BASE_DIR, "state", "payload.json")
OUTDIR = os.path.join(BASE_DIR, "out")

START_MARK = "const DATA="
END_MARK = "const SL="


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


def main():
    run_id = journal_start("render_dashboard")
    try:
        with open(TEMPLATE, "r", encoding="utf-8") as f:
            tpl = f.read()
        log(f"template loaded: {len(tpl)} bytes")

        if tpl.count(START_MARK) != 1:
            raise RuntimeError(f"expected exactly 1 '{START_MARK}', "
                               f"found {tpl.count(START_MARK)}")
        i = tpl.index(START_MARK)
        j = tpl.index(END_MARK, i)
        k = tpl.rindex(";", i, j)
        log(f"DATA block: {i}..{k}, {k - i} bytes to replace")

        with open(PAYLOAD, "r", encoding="utf-8") as f:
            payload = json.load(f)
        period = payload["period"]
        log(f"payload period: {period[0]} .. {period[1]} ({period[2]} days)")

        yesterday = (date.today() - timedelta(days=1)).isoformat()
        if period[1] != yesterday:
            raise RuntimeError(f"payload ends {period[1]}, expected {yesterday}")

        data_js = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        out_html = tpl[:i] + START_MARK + data_js + tpl[k:]

        if out_html.count(START_MARK) != 1:
            raise RuntimeError("post-check: DATA marker count != 1")
        size = len(out_html.encode("utf-8"))
        if not (1_000_000 < size < 8_000_000):
            raise RuntimeError(f"post-check: suspicious size {size} bytes")

        os.makedirs(OUTDIR, exist_ok=True)
        dated = os.path.join(OUTDIR, f"funnel_dashboard_{period[1]}.html")
        latest = os.path.join(OUTDIR, "funnel_dashboard_latest.html")
        with open(dated, "w", encoding="utf-8") as f:
            f.write(out_html)
        shutil.copyfile(dated, latest)

        journal_end(run_id, "ok", size)
        log(f"DONE render_dashboard: {dated} ({size / 1024 / 1024:.2f} MB)")
        log(f"latest: {latest}")
        return 0
    except Exception as e:
        journal_end(run_id, "fail", None, e)
        log(f"FAIL render_dashboard: {type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())