import glob
import os
import socket
import subprocess
import sys
import time
from datetime import date, datetime, timedelta

import psycopg2
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PIPE_DIR = os.path.join(BASE_DIR, "pipeline")
LOG_DIR = os.path.join(BASE_DIR, "logs")
LOG_KEEP_DAYS = int(os.environ.get("LOG_KEEP_DAYS", "7"))

PG_HOST = os.environ["PG_HOST"]
PG_PORT = int(os.environ["PG_PORT"])
SCHEMA = os.environ["PG_SCHEMA"]
PREFIX = os.environ["TABLE_PREFIX"]
RUNS = f"{SCHEMA}.{PREFIX}pipeline_runs"

NET_WAIT_SECONDS = int(os.environ.get("NET_WAIT_SECONDS", "1800"))
NET_PROBE_INTERVAL = 30
STAGE_ATTEMPTS = int(os.environ.get("STAGE_ATTEMPTS", "3"))
STALE_RUN_HOURS = int(os.environ.get("STALE_RUN_HOURS", "6"))

STAGES = [
    ("sync_goals", "sync_goals.py", []),
    ("ingest_metrica", "ingest_metrica.py", []),
    ("build_payload", "build_payload.py", []),
    ("render_dashboard", "render_dashboard.py", []),
    ("deliver", "deliver.py", []),
]


def tcp_alive(host, port, timeout=5):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def wait_for_db(emit, label):
    if tcp_alive(PG_HOST, PG_PORT):
        return True
    emit(f"network DOWN ({PG_HOST}:{PG_PORT}) before {label} — waiting up to "
         f"{NET_WAIT_SECONDS // 60} min. Connect corporate VPN.")
    t0 = time.time()
    while time.time() - t0 < NET_WAIT_SECONDS:
        time.sleep(NET_PROBE_INTERVAL)
        if tcp_alive(PG_HOST, PG_PORT):
            emit(f"network UP after {int(time.time() - t0)}s — continuing")
            return True
        waited = int(time.time() - t0)
        if waited % 300 < NET_PROBE_INTERVAL:
            emit(f"  still waiting for network... {waited}s")
    emit(f"network still DOWN after {NET_WAIT_SECONDS}s — giving up")
    return False


def cleanup_stale_runs(emit):
    try:
        conn = psycopg2.connect(
            host=PG_HOST, port=PG_PORT, dbname=os.environ["PG_DB"],
            user=os.environ["PG_USER"], password=os.environ["PG_PASSWORD"],
            connect_timeout=15,
        )
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE {RUNS} SET status='orphaned', finished_at=now(), "
                "error_text='no finish record: process killed or network lost' "
                "WHERE status='start' AND started_at < now() - %s::interval",
                (f"{STALE_RUN_HOURS} hours",),
            )
            n = cur.rowcount
        conn.close()
        emit(f"stale runs marked orphaned: {n}")
    except Exception as e:
        emit(f"stale cleanup skipped: {type(e).__name__}: {e}")


def cleanup_logs(emit):
    cutoff = time.time() - LOG_KEEP_DAYS * 86400
    removed = 0
    for path in glob.glob(os.path.join(LOG_DIR, "run_*.log")):
        try:
            if os.path.getmtime(path) < cutoff:
                os.remove(path)
                removed += 1
        except OSError as e:
            emit(f"cleanup skip {os.path.basename(path)}: {e}")
    emit(f"log cleanup: removed {removed} file(s) older than {LOG_KEEP_DAYS}d")


def notify_failure(emit, stage, err):
    try:
        sys.path.insert(0, PIPE_DIR)
        from deliver import deliver_failure
        deliver_failure(stage, err)
        emit("failure notification sent")
    except Exception as e:
        emit(f"failure notification error: {type(e).__name__}: {e}")


def run_stage(emit, name, script, extra):
    path = os.path.join(PIPE_DIR, script)
    last_err = ""
    for attempt in range(1, STAGE_ATTEMPTS + 1):
        if not wait_for_db(emit, f"{name} (attempt {attempt})"):
            last_err = "network unavailable: corporate VPN is down"
            break
        emit(f"--- stage: {name} (attempt {attempt}/{STAGE_ATTEMPTS}) ---")
        s0 = time.time()
        proc = subprocess.run(
            [sys.executable, path] + extra,
            cwd=BASE_DIR, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
        for line in (proc.stdout or "").splitlines():
            emit(f"    {line}")
        if proc.returncode == 0:
            emit(f"--- stage {name} ok in {time.time() - s0:.0f}s ---")
            return True, ""
        for line in (proc.stderr or "").splitlines():
            emit(f"  ERR {line}")
        last_err = (proc.stderr or proc.stdout or "")[-1500:]
        emit(f"--- stage {name} FAILED (rc={proc.returncode}) after "
             f"{time.time() - s0:.0f}s ---")
        if attempt < STAGE_ATTEMPTS:
            emit(f"retrying {name} in 60s")
            time.sleep(60)
    return False, last_err


def main():
    os.makedirs(LOG_DIR, exist_ok=True)
    day = (date.today() - timedelta(days=1)).isoformat()
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    logfile = os.path.join(LOG_DIR, f"run_{stamp}.log")
    t0 = time.time()

    def emit(msg):
        line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
        print(line, flush=True)
        with open(logfile, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    emit(f"=== PIPELINE START (target day: {day}) ===")
    emit(f"python: {sys.executable}")
    emit(f"cwd: {BASE_DIR}")
    emit(f"log: {logfile}")
    cleanup_logs(emit)

    if not wait_for_db(emit, "startup"):
        emit("=== PIPELINE ABORTED: no network ===")
        notify_failure(emit, "startup",
                       "Корпоративный VPN не поднялся за "
                       f"{NET_WAIT_SECONDS // 60} минут. Пайплайн не запускался.")
        return 1

    cleanup_stale_runs(emit)

    for name, script, extra in STAGES:
        ok, err = run_stage(emit, name, script, extra)
        if not ok:
            emit(f"=== PIPELINE FAILED at stage: {name} ===")
            if name != "deliver":
                notify_failure(emit, name, err)
            return 1

    emit(f"=== PIPELINE OK in {time.time() - t0:.0f}s ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())