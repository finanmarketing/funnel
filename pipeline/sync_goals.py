import json
import os
import sys
import time
from datetime import datetime

import psycopg2
import requests
from dotenv import load_dotenv

load_dotenv()

SCHEMA = os.environ["PG_SCHEMA"]
PREFIX = os.environ["TABLE_PREFIX"]
COUNTER = os.environ["METRICA_COUNTER_ID"]
TOKEN = os.environ["METRICA_TOKEN"]

GOALS = f"{SCHEMA}.{PREFIX}goals_dict"
RUNS = f"{SCHEMA}.{PREFIX}pipeline_runs"
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


def fetch_goals():
    last = None
    for attempt in range(5):
        try:
            r = requests.get(
                f"{API}/management/v1/counter/{COUNTER}/goals",
                headers=HEAD,
                params={"useDeleted": "true"},
                timeout=60,
            )
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
            return r.json().get("goals", [])
        except Exception as e:
            last = e
            wait = 2 ** attempt
            log(f"  retry {attempt + 1}/5 in {wait}s — {type(e).__name__}")
            time.sleep(wait)
    raise RuntimeError(f"network failed after 5 attempts: {last}")


def main():
    run_id = journal_start("sync_goals")
    try:
        goals = fetch_goals()
        log(f"fetched {len(goals)} goals")
        conn = connect()
        with conn.cursor() as cur:
            for g in goals:
                cur.execute(
                    f"INSERT INTO {GOALS}"
                    "(goal_id,name,goal_type,status,is_retargeting,raw,synced_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,now()) "
                    "ON CONFLICT (goal_id) DO UPDATE SET "
                    "name=EXCLUDED.name, goal_type=EXCLUDED.goal_type, "
                    "status=EXCLUDED.status, is_retargeting=EXCLUDED.is_retargeting, "
                    "raw=EXCLUDED.raw, synced_at=now()",
                    (
                        g["id"],
                        g.get("name"),
                        g.get("type"),
                        g.get("status"),
                        bool(g.get("is_retargeting")),
                        json.dumps(g, ensure_ascii=False),
                    ),
                )
        conn.commit()
        with conn.cursor() as cur:
            cur.execute(f"SELECT count(*) FROM {GOALS}")
            total = cur.fetchone()[0]
        conn.close()
        journal_end(run_id, "ok", total)
        log(f"DONE sync_goals: {total} rows in dictionary")
        return 0
    except Exception as e:
        journal_end(run_id, "fail", None, e)
        log(f"FAIL sync_goals: {type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())