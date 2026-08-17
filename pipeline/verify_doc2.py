import os
import sys

import psycopg2
from dotenv import load_dotenv

from funnel_goals import FUNNEL, PWA, RECOVERY

load_dotenv()

S = os.environ["PG_SCHEMA"]
P = os.environ["TABLE_PREFIX"]
V = f"{S}.{P}visits"
M = f"{S}.{P}person_map"

SUSP = ("adm.", "test", "stage", "dev", "localhost", "moneza", "finlove")
COND = " OR ".join(f"lower(url) LIKE '%%{s}%%'" for s in SUSP)

Q = f"""
WITH v AS (
  SELECT coalesce(m.pkey,'br:'||t.ym_s_clientid) AS pkey,
         t.ym_s_starturl AS url,
         nullif(trim(both '[]' from coalesce(t.ym_s_goalsid,'')),'') AS gs
  FROM {V} t LEFT JOIN {M} m ON m.cid=t.ym_s_clientid
  WHERE t.load_date BETWEEN %(d1)s AND %(d2)s)
SELECT count(DISTINCT v.pkey) FROM v
CROSS JOIN LATERAL unnest(string_to_array(v.gs,',')) AS g(gid)
WHERE v.gs IS NOT NULL AND g.gid=%(g)s
"""


def conn():
    return psycopg2.connect(
        host=os.environ["PG_HOST"], port=int(os.environ["PG_PORT"]),
        dbname=os.environ["PG_DB"], user=os.environ["PG_USER"],
        password=os.environ["PG_PASSWORD"], connect_timeout=60)


def n(cur, gid, d1, d2, extra=""):
    cur.execute(Q + extra, {"d1": d1, "d2": d2, "g": gid})
    return cur.fetchone()[0]


def chain(cur, ch, d1, d2, title):
    print(f"\n  {title}  ({d1}..{d2})")
    base = n(cur, ch[0][1], d1, d2)
    for nm, gid in ch:
        c = n(cur, gid, d1, d2)
        pct = 100.0 * c / base if base else 0
        print(f"    {nm:<24} {c:>8} {pct:>6.1f}%")


def main():
    print("START", flush=True)
    c = conn()
    cur = c.cursor()

    print("\n[1] Восстановление доступа")
    chain(cur, RECOVERY, "2026-07-03", "2026-07-31", "общее покрытие целей")
    chain(cur, RECOVERY, "2026-07-01", "2026-07-31", "весь июль")

    print("\n[2] PWA, июль")
    chain(cur, PWA, "2026-07-01", "2026-07-31", "покрытие полное")

    print("\n[3] Посторонние домены внутри воронки (июль, люди)")
    g = dict(FUNNEL)
    for nm in ("MAIN_PAGE_LOADED", "CONFIRM_PAGE_OK"):
        tot = n(cur, g[nm], "2026-07-01", "2026-07-31")
        sus = n(cur, g[nm], "2026-07-01", "2026-07-31",
                f" AND ({COND})")
        pct = 100.0 * sus / tot if tot else 0
        print(f"  {nm:<20} всего {tot:>8}, "
              f"с посторонних {sus:>6} ({pct:.2f}%)")

    cur.close()
    c.close()
    print("\nDONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())