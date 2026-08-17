import argparse
import json
import os
import sys
import time
from datetime import date, datetime, timedelta

import psycopg2
import requests
from dotenv import load_dotenv

load_dotenv()

SCHEMA = os.environ["PG_SCHEMA"]
PREFIX = os.environ["TABLE_PREFIX"]
VISITS = f"{SCHEMA}.{PREFIX}visits"
COUNTER = os.environ["METRICA_COUNTER_ID"]
TOKEN = os.environ["METRICA_TOKEN"]

API = "https://api-metrika.yandex.ru/stat/v1/data"
HEAD = {"Authorization": f"OAuth {TOKEN}"}


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def connect():
    return psycopg2.connect(
        host=os.environ["PG_HOST"], port=int(os.environ["PG_PORT"]),
        dbname=os.environ["PG_DB"], user=os.environ["PG_USER"],
        password=os.environ["PG_PASSWORD"], connect_timeout=30,
    )


def stat_query(params, label):
    """Reports API call. Prints raw error body so failures are diagnosable."""
    for attempt in range(3):
        try:
            r = requests.get(API, headers=HEAD, params=params, timeout=90)
        except Exception as e:
            log(f"  {label}: network error {type(e).__name__}, retry")
            time.sleep(3)
            continue
        if r.status_code == 200:
            return r.json()
        log(f"  {label}: HTTP {r.status_code}")
        log(f"    {r.text[:400]}")
        if r.status_code < 500:
            return None
        time.sleep(3)
    return None


def rows_by_date(js):
    out = {}
    if not js:
        return out
    for row in js.get("data", []):
        d = row["dimensions"][0].get("name") or row["dimensions"][0].get("id")
        out[d] = [int(x) for x in row["metrics"]]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--month", default="2026-07")
    args = ap.parse_args()

    mo = args.month
    y, m = int(mo[:4]), int(mo[5:7])
    d1 = date(y, m, 1)
    d2 = (date(y + (m == 12), (m % 12) + 1, 1) - timedelta(days=1))
    log(f"month {mo}: {d1} .. {d2}")

    base = {
        "ids": COUNTER,
        "metrics": "ym:s:visits,ym:s:users",
        "dimensions": "ym:s:date",
        "date1": d1.isoformat(),
        "date2": d2.isoformat(),
        "group": "day",
        "limit": 200,
        "accuracy": "full",
    }

    print("\n[1] Метрика, отчёт БЕЗ роботов (по умолчанию)")
    clean = rows_by_date(stat_query(base, "clean"))
    print(f"  дней получено: {len(clean)}")

    print("\n[2] Метрика, отчёт С роботами")
    withrobots = rows_by_date(
        stat_query({**base, "filters": "ym:s:isRobot=='Yes'"}, "robots-only")
    )
    print(f"  дней получено: {len(withrobots)}")

    print("\n[3] Наши числа из metrica_visits")
    conn = connect()
    cur = conn.cursor()
    cur.execute(
        f"SELECT load_date::text, count(*), count(distinct ym_s_clientid) "
        f"FROM {VISITS} WHERE load_date BETWEEN %s AND %s GROUP BY 1 ORDER BY 1",
        (d1.isoformat(), d2.isoformat()),
    )
    ours = {d: [v, u] for d, v, u in cur.fetchall()}
    print(f"  дней в базе: {len(ours)}")

    print("\n[4] Сопоставление по дням (визиты)")
    print(f"  {'дата':<12} {'наши':>9} {'Метрика':>9} {'разница':>9} {'роботы':>8}")
    print("  " + "-" * 54)
    tot_ours = tot_met = tot_rob = 0
    for d in sorted(ours):
        o = ours[d][0]
        mv = clean.get(d, [0, 0])[0]
        rb = withrobots.get(d, [0, 0])[0]
        tot_ours += o
        tot_met += mv
        tot_rob += rb
        diff = 100.0 * (o - mv) / mv if mv else 0
        print(f"  {d:<12} {o:>9} {mv:>9} {diff:>8.2f}% {rb:>8}")
    print("  " + "-" * 54)
    if tot_met:
        print(f"  {'ИТОГО':<12} {tot_ours:>9} {tot_met:>9} "
              f"{100.0 * (tot_ours - tot_met) / tot_met:>8.2f}% {tot_rob:>8}")
        print(f"\n  роботов по данным Метрики: {tot_rob} "
              f"({100.0 * tot_rob / tot_met:.2f}% от чистых визитов)")

    print("\n[5] Уникальные посетители")
    ou = sum(v[1] for v in ours.values())
    mu = sum(v[1] for v in clean.values())
    print(f"  наши (сумма по дням):    {ou}")
    print(f"  Метрика (сумма по дням): {mu}")
    if mu:
        print(f"  разница: {100.0 * (ou - mu) / mu:+.2f}%")

    print("\n[6] Есть ли признак робота в нашей выгрузке")
    cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema=%s AND table_name=%s "
        "AND column_name LIKE '%%robot%%'",
        (SCHEMA, f"{PREFIX}visits"),
    )
    cols = [r[0] for r in cur.fetchall()]
    if cols:
        print(f"  найдено: {cols}")
        for c in cols:
            cur.execute(
                f'SELECT "{c}", count(*) FROM {VISITS} '
                "WHERE load_date BETWEEN %s AND %s GROUP BY 1 ORDER BY 2 DESC",
                (d1.isoformat(), d2.isoformat()),
            )
            for v, n in cur.fetchall():
                print(f"    {c}={v!r}: {n}")
    else:
        print("  признака робота в выгрузке нет — фильтровать нечем")

    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())