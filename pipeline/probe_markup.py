import os
import sys
from datetime import datetime

import psycopg2
from dotenv import load_dotenv

from funnel_goals import FUNNEL

load_dotenv()

SCHEMA = os.environ["PG_SCHEMA"]
PREFIX = os.environ["TABLE_PREFIX"]
VISITS = f"{SCHEMA}.{PREFIX}visits"
PMAP = f"{SCHEMA}.{PREFIX}person_map"

D1, D2 = "2026-05-01", "2026-08-13"

# A -> B where B is technically unavoidable right after A.
# MOBILE_VERIFICATION_PAGE_OK -> CONFIRM_PAGE is NOT here: the confirm
# screen is a real drop-off point, not a forced transition.
PAIRS = [
    ("REGISTRATION_PAGE_OK", "PASSPORT_PAGE"),
    ("PASSPORT_PAGE_OK", "ADDRESS_PAGE"),
    ("ADDRESS_PAGE_OK", "ADDITIONAL_INFO_PAGE"),
    ("ADDITIONAL_INFO_PAGE_OK", "IDENTIFICATION_PAGE"),
    ("IDENTIFICATION_PAGE_OK", "MOBILE_VERIFICATION_PAGE"),
]

# The OK event implies its own screen was shown.
SELF = [
    ("REGISTRATION_PAGE_OK", "REGISTRATION_PAGE"),
    ("PASSPORT_PAGE_OK", "PASSPORT_PAGE"),
    ("ADDRESS_PAGE_OK", "ADDRESS_PAGE"),
    ("ADDITIONAL_INFO_PAGE_OK", "ADDITIONAL_INFO_PAGE"),
    ("IDENTIFICATION_PAGE_OK", "IDENTIFICATION_PAGE"),
    ("MOBILE_VERIFICATION_PAGE_OK", "MOBILE_VERIFICATION_PAGE"),
    ("CONFIRM_PAGE_OK", "CONFIRM_PAGE"),
]

GID = dict(FUNNEL)


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def connect():
    return psycopg2.connect(
        host=os.environ["PG_HOST"], port=int(os.environ["PG_PORT"]),
        dbname=os.environ["PG_DB"], user=os.environ["PG_USER"],
        password=os.environ["PG_PASSWORD"], connect_timeout=60,
    )


def prepare(conn, d1, d2):
    cur = conn.cursor()
    for t in ("pm_e",):
        cur.execute(f"DROP TABLE IF EXISTS {t}")
    conn.commit()
    cur.execute(
        f"""
        CREATE TEMP TABLE pm_e AS
        WITH v AS (
          SELECT coalesce(pm.pkey, 'br:' || t.ym_s_clientid) AS pkey,
                 to_char(t.load_date, 'YYYY-MM') AS mo,
                 nullif(trim(both '[]' from coalesce(t.ym_s_goalsid,'')),'')
                   AS gs
          FROM {VISITS} t
          LEFT JOIN {PMAP} pm ON pm.cid = t.ym_s_clientid
          WHERE t.load_date BETWEEN %(d1)s AND %(d2)s
        )
        SELECT DISTINCT v.pkey, v.mo, g.gid
        FROM v CROSS JOIN LATERAL unnest(string_to_array(v.gs, ',')) AS g(gid)
        WHERE v.gs IS NOT NULL
        """,
        {"d1": d1, "d2": d2},
    )
    conn.commit()
    cur.execute("CREATE INDEX ON pm_e (mo, gid)")
    cur.execute("CREATE INDEX ON pm_e (pkey, mo)")
    cur.execute("ANALYZE pm_e")
    conn.commit()
    cur.execute("SELECT count(*) FROM pm_e")
    log(f"  materialized: {cur.fetchone()[0]} person-month-goal rows")
    cur.close()


def measure(cur, a, b, title):
    """Both A and B are evaluated INSIDE the same month for the same person."""
    cur.execute(
        "SELECT x.mo, count(*) AS have_a, "
        "count(*) FILTER (WHERE y.pkey IS NOT NULL) AS have_both "
        "FROM (SELECT pkey, mo FROM pm_e WHERE gid = %(ga)s) x "
        "LEFT JOIN (SELECT pkey, mo FROM pm_e WHERE gid = %(gb)s) y "
        "ON y.pkey = x.pkey AND y.mo = x.mo "
        "GROUP BY 1 ORDER BY 1",
        {"ga": GID[a], "gb": GID[b]},
    )
    rows = cur.fetchall()
    print(f"\n  {title}")
    print(f"    {'месяц':<10} {'сделали A':>10} {'видели B':>10} {'потеря':>8}")
    losses = []
    for mo, ha, hb in rows:
        loss = 100.0 * (ha - hb) / ha if ha else 0
        losses.append((mo, loss))
        print(f"    {mo:<10} {ha:>10} {hb:>10} {loss:>7.2f}%")
    if losses:
        vals = [x[1] for x in losses]
        print(f"    среднее {sum(vals) / len(vals):.2f}%, "
              f"разброс {min(vals):.2f}%..{max(vals):.2f}%")
    return losses


def main():
    conn = connect()
    log(f"period {D1}..{D2}")
    log("preparing person-month-goal table...")
    prepare(conn, D1, D2)
    cur = conn.cursor()

    print("\n[0] Контроль: числа шагов должны совпасть с payload")
    for name in ("REGISTRATION_PAGE_OK", "CONFIRM_PAGE_OK"):
        cur.execute(
            "SELECT mo, count(*) FROM pm_e WHERE gid = %s GROUP BY 1 ORDER BY 1",
            (GID[name],),
        )
        vals = ", ".join(f"{m}={n}" for m, n in cur.fetchall())
        print(f"  {name}: {vals}")
    print("  (сверь с payload: май REG_OK=39119, июнь=29995)")

    print("\n[1] Обязательные переходы: экран следующего шага не записан")
    summary = {}
    for a, b in PAIRS:
        summary[(a, b)] = measure(cur, a, b, f"{a} -> {b}")

    print("\n[2] Свой экран не записан при отправке формы")
    self_sum = {}
    for a, b in SELF:
        self_sum[(a, b)] = measure(cur, a, b, f"{a} -> {b}")

    print("\n[3] Сводка")
    print(f"  {'проверка':<56} {'среднее':>9} {'макс':>8}")
    print("  " + "-" * 76)
    worst = 0.0
    for group in (summary, self_sum):
        for (a, b), losses in group.items():
            if not losses:
                continue
            vals = [x[1] for x in losses]
            avg, mx = sum(vals) / len(vals), max(vals)
            worst = max(worst, mx)
            print(f"  {a + ' -> ' + b:<56} {avg:>8.2f}% {mx:>7.2f}%")
    print(f"\n  Максимальная потеря: {worst:.2f}%")

    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())