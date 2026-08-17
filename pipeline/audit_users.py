import os
import sys
from datetime import datetime

import psycopg2
import requests
from dotenv import load_dotenv

load_dotenv()

S = os.environ["PG_SCHEMA"]
P = os.environ["TABLE_PREFIX"]
V = f"{S}.{P}visits"
M = f"{S}.{P}person_map"
D1, D2 = "2026-07-01", "2026-07-31"
API = "https://api-metrika.yandex.ru/stat/v1/data"
HEAD = {"Authorization": f"OAuth {os.environ['METRICA_TOKEN']}"}


def log(m):
    print(f"[{datetime.now():%H:%M:%S}] {m}", flush=True)


def conn():
    return psycopg2.connect(
        host=os.environ["PG_HOST"], port=int(os.environ["PG_PORT"]),
        dbname=os.environ["PG_DB"], user=os.environ["PG_USER"],
        password=os.environ["PG_PASSWORD"], connect_timeout=60)


def ask(params, label):
    try:
        r = requests.get(API, headers=HEAD, params=params, timeout=90)
    except Exception as e:
        print(f"  {label}: {type(e).__name__}")
        return None
    if r.status_code != 200:
        print(f"  {label}: HTTP {r.status_code} {r.text[:200]}")
        return None
    return r.json()


def main():
    print("START", flush=True)
    c = conn()
    cur = c.cursor()
    p = {"d1": D1, "d2": D2}

    base = {"ids": os.environ["METRICA_COUNTER_ID"],
            "date1": D1, "date2": D2, "accuracy": "full"}

    print("\n[1] Итоги за месяц: Метрика против нас")
    js = ask({**base, "metrics": "ym:s:users,ym:s:visits,ym:s:newUsers"},
             "totals")
    if js:
        u, v, nu = [int(x) for x in js["totals"]]
        print(f"  Метрика: users={u}, visits={v}, newUsers={nu}")
        cur.execute(
            f"SELECT count(*), count(DISTINCT ym_s_clientid) FROM {V} "
            "WHERE load_date BETWEEN %(d1)s AND %(d2)s", p)
        ov, ob = cur.fetchone()
        cur.execute(
            f"SELECT count(DISTINCT coalesce(m.pkey,'br:'||t.ym_s_clientid)) "
            f"FROM {V} t LEFT JOIN {M} m ON m.cid=t.ym_s_clientid "
            "WHERE t.load_date BETWEEN %(d1)s AND %(d2)s", p)
        op = cur.fetchone()[0]
        print(f"  наши:    визиты={ov}, браузеры={ob}, люди={op}")
        print(f"  визиты   {100.0*(ov-v)/v:+.2f}%")
        print(f"  браузеры {100.0*(ob-u)/u:+.2f}%")

    print("\n[2] По дням: сходятся ли дневные users")
    js = ask({**base, "metrics": "ym:s:users,ym:s:visits",
              "dimensions": "ym:s:date", "limit": 100, "group": "day"},
             "by day")
    if js:
        met = {}
        for row in js.get("data", []):
            d = row["dimensions"][0].get("name")
            met[d] = [int(x) for x in row["metrics"]]
        cur.execute(
            f"SELECT load_date::text, count(*), count(DISTINCT ym_s_clientid) "
            f"FROM {V} WHERE load_date BETWEEN %(d1)s AND %(d2)s "
            "GROUP BY 1 ORDER BY 1", p)
        ours = {d: [v, u] for d, v, u in cur.fetchall()}
        su = sm = 0
        print(f"  {'дата':<12} {'наши бр.':>9} {'Метрика':>9} {'разница':>9}")
        for d in sorted(ours):
            ou = ours[d][1]
            mu = met.get(d, [0, 0])[0]
            su += ou
            sm += mu
            if d[-2:] in ("01", "08", "15", "22", "29"):
                print(f"  {d:<12} {ou:>9} {mu:>9} "
                      f"{100.0*(ou-mu)/mu if mu else 0:>8.2f}%")
        print(f"  {'сумма дней':<12} {su:>9} {sm:>9} "
              f"{100.0*(su-sm)/sm if sm else 0:>8.2f}%")

    print("\n[3] Гипотеза: clientID='0' скрывает много людей")
    cur.execute(
        f"SELECT count(*), count(DISTINCT ym_s_visitid) FROM {V} "
        "WHERE load_date BETWEEN %(d1)s AND %(d2)s "
        "AND ym_s_clientid='0'", p)
    zv, zvis = cur.fetchone()
    print(f"  визитов с clientID='0': {zv} (уникальных visitID {zvis})")
    print(f"  максимум они могут скрывать {zvis-1} человек")

    print("\n[4] Гипотеза: дубли visitID (визит загружен дважды)")
    cur.execute(
        f"SELECT count(*), count(DISTINCT ym_s_visitid) FROM {V} "
        "WHERE load_date BETWEEN %(d1)s AND %(d2)s", p)
    tv, tvis = cur.fetchone()
    print(f"  визитов {tv}, уникальных visitID {tvis}, "
          f"дублей {tv-tvis}")

    print("\n[5] Гипотеза: часть визитов без clientID склеена")
    cur.execute(
        f"SELECT length(ym_s_clientid) AS l, count(*), "
        "count(DISTINCT ym_s_clientid) "
        f"FROM {V} WHERE load_date BETWEEN %(d1)s AND %(d2)s "
        "GROUP BY 1 ORDER BY 1", p)
    for l, n, u in cur.fetchall():
        print(f"  длина {l:>3}: визитов {n:>8}, уникальных {u:>8}")

    cur.close()
    c.close()
    print("\nDONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())