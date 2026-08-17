import os
import sys

import psycopg2
import requests
from dotenv import load_dotenv

from funnel_goals import FUNNEL

load_dotenv()

S = os.environ["PG_SCHEMA"]
P = os.environ["TABLE_PREFIX"]
V = f"{S}.{P}visits"
M = f"{S}.{P}person_map"
APPS = "public.loan_applications"
STEPS = "public.risk_finished_detail_steps"
D1, D2 = "2026-07-01", "2026-07-31"
CONF = dict(FUNNEL)["CONFIRM_PAGE_OK"]
API = "https://api-metrika.yandex.ru/stat/v1/data"


def conn():
    return psycopg2.connect(
        host=os.environ["PG_HOST"], port=int(os.environ["PG_PORT"]),
        dbname=os.environ["PG_DB"], user=os.environ["PG_USER"],
        password=os.environ["PG_PASSWORD"], connect_timeout=60)


def main():
    print("START", flush=True)
    c = conn()
    cur = c.cursor()
    p = {"d1": D1, "d2": D2, "g": CONF}

    print("\n[1] UserID: сколько значений отброшено фильтром")
    cur.execute(f"SELECT count(*), count(uid) FROM {M}")
    tot, withuid = cur.fetchone()
    cur.execute(
        f"SELECT count(*) FROM {M} WHERE uid IS NOT NULL "
        "AND uid !~ '^[0-9]+$'")
    nonnum = cur.fetchone()[0]
    cur.execute(
        f"SELECT count(*) FROM {M} m LEFT JOIN public.clients cl "
        "ON cl.client_number::text = m.uid "
        "WHERE m.uid IS NOT NULL AND cl.client_number IS NULL")
    miss = cur.fetchone()[0]
    print(f"  браузеров {tot}, с UserID {withuid}")
    print(f"  нечисловых UserID: {nonnum}")
    print(f"  UserID без пары в clients: {miss}")

    print("\n[2] clientID='0' и его вклад в число людей")
    cur.execute(
        f"SELECT count(*) FROM {V} WHERE load_date BETWEEN %(d1)s AND %(d2)s "
        "AND ym_s_clientid = '0'", p)
    z = cur.fetchone()[0]
    cur.execute(
        f"SELECT count(DISTINCT coalesce(m.pkey,'br:'||t.ym_s_clientid)) "
        f"FROM {V} t LEFT JOIN {M} m ON m.cid=t.ym_s_clientid "
        "WHERE t.load_date BETWEEN %(d1)s AND %(d2)s", p)
    ppl = cur.fetchone()[0]
    print(f"  визитов с clientID='0': {z}")
    print(f"  всего людей за июль: {ppl} (включая 'br:0' как одного)")

    print("\n[3] 98.9%: заявка ПОСЛЕ веб-шага, а не любая")
    web = f"""
    WITH v AS (SELECT coalesce(m.pkey,'br:'||t.ym_s_clientid) AS pkey,
      t.load_date AS d,
      nullif(trim(both '[]' from coalesce(t.ym_s_goalsid,'')),'') AS gs
      FROM {V} t LEFT JOIN {M} m ON m.cid=t.ym_s_clientid
      WHERE t.load_date BETWEEN %(d1)s AND %(d2)s),
    e AS (SELECT v.pkey, v.d, g.gid FROM v
      CROSS JOIN LATERAL unnest(string_to_array(v.gs,',')) AS g(gid)
      WHERE v.gs IS NOT NULL),
    conf AS (SELECT pkey, min(d) AS wd FROM e WHERE gid=%(g)s GROUP BY 1)
    """
    for label, extra in (
            ("любая заявка в периоде +7д",
             "AND la.entity_created::date BETWEEN %(d1)s AND (%(d2)s::date+7)"),
            ("заявка НЕ РАНЬШЕ веб-шага",
             "AND la.entity_created::date >= c.wd "
             "AND la.entity_created::date <= c.wd + 7")):
        cur.execute(
            web + "SELECT count(*) FROM conf c WHERE EXISTS ("
            f"SELECT 1 FROM {APPS} la JOIN public.clients cl "
            "ON cl.id=la.client_id WHERE cl.client_number::text=c.pkey "
            "AND la.is_additional_amount_application='f' " + extra + ")", p)
        n = cur.fetchone()[0]
        cur.execute(web + "SELECT count(*) FROM conf", p)
        tt = cur.fetchone()[0]
        print(f"  {label:<32} {n}/{tt} = {100.0*n/tt:.1f}%")

    print("\n[4] Соответствие шагу 9: ровно 9 и 9+")
    for label, cond in (("max_step = 9", "= 9"), ("max_step >= 9", ">= 9")):
        cur.execute(
            web + f"SELECT count(DISTINCT cl.client_number::text) "
            f"FROM {APPS} la JOIN LATERAL (SELECT max(finished_details_step) "
            f"AS mx FROM {STEPS} r WHERE r.loan_application_id=la.id) s ON true "
            "JOIN public.clients cl ON cl.id=la.client_id "
            "JOIN conf c ON c.pkey=cl.client_number::text "
            "WHERE la.entity_created::date BETWEEN %(d1)s AND %(d2)s "
            f"AND la.is_additional_amount_application='f' AND s.mx {cond}", p)
        both = cur.fetchone()[0]
        cur.execute(
            f"SELECT count(DISTINCT la.client_id) FROM {APPS} la "
            f"JOIN LATERAL (SELECT max(finished_details_step) AS mx "
            f"FROM {STEPS} r WHERE r.loan_application_id=la.id) s ON true "
            "WHERE la.entity_created::date BETWEEN %(d1)s AND %(d2)s "
            f"AND la.is_additional_amount_application='f' AND s.mx {cond}", p)
        allc = cur.fetchone()[0]
        print(f"  {label:<16} клиентов {allc}, с веб-следом {both} "
              f"= {100.0*both/allc if allc else 0:.1f}%")

    print("\n[5] Коэффициент повторных заходов В ЛЮДЯХ")
    cur.execute(
        f"SELECT sum(u) FROM (SELECT count(DISTINCT "
        f"coalesce(m.pkey,'br:'||t.ym_s_clientid)) AS u FROM {V} t "
        f"LEFT JOIN {M} m ON m.cid=t.ym_s_clientid "
        "WHERE t.load_date BETWEEN %(d1)s AND %(d2)s GROUP BY t.load_date) x",
        p)
    ds = cur.fetchone()[0]
    print(f"  сумма дневных: {ds}, месячная: {ppl}, "
          f"коэффициент {ds/ppl:.3f}")

    print("\n[6] Сверка ЛЮДЕЙ с API Метрики за июль")
    try:
        r = requests.get(API, headers={
            "Authorization": f"OAuth {os.environ['METRICA_TOKEN']}"},
            params={"ids": os.environ["METRICA_COUNTER_ID"],
                    "metrics": "ym:s:users,ym:s:visits",
                    "date1": D1, "date2": D2, "accuracy": "full"},
            timeout=90)
        if r.status_code == 200:
            m = r.json()["totals"]
            print(f"  Метрика: users={int(m[0])}, visits={int(m[1])}")
            cur.execute(
                f"SELECT count(*), count(DISTINCT ym_s_clientid) FROM {V} "
                "WHERE load_date BETWEEN %(d1)s AND %(d2)s", p)
            ov, obr = cur.fetchone()
            print(f"  наши:    визитов={ov}, браузеров={obr}, людей={ppl}")
            print(f"  визиты:   {100.0*(ov-int(m[1]))/int(m[1]):+.2f}%")
            print(f"  браузеры: {100.0*(obr-int(m[0]))/int(m[0]):+.2f}%")
        else:
            print(f"  HTTP {r.status_code}: {r.text[:200]}")
    except Exception as e:
        print(f"  ошибка: {type(e).__name__}: {e}")

    cur.close()
    c.close()
    print("\nDONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())