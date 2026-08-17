import argparse
import os
import sys
from datetime import date, datetime, timedelta

import psycopg2
from dotenv import load_dotenv

from funnel_goals import FUNNEL

load_dotenv()

SCHEMA = os.environ["PG_SCHEMA"]
PREFIX = os.environ["TABLE_PREFIX"]
VISITS = f"{SCHEMA}.{PREFIX}visits"

HOST = "substring({col} from '^[a-zA-Z]+://([^/?#]+)')"
PATH = "substring({col} from '^[a-zA-Z]+://[^/]+(/[^?#]*)')"

SUSPECT = ("adm.", "test", "stage", "dev", "localhost", "127.0.0.1",
           "moneza", "finlove", "preprod", "beta")


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def connect():
    return psycopg2.connect(
        host=os.environ["PG_HOST"], port=int(os.environ["PG_PORT"]),
        dbname=os.environ["PG_DB"], user=os.environ["PG_USER"],
        password=os.environ["PG_PASSWORD"], connect_timeout=30,
    )


def url_columns(cur):
    cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema=%s AND table_name=%s "
        "AND (column_name LIKE '%%url%%' OR column_name LIKE '%%referer%%') "
        "ORDER BY ordinal_position",
        (SCHEMA, f"{PREFIX}visits"),
    )
    return [r[0] for r in cur.fetchall()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    args = ap.parse_args()

    d2 = (date.today() - timedelta(days=1)).isoformat()
    d1 = (date.today() - timedelta(days=args.days)).isoformat()
    p = {"d1": d1, "d2": d2}
    log(f"window {d1}..{d2} ({args.days} days)")

    conn = connect()
    cur = conn.cursor()

    cols = url_columns(cur)
    print(f"\n[0] Колонки с адресами: {cols}")
    if not cols:
        print("  адресов в выгрузке нет — проверка невозможна")
        return 1
    col = "ym_s_starturl" if "ym_s_starturl" in cols else cols[0]
    print(f"  анализирую: {col}")

    cur.execute(
        f"SELECT count(*) FROM {VISITS} "
        "WHERE load_date BETWEEN %(d1)s AND %(d2)s", p,
    )
    total = cur.fetchone()[0]
    print(f"\n[1] Всего визитов за период: {total}")

    print("\n[2] Домены входа")
    cur.execute(
        f"SELECT coalesce({HOST.format(col=col)}, '(пусто)') AS h, "
        f"count(*), count(distinct ym_s_clientid) "
        f"FROM {VISITS} WHERE load_date BETWEEN %(d1)s AND %(d2)s "
        "GROUP BY 1 ORDER BY 2 DESC LIMIT 25", p,
    )
    rows = cur.fetchall()
    print(f"  {'домен':<40} {'визитов':>10} {'браузеров':>11} {'доля':>7}")
    print("  " + "-" * 72)
    suspect_visits = 0
    for h, v, c in rows:
        low = (h or "").lower()
        mark = "  <== ПОСТОРОННИЙ" if any(s in low for s in SUSPECT) else ""
        if mark:
            suspect_visits += v
        print(f"  {str(h)[:40]:<40} {v:>10} {c:>11} "
              f"{100.0 * v / total:>6.2f}%{mark}")
    print(f"\n  посторонних визитов в топ-25: {suspect_visits} "
          f"({100.0 * suspect_visits / total:.2f}%)")

    print("\n[3] Посторонние домены и цели воронки")
    where_susp = " OR ".join(
        f"lower({col}) LIKE '%%{s}%%'" for s in SUSPECT
    )
    cur.execute(
        f"SELECT count(*), count(distinct ym_s_clientid), "
        "count(*) FILTER (WHERE ym_s_goalsid IS NOT NULL "
        "AND trim(both '[]' from ym_s_goalsid) <> '') "
        f"FROM {VISITS} WHERE load_date BETWEEN %(d1)s AND %(d2)s "
        f"AND ({where_susp})", p,
    )
    sv, sc, sg = cur.fetchone()
    print(f"  визитов: {sv} ({100.0 * sv / total:.2f}% от всех)")
    print(f"  браузеров: {sc}")
    print(f"  из них с целями: {sg}")

    if sv:
        main_gid = dict(FUNNEL)["MAIN_PAGE_LOADED"]
        reg_gid = dict(FUNNEL)["REGISTRATION_PAGE_OK"]
        for nm, gid in (("MAIN_PAGE_LOADED", main_gid),
                        ("REGISTRATION_PAGE_OK", reg_gid)):
            cur.execute(
                f"SELECT count(distinct ym_s_clientid) FROM {VISITS} "
                "WHERE load_date BETWEEN %(d1)s AND %(d2)s "
                f"AND ({where_susp}) "
                "AND string_to_array(trim(both '[]' from "
                "coalesce(ym_s_goalsid,'')), ',') @> ARRAY[%(g)s]",
                {**p, "g": gid},
            )
            n = cur.fetchone()[0]
            cur.execute(
                f"SELECT count(distinct ym_s_clientid) FROM {VISITS} "
                "WHERE load_date BETWEEN %(d1)s AND %(d2)s "
                "AND string_to_array(trim(both '[]' from "
                "coalesce(ym_s_goalsid,'')), ',') @> ARRAY[%(g)s]",
                {**p, "g": gid},
            )
            allc = cur.fetchone()[0]
            print(f"  {nm}: {n} из {allc} "
                  f"({100.0 * n / allc if allc else 0:.2f}%)")

    print("\n[4] Топ страниц входа (пути)")
    cur.execute(
        f"SELECT coalesce({PATH.format(col=col)}, '(пусто)') AS pth, "
        f"count(*) FROM {VISITS} "
        "WHERE load_date BETWEEN %(d1)s AND %(d2)s "
        "GROUP BY 1 ORDER BY 2 DESC LIMIT 20", p,
    )
    for pth, v in cur.fetchall():
        mark = "  <== ПОДОЗРИТЕЛЬНО" if "undefined" in str(pth).lower() else ""
        print(f"  {str(pth)[:56]:<56} {v:>9} "
              f"{100.0 * v / total:>6.2f}%{mark}")

    print("\n[5] Адреса, содержащие undefined")
    cur.execute(
        f"SELECT count(*), count(distinct ym_s_clientid) FROM {VISITS} "
        "WHERE load_date BETWEEN %(d1)s AND %(d2)s "
        f"AND lower({col}) LIKE '%%undefined%%'", p,
    )
    uv, uc = cur.fetchone()
    print(f"  визитов: {uv} ({100.0 * uv / total:.3f}%), браузеров: {uc}")

    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())