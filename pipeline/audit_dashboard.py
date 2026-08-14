import json
import os
import sys
from datetime import date, timedelta

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TPL = os.path.join(BASE_DIR, "pipeline", "dashboard_template.html")
HTML = os.path.join(BASE_DIR, "out", "funnel_dashboard_latest.html")

TOL = 101.0
UNANCHORED = {}

MARKERS = [
    ("p1 helper VV", "function VV(c,g)"),
    ("p1 css bout", ".bout{"),
    ("p1 bar label out", "w<25?`<span class="),
    ("p1 event den", "const den=ua?0:V(c,gid)"),
    ("p1 visits sub", "sub vis"),
    ("p1 event header", "уникальные посетители"),
    ("p2 DIMBASE", "function DIMBASE(g)"),
    ("p2 UNANCH set", "const UNANCH=new Set"),
    ("p2 ladder tooltip", "переход от предыдущего шага"),
    ("p2 unanchored label", "экран уточняется"),
    ("p2 dim denominator", "DIMBASE(gid)||vs.reduce"),
    ("p2 dim header", "% от посетителей шага"),
    ("p3 raw visits agg", "a[g]=[0,0,0,0]"),
    ("p3 hour events note", "на почасовом уровне события не рассчитываются"),
    ("p3 db gap raw", "V(c,REGOK)/dbn"),
    ("p3 all-level warning", "суммируются по месяцам"),
    ("p4 apply container", 'id="applyf"'),
    ("p4 chain signature", "function chain(list,c,isL,isF)"),
    ("p4 chain denominator", "dn0=(!isF&&(mode===1||isL))"),
    ("p4 apply render", "chain(DATA.apply"),
    ("p5 generic tooltip", "'доля от «'+(SL[list[0][0]]"),
    ("p5 recovery container", 'id="recoveryf"'),
    ("p5 pwa container", 'id="pwaf"'),
    ("p5 recovery render", "chain(DATA.recovery"),
    ("p5 pwa render", "chain(DATA.pwa"),
    ("p5 recovery labels", "SL['FORGOT_PASSWORD']"),
    ("p5 pwa labels", "SL['PWA_LAUNCH_WEB']"),
]

CHAIN_KEYS = [("apply", "Заявка и решение"),
              ("recovery", "Восстановление доступа"),
              ("pwa", "Запуск и установка приложения")]

# Steps that must not exceed the previous one within a chain.
CHAIN_MONOTONIC = {
    "pwa": [("PWA_INSTALLED", "PWA_INSTALL_ACCEPTED")],
    "recovery": [("FORGOT_PHONE_COMPLETE", "FORGOT_PHONE"),
                 ("FORGOT_EMAIL_COMPLETE", "FORGOT_EMAIL")],
}

fails = []
warns = []


def ok(msg):
    print(f"  OK    {msg}")


def warn(msg):
    warns.append(msg)
    print(f"  WARN  {msg}")


def fail(msg):
    fails.append(msg)
    print(f"  FAIL  {msg}")


def load_payload(path):
    with open(path, "r", encoding="utf-8") as f:
        s = f.read()
    i = s.find("const DATA=")
    if i < 0:
        raise RuntimeError("const DATA= not found")
    i += len("const DATA=")
    j = s.find("const SL=", i)
    k = s.rindex(";", i, j)
    return json.loads(s[i:k]), s


def steps_map(D):
    m = {}
    for key in ("funnel", "login", "apply", "recovery", "pwa"):
        for pair in D.get(key, []):
            m.setdefault(pair[0], pair[1])
    return m


def month_covered(D, gid, mo):
    """True if the goal has data inside that month."""
    cov = D["cov"].get(gid)
    if not cov:
        return False
    return cov[0][:7] <= mo <= cov[1][:7]


def check_markers():
    print("\n[1] Маркеры патчей в шаблоне")
    with open(TPL, "r", encoding="utf-8") as f:
        t = f.read()
    for name, m in MARKERS:
        if m in t:
            ok(name)
        else:
            fail(f"маркер отсутствует: {name}")


def check_period(D):
    print("\n[2] Период и непрерывность дней")
    p = D["period"]
    days = sorted(D["day"].keys())
    ok(f"период {p[0]}..{p[1]}, {p[2]} дней, единица: {D.get('unit', '?')}")
    d1, d2 = date.fromisoformat(p[0]), date.fromisoformat(p[1])
    expect = (d2 - d1).days + 1
    if expect != p[2]:
        fail(f"дней в периоде {p[2]}, календарно {expect}")
    else:
        ok("пропусков в днях нет")
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    if p[1] != yesterday:
        warn(f"период заканчивается {p[1]}, вчера — {yesterday}")
    if days[0] != p[0] or days[-1] != p[1]:
        fail("границы day не совпадают с period")


def check_strict(D):
    print("\n[3] Строгая лесенка: монотонность")
    bad = 0
    for lvl in ("month", "week", "day"):
        for k, arr in D["strict"][lvl].items():
            for i in range(1, len(arr)):
                if arr[i][0] > arr[i - 1][0]:
                    bad += 1
                    if bad <= 3:
                        fail(f"{lvl}/{k}: шаг {i} = {arr[i][0]} > {arr[i-1][0]}")
    if bad == 0:
        ok("нарушений нет на всех уровнях")
    else:
        fail(f"всего нарушений: {bad}")


def check_segments(D):
    print("\n[4] Сегмент «Новые» ≤ «Все»")
    bad = 0
    for lvl in ("month", "week", "day", "hour"):
        for k, c in D[lvl].items():
            for g, v in c.items():
                if v[1] > v[0]:
                    bad += 1
                if len(v) > 3 and v[3] > v[2]:
                    bad += 1
    if bad == 0:
        ok("нарушений нет")
    else:
        fail(f"нарушений: {bad}")


def check_events(D):
    print("\n[5] События против популяции своего экрана")
    step_of = steps_map(D)
    agg = {}
    for lvl in ("month", "week", "day"):
        for k, c in D[lvl].items():
            for rw, evs in D["reasons"].items():
                gid = step_of.get(rw)
                if not gid or gid not in c:
                    continue
                den = c[gid][0]
                if den <= 0:
                    continue
                for nm, g in evs:
                    if g in UNANCHORED:
                        continue
                    v = c.get(g)
                    if v and v[0] > den:
                        agg.setdefault((rw, nm, g), []).append(100.0 * v[0] / den)
    if not agg:
        ok("ни одно событие не превышает свой экран")
    else:
        for (rw, nm, g), pcts in sorted(agg.items(), key=lambda x: -max(x[1])):
            line = f"{rw}/{nm} ({g}): до {max(pcts):.1f}%, случаев {len(pcts)}"
            if max(pcts) >= TOL:
                fail(line)
            else:
                ok(f"{line} — в пределах допуска разметки {TOL - 100:.0f}%")


def check_chains(D):
    print("\n[6] Дополнительные цепочки")
    total_days = D["period"][2]
    for key, title in CHAIN_KEYS:
        ch = D.get(key)
        if not ch:
            fail(f"{title}: ключ '{key}' отсутствует в payload")
            continue
        names = [x[0] for x in ch]
        gid_of = {n: g for n, g in ch}
        ok(f"{title}: {' -> '.join(names)}")
        base_g = ch[0][1]
        for mo in D["nav"]["months"]:
            c = D["month"][mo]
            base = c.get(base_g, [0])[0]
            if base <= 0:
                if not month_covered(D, base_g, mo):
                    print(f"  INFO  {mo}: цель-база ещё не размечена, "
                          "месяц пропущен")
                else:
                    fail(f"{title} / {mo}: база = 0 при наличии покрытия")
                continue
            parts = []
            for nm, g in ch:
                v = c.get(g, [0])[0]
                cov = D["cov"].get(g)
                mark = ""
                if cov and cov[2] < total_days:
                    mark = f" [с {cov[0]}]"
                parts.append(f"{nm}={v} ({100.0 * v / base:.1f}%){mark}")
                if v > base * 1.01 and g != base_g:
                    fail(f"{title} / {mo} / {nm}: {v} > базы {base}")
            print(f"  INFO  {mo}: {', '.join(parts)}")
            for child, parent in CHAIN_MONOTONIC.get(key, []):
                cg, pg = gid_of.get(child), gid_of.get(parent)
                if not cg or not pg:
                    continue
                cv, pv = c.get(cg, [0])[0], c.get(pg, [0])[0]
                if pv > 0 and cv > pv * 1.01:
                    fail(f"{title} / {mo}: {child}={cv} > {parent}={pv}")
                elif pv > 0 and cv > pv:
                    warn(f"{title} / {mo}: {child}={cv} чуть больше "
                         f"{parent}={pv} (погрешность разметки)")
        for nm, g in ch:
            cov = D["cov"].get(g)
            if cov and cov[2] < total_days:
                warn(f"{title} / {nm}: данные с {cov[0]} "
                     f"({cov[2]} из {total_days} дней) — проценты по месяцам "
                     "несопоставимы")


def check_events_format(D):
    print("\n[7] Формат событий: 4 значения (люди + визиты)")
    ev = set(g for lst in D["reasons"].values() for _, g in lst)
    bad2, tot = 0, 0
    for lvl in ("month", "week", "day"):
        for k, c in D[lvl].items():
            for g in ev & set(c):
                tot += 1
                if len(c[g]) != 4:
                    bad2 += 1
    if tot == 0:
        fail("событий в payload нет вообще")
    elif bad2:
        fail(f"{bad2} из {tot} событий без визитов")
    else:
        ok(f"все {tot} записей событий содержат людей и визиты")
    hk = next(iter(D["hour"]))
    n = len(ev & set(D["hour"][hk]))
    if n == 0:
        ok("на уровне hour событий нет (ожидаемо, документировано)")
    else:
        warn(f"на уровне hour найдено {n} событий — уровень изменился")


def check_dims(D):
    print("\n[8] Разрезы: покрытие топ-7 относительно шага")
    for mo in D["nav"]["months"]:
        dm = D["dim"].get(mo, {})
        for nm, g in D["funnel"][:1]:
            base = D["month"][mo].get(g, [0])[0]
            for dn, vals in sorted(dm.get(g, {}).items()):
                tot = sum(x[1] for x in vals)
                pct = 100.0 * tot / base if base else 0
                mark = "перекрытие" if pct > 100 else "хвост скрыт"
                print(f"  INFO  {mo} {nm} {dn:8} {pct:5.1f}% ({mark})")
                if max((x[1] for x in vals), default=0) > base:
                    fail(f"{mo}/{nm}/{dn}: значение разреза больше шага")


def check_db(D):
    print("\n[9] Метрика vs БД по месяцам")
    reg = dict([tuple(x) for x in D["funnel"]])["REGISTRATION_PAGE_OK"]
    for mo in D["nav"]["months"]:
        met = D["month"][mo].get(reg, [0])[0]
        db = sum(n for d, n in D["dbreg"].items() if d.startswith(mo))
        if db == 0:
            fail(f"{mo}: нет данных БД")
            continue
        gap = 100.0 * (db - met) / db
        line = f"{mo}: Метрика {met}, БД {db}, разрыв {gap:.1f}%"
        if 0 <= gap <= 15:
            ok(line)
        else:
            fail(line + " — вне коридора 0–15%")


def check_steps_present(D):
    print("\n[10] Все шаги присутствуют в каждом дне")
    missing = 0
    for d, c in D["day"].items():
        for nm, g in D["funnel"]:
            if g not in c:
                missing += 1
                if missing <= 3:
                    warn(f"{d}: нет шага {nm}")
    if missing == 0:
        ok(f"все {len(D['funnel'])} шагов есть во всех днях")
    else:
        warn(f"всего пропусков шагов: {missing}")


def check_coverage(D):
    print("\n[11] Покрытие целей")
    total = D["period"][2]
    partial = [g for g, c in D["cov"].items() if c[2] < total]
    ok(f"целей с данными: {len(D['cov'])}, с неполным покрытием: {len(partial)}")
    ev = {g: nm for lst in D["reasons"].values() for nm, g in lst}
    dead = [ev[g] for g in ev if g not in D["cov"]]
    if dead:
        warn(f"события без единого срабатывания: {', '.join(dead)}")


def main():
    if not os.path.exists(HTML):
        print(f"FAIL: {HTML} не найден — сначала render_dashboard.py")
        return 1
    print(f"AUDIT: {HTML}")
    D, s = load_payload(HTML)
    print(f"размер файла: {len(s)} символов")

    check_markers()
    check_period(D)
    check_strict(D)
    check_segments(D)
    check_events(D)
    check_chains(D)
    check_events_format(D)
    check_dims(D)
    check_db(D)
    check_steps_present(D)
    check_coverage(D)

    print("\n" + "=" * 60)
    if fails:
        print(f"РЕЗУЛЬТАТ: {len(fails)} ОШИБОК, {len(warns)} предупреждений")
        for f in fails:
            print(f"  FAIL  {f}")
        return 1
    print(f"РЕЗУЛЬТАТ: ОШИБОК НЕТ, предупреждений {len(warns)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())