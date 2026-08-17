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

D1, D2 = "2026-07-01", "2026-07-31"


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def connect():
    return psycopg2.connect(
        host=os.environ["PG_HOST"], port=int(os.environ["PG_PORT"]),
        dbname=os.environ["PG_DB"], user=os.environ["PG_USER"],
        password=os.environ["PG_PASSWORD"], connect_timeout=30,
    )


def main():
    p = {"d1": D1, "d2": D2}
    conn = connect()
    cur = conn.cursor()

    print(f"\n[1] Качество clientID за {D1}..{D2}")
    cur.execute(
        "SELECT count(*), "
        "count(*) FILTER (WHERE ym_s_clientid IS NULL), "
        "count(*) FILTER (WHERE ym_s_clientid = ''), "
        "count(*) FILTER (WHERE ym_s_clientid = '0'), "
        "count(distinct ym_s_clientid) "
        f"FROM {VISITS} WHERE load_date BETWEEN %(d1)s AND %(d2)s", p,
    )
    total, isnull, isempty, iszero, uniq = [int(x) for x in cur.fetchone()]
    print(f"  визитов:        {total}")
    print(f"  clientID NULL:  {isnull}")
    print(f"  clientID '':    {isempty}")
    print(f"  clientID '0':   {iszero} ({100.0 * iszero / total:.2f}%)")
    print(f"  уникальных за месяц: {uniq}")

    print("\n[2] Визиты с clientID='0': попадают ли в воронку")
    cur.execute(
        "SELECT g.gid, count(*) FROM ("
        f"SELECT nullif(trim(both '[]' from coalesce(ym_s_goalsid,'')),'') AS gs "
        f"FROM {VISITS} WHERE load_date BETWEEN %(d1)s AND %(d2)s "
        "AND ym_s_clientid = '0') v "
        "CROSS JOIN LATERAL unnest(string_to_array(v.gs, ',')) AS g(gid) "
        "WHERE v.gs IS NOT NULL GROUP BY 1 ORDER BY 2 DESC", p,
    )
    hits = {r[0].strip(): int(r[1]) for r in cur.fetchall()}
    if not hits:
        print("  целей нет — на числа воронки не влияют")
    else:
        name_of = {g: n for n, g in FUNNEL}
        shown = 0
        for gid, n in sorted(hits.items(), key=lambda x: -x[1]):
            if gid in name_of:
                print(f"  {name_of[gid]:<30} {n:>7} визитов")
                shown += 1
        if shown == 0:
            print(f"  шагов воронки нет, прочих целей: {len(hits)}")
        else:
            print(f"\n  ВАЖНО: эти визиты считаются как ОДИН человек")

    print("\n[3] Сумма дневных уникальных против месячной")
    cur.execute(
        f"SELECT sum(u) FROM (SELECT count(distinct ym_s_clientid) AS u "
        f"FROM {VISITS} WHERE load_date BETWEEN %(d1)s AND %(d2)s "
        "GROUP BY load_date) t", p,
    )
    daily_sum = int(cur.fetchone()[0])
    print(f"  сумма по дням: {daily_sum}")
    print(f"  месячная уникальность: {uniq}")
    print(f"  коэффициент повторных заходов: {daily_sum / uniq:.3f}")

    print("\n[4] Распределение визитов на посетителя")
    cur.execute(
        "SELECT bucket, count(*) FROM (SELECT CASE "
        "WHEN n = 1 THEN '1' WHEN n <= 3 THEN '2-3' "
        "WHEN n <= 10 THEN '4-10' WHEN n <= 50 THEN '11-50' "
        "WHEN n <= 200 THEN '51-200' ELSE '200+' END AS bucket "
        f"FROM (SELECT ym_s_clientid, count(*) AS n FROM {VISITS} "
        "WHERE load_date BETWEEN %(d1)s AND %(d2)s "
        "AND ym_s_clientid <> '0' GROUP BY 1) x) y "
        "GROUP BY 1 ORDER BY 1", p,
    )
    for b, n in cur.fetchall():
        print(f"  {b:<8} {int(n):>9} посетителей")

    print("\n[5] Аномальные посетители (более 200 визитов, кроме '0')")
    cur.execute(
        "SELECT count(*), coalesce(sum(n), 0) FROM ("
        f"SELECT ym_s_clientid, count(*) AS n FROM {VISITS} "
        "WHERE load_date BETWEEN %(d1)s AND %(d2)s "
        "AND ym_s_clientid <> '0' GROUP BY 1 HAVING count(*) > 200) t", p,
    )
    cnt, vis = [int(x) for x in cur.fetchone()]
    print(f"  посетителей: {cnt}, их визитов: {vis} "
          f"({100.0 * vis / total:.2f}% от всех)")

    print("\n[6] Тяжёлые посетители 51-200 визитов: есть ли шаги воронки")
    reg = dict(FUNNEL)["REGISTRATION_PAGE_OK"]
    cur.execute(
        "SELECT count(*) FROM (SELECT v.cid FROM ("
        "SELECT ym_s_clientid AS cid, "
        "nullif(trim(both '[]' from coalesce(ym_s_goalsid,'')),'') AS gs "
        f"FROM {VISITS} WHERE load_date BETWEEN %(d1)s AND %(d2)s "
        "AND ym_s_clientid IN (SELECT ym_s_clientid FROM "
        f"{VISITS} WHERE load_date BETWEEN %(d1)s AND %(d2)s "
        "AND ym_s_clientid <> '0' GROUP BY 1 HAVING count(*) > 50)) v "
        "CROSS JOIN LATERAL unnest(string_to_array(v.gs, ',')) AS g(gid) "
        "WHERE v.gs IS NOT NULL AND g.gid = %(reg)s GROUP BY 1) t",
        {**p, "reg": reg},
    )
    n = int(cur.fetchone()[0])
    print(f"  из тяжёлых посетителей зарегистрировались: {n}")
    print("  (если близко к нулю — это боты или мониторинг, "
          "если сопоставимо — живые люди)")

    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())