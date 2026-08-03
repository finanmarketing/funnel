#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Выгрузка ВИЗИТОВ из Яндекс.Метрики (Logs API) → PostgreSQL DWH.

Назначение: получить сырые визиты со связкой clientID + достигнутые цели (шаги воронки)
+ источник трафика, чтобы построить ВЕРХ воронки (TRAFFIC, CR, FCR), которого нет в БД.

Цикл Logs API (4 шага):
  1) evaluate  — проверить, что выгрузка допустима (не слишком большая)
  2) create    — создать logrequest, получить request_id
  3) poll      — дождаться статуса 'processed'
  4) download  — скачать части (TSV) и распарсить

ВАЖНО (безопасность):
  - Токен НЕ хранить в коде. Брать из переменной окружения METRICA_TOKEN.
  - Пароль БД — из переменной окружения PG_DSN.
  - Данные Метрики (в т.ч. clientID) грузим в DWH компании, не на личный диск.

Данные Logs API доступны за ПРЕДЫДУЩИЙ день и старше (не за сегодня).
"""

import os
import sys
import time
import io
import csv
import datetime as dt
import requests

# автозагрузка .env (если установлен python-dotenv)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ---------------------- КОНФИГ (из окружения) ----------------------
COUNTER_ID = os.getenv("METRICA_COUNTER", "21703744")          # eZaem по умолчанию
TOKEN      = os.getenv("METRICA_TOKEN")                          # OAuth-токен Метрики
PG_DSN     = os.getenv("PG_DSN")  # напр.: postgresql://user:pass@10.174.17.38:5432/dwh_ezru_loans
TARGET_TABLE = os.getenv("METRICA_TABLE", "public.mtr_visits_raw")

API = "https://api-metrika.yandex.ru/management/v1/counter/{counter}/logrequests"

# Поля визита для воронки. Полный список — в доке Logs API (fields/visits).
FIELDS = ",".join([
    "ym:s:date",              # дата визита
    "ym:s:dateTime",          # точное время
    "ym:s:clientID",          # ← КЛЮЧ для связки с БД (наш IT-2)
    "ym:s:visitID",
    "ym:s:lastTrafficSource", # источник (канал)
    "ym:s:lastAdvEngine",     # рекламная система (если реклама)
    "ym:s:UTMSource",
    "ym:s:UTMMedium",
    "ym:s:UTMCampaign",
    "ym:s:goalsID",           # ← достигнутые цели визита = шаги воронки
    "ym:s:isNewUser",         # новый/вернувшийся (NEW/REP трафик)
    "ym:s:deviceCategory",
    "ym:s:regionCity",
])
SOURCE = "visits"


def _auth_headers():
    if not TOKEN:
        sys.exit("ERROR: не задан METRICA_TOKEN (export METRICA_TOKEN=...)")
    return {"Authorization": f"OAuth {TOKEN}"}


def evaluate(date1, date2):
    """Шаг 1: проверить допустимость выгрузки."""
    url = (API + "/evaluate").format(counter=COUNTER_ID)
    r = requests.get(url, headers=_auth_headers(), params={
        "date1": date1, "date2": date2, "fields": FIELDS, "source": SOURCE
    }, timeout=60)
    r.raise_for_status()
    ev = r.json()["log_request_evaluation"]
    print(f"[evaluate] возможна выгрузка: {ev['possible']}, "
          f"макс. дней за раз: {ev.get('max_possible_day_quantity')}")
    if not ev["possible"]:
        sys.exit("ERROR: выгрузка невозможна — сузьте период или число полей.")
    return ev


def create(date1, date2):
    """Шаг 2: создать logrequest → request_id."""
    url = API.format(counter=COUNTER_ID)
    r = requests.post(url, headers=_auth_headers(), params={
        "date1": date1, "date2": date2, "fields": FIELDS, "source": SOURCE
    }, timeout=60)
    r.raise_for_status()
    req = r.json()["log_request"]
    print(f"[create] request_id={req['request_id']} status={req['status']}")
    return req["request_id"]


def poll(request_id, interval=20, max_wait=1800):
    """Шаг 3: ждать, пока статус станет 'processed'."""
    url = "https://api-metrika.yandex.ru/management/v1/counter/{counter}/logrequest/{rid}".format(counter=COUNTER_ID, rid=request_id)
    waited = 0
    while waited < max_wait:
        r = requests.get(url, headers=_auth_headers(), timeout=60)
        r.raise_for_status()
        req = r.json()["log_request"]
        status = req["status"]
        print(f"[poll] status={status} ({waited}s)")
        if status == "processed":
            # число частей файла
            parts = req.get("parts", [])
            return len(parts) if parts else 1
        if status in ("canceled", "processing_failed"):
            sys.exit(f"ERROR: выгрузка завершилась статусом {status}")
        time.sleep(interval)
        waited += interval
    sys.exit("ERROR: превышено время ожидания готовности выгрузки")


def download_part(request_id, part):
    """Шаг 4: скачать одну часть (TSV) и вернуть список dict-строк."""
    url = "https://api-metrika.yandex.ru/management/v1/counter/{counter}/logrequest/{rid}/part/{part}/download".format(
        counter=COUNTER_ID, rid=request_id, part=part)
    r = requests.get(url, headers=_auth_headers(), timeout=300)
    r.raise_for_status()
    text = r.content.decode("utf-8")
    reader = csv.DictReader(io.StringIO(text), delimiter="\t")
    return list(reader)


def clean(rows):
    """Нормализуем имена колонок: ym:s:clientID → clientid и т.п."""
    out = []
    for row in rows:
        out.append({k.split(":")[-1].lower(): (v if v != "" else None)
                    for k, v in row.items()})
    return out


def load_to_pg(rows):
    """Записать строки в PostgreSQL. Требует PG_DSN и psycopg2."""
    if not PG_DSN:
        print("[load] PG_DSN не задан — пропускаю запись в БД, сохраняю в TSV-файл.")
        _save_tsv(rows)
        return
    try:
        import psycopg2
        from psycopg2.extras import execute_values
    except ImportError:
        sys.exit("ERROR: нужен psycopg2 (pip install psycopg2-binary)")

    if not rows:
        print("[load] нет строк для записи."); return
    cols = list(rows[0].keys())
    schema, table = (TARGET_TABLE.split(".") + ["public"])[:2][::-1] if "." in TARGET_TABLE else ("public", TARGET_TABLE)
    schema, table = TARGET_TABLE.split(".") if "." in TARGET_TABLE else ("public", TARGET_TABLE)

    coldefs = ",\n  ".join(f'"{c}" text' for c in cols)
    ddl = f'CREATE TABLE IF NOT EXISTS {schema}.{table} (\n  {coldefs}\n);'
    ins = f'INSERT INTO {schema}.{table} ({",".join(chr(34)+c+chr(34) for c in cols)}) VALUES %s'

    conn = psycopg2.connect(PG_DSN)
    try:
        with conn, conn.cursor() as cur:
            cur.execute(ddl)
            execute_values(cur, ins, [[r.get(c) for c in cols] for r in rows], page_size=1000)
        print(f"[load] записано строк: {len(rows)} → {schema}.{table}")
    finally:
        conn.close()


def _save_tsv(rows, path="metrica_visits.tsv"):
    if not rows:
        print("[save] нет данных."); return
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()), delimiter="\t")
        w.writeheader(); w.writerows(rows)
    print(f"[save] сохранено {len(rows)} строк → {path}")


def run(date1, date2):
    print(f"=== Logs API выгрузка визитов, счётчик {COUNTER_ID}, {date1}..{date2} ===")
    evaluate(date1, date2)
    rid = create(date1, date2)
    nparts = poll(rid)
    print(f"[parts] частей файла: {nparts}")
    all_rows = []
    for p in range(nparts):
        rows = clean(download_part(rid, p))
        print(f"[download] часть {p}: {len(rows)} строк")
        all_rows.extend(rows)
    load_to_pg(all_rows)
    print("=== готово ===")


if __name__ == "__main__":
    # по умолчанию — вчерашний день (Logs API не отдаёт сегодня)
    if len(sys.argv) == 3:
        d1, d2 = sys.argv[1], sys.argv[2]
    else:
        y = (dt.date.today() - dt.timedelta(days=1)).isoformat()
        d1 = d2 = y
    run(d1, d2)