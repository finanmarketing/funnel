import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULES = ("requests", "psycopg2", "dotenv")
ENV_KEYS = ("PG_HOST", "PG_PORT", "PG_DB", "PG_USER", "PG_PASSWORD",
            "PG_SCHEMA", "TABLE_PREFIX", "METRICA_TOKEN",
            "METRICA_COUNTER_ID", "SMTP_HOST", "MAIL_TO", "PYTHON_EXE")


def main():
    print(f"version: {sys.version.split()[0]}")
    print(f"executable: {sys.executable}")

    bad = []
    for m in MODULES:
        try:
            __import__(m)
        except Exception:
            bad.append(m)
    print(f"modules: {'ok' if not bad else 'MISSING ' + ', '.join(bad)}")
    if bad:
        print(f'  install: "{sys.executable}" -m pip install '
              "requests psycopg2-binary python-dotenv")
        return 1

    env_path = os.path.join(BASE_DIR, ".env")
    if not os.path.exists(env_path):
        print(".env: MISSING")
        return 1
    from dotenv import load_dotenv
    load_dotenv(env_path)
    missing = [k for k in ENV_KEYS if not os.environ.get(k)]
    print(f".env: found, keys {'ok' if not missing else 'MISSING ' + ', '.join(missing)}")

    print("\nUsage:")
    print("  run pipeline\\run_pipeline.py")
    print("  run pipeline\\build_payload.py --date2 2026-08-11")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())