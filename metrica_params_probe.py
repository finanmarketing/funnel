#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ПРОБА: есть ли в параметрах визита Метрики внутренний идентификатор клиента.

Зачем: в отчёте «Параметры визитов» видно, что сайт передаёт параметр UserID.
Если его значение совпадает с client_id (или client_number) в базе — связку
Метрика ↔ БД можно построить БЕЗ доработок фронта.

Что делает: выгружает за один день визиты с полями
  clientID + parsedParamsKey1..5
и показывает, что реально лежит в параметрах.

Запуск (PowerShell):
    python metrica_params_probe.py 2026-07-22

Токен берётся из .env (METRICA_TOKEN) или из переменной окружения.
"""

import os, sys, time, io, csv, json
import datetime as dt
from collections import Counter
import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

COUNTER = os.getenv("METRICA_COUNTER", "21703744")
TOKEN   = os.getenv("METRICA_TOKEN")
BASE    = "https://api-metrika.yandex.ru/management/v1/counter/{c}".format(c=COUNTER)

FIELDS = ",".join([
    "ym:s:date",
    "ym:s:clientID",
    "ym:s:visitID",
    "ym:s:parsedParamsKey1",
    "ym:s:parsedParamsKey2",
    "ym:s:parsedParamsKey3",
    "ym:s:parsedParamsKey4",
    "ym:s:parsedParamsKey5",
])

def H():
    if not TOKEN:
        sys.exit("ERROR: не задан METRICA_TOKEN (положите его в .env)")
    return {"Authorization": "OAuth " + TOKEN}

def evaluate(d1, d2):
    r = requests.get(BASE + "/logrequests/evaluate", headers=H(),
                     params={"date1": d1, "date2": d2, "fields": FIELDS, "source": "visits"},
                     timeout=60)
    if r.status_code != 200:
        sys.exit("evaluate вернул {}: {}".format(r.status_code, r.text[:400]))
    ev = r.json()["log_request_evaluation"]
    print("[evaluate] выгрузка возможна: {}".format(ev["possible"]))
    if not ev["possible"]:
        sys.exit("Выгрузка невозможна — сузьте период.")

def create(d1, d2):
    r = requests.post(BASE + "/logrequests", headers=H(),
                      params={"date1": d1, "date2": d2, "fields": FIELDS, "source": "visits"},
                      timeout=60)
    if r.status_code != 200:
        sys.exit("create вернул {}: {}".format(r.status_code, r.text[:400]))
    req = r.json()["log_request"]
    print("[create] request_id={} status={}".format(req["request_id"], req["status"]))
    return req["request_id"]

def poll(rid, interval=15, max_wait=1800):
    waited = 0
    while waited < max_wait:
        r = requests.get(BASE + "/logrequest/{}".format(rid), headers=H(), timeout=60)
        r.raise_for_status()
        req = r.json()["log_request"]
        print("[poll] {} ({}s)".format(req["status"], waited))
        if req["status"] == "processed":
            return len(req.get("parts", [])) or 1
        if req["status"] in ("canceled", "processing_failed"):
            sys.exit("Выгрузка завершилась статусом " + req["status"])
        time.sleep(interval); waited += interval
    sys.exit("Не дождались готовности выгрузки")

def download(rid, part):
    r = requests.get(BASE + "/logrequest/{}/part/{}/download".format(rid, part),
                     headers=H(), timeout=300)
    r.raise_for_status()
    return list(csv.DictReader(io.StringIO(r.content.decode("utf-8")), delimiter="\t"))

def analyze(rows):
    print("\n" + "=" * 64)
    print("ВСЕГО ВИЗИТОВ: {}".format(len(rows)))
    if not rows:
        return
    keys = [k for k in rows[0].keys() if "parsedParamsKey" in k]
    for k in keys:
        vals = [r[k] for r in rows if r.get(k, "").strip()]
        short = k.split(":")[-1]
        print("\n--- {} : заполнено {} из {} ({:.1f}%) ---".format(
            short, len(vals), len(rows), 100.0 * len(vals) / len(rows)))
        for v, c in Counter(vals).most_common(10):
            print("   {:>7}  {}".format(c, v[:70]))

    # ищем значения, похожие на внутренний идентификатор (число 5-9 знаков)
    print("\n--- ПОХОЖЕЕ НА ВНУТРЕННИЙ ID (число 5-9 знаков) ---")
    found = []
    for r in rows:
        for k in keys:
            v = (r.get(k) or "").strip()
            if v.isdigit() and 5 <= len(v) <= 9:
                found.append((r.get("ym:s:clientID") or r.get("clientid"), k.split(":")[-1], v))
    print("найдено значений: {}".format(len(found)))
    for c, k, v in found[:15]:
        print("   clientid={}  {}={}".format(c, k, v))
    if found:
        print("\nВозьмите эти значения и проверьте в базе:")
        ids = ",".join(sorted({v for _, _, v in found[:20]}))
        print("   SELECT id, client_number FROM public.clients WHERE id IN ({});".format(ids))
        print("   SELECT id, client_number FROM public.clients WHERE client_number IN ({});".format(ids))

def save(rows, path="metrica_params.tsv"):
    if not rows:
        return
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()), delimiter="\t")
        w.writeheader(); w.writerows(rows)
    print("\n[save] сохранено {} строк -> {}".format(len(rows), path))

if __name__ == "__main__":
    day = sys.argv[1] if len(sys.argv) > 1 else (dt.date.today() - dt.timedelta(days=2)).isoformat()
    print("=== Проба параметров визита, счётчик {}, дата {} ===".format(COUNTER, day))
    evaluate(day, day)
    request_id = create(day, day)
    n_parts = poll(request_id)
    print("[parts] частей файла: {}".format(n_parts))
    rows = []
    for p in range(n_parts):
        part = download(request_id, p)
        print("[download] часть {}: {} строк".format(p, len(part)))
        rows.extend(part)
    save(rows)
    analyze(rows)