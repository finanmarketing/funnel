import os
import smtplib
import ssl
import sys

import psycopg2
import requests
from dotenv import load_dotenv

load_dotenv()

ok = True


def check(name, fn):
    global ok
    try:
        print(f"[ OK ] {name}: {fn()}")
    except Exception as e:
        ok = False
        print(f"[FAIL] {name}: {type(e).__name__}: {e}")


def env_sanity():
    p = os.environ["PG_PASSWORD"]
    return f"PG_PASSWORD len={len(p)}, first={p[0]!r}, last={p[-1]!r}"


def pg():
    conn = psycopg2.connect(
        host=os.environ["PG_HOST"],
        port=int(os.environ["PG_PORT"]),
        dbname=os.environ["PG_DB"],
        user=os.environ["PG_USER"],
        password=os.environ["PG_PASSWORD"],
        connect_timeout=15,
    )
    with conn, conn.cursor() as cur:
        cur.execute(
            "SELECT current_database(), count(*) FROM public.clients "
            "WHERE entity_created >= %s",
            ("2026-05-01",),
        )
        row = cur.fetchone()
    conn.close()
    return f"db={row[0]}, clients since 2026-05-01: {row[1]}"


def metrica():
    r = requests.get(
        "https://api-metrika.yandex.ru/management/v1/counter/"
        f"{os.environ['METRICA_COUNTER_ID']}",
        headers={"Authorization": f"OAuth {os.environ['METRICA_TOKEN']}"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["counter"]["name"]


def smtp():
    host = os.environ["SMTP_HOST"]
    port = int(os.environ["SMTP_PORT"])
    s = smtplib.SMTP(host, port, timeout=30)
    s.ehlo()
    if port == 587:
        s.starttls(context=ssl.create_default_context())
        s.ehlo()
    pwd = os.environ.get("SMTP_PASS", "")
    if pwd:
        s.login(os.environ["SMTP_USER"], pwd)
        result = "connect + auth ok"
    else:
        result = "connect ok, auth NOT tested (SMTP_PASS empty)"
    s.quit()
    return result


check("ENV parse", env_sanity)
check("PostgreSQL", pg)
check("Metrica API", metrica)
check("SMTP", smtp)

print("-" * 50)
print("RESULT:", "ALL GREEN" if ok else "HAS FAILURES")
sys.exit(0 if ok else 1)