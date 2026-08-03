#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
СТУПЕНЬ 3 — ЦЕЛИ: список + статистика (по дням и по источникам).

Три источника данных Метрики:
  1) Management API  -> полный список целей счётчика (id, имя, тип, условия,
     retargeting-флаг, вложенные шаги составных целей);
  2) Reporting API   -> по каждой цели: визиты, достижения, конверсия, юзеры
     в разбивке ПО ДНЯМ за период;
  3) Reporting API   -> то же ПО ИСТОЧНИКАМ ТРАФИКА (для CAC/EPC по каналам).

Пишет 3 TSV в metrica_dump/goals/:
     goals_list.tsv               — справочник целей
     goals_stat_by_day.tsv        — цель × день
     goals_stat_by_source.tsv     — цель × источник × день

Период: 2026-05-01 .. 2026-07-22 (как выгрузка визитов/хитов).
Токен — из .env (METRICA_TOKEN).

Запуск:
    python metrica_dump_step3_goals.py
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
MGMT    = "https://api-metrika.yandex.ru/management/v1/counter/{c}".format(c=COUNTER)
STAT    = "https://api-metrika.yandex.ru/stat/v1/data"

DATE1, DATE2 = "2026-05-01", "2026-07-22"
OUT_DIR = os.path.join("metrica_dump", "goals")


def H():
    if not TOKEN:
        sys.exit("ERROR: не задан METRICA_TOKEN (положите его в .env)")
    return {"Authorization": "OAuth " + TOKEN}


# ---------------------------------------------------------------
# 1. СПИСОК ЦЕЛЕЙ (Management API)
# ---------------------------------------------------------------
def fetch_goals():
    r = requests.get(MGMT + "/goals", headers=H(),
                     params={"useDeleted": "true"}, timeout=60)
    r.raise_for_status()
    goals = r.json().get("goals", [])
    print("[цели] получено целей (включая удалённые): {}".format(len(goals)))
    return goals


def flatten_goal(g):
    """Разворачивает цель в строки: сама цель + её шаги/условия."""
    base = {
        "goal_id": g.get("id"),
        "name": g.get("name"),
        "type": g.get("type"),
        "is_retargeting": g.get("is_retargeting"),
        "default_price": g.get("default_price"),
        "goal_source": g.get("goal_source"),
        "status": g.get("status"),
    }
    rows = []
    conds = g.get("conditions") or []
    steps = g.get("steps") or []       # для составных целей (URL-цепочки)
    if steps:
        for si, st in enumerate(steps):
            for c in (st.get("conditions") or [{}]):
                row = dict(base); row.update({
                    "step_index": si, "step_name": st.get("name"),
                    "condition_type": c.get("type"), "condition_value": c.get("url") or c.get("value"),
                })
                rows.append(row)
    elif conds:
        for c in conds:
            row = dict(base); row.update({
                "step_index": "", "step_name": "",
                "condition_type": c.get("type"), "condition_value": c.get("url") or c.get("value"),
            })
            rows.append(row)
    else:
        row = dict(base); row.update({"step_index": "", "step_name": "",
                                      "condition_type": "", "condition_value": ""})
        rows.append(row)
    return rows


def save_goals_list(goals):
    path = os.path.join(OUT_DIR, "goals_list.tsv")
    cols = ["goal_id", "name", "type", "is_retargeting", "default_price",
            "goal_source", "status", "step_index", "step_name",
            "condition_type", "condition_value"]
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, delimiter="\t", extrasaction="ignore")
        w.writeheader()
        for g in goals:
            for row in flatten_goal(g):
                w.writerow(row)
    print("[цели] справочник -> {}".format(path))
    return [g.get("id") for g in goals if g.get("id") is not None]


# ---------------------------------------------------------------
# 2 и 3. СТАТИСТИКА ПО ЦЕЛЯМ (Reporting API)
# ---------------------------------------------------------------
def stat_request(metrics, dimensions):
    """Тянет отчёт постранично (limit 10000), возвращает список строк data."""
    out = []
    offset = 1
    while True:
        params = {
            "ids": COUNTER, "date1": DATE1, "date2": DATE2,
            "metrics": metrics, "dimensions": dimensions,
            "limit": 10000, "offset": offset, "accuracy": "full",
        }
        r = requests.get(STAT, headers=H(), params=params, timeout=120)
        if r.status_code != 200:
            print("   ! stat {}: {}".format(r.status_code, r.text[:300]))
            break
        js = r.json()
        data = js.get("data", [])
        out.extend(data)
        total = js.get("total_rows", 0)
        if offset + 10000 > total or not data:
            break
        offset += 10000
        time.sleep(0.3)
    return out


def save_by_day(goal_ids):
    """Цель × день: визиты, достижения, конверсия, пользователи."""
    path = os.path.join(OUT_DIR, "goals_stat_by_day.tsv")
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["goal_id", "date", "visits", "goal_reaches",
                    "goal_conversion_pct", "goal_users"])
        for i, gid in enumerate(goal_ids, 1):
            metrics = ("ym:s:visits,ym:s:goal{g}reaches,"
                       "ym:s:goal{g}conversionRate,ym:s:goal{g}users").format(g=gid)
            rows = stat_request(metrics, "ym:s:date")
            for d in rows:
                date = d["dimensions"][0]["name"]
                m = d["metrics"]
                w.writerow([gid, date, m[0], m[1], m[2], m[3]])
            print("   [{}/{}] цель {} — дней: {}".format(i, len(goal_ids), gid, len(rows)))
            time.sleep(0.2)
    print("[стата] по дням -> {}".format(path))


def save_by_source(goal_ids):
    """Цель × источник трафика × день — для CAC/EPC по каналам."""
    path = os.path.join(OUT_DIR, "goals_stat_by_source.tsv")
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["goal_id", "date", "traffic_source", "utm_source",
                    "visits", "goal_reaches", "goal_conversion_pct"])
        for i, gid in enumerate(goal_ids, 1):
            metrics = ("ym:s:visits,ym:s:goal{g}reaches,"
                       "ym:s:goal{g}conversionRate").format(g=gid)
            dims = "ym:s:date,ym:s:lastTrafficSource,ym:s:UTMSource"
            rows = stat_request(metrics, dims)
            for d in rows:
                dd = d["dimensions"]; m = d["metrics"]
                w.writerow([gid, dd[0]["name"], dd[1]["name"], dd[2]["name"],
                            m[0], m[1], m[2]])
            print("   [{}/{}] цель {} — строк: {}".format(i, len(goal_ids), gid, len(rows)))
            time.sleep(0.2)
    print("[стата] по источникам -> {}".format(path))


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    print("СТУПЕНЬ 3 — цели, счётчик {}, период {}..{}".format(COUNTER, DATE1, DATE2))

    goals = fetch_goals()
    goal_ids = save_goals_list(goals)
    if not goal_ids:
        print("Целей нет — статистику тянуть не из чего.")
        return

    print("\n[стата] тяну по дням для {} целей ...".format(len(goal_ids)))
    save_by_day(goal_ids)

    print("\n[стата] тяну по источникам для {} целей ...".format(len(goal_ids)))
    save_by_source(goal_ids)

    print("\n" + "=" * 60)
    print("ГОТОВО. Файлы в {}/:".format(OUT_DIR))
    print("  goals_list.tsv, goals_stat_by_day.tsv, goals_stat_by_source.tsv")
    print("Скинь goals_list.tsv целиком (он маленький) и размеры двух других.")
    print("=" * 60)


if __name__ == "__main__":
    main()