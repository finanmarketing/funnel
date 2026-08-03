#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
СТУПЕНЬ 2 — ПОЛНАЯ ВЫГРУЗКА визитов и хитов из Logs API Метрики.

Счётчик 21703744 (eZaem). Период 2026-05-01 .. 2026-07-22, помесячно.
Забирает МАКСИМАЛЬНЫЙ набор полей. Если API отклонит поле —
скрипт сам уберёт его и повторит (авто-отбраковка), не падая целиком.
Большие ответы Logs API отдаёт частями (parts) — качаются все.
Результат — .tsv.gz по каждому месяцу и источнику.

Структура на диске (создаётся автоматически):
    metrica_dump/
        visits/  ym_visits_2026-05.tsv.gz ...
        hits/    ym_hits_2026-05.tsv.gz ...
        _log/    отбракованные поля, манифест

Докачка: если файл месяца уже готов — пропускается.
Обрыв в середине — перезапусти скрипт, продолжит с недокачанного.

Запуск (PowerShell):
    python metrica_dump_step2_logs.py
    python metrica_dump_step2_logs.py --only visits      # только визиты
    python metrica_dump_step2_logs.py --only hits
    python metrica_dump_step2_logs.py --clean-requests   # подчистить зависшие log-запросы на стороне API

Токен — из .env (METRICA_TOKEN).
"""

import os, sys, time, json, gzip, argparse
import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

COUNTER = os.getenv("METRICA_COUNTER", "21703744")
TOKEN   = os.getenv("METRICA_TOKEN")
BASE    = "https://api-metrika.yandex.ru/management/v1/counter/{c}".format(c=COUNTER)

OUT_ROOT = "metrica_dump"
PERIODS = [
    ("2026-05-01", "2026-05-31", "2026-05"),
    ("2026-06-01", "2026-06-30", "2026-06"),
    ("2026-07-01", "2026-07-22", "2026-07"),
]

# Максимальный набор полей. Авто-отбраковка уберёт то, что API не примет.
FIELDS = {
    "visits": ("ym:s:visitID ym:s:counterID ym:s:watchIDs ym:s:date ym:s:dateTime "
        "ym:s:dateTimeUTC ym:s:isNewUser ym:s:startURL ym:s:endURL ym:s:pageViews "
        "ym:s:visitDuration ym:s:bounce ym:s:ipAddress ym:s:regionCountry ym:s:regionCity "
        "ym:s:regionCountryID ym:s:regionCityID ym:s:clientID ym:s:networkType ym:s:goalsID "
        "ym:s:goalsSerialID ym:s:goalsDateTime ym:s:goalsPrice ym:s:goalsOrder ym:s:goalsCurrency "
        "ym:s:lastTrafficSource ym:s:lastAdvEngine ym:s:lastReferalSource ym:s:lastSearchEngineRoot "
        "ym:s:lastSearchEngine ym:s:lastSocialNetwork ym:s:lastSocialNetworkProfile ym:s:referer "
        "ym:s:lastDirectClickOrder ym:s:lastDirectBannerGroup ym:s:lastDirectClickBanner "
        "ym:s:lastDirectClickOrderName ym:s:lastClickBannerGroupName ym:s:lastDirectClickBannerName "
        "ym:s:lastDirectPhraseOrCond ym:s:lastDirectPlatformType ym:s:lastDirectPlatform "
        "ym:s:lastDirectConditionType ym:s:lastCurrencyID ym:s:from ym:s:UTMCampaign ym:s:UTMContent "
        "ym:s:UTMMedium ym:s:UTMSource ym:s:UTMTerm ym:s:openstatAd ym:s:openstatCampaign "
        "ym:s:openstatService ym:s:openstatSource ym:s:hasGCLID ym:s:lastGCLID ym:s:browserLanguage "
        "ym:s:browserCountry ym:s:clientTimeZone ym:s:deviceCategory ym:s:mobilePhone "
        "ym:s:mobilePhoneModel ym:s:operatingSystemRoot ym:s:operatingSystem ym:s:browser "
        "ym:s:browserMajorVersion ym:s:browserMinorVersion ym:s:browserCountryOfBase ym:s:browserEngine "
        "ym:s:browserEngineVersion1 ym:s:cookieEnabled ym:s:javascriptEnabled ym:s:screenFormat "
        "ym:s:screenColors ym:s:screenOrientation ym:s:screenWidth ym:s:screenHeight "
        "ym:s:physicalScreenWidth ym:s:physicalScreenHeight ym:s:windowClientWidth ym:s:windowClientHeight "
        "ym:s:purchaseID ym:s:purchaseDateTime ym:s:purchaseAffiliation ym:s:purchaseRevenue "
        "ym:s:purchaseTax ym:s:purchaseShipping ym:s:purchaseCoupon ym:s:purchaseCurrency "
        "ym:s:purchaseProductQuantity ym:s:productsPurchaseID ym:s:productsID ym:s:productsName "
        "ym:s:productsBrand ym:s:productsCategory ym:s:productsVariant ym:s:productsPosition "
        "ym:s:productsPrice ym:s:productsCurrency ym:s:productsQuantity ym:s:productsList "
        "ym:s:productsEventTime ym:s:impressionsURL ym:s:impressionsDateTime ym:s:impressionsProductID "
        "ym:s:impressionsProductName ym:s:parsedParamsKey1 ym:s:parsedParamsKey2 ym:s:parsedParamsKey3 "
        "ym:s:parsedParamsKey4 ym:s:parsedParamsKey5 ym:s:parsedParamsKey6 ym:s:parsedParamsKey7 "
        "ym:s:parsedParamsKey8 ym:s:parsedParamsKey9 ym:s:parsedParamsKey10").split(),
    "hits": ("ym:pv:watchID ym:pv:counterID ym:pv:date ym:pv:dateTime ym:pv:dateTimeUTC ym:pv:URL "
        "ym:pv:referer ym:pv:title ym:pv:isPageView ym:pv:clientID ym:pv:counterUserIDHash "
        "ym:pv:pageCharset ym:pv:regionCountry ym:pv:regionCity ym:pv:regionCountryID ym:pv:regionCityID "
        "ym:pv:ipAddress ym:pv:lastTrafficSource ym:pv:lastAdvEngine ym:pv:lastReferalSource "
        "ym:pv:lastSearchEngineRoot ym:pv:lastSearchEngine ym:pv:lastSocialNetwork "
        "ym:pv:lastSocialNetworkProfile ym:pv:from ym:pv:UTMCampaign ym:pv:UTMContent ym:pv:UTMMedium "
        "ym:pv:UTMSource ym:pv:UTMTerm ym:pv:openstatAd ym:pv:openstatCampaign ym:pv:openstatService "
        "ym:pv:openstatSource ym:pv:hasGCLID ym:pv:GCLID ym:pv:browserLanguage ym:pv:browserCountry "
        "ym:pv:clientTimeZone ym:pv:deviceCategory ym:pv:mobilePhone ym:pv:mobilePhoneModel "
        "ym:pv:operatingSystemRoot ym:pv:operatingSystem ym:pv:browser ym:pv:browserMajorVersion "
        "ym:pv:browserMinorVersion ym:pv:browserEngine ym:pv:browserEngineVersion1 ym:pv:cookieEnabled "
        "ym:pv:javascriptEnabled ym:pv:screenFormat ym:pv:screenColors ym:pv:screenOrientation "
        "ym:pv:screenWidth ym:pv:screenHeight ym:pv:physicalScreenWidth ym:pv:physicalScreenHeight "
        "ym:pv:windowClientWidth ym:pv:windowClientHeight ym:pv:parsedParamsKey1 ym:pv:parsedParamsKey2 "
        "ym:pv:parsedParamsKey3 ym:pv:parsedParamsKey4 ym:pv:parsedParamsKey5 ym:pv:parsedParamsKey6 "
        "ym:pv:parsedParamsKey7 ym:pv:parsedParamsKey8 ym:pv:parsedParamsKey9 ym:pv:parsedParamsKey10").split(),
}


def H():
    if not TOKEN:
        sys.exit("ERROR: не задан METRICA_TOKEN (положите его в .env)")
    return {"Authorization": "OAuth " + TOKEN}


def ensure_dirs():
    for sub in ("visits", "hits", "_log"):
        os.makedirs(os.path.join(OUT_ROOT, sub), exist_ok=True)


def clean_stuck_requests():
    """Отменяет зависшие/старые log-запросы на стороне API (лимит на счётчик)."""
    r = requests.get(BASE + "/logrequests", headers=H(), timeout=60)
    if r.status_code != 200:
        print("  не удалось получить список logrequests:", r.status_code, r.text[:200]); return
    reqs = r.json().get("requests", [])
    print("  активных log-запросов на счётчике:", len(reqs))
    for rq in reqs:
        rid = rq.get("request_id"); st = rq.get("status")
        if st in ("created", "processed"):
            # можно очистить обработанные и отменить готовящиеся
            if st == "processed":
                requests.post(BASE + "/logrequest/{}/clean".format(rid), headers=H(), timeout=60)
                print("    cleaned", rid)
            elif st == "created":
                requests.post(BASE + "/logrequest/{}/cancel".format(rid), headers=H(), timeout=60)
                print("    cancelled", rid)


def evaluate(d1, d2, fields, source):
    r = requests.get(BASE + "/logrequests/evaluate", headers=H(),
                     params={"date1": d1, "date2": d2, "fields": ",".join(fields), "source": source},
                     timeout=60)
    return r


def create_request(d1, d2, fields, source):
    """Создаёт log-запрос с авто-отбраковкой полей, которые API не принял."""
    cur = list(fields)
    dropped = []
    for _ in range(len(fields) + 2):  # с запасом на несколько отбраковок
        r = requests.post(BASE + "/logrequests", headers=H(),
                          params={"date1": d1, "date2": d2, "source": source,
                                  "fields": ",".join(cur)}, timeout=60)
        if r.status_code == 200:
            return r.json()["log_request"], dropped
        txt = r.text
        # пытаемся вытащить имя неверного поля из текста ошибки
        bad = None
        for f in cur:
            if f in txt and ("field" in txt.lower() or "поле" in txt.lower() or "not" in txt.lower()):
                bad = f; break
        if bad:
            cur.remove(bad); dropped.append(bad)
            print("    ! API отклонил поле {} — убираю, повтор".format(bad))
            continue
        # не смогли определить конкретное поле — выходим с ошибкой
        raise RuntimeError("create logrequest {}: {}".format(r.status_code, txt[:400]))
    raise RuntimeError("слишком много отбракованных полей — проверь набор")


def wait_ready(request_id):
    t0 = time.time()
    while True:
        r = requests.get(BASE + "/logrequest/{}".format(request_id), headers=H(), timeout=60)
        r.raise_for_status()
        info = r.json()["log_request"]
        st = info["status"]
        el = int(time.time() - t0)
        if st == "processed":
            parts = info.get("parts", [])
            print("    [{:>4}s] processed, частей: {}".format(el, len(parts)))
            return info
        if st in ("canceled", "processing_failed", "cleaned_by_user", "expired"):
            raise RuntimeError("запрос {} в статусе {}".format(request_id, st))
        print("    [{:>4}s] {} ...".format(el, st))
        time.sleep(10)


def download(request_id, parts, out_path):
    """Качает все части в один .tsv.gz. Шапку берём только из части 0."""
    tmp = out_path + ".part"
    with gzip.open(tmp, "wt", encoding="utf-8", newline="") as gz:
        for pi in range(len(parts)):
            url = BASE + "/logrequest/{}/part/{}/download".format(request_id, pi)
            with requests.get(url, headers=H(), stream=True, timeout=600) as resp:
                resp.raise_for_status()
                first_line = True
                for chunk in resp.iter_lines(decode_unicode=True):
                    if chunk is None:
                        continue
                    if pi > 0 and first_line:
                        first_line = False  # пропускаем повтор шапки в частях >0
                        continue
                    first_line = False
                    gz.write(chunk + "\n")
            print("      часть {} скачана".format(pi))
    os.replace(tmp, out_path)


def clean_request(request_id):
    requests.post(BASE + "/logrequest/{}/clean".format(request_id), headers=H(), timeout=60)


def dump_source(source, only=None):
    if only and only != source:
        return
    print("\n" + "=" * 64)
    print("ВЫГРУЗКА: {}  ({} полей максимум)".format(source.upper(), len(FIELDS[source])))
    print("=" * 64)
    for d1, d2, tag in PERIODS:
        out_path = os.path.join(OUT_ROOT, source, "ym_{}_{}.tsv.gz".format(source, tag))
        if os.path.exists(out_path):
            print("  {} {} — уже есть, пропускаю".format(source, tag))
            continue
        print("  {} {} ({}..{}):".format(source, tag, d1, d2))
        info, dropped = create_request(d1, d2, FIELDS[source], source)
        rid = info["request_id"]
        print("    request_id={} создан".format(rid))
        if dropped:
            with open(os.path.join(OUT_ROOT, "_log",
                      "dropped_{}_{}.txt".format(source, tag)), "w", encoding="utf-8") as f:
                f.write("\n".join(dropped))
            print("    отбраковано полей: {} (см. _log)".format(len(dropped)))
        info = wait_ready(rid)
        download(rid, info.get("parts", [{}]), out_path)
        clean_request(rid)  # освобождаем слот на стороне API
        sz = os.path.getsize(out_path) / 1e6
        print("    готово -> {} ({:.1f} MB)".format(out_path, sz))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["visits", "hits"], default=None)
    ap.add_argument("--clean-requests", action="store_true")
    args = ap.parse_args()

    ensure_dirs()
    if args.clean_requests:
        print("Чищу зависшие log-запросы на стороне API:")
        clean_stuck_requests()
        return

    print("ПОЛНАЯ ВЫГРУЗКА Метрики — счётчик {}".format(COUNTER))
    print("Период 2026-05-01 .. 2026-07-22, помесячно, формат .tsv.gz")
    dump_source("visits", args.only)
    dump_source("hits", args.only)

    print("\n" + "=" * 64)
    print("ВЫГРУЗКА ЗАВЕРШЕНА. Файлы в папке {}/".format(OUT_ROOT))
    print("Дальше — цели (Ступень 3) и загрузка в DWH.")
    print("Скинь список файлов и их размеры (dir metrica_dump\\visits, \\hits).")
    print("=" * 64)


if __name__ == "__main__":
    main()