#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
СТУПЕНЬ 3 v2 — ЦЕЛИ: список + статистика. УСТОЙЧИВАЯ версия.

Что исправлено против v1 (упала таймаутом на цели 91/215):
  * РЕТРАИ: каждый запрос повторяется до 5 раз с нарастающей паузой —
    одиночный сетевой таймаут больше не убивает весь скрипт;
  * ЧЕКПОИНТ/ДОКАЧКА: прогресс пишется по ходу; при перезапуске
    уже собранные цели пропускаются, продолжаем с места обрыва;
  * поднят timeout и добавлен httpAdapter с backoff;
  * стата по дням теперь одним отчётом через ym:s:goalDimension,
    а не 215 отдельными запросами (быстрее и надёжнее). По источникам —
    поцелевой цикл сохранён (goalDimension нельзя смешивать с реаче-метриками
    по конкретной цели), но с ретраями и чекпоинтом.

Файлы в metrica_dump/goals/:
    goals_list.tsv               — справочник целей (уже собран в v1, не трогаем если есть)
    goals_stat_by_day.tsv        — цель × день
    goals_stat_by_source.tsv     — цель × источник × день
    _progress_by_source.txt      — чекпоинт: id уже собранных целей

Запуск (можно повторно — продолжит с места обрыва):
    python metrica_dump_step3_goals_v2.py
"""

import os, sys, csv, time
import requests
from requests.adapters import HTTPAdapter

try:
    from urllib3.util.retry import Retry
except Exception:
    Retry = None

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
CKPT    = os.path.join(OUT_DIR, "_progress_by_source.txt")

MAX_RETRY = 5
TIMEOUT   = 180


def H():
    if not TOKEN:
        sys.exit("ERROR: не задан METRICA_TOKEN (положите его в .env)")
    return {"Authorization": "OAuth " + TOKEN}


def make_session():
    s = requests.Session()
    if Retry:
        retry = Retry(total=MAX_RETRY, backoff_factor=2,
                      status_forcelist=[429, 500, 502, 503, 504],
                      allowed_methods=["GET"])
        s.mount("https://", HTTPAdapter(max_retries=retry))
    return s


SESSION = make_session()


def get_with_retry(url, params):
    """GET с ретраями на таймаут/обрыв (сверх встроенных ретраев адаптера)."""
    last = None
    for attempt in range(1, MAX_RETRY + 1):
        try:
            r = SESSION.get(url, headers=H(), params=params, timeout=TIMEOUT)
            if r.status_code == 200:
                return r
            # 400-е (кроме 429) — не сетевые, повтор не поможет
            if 400 <= r.status_code < 500 and r.status_code != 429:
                return r
            last = "HTTP {}: {}".format(r.status_code, r.text[:200])
        except (requests.exceptions.ConnectionError,
                requests.exceptions.Timeout) as e:
            last = "network: {}".format(e)
        wait = 2 ** attempt
        print("      ретрай {}/{} через {}с ({})".format(attempt, MAX_RETRY, wait, last))
        time.sleep(wait)
    raise RuntimeError("исчерпаны ретраи: {}".format(last))


# ---------------------------------------------------------------
# 1. СПИСОК ЦЕЛЕЙ
# ---------------------------------------------------------------
def fetch_goals():
    r = get_with_retry(MGMT + "/goals", {"useDeleted": "true"})
    r.raise_for_status()
    goals = r.json().get("goals", [])
    print("[цели] получено целей (включая удалённые): {}".format(len(goals)))
    return goals


def flatten_goal(g):
    base = {"goal_id": g.get("id"), "name": g.get("name"), "type": g.get("type"),
            "is_retargeting": g.get("is_retargeting"), "default_price": g.get("default_price"),
            "goal_source": g.get("goal_source"), "status": g.get("status")}
    rows = []
    steps = g.get("steps") or []
    conds = g.get("conditions") or []
    if steps:
        for si, st in enumerate(steps):
            for c in (st.get("conditions") or [{}]):
                r = dict(base); r.update({"step_index": si, "step_name": st.get("name"),
                    "condition_type": c.get("type"), "condition_value": c.get("url") or c.get("value")})
                rows.append(r)
    elif conds:
        for c in conds:
            r = dict(base); r.update({"step_index": "", "step_name": "",
                "condition_type": c.get("type"), "condition_value": c.get("url") or c.get("value")})
            rows.append(r)
    else:
        r = dict(base); r.update({"step_index": "", "step_name": "",
            "condition_type": "", "condition_value": ""})
        rows.append(r)
    return rows


def save_goals_list(goals):
    path = os.path.join(OUT_DIR, "goals_list.tsv")
    if os.path.exists(path) and os.path.getsize(path) > 0:
        print("[цели] справочник уже есть — пропускаю")
        return [g.get("id") for g in goals if g.get("id") is not None]
    cols = ["goal_id", "name", "type", "is_retargeting", "default_price", "goal_source",
            "status", "step_index", "step_name", "condition_type", "condition_value"]
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, delimiter="\t", extrasaction="ignore")
        w.writeheader()
        for g in goals:
            for row in flatten_goal(g):
                w.writerow(row)
    print("[цели] справочник -> {}".format(path))
    return [g.get("id") for g in goals if g.get("id") is not None]


def stat_paged(metrics, dimensions, filters=None):
    out = []; offset = 1
    while True:
        params = {"ids": COUNTER, "date1": DATE1, "date2": DATE2,
                  "metrics": metrics, "dimensions": dimensions,
                  "limit": 10000, "offset": offset, "accuracy": "full"}
        if filters:
            params["filters"] = filters
        r = get_with_retry(STAT, params)
        if r.status_code != 200:
            print("   ! stat {}: {}".format(r.status_code, r.text[:200])); break
        js = r.json()
        data = js.get("data", [])
        out.extend(data)
        total = js.get("total_rows", 0)
        if offset + 10000 > total or not data:
            break
        offset += 10000
        time.sleep(0.3)
    return out


# ---------------------------------------------------------------
# 2. СТАТА ПО ДНЯМ — одним отчётом через goalDimension
# ---------------------------------------------------------------
def save_by_day_bulk():
    path = os.path.join(OUT_DIR, "goals_stat_by_day.tsv")
    if os.path.exists(path) and os.path.getsize(path) > 0:
        print("[стата] by_day уже есть — пропускаю"); return
    # ym:s:goalDimension разворачивает КАЖДУЮ цель в отдельную строку,
    # ym:s:sumGoalReachesAny/Serving даёт достижения по цели строки.
    rows = stat_paged(
        metrics="ym:s:visits,ym:s:sumGoalReachesAny",
        dimensions="ym:s:date,ym:s:goalDimension")
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["date", "goal", "visits", "goal_reaches"])
        for d in rows:
            dd = d["dimensions"]; m = d["metrics"]
            w.writerow([dd[0]["name"], dd[1]["name"], m[0], m[1]])
    print("[стата] by_day (одним отчётом) -> {} ({} строк)".format(path, len(rows)))


# ---------------------------------------------------------------
# 3. СТАТА ПО ИСТОЧНИКАМ — поцелевой цикл с чекпоинтом
# ---------------------------------------------------------------
def load_ckpt():
    if os.path.exists(CKPT):
        return set(open(CKPT, encoding="utf-8").read().split())
    return set()


def mark_done(gid):
    with open(CKPT, "a", encoding="utf-8") as f:
        f.write(str(gid) + "\n")


def save_by_source(goal_ids):
    path = os.path.join(OUT_DIR, "goals_stat_by_source.tsv")
    done = load_ckpt()
    mode = "a" if (os.path.exists(path) and done) else "w"
    f = open(path, mode, encoding="utf-8", newline="")
    w = csv.writer(f, delimiter="\t")
    if mode == "w":
        w.writerow(["goal_id", "date", "traffic_source", "utm_source",
                    "visits", "goal_reaches", "goal_conversion_pct"])
    todo = [g for g in goal_ids if str(g) not in done]
    print("[стата] by_source: осталось {} из {} целей (готово {})".format(
        len(todo), len(goal_ids), len(done)))
    for i, gid in enumerate(todo, 1):
        metrics = ("ym:s:visits,ym:s:goal{g}reaches,ym:s:goal{g}conversionRate").format(g=gid)
        dims = "ym:s:date,ym:s:lastTrafficSource,ym:s:UTMSource"
        try:
            rows = stat_paged(metrics, dims)
        except RuntimeError as e:
            print("   цель {} — не удалось ({}), пропускаю до след. запуска".format(gid, e))
            f.flush(); continue
        for d in rows:
            dd = d["dimensions"]; m = d["metrics"]
            w.writerow([gid, dd[0]["name"], dd[1]["name"], dd[2]["name"], m[0], m[1], m[2]])
        f.flush()
        mark_done(gid)
        print("   [{}/{}] цель {} — строк {}".format(i, len(todo), gid, len(rows)))
        time.sleep(0.2)
    f.close()
    print("[стата] by_source -> {}".format(path))


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    print("СТУПЕНЬ 3 v2 — цели, счётчик {}, {}..{}".format(COUNTER, DATE1, DATE2))

    goals = fetch_goals()
    goal_ids = save_goals_list(goals)
    if not goal_ids:
        print("Целей нет."); return

    print("\n[стата] by_day одним отчётом ...")
    save_by_day_bulk()

    print("\n[стата] by_source поцелевой (с докачкой) ...")
    save_by_source(goal_ids)

    print("\n" + "=" * 60)
    print("ГОТОВО. Файлы в {}/".format(OUT_DIR))
    print("Если by_source прервётся — просто запусти скрипт ещё раз,")
    print("он продолжит с недобранных целей (чекпоинт _progress_by_source.txt).")
    print("=" * 60)


if __name__ == "__main__":
    main()