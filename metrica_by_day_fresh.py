#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ПЕРЕВЫГРУЗКА by_day НАЧИСТО — все цели, одним отчётом.

Зачем: прошлый goals_stat_by_day.tsv остался от v1 (поцелевой, упал на 91/215),
поэтому в нём только 90 целей и перекос на удалённые. Здесь тянем ВСЕ цели
разом через измерение ym:s:goalDimension — Reporting API сам разворачивает
каждую цель в отдельную строку. Мёртвые цели придут с нулями и отсеются сами.

Даёт metrica_dump/goals/goals_stat_by_day.tsv со столбцами:
    date, goal_id, goal_name, visits, goal_reaches, users

Период 2026-05-01 .. 2026-07-22. Токен из .env (METRICA_TOKEN).
Есть ретраи на таймаут и авто-дробление периода при "запрос слишком сложный".

Запуск:
    python metrica_by_day_fresh.py
"""

import os, sys, csv, time
import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

COUNTER = os.getenv("METRICA_COUNTER", "21703744")
TOKEN   = os.getenv("METRICA_TOKEN")
STAT    = "https://api-metrika.yandex.ru/stat/v1/data"

DATE1, DATE2 = "2026-05-01", "2026-07-22"
OUT_DIR = os.path.join("metrica_dump", "goals")
OUT     = os.path.join(OUT_DIR, "goals_stat_by_day.tsv")
MAX_RETRY, TIMEOUT = 5, 180


def H():
    if not TOKEN:
        sys.exit("ERROR: не задан METRICA_TOKEN (положите его в .env)")
    return {"Authorization": "OAuth " + TOKEN}


def get_retry(params):
    last = None
    for a in range(1, MAX_RETRY + 1):
        try:
            r = requests.get(STAT, headers=H(), params=params, timeout=TIMEOUT)
            if r.status_code == 200:
                return r
            if r.status_code == 400:
                return r  # обрабатываем «слишком сложный» выше
            last = "HTTP {}: {}".format(r.status_code, r.text[:150])
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            last = "net: {}".format(e)
        w = 2 ** a
        print("    ретрай {}/{} через {}с ({})".format(a, MAX_RETRY, w, last))
        time.sleep(w)
    raise RuntimeError("исчерпаны ретраи: {}".format(last))


def pull(d1, d2, writer):
    """Тянет диапазон постранично. При 'слишком сложный' делит период пополам."""
    offset = 1
    while True:
        params = {"ids": COUNTER, "date1": d1, "date2": d2,
                  "metrics": "ym:s:visits,ym:s:sumGoalReachesAny,ym:s:users",
                  "dimensions": "ym:s:date,ym:s:goalDimension",
                  "limit": 10000, "offset": offset, "accuracy": "full"}
        r = get_retry(params)
        if r.status_code == 400 and "слишком сложн" in r.text:
            # делим период пополам и тянем рекурсивно
            from datetime import date, timedelta
            a = date.fromisoformat(d1); b = date.fromisoformat(d2)
            if a >= b:
                print("    ! не делится дальше, пропуск {}..{}".format(d1, d2)); return
            mid = a + (b - a) // 2
            print("    период {}..{} слишком тяжёлый — делю на {} | {}".format(
                d1, d2, mid, mid + timedelta(days=1)))
            pull(d1, mid.isoformat(), writer)
            pull((mid + timedelta(days=1)).isoformat(), d2, writer)
            return
        js = r.json()
        data = js.get("data", [])
        for d in data:
            dd = d["dimensions"]; m = d["metrics"]
            date_v = dd[0]["name"]
            goal_id = dd[1].get("id", "")
            goal_nm = dd[1].get("name", "")
            writer.writerow([date_v, goal_id, goal_nm, m[0], m[1], m[2]])
        total = js.get("total_rows", 0)
        got = offset - 1 + len(data)
        print("    {}..{}: +{} строк (offset {}, всего {})".format(d1, d2, len(data), offset, total))
        if offset + 10000 > total or not data:
            break
        offset += 10000
        time.sleep(0.3)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    if os.path.exists(OUT):
        bak = OUT + ".v1_bak"
        os.replace(OUT, bak)
        print("старый by_day переименован в {}".format(os.path.basename(bak)))
    print("ПЕРЕВЫГРУЗКА by_day начисто, {}..{}, все цели одним отчётом".format(DATE1, DATE2))
    with open(OUT, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["date", "goal_id", "goal_name", "visits", "goal_reaches", "users"])
        pull(DATE1, DATE2, w)
    # краткая сводка
    n = sum(1 for _ in open(OUT, encoding="utf-8")) - 1
    goals = set()
    for i, row in enumerate(csv.reader(open(OUT, encoding="utf-8"), delimiter="\t")):
        if i == 0:
            continue
        goals.add(row[1])
    print("\nГОТОВО -> {}".format(OUT))
    print("строк: {}, уникальных целей в отчёте: {}".format(n, len(goals)))
    print("Скинь этот файл — соберу воронку по клиентам через мост.")


if __name__ == "__main__":
    main()