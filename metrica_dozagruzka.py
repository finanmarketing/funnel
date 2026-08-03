#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ДОГРУЗКА визитов за произвольный период (для обновления дашборда).

Тянет только ВИЗИТЫ — дашборд построен на них, хиты не нужны.
Это втрое быстрее полной выгрузки.

По умолчанию: 2026-07-23 .. 2026-07-30 (продолжение после имеющихся данных).
Можно задать свои даты:
    python metrica_dozagruzka.py 2026-07-23 2026-07-30

Результат: metrica_dump/visits/ym_visits_<date1>_<date2>.tsv.gz
Токен — из .env (METRICA_TOKEN), как в остальных скриптах.

Есть ретраи на обрыв связи и авто-отбраковка полей, которые API не принимает.
"""

import os, sys, time, gzip
import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

COUNTER = os.getenv("METRICA_COUNTER", "21703744")
TOKEN   = os.getenv("METRICA_TOKEN")
BASE    = "https://api-metrika.yandex.ru/management/v1/counter/{}".format(COUNTER)
OUT_DIR = os.path.join("metrica_dump", "visits")

DATE1 = sys.argv[1] if len(sys.argv) > 1 else "2026-07-23"
DATE2 = sys.argv[2] if len(sys.argv) > 2 else "2026-07-30"

MAX_RETRY, TIMEOUT = 5, 300

# Те же поля, что в основной выгрузке. Четыре поля API стабильно отклоняет
# (goalsSerialID, browserCountry, browser, browserCountryOfBase) —
# авто-отбраковка уберёт их сама, как и в прошлый раз.
FIELDS = ("ym:s:visitID ym:s:counterID ym:s:watchIDs ym:s:date ym:s:dateTime "
 "ym:s:dateTimeUTC ym:s:isNewUser ym:s:startURL ym:s:endURL ym:s:pageViews "
 "ym:s:visitDuration ym:s:bounce ym:s:ipAddress ym:s:regionCountry ym:s:regionCity "
 "ym:s:regionCountryID ym:s:regionCityID ym:s:clientID ym:s:networkType ym:s:goalsID "
 "ym:s:goalsDateTime ym:s:goalsPrice ym:s:goalsOrder ym:s:goalsCurrency "
 "ym:s:lastTrafficSource ym:s:lastAdvEngine ym:s:lastReferalSource ym:s:lastSearchEngineRoot "
 "ym:s:lastSearchEngine ym:s:lastSocialNetwork ym:s:lastSocialNetworkProfile ym:s:referer "
 "ym:s:lastDirectClickOrder ym:s:lastDirectBannerGroup ym:s:lastDirectClickBanner "
 "ym:s:lastDirectClickOrderName ym:s:lastClickBannerGroupName ym:s:lastDirectClickBannerName "
 "ym:s:lastDirectPhraseOrCond ym:s:lastDirectPlatformType ym:s:lastDirectPlatform "
 "ym:s:lastDirectConditionType ym:s:lastCurrencyID ym:s:from ym:s:UTMCampaign ym:s:UTMContent "
 "ym:s:UTMMedium ym:s:UTMSource ym:s:UTMTerm ym:s:openstatAd ym:s:openstatCampaign "
 "ym:s:openstatService ym:s:openstatSource ym:s:hasGCLID ym:s:lastGCLID ym:s:browserLanguage "
 "ym:s:clientTimeZone ym:s:deviceCategory ym:s:mobilePhone ym:s:mobilePhoneModel "
 "ym:s:operatingSystemRoot ym:s:operatingSystem ym:s:browserMajorVersion ym:s:browserMinorVersion "
 "ym:s:browserEngine ym:s:browserEngineVersion1 ym:s:cookieEnabled ym:s:javascriptEnabled "
 "ym:s:screenFormat ym:s:screenColors ym:s:screenOrientation ym:s:screenWidth ym:s:screenHeight "
 "ym:s:physicalScreenWidth ym:s:physicalScreenHeight ym:s:windowClientWidth ym:s:windowClientHeight "
 "ym:s:purchaseID ym:s:purchaseDateTime ym:s:purchaseRevenue ym:s:purchaseCurrency "
 "ym:s:parsedParamsKey1 ym:s:parsedParamsKey2 ym:s:parsedParamsKey3 ym:s:parsedParamsKey4 "
 "ym:s:parsedParamsKey5 ym:s:parsedParamsKey6 ym:s:parsedParamsKey7 ym:s:parsedParamsKey8 "
 "ym:s:parsedParamsKey9 ym:s:parsedParamsKey10").split()


def H():
    if not TOKEN:
        sys.exit("ERROR: не задан METRICA_TOKEN (положите его в .env)")
    return {"Authorization": "OAuth " + TOKEN}


def get_retry(url, **kw):
    last = None
    for a in range(1, MAX_RETRY + 1):
        try:
            r = requests.get(url, headers=H(), timeout=TIMEOUT, **kw)
            if r.status_code == 200 or 400 <= r.status_code < 500:
                return r
            last = "HTTP {}".format(r.status_code)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            last = str(e)[:120]
        w = 2 ** a
        print("    ретрай {}/{} через {}с ({})".format(a, MAX_RETRY, w, last))
        time.sleep(w)
    raise RuntimeError("исчерпаны ретраи: {}".format(last))


def evaluate():
    r = get_retry(BASE + "/logrequests/evaluate",
                  params={"date1": DATE1, "date2": DATE2,
                          "fields": ",".join(FIELDS[:6]), "source": "visits"})
    if r.status_code != 200:
        print("  evaluate вернул {}: {}".format(r.status_code, r.text[:250]))
        return False
    ok = r.json()["log_request_evaluation"]["possible"]
    print("  выгрузка одним запросом: {}".format("ДА" if ok else "НЕТ"))
    return ok


def create():
    cur, dropped = list(FIELDS), []
    for _ in range(len(FIELDS) + 2):
        r = requests.post(BASE + "/logrequests", headers=H(),
                          params={"date1": DATE1, "date2": DATE2, "source": "visits",
                                  "fields": ",".join(cur)}, timeout=90)
        if r.status_code == 200:
            return r.json()["log_request"], dropped
        bad = next((f for f in cur if f in r.text), None)
        if bad:
            cur.remove(bad); dropped.append(bad)
            print("    ! поле {} отклонено — убираю".format(bad))
            continue
        raise RuntimeError("create {}: {}".format(r.status_code, r.text[:300]))
    raise RuntimeError("слишком много отбракованных полей")


def wait(rid):
    t0 = time.time()
    while True:
        r = get_retry(BASE + "/logrequest/{}".format(rid))
        r.raise_for_status()
        info = r.json()["log_request"]; s = info["status"]; el = int(time.time() - t0)
        if s == "processed":
            print("    [{:>4}s] готово, частей: {}".format(el, len(info.get("parts", []))))
            return info
        if s in ("canceled", "processing_failed", "cleaned_by_user", "expired"):
            raise RuntimeError("статус {}".format(s))
        print("    [{:>4}s] {} ...".format(el, s))
        time.sleep(10)


def download(rid, parts, path):
    tmp = path + ".part"
    with gzip.open(tmp, "wt", encoding="utf-8", newline="") as gz:
        for pi in range(len(parts)):
            with requests.get(BASE + "/logrequest/{}/part/{}/download".format(rid, pi),
                              headers=H(), stream=True, timeout=900) as resp:
                resp.raise_for_status()
                first = True
                for line in resp.iter_lines(decode_unicode=True):
                    if line is None:
                        continue
                    if pi > 0 and first:
                        first = False; continue
                    first = False
                    gz.write(line + "\n")
            print("      часть {} скачана".format(pi))
    os.replace(tmp, path)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, "ym_visits_{}_{}.tsv.gz".format(DATE1, DATE2))
    print("ДОГРУЗКА визитов {} .. {} (счётчик {})".format(DATE1, DATE2, COUNTER))
    if os.path.exists(path):
        print("Файл уже есть: {} — удалите, если нужна перевыгрузка.".format(path)); return
    evaluate()
    info, dropped = create()
    rid = info["request_id"]
    print("  request_id={}".format(rid))
    if dropped:
        print("  отбраковано полей: {}".format(len(dropped)))
    info = wait(rid)
    download(rid, info.get("parts", [{}]), path)
    requests.post(BASE + "/logrequest/{}/clean".format(rid), headers=H(), timeout=60)
    mb = os.path.getsize(path) / 1e6
    print("\nГОТОВО -> {} ({:.1f} MB)".format(path, mb))
    print("Пришлите этот файл — пересоберу дашборд с данными по {}.".format(DATE2))


if __name__ == "__main__":
    main()