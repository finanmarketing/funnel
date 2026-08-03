import os

import psycopg2
from dotenv import load_dotenv

from build_payload import BASE, connect
from funnel_goals import FUNNEL

load_dotenv()

D1, D2 = "2026-07-01", "2026-07-30"
REG = dict(FUNNEL)["REGISTRATION_PAGE_OK"]
p = {"d1": D1, "d2": D2}

conn = connect()
cur = conn.cursor()

cur.execute(
    BASE + "SELECT count(distinct cid) FROM e WHERE gid = %(g)s",
    {**p, "g": REG},
)
got = cur.fetchone()[0]
print(f"REG_OK unique clientID {D1}..{D2}: {got}  "
      f"{'MATCH' if got == 34823 else 'DIFF ref=34823'}")

bits = ",".join(f"('{g}',{1 << i})" for i, (_, g) in enumerate(FUNNEL))
cur.execute(
    BASE + f"SELECT mask, count(*) FROM ("
    f"SELECT cid, bit_or(s.b) AS mask FROM e "
    f"JOIN (VALUES {bits}) AS s(gid,b) ON s.gid = e.gid GROUP BY 1) t "
    "GROUP BY 1",
    p,
)
ladder = [0] * len(FUNNEL)
for mask, cnt in cur.fetchall():
    d = 0
    while d < len(FUNNEL) and (mask >> d) & 1:
        d += 1
    for i in range(d):
        ladder[i] += cnt

refs = {2: 34034, 12: 19238, 13: 3653}
print(f"\nSTRICT ladder {D1}..{D2}:")
for i, (name, _) in enumerate(FUNNEL):
    r = refs.get(i)
    mark = "" if r is None else ("  MATCH" if ladder[i] == r else f"  DIFF ref={r}")
    print(f"  {i:>2} {name:<30} {ladder[i]}{mark}")
conn.close()