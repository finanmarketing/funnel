#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
СТУПЕНЬ 1 — РАЗВЕДКА (ничего не качает, только смотрит).

Что делает:
  1) спрашивает у Logs API полный список доступных полей для visits и hits;
  2) для каждого месяца (май/июнь/июль 2026) прогоняет evaluate —
     это оценка API, "потянет ли выгрузку одним запросом";
  3) печатает список полей и вердикт по объёму.

Ничего не выгружает. Нужно, чтобы Ступень 2 не упала на первом же запросе
из-за неверного поля или слишком большого месяца.

Запуск (PowerShell):
    python metrica_dump_step1_probe.py

Токен — из .env (METRICA_TOKEN), как в пробном скрипте.
"""

import os, sys, json
import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

COUNTER = os.getenv("METRICA_COUNTER", "21703744")
TOKEN   = os.getenv("METRICA_TOKEN")
BASE    = "https://api-metrika.yandex.ru/management/v1/counter/{c}".format(c=COUNTER)

# Периоды помесячно. Июль — до 22-го (день последней известной выгрузки).
PERIODS = [
    ("2026-05-01", "2026-05-31", "may"),
    ("2026-06-01", "2026-06-30", "jun"),
    ("2026-07-01", "2026-07-22", "jul"),
]


def H():
    if not TOKEN:
        sys.exit("ERROR: не задан METRICA_TOKEN (положите его в .env)")
    return {"Authorization": "OAuth " + TOKEN}


def get_available_fields(source):
    """Спрашивает у API список полей, реально доступных для выгрузки."""
    # У Logs API нет отдельного эндпоинта «список полей», но evaluate/logrequests
    # принимает поля; корректный источник истины — документация счётчика.
    # Практичный путь: пробуем узнать через служебный вызов; если недоступно —
    # берём максимальный известный набор и проверяем его через evaluate.
    url = "https://api-metrika.yandex.ru/internal/log_request_fields"
    try:
        r = requests.get(url, headers=H(),
                         params={"source": source}, timeout=30)
        if r.status_code == 200:
            data = r.json()
            fields = data.get("fields") or data.get("log_request_fields") or []
            if fields:
                return [f if isinstance(f, str) else f.get("name") for f in fields]
    except Exception:
        pass
    return None  # не отдал — будем проверять максимальный набор в Ступени 2


def evaluate(d1, d2, fields, source):
    r = requests.get(BASE + "/logrequests/evaluate", headers=H(),
                     params={"date1": d1, "date2": d2,
                             "fields": ",".join(fields), "source": source},
                     timeout=60)
    if r.status_code != 200:
        return None, "evaluate {}: {}".format(r.status_code, r.text[:300])
    ev = r.json()["log_request_evaluation"]
    return ev, None


# Минимальный «якорный» набор — гарантированно валидные поля,
# чтобы evaluate не падал и дал оценку объёма по месяцу.
ANCHOR = {
    "visits": ["ym:s:date", "ym:s:clientID", "ym:s:visitID",
               "ym:s:dateTime", "ym:s:startURL", "ym:s:lastTrafficSource"],
    "hits":   ["ym:pv:date", "ym:pv:clientID", "ym:pv:watchID",
               "ym:pv:dateTime", "ym:pv:URL", "ym:pv:title"],
}


def probe_source(source):
    print("\n" + "=" * 64)
    print("ИСТОЧНИК: {}".format(source.upper()))
    print("=" * 64)

    fields = get_available_fields(source)
    if fields:
        print("[поля] API отдал список доступных полей: {} шт.".format(len(fields)))
        print("       первые 25:", ", ".join(fields[:25]))
        with open("fields_{}.txt".format(source), "w", encoding="utf-8") as f:
            f.write("\n".join(fields))
        print("       полный список -> fields_{}.txt".format(source))
    else:
        print("[поля] API не отдал список напрямую.")
        print("       В Ступени 2 возьмём максимальный известный набор и")
        print("       проверим его через evaluate (поле за полем при ошибке).")

    print("\n[объём] прогоняю evaluate по месяцам (якорный набор полей):")
    for d1, d2, tag in PERIODS:
        ev, err = evaluate(d1, d2, ANCHOR[source], source)
        if err:
            print("  {} {}..{}: ОШИБКА {}".format(tag, d1, d2, err))
            continue
        possible = ev["possible"]
        # у некоторых версий API есть оценка числа строк:
        approx = ev.get("log_request_evaluation", {})
        print("  {} {}..{}: выгрузка одним запросом = {}".format(
            tag, d1, d2, "ДА" if possible else "НЕТ (бить мельче)"))


def main():
    print("РАЗВЕДКА выгрузки Метрики — счётчик {}".format(COUNTER))
    print("Периоды: 2026-05-01 .. 2026-07-22, помесячно")
    for source in ("visits", "hits"):
        probe_source(source)

    print("\n" + "=" * 64)
    print("ГОТОВО. Скинь весь вывод — по нему соберу Ступень 2:")
    print("  - точный список полей (или подтверждение якорного набора),")
    print("  - разбивку (месяц/декада) по вердикту 'одним запросом'.")
    print("=" * 64)


if __name__ == "__main__":
    main()