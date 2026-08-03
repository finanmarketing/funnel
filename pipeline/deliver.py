import json
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
PAYLOAD = os.path.join(BASE_DIR, "state", "payload.json")

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


def send_mail(subject, body, attach=None, to=None):
    msg = EmailMessage()
    msg["From"] = os.environ["SMTP_FROM"]
    raw = to if to else os.environ["MAIL_TO"]
    recipients = [a.strip() for a in raw.split(",") if a.strip()]
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


def deliver_success():
    if not os.path.exists(LATEST):
        raise RuntimeError(f"file not found: {LATEST}")
    size = os.path.getsize(LATEST)
    if size < 500_000:
        raise RuntimeError(f"file too small: {size} bytes")

    with open(PAYLOAD, "r", encoding="utf-8") as f:
        period = json.load(f)["period"]
    tag = f"{period[0]}_{period[1]}"
    named = os.path.join(OUTDIR, f"funnel_dashboard_{tag}.html")
    attach = named if os.path.exists(named) else LATEST
    log(f"delivering {attach} ({size / 1024 / 1024:.2f} MB), mode={MODE}")

    if MODE == "smtp":
        subject = f"Воронка eZaem — {period[0]} — {period[1]}"
        body = (
            f"Дашборд воронки eZaem обновлён автоматически.\n\n"
            f"Период в файле: {period[0]} — {period[1]} ({period[2]} дней)\n"
            f"Источник: Яндекс.Метрика, счётчик 21703744 + БД dwh_ezru_loans\n\n"
            f"Файл во вложении — откройте в браузере.\n"
            f"Данные последних 3 дней могут уточняться: Метрика "
            f"достраивает визиты постфактум.\n"
        )
        rec = send_mail(subject, body, attach)
        log(f"sent to: {rec}")
    else:
        target_dir = DELIVERY_DIR
        if not os.path.isabs(target_dir):
            target_dir = os.path.join(BASE_DIR, target_dir)
        os.makedirs(target_dir, exist_ok=True)
        dst = os.path.join(target_dir, os.path.basename(attach))
        if os.path.abspath(dst) != os.path.abspath(attach):
            shutil.copyfile(attach, dst)
        log(f"copied to: {dst}")
    return size


def classify(err):
    t = str(err)
    if "timeout expired" in t or "Connection timed out" in t or "10060" in t:
        return ("Нет связи с DWH (10.174.17.38:5432)",
                "Скорее всего отвалился корпоративный VPN. "
                "Подключить VPN и запустить: python pipeline\\run_pipeline.py")
    if "password authentication failed" in t:
        return ("Отказ авторизации в PostgreSQL",
                "Проверить PG_USER / PG_PASSWORD в .env.")
    if "api-metrika" in t or "logrequest" in t or "ChunkedEncoding" in t:
        return ("Сбой Logs API Яндекс.Метрики",
                "Проверить METRICA_TOKEN и висящие заказы: "
                "python pipeline\\ingest_metrica.py --cleanup")
    if "SmtpClientAuthentication" in t or "5.7.139" in t:
        return ("Отказ SMTP AUTH на арендаторе Microsoft 365",
                "Временно переключить DELIVERY_MODE=copy, завести заявку в ИТ.")
    if "QC:" in t:
        return ("Провален контроль качества данных",
                "Расхождение Метрика/БД вне коридора или отсутствуют шаги. "
                "Разобрать до рассылки — цифры недостоверны.")
    return ("Неклассифицированная ошибка", "Смотреть лог в logs\\ и журнал прогонов.")


def deliver_failure(stage, err):
    """Письмо о сбое — только владельцу пайплайна (ALERT_TO)."""
    day = (date.today() - timedelta(days=1)).isoformat()
    reason, action = classify(err)
    alert_to = os.environ.get("ALERT_TO") or os.environ["SMTP_FROM"]
    subject = f"[СБОЙ] Воронка eZaem — {stage} — {day}"
    body = (
        f"Автоматический пайплайн воронки завершился с ошибкой.\n\n"
        f"Этап:      {stage}\n"
        f"Причина:   {reason}\n"
        f"Что делать: {action}\n"
        f"Время:     {datetime.now():%Y-%m-%d %H:%M:%S}\n\n"
        f"Дашборд НЕ обновлён, рассылка получателям НЕ выполнена.\n"
        f"Журнал прогонов: {RUNS}\n\n"
        f"--- технический текст ошибки ---\n{str(err)[:2000]}\n"
    )
    try:
        if MODE == "smtp":
            rec = send_mail(subject, body, to=alert_to)
            log(f"failure notification sent to: {rec}")
        else:
            log(f"failure (mode=copy, no mail): {stage}: {reason}")
    except Exception as e:
        log(f"failure notification itself failed: {type(e).__name__}: {e}")


def main():
    run_id = journal_start("deliver")
    try:
        size = deliver_success()
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