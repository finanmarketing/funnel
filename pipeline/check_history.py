import argparse
import json
import os
import sys
from datetime import date, datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAYLOAD = os.path.join(BASE_DIR, "state", "payload.json")
BASELINE = os.path.join(BASE_DIR, "state", "baseline_history.json")

# Tolerances calibrated by probe_maturation (2026-08-13, three months).
# Browser-to-person link matures: -0.5% plateau over 60 days, +0.02%/month after.
TOL_ALL_DOWN = 1.0
TOL_ALL_UP = 0.1
TOL_NEW = 1.5
TOL_STRICT = 2.0
ETALON_TOL = 0.1

# Recalculated 2026-08-13 when counting switched from browsers to persons.
ETALON = {
    "metrica_reg": {"2026-05": 39119, "2026-06": 29995},
    "db_reg": {"2026-05": 42116, "2026-06": 32214},
}

fails = []
stats = {"all": 0.0, "new": 0.0, "strict": 0.0}


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def fail(msg):
    fails.append(msg)
    print(f"  FAIL  {msg}", flush=True)


def ok(msg):
    print(f"  OK    {msg}", flush=True)


def load(path, what):
    if not os.path.exists(path):
        raise RuntimeError(f"{what} not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def closed_months(P):
    cur = date.today().strftime("%Y-%m")
    return [m for m in P["nav"]["months"] if m < cur]


def reg_goal(P):
    return dict([tuple(x) for x in P["funnel"]])["REGISTRATION_PAGE_OK"]


def pct(new, old):
    return 100.0 * (new - old) / old if old else 0.0


def snapshot(P):
    months = closed_months(P)
    return {
        "created": datetime.now().isoformat(timespec="seconds"),
        "unit": P.get("unit", "?"),
        "months": months,
        "month": {m: P["month"][m] for m in months},
        "strict": {m: P["strict"]["month"][m] for m in months
                   if m in P["strict"]["month"]},
        "dbreg": {d: n for d, n in P["dbreg"].items() if d[:7] in months},
    }


def check_etalon(P):
    print("\n[1] Внешний эталон")
    rg = reg_goal(P)
    for m, ref in ETALON["metrica_reg"].items():
        got = P["month"].get(m, {}).get(rg, [0])[0]
        d = pct(got, ref)
        if abs(d) <= ETALON_TOL:
            ok(f"Метрика, регистрации {m}: {got} (эталон {ref}, {d:+.2f}%)")
        else:
            fail(f"Метрика, регистрации {m}: {got}, эталон {ref} ({d:+.2f}%)")
    for m, ref in ETALON["db_reg"].items():
        got = sum(n for d, n in P["dbreg"].items() if d.startswith(m))
        if got == ref:
            ok(f"База, регистрации {m}: {got}")
        else:
            fail(f"База, регистрации {m}: {got}, эталон {ref}")


def check_snapshot(P, B):
    print("\n[2] Закрытые месяцы")
    print(f"  снимок от {B.get('created', '?')}, единица: {B.get('unit', '?')}")

    lost = [m for m in B["months"] if m not in closed_months(P)]
    if lost:
        fail(f"месяцы пропали из payload: {lost}")

    for m in B["months"]:
        cur = P["month"].get(m)
        if cur is None:
            fail(f"{m}: месяц отсутствует в payload")
            continue
        base = B["month"][m]
        hard, soft = [], 0
        for g, v in base.items():
            c = cur.get(g)
            if c is None:
                hard.append(f"{g}: цель исчезла (было {v[0]})")
                continue
            d_all = pct(c[0], v[0])
            stats["all"] = min(stats["all"], d_all)
            if d_all < -TOL_ALL_DOWN or d_all > TOL_ALL_UP:
                hard.append(f"{g} всего: {v[0]} -> {c[0]} ({d_all:+.2f}%)")
            elif d_all != 0:
                soft += 1
            d_new = pct(c[1], v[1])
            stats["new"] = max(stats["new"], abs(d_new))
            if abs(d_new) > TOL_NEW:
                hard.append(f"{g} новые: {v[1]} -> {c[1]} ({d_new:+.2f}%)")
            # Total visits do not depend on the person key -> exact match.
            # Visits of new segment do: is_new is derived per person.
            if len(v) > 3 and len(c) > 3:
                if c[2] != v[2]:
                    hard.append(f"{g} ВИЗИТЫ всего: {v[2]} -> {c[2]}")
                d_vn = pct(c[3], v[3])
                stats["new"] = max(stats["new"], abs(d_vn))
                if abs(d_vn) > TOL_NEW:
                    hard.append(f"{g} визиты новых: {v[3]} -> {c[3]} "
                                f"({d_vn:+.2f}%)")
        if hard:
            fail(f"{m}: нарушений {len(hard)}")
            for h in hard[:5]:
                print(f"          {h}")
        else:
            ok(f"{m}: {len(base)} целей в допуске (дозрело {soft})")

    print("\n[3] Строгая лесенка")
    for m, arr in B["strict"].items():
        cur = P["strict"]["month"].get(m)
        if cur is None:
            fail(f"{m}: строгая лесенка отсутствует")
            continue
        bad, mx = [], 0.0
        for i in range(min(len(cur), len(arr))):
            for j in (0, 1):
                d = pct(cur[i][j], arr[i][j])
                mx = max(mx, abs(d))
                if abs(d) > TOL_STRICT:
                    bad.append(f"шаг {i}[{j}]: {arr[i][j]} -> "
                               f"{cur[i][j]} ({d:+.2f}%)")
        stats["strict"] = max(stats["strict"], mx)
        if bad:
            fail(f"{m}: лесенка вне допуска, {len(bad)} шагов")
            for b in bad[:3]:
                print(f"          {b}")
        else:
            ok(f"{m}: лесенка в допуске (макс отклонение {mx:.2f}%)")

    print("\n[4] Регистрации из базы (точное совпадение)")
    for m in B["months"]:
        bd = {d: n for d, n in B["dbreg"].items() if d.startswith(m)}
        cd = {d: n for d, n in P["dbreg"].items() if d.startswith(m)}
        if bd != cd:
            diff = [d for d in bd if cd.get(d) != bd[d]]
            fail(f"{m}: dbreg изменился в {len(diff)} днях, напр. {diff[:3]}")
        else:
            ok(f"{m}: {len(bd)} дней без изменений")

    print("\n[5] Фактический дрейф")
    print(f"  шаги всего:    {stats['all']:+.2f}%  "
          f"(допуск -{TOL_ALL_DOWN}% .. +{TOL_ALL_UP}%)")
    print(f"  сегмент новые: {stats['new']:.2f}%  (допуск {TOL_NEW}%)")
    print(f"  лесенка:       {stats['strict']:.2f}%  (допуск {TOL_STRICT}%)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--freeze", action="store_true")
    args = ap.parse_args()

    P = load(PAYLOAD, "payload")
    log(f"payload {P['period'][0]}..{P['period'][1]}, {P['period'][2]} дней, "
        f"единица: {P.get('unit', '?')}")

    if args.freeze:
        check_etalon(P)
        if fails:
            print("\nОТКАЗ: эталон не сходится, снимок не снят.")
            return 1
        snap = snapshot(P)
        os.makedirs(os.path.dirname(BASELINE), exist_ok=True)
        with open(BASELINE, "w", encoding="utf-8") as f:
            json.dump(snap, f, ensure_ascii=False, separators=(",", ":"))
        print(f"\nСНИМОК СНЯТ: {BASELINE} "
              f"({os.path.getsize(BASELINE) / 1024:.0f} КБ)")
        print(f"  месяцы: {', '.join(snap['months'])}, единица: {snap['unit']}")
        return 0

    check_etalon(P)

    if not os.path.exists(BASELINE):
        print(f"\n[2] Снимок отсутствует: {BASELINE}")
        print("     Снять: run pipeline\\check_history.py --freeze")
        return 1 if fails else 0

    B = load(BASELINE, "baseline")
    check_snapshot(P, B)

    print("\n" + "=" * 60)
    if fails:
        print(f"РЕЗУЛЬТАТ: ИСТОРИЯ ИЗМЕНИЛАСЬ — {len(fails)} нарушений")
        for f in fails:
            print(f"  FAIL  {f}")
        return 1
    print("РЕЗУЛЬТАТ: история в допуске")
    return 0


if __name__ == "__main__":
    sys.exit(main())