import os
import sys
from datetime import datetime

import psycopg2
from dotenv import load_dotenv

load_dotenv()

SCHEMA = os.environ["PG_SCHEMA"]
PREFIX = os.environ["TABLE_PREFIX"]
VISITS = f"{SCHEMA}.{PREFIX}visits"
MAP = f"{SCHEMA}.{PREFIX}person_map"
RUNS = f"{SCHEMA}.{PREFIX}pipeline_runs"

REBUILD = f"""
WITH v AS (
  SELECT ym_s_clientid AS cid,
         string_to_array(regexp_replace(coalesce(ym_s_parsedparamskey1,''),
           '[\\[\\]'']', '', 'g'), ',') AS k1,
         string_to_array(regexp_replace(coalesce(ym_s_parsedparamskey2,''),
           '[\\[\\]'']', '', 'g'), ',') AS k2,
         string_to_array(regexp_replace(coalesce(ym_s_parsedparamskey3,''),
           '[\\[\\]'']', '', 'g'), ',') AS k3
  FROM {VISITS}
),
w AS (
  SELECT v.cid,
         (SELECT min(CASE
            WHEN trim(v.k1[i]) = 'UserID' AND trim(v.k2[i]) ~ '^[0-9]+$'
                 THEN trim(v.k2[i])
            WHEN trim(v.k1[i]) = 'params' AND trim(v.k2[i]) = 'UserID'
                 AND trim(v.k3[i]) ~ '^[0-9]+$' THEN trim(v.k3[i])
          END)
          FROM generate_subscripts(v.k1, 1) AS i) AS uid
  FROM v
),
agg AS (
  SELECT cid, min(uid) AS uid, count(*) AS visits_seen
  FROM w GROUP BY 1
)
INSERT INTO {MAP} (cid, uid, pkey, visits_seen, updated_at)
SELECT cid, uid, coalesce(uid, 'br:' || cid), visits_seen, now()
FROM agg
ON CONFLICT (cid) DO UPDATE SET
  uid = EXCLUDED.uid,
  pkey = EXCLUDED.pkey,
  visits_seen = EXCLUDED.visits_seen,
  updated_at = now()
"""


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def connect():
    return psycopg2.connect(
        host=os.environ["PG_HOST"], port=int(os.environ["PG_PORT"]),
        dbname=os.environ["PG_DB"], user=os.environ["PG_USER"],
        password=os.environ["PG_PASSWORD"], connect_timeout=30,
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
    run_id = journal_start("build_person_map")
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass(%s)", (MAP,))
            if cur.fetchone()[0] is None:
                raise RuntimeError(
                    f"table {MAP} does not exist. Apply migration first: "
                    "python pipeline\\apply_sql.py "
                    "pipeline\\sql\\02_create_person_map.sql"
                )
            log("rebuilding person map from all visits...")
            cur.execute(REBUILD)
            touched = cur.rowcount
        conn.commit()
        log(f"rows upserted: {touched}")

        with conn.cursor() as cur:
            cur.execute(
                f"SELECT count(*), count(uid), count(distinct uid), "
                f"count(distinct pkey) FROM {MAP}"
            )
            total, with_uid, uids, pkeys = cur.fetchone()
            cur.execute(
                f"SELECT count(*) FROM {MAP} m "
                "JOIN public.clients c ON c.client_number::text = m.uid"
            )
            in_clients = cur.fetchone()[0]
        conn.commit()

        log(f"browsers total:        {total}")
        log(f"  with UserID:         {with_uid} "
            f"({100.0 * with_uid / total:.1f}%)")
        log(f"  unique persons:      {pkeys}")
        log(f"  unique UserID:       {uids}")
        log(f"  UserID matched in clients: {in_clients} of {with_uid} "
            f"({100.0 * in_clients / with_uid if with_uid else 0:.1f}%)")
        log(f"  collapse ratio:      "
            f"{100.0 * (total - pkeys) / total:.1f}% browsers merged")

        if with_uid and in_clients / with_uid < 0.99:
            raise RuntimeError(
                f"QC: only {100.0 * in_clients / with_uid:.1f}% of UserID "
                "found in public.clients, expected >=99%"
            )

        conn.close()
        journal_end(run_id, "ok", total)
        log("DONE build_person_map")
        return 0
    except Exception as e:
        try:
            conn.rollback()
            conn.close()
        except Exception:
            pass
        journal_end(run_id, "fail", None, e)
        log(f"FAIL build_person_map: {type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())