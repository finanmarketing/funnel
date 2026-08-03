import os
import shutil
import smtplib
import ssl
import sys
from datetime import date, datetime, timedelta
from email.message import EmailMessage

import psycopg2
from dotenv import load_dotenv

load_dotenv()

SCHEMA = os.environ["PG_SCHEMA"]
PREFIX = os.environ["TABLE_PREFIX"]
RUNS = f"{SCHEMA}.{PREFIX}pipeline_runs"

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTDIR = os.path.join(BASE_DIR, "out")
LATEST = os.path.join(OUTDIR, "funnel_dashboard_latest.html")

MODE = os.environ.get("DELIVERY_MODE", "copy")
DELIVERY_DIR = os.environ.get("DELIVERY_DIR", "out")


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def connect():
    return psycopg2.connect(
        host=os.environ["PG_HOST"], port=int(os.environ["PG_PORT"]),
        dbname=os.environ["PG_DB"], user=os.environ["PG_USER"],
        password=os.environ["PG_PASSWORD"], connect_timeout=15,
    )


def journal_start(stage):
    conn = connect()
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO {RUNS}(stage,status) VALUES (%s,'start') RETURNING run_id",
            (stage,),
        )
        rid = cur.fetchone()[0]
    conn.close()
    return rid


def journal_end(run_id, status, rows=None, err=None):
    conn = connect()
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(
            f"UPDATE {RUNS} SET finished_at=now(), status=%s, rows_loaded=%s, "
            "error_text=%s WHERE run_id=%s",
            (status, rows, (str(err)[:4000] if err else None), run_id),
        )
    conn.close()


def send_mail(subject, body, attach=None):
    msg = EmailMessage()
    msg["From"] = os.environ["SMTP_FROM"]
    recipients = [a.strip() for a in os.environ["MAIL_TO"].split(",") if a.strip()]
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.set_content(body)
    if attach:
        with open(attach, "rb") as f:
            msg.add_attachment(
                f.read(), maintype="text", subtype="html",
                filename=os.path.basename(attach),
            )
    host = os.environ["SMTP_HOST"]
    port = int(os.environ["SMTP_PORT"])
    s = smtplib.SMTP(host, port, timeout=60)
    s.ehlo()
    if port == 587:
        s.starttls(context=ssl.create_default_context())
        s.ehlo()
    pwd = os.environ.get("SMTP_PASS", "")
    if pwd:
        s.login(os.environ["SMTP_USER"], pwd)
    s.send_message(msg)
    s.quit()
    return recipients


def deliver_success(day):
    if not os.path.exists(LATEST):
        raise RuntimeError(f"file not found: {LATEST}")
    size = os.path.getsize(LATEST)
    if size < 500_000:
        raise RuntimeError(f"file too small: {size} bytes")
    log(f"delivering {LATEST} ({size / 1024 / 1024:.2f} MB), mode={MODE}")

    if MODE == "smtp":
        subject = f"Воронка eZaem — данные по {day}"
        body = (
            f"Дашборд воронки eZaem обновлён автоматически.\n\n"
            f"Период: 2026-05-01 — {day}\n"
            f"Источник: Яндекс.Метрика, счётчик 21703744 + БД dwh_ezru_loans\n\n"
            f"Файл во вложении — откройте в браузере.\n"
            f"Данные последних 3 дней могут уточняться: Метрика "
            f"достраивает визиты постфактум.\n"
        )
        rec = send_mail(subject, body, LATEST)
        log(f"sent to: {rec}")
    else:
        target_dir = DELIVERY_DIR
        if not os.path.isabs(target_dir):
            target_dir = os.path.join(BASE_DIR, target_dir)
        os.makedirs(target_dir, exist_ok=True)
        dst = os.path.join(target_dir, "funnel_dashboard_latest.html")
        if os.path.abspath(dst) != os.path.abspath(LATEST):
            shutil.copyfile(LATEST, dst)
        log(f"copied to: {dst}")
    return size


def deliver_failure(stage, err):
    """Письмо о сбое. Вызывается оркестратором при падении любого этапа."""
    day = (date.today() - timedelta(days=1)).isoformat()
    subject = f"[СБОЙ] Воронка eZaem — {day}"
    body = (
        f"Автоматический пайплайн воронки завершился с ошибкой.\n\n"
        f"Этап: {stage}\n"
        f"Ошибка: {err}\n"
        f"Время: {datetime.now():%Y-%m-%d %H:%M:%S}\n\n"
        f"Дашборд НЕ обновлён. Журнал: {RUNS}\n"
    )
    try:
        if MODE == "smtp":
            send_mail(subject, body)
            log("failure notification sent")
        else:
            log(f"failure (mode=copy, no mail): {stage}: {err}")
    except Exception as e:
        log(f"failure notification itself failed: {type(e).__name__}: {e}")


def main():
    day = (date.today() - timedelta(days=1)).isoformat()
    run_id = journal_start("deliver")
    try:
        size = deliver_success(day)
        journal_end(run_id, "ok", size)
        log("DONE deliver")
        return 0
    except Exception as e:
        journal_end(run_id, "fail", None, e)
        log(f"FAIL deliver: {type(e).__name__}: {e}")
        deliver_failure("deliver", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())