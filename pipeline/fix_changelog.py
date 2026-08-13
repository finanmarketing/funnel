import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(BASE_DIR, "state", "db_changelog.md")
MARKER = "## 2026-08-13"

ENTRY = """
## 2026-08-13. Переход на подсчёт людей вместо браузеров

Единица счёта изменена: браузер (clientID) -> человек.
Ключ человека = UserID из parsedParams, иначе br:clientID.
UserID = clients.client_number, совпадение 100% на 383 882 значениях.

Добавлена таблица metrica_person_map (браузер -> человек), строится
скриптом build_person_map.py перед расчётом payload.
Схлопывание: 762 552 браузера -> 653 053 человека (14.4%).

Эталон пересчитан: май 39158 -> 39119, июнь 30034 -> 29995.
Прежний эталон относился к браузерам и более не применяется.

Допуски check_history откалиброваны замером дозревания
(probe_maturation, три месяца): плато дрейфа -0.5% за 60 дней,
далее +0.02% в месяц. Глубокие шаги воронки практически неподвижны
(регистрация -0.01%). Визиты у событий от ключа человека не зависят
и проверяются на точное совпадение.

ИСПРАВЛЕНИЕ ДОКУМЕНТАЦИИ: ранее было записано
ym_s_clientid = clients.client_number. Это неверно.
ym_s_clientid - внутренний идентификатор Метрики (до 20 знаков),
к номерам клиентов отношения не имеет. Связь идёт через параметр
UserID внутри визита (parsedParamsKey1..3, плоская и вложенная формы).
"""


def read_any(path):
    with open(path, "rb") as f:
        raw = f.read()
    for enc in ("utf-8", "utf-8-sig", "cp1251"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8/replace"


def main():
    if not os.path.exists(PATH):
        print(f"FAIL: {PATH} not found")
        return 1
    text, enc = read_any(PATH)
    print(f"read as {enc}: {len(text)} chars")

    idx = text.find(MARKER)
    if idx >= 0:
        text = text[:idx].rstrip()
        print(f"removed broken entry at {idx}")
    else:
        text = text.rstrip()
        print("no previous entry, appending")

    text += "\n" + ENTRY
    with open(PATH, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print(f"written as utf-8: {len(text)} chars")

    check, enc2 = read_any(PATH)
    print(f"verify: reads back as {enc2}, "
          f"marker {'present' if MARKER in check else 'MISSING'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())