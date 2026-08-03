import json

P = json.load(open("state/payload.json", encoding="utf-8"))
F = {n: g for n, g in [tuple(x) for x in P["funnel"]]}
REG = F["REGISTRATION_PAGE_OK"]

REF_METRICA = {"2026-05": 39158, "2026-06": 30034}
REF_DB = {"2026-05": 42116, "2026-06": 32214}

print("=== REGISTRATION_PAGE_OK by month (Metrica, unique clientID) ===")
for m in P["nav"]["months"]:
    got = P["month"].get(m, {}).get(REG, [0, 0])[0]
    ref = REF_METRICA.get(m)
    mark = "" if ref is None else ("  MATCH" if got == ref else f"  DIFF ref={ref}")
    print(f"  {m}: {got}{mark}")

print("\n=== dbreg by month (public.clients) ===")
agg = {}
for d, n in P["dbreg"].items():
    agg[d[:7]] = agg.get(d[:7], 0) + n
for m in sorted(agg):
    ref = REF_DB.get(m)
    mark = "" if ref is None else ("  MATCH" if agg[m] == ref else f"  DIFF ref={ref}")
    print(f"  {m}: {agg[m]}{mark}")

print("\n=== July 1-30 partial sums (ref: metrica 34823 / db 37795) ===")
jm = sum(
    P["day"].get(d, {}).get(REG, [0, 0])[0]
    for d in P["day"] if d.startswith("2026-07") and d <= "2026-07-30"
)
jd = sum(n for d, n in P["dbreg"].items() if d.startswith("2026-07") and d <= "2026-07-30")
print(f"  metrica: {jm}  {'MATCH' if jm == 34823 else 'DIFF ref=34823'}")
print(f"  db:      {jd}  {'MATCH' if jd == 37795 else 'DIFF ref=37795'}")

print("\n=== STRICT ladder 2026-07 (ref: REG_OK=34034, MOBILE_OK=19238, CONFIRM=3653) ===")
order = [n for n, _ in [tuple(x) for x in P["funnel"]]]
st = P["strict"]["month"].get("2026-07", [])
refs = {"REGISTRATION_PAGE_OK": 34034, "MOBILE_VERIFICATION_PAGE_OK": 19238,
        "CONFIRM_PAGE": 3653}
for i, name in enumerate(order):
    val = st[i][0] if i < len(st) else 0
    r = refs.get(name)
    mark = "" if r is None else ("  MATCH" if val == r else f"  DIFF ref={r}")
    print(f"  {i:>2} {name:<30} {val}{mark}")

print("\n=== structure ===")
print(f"  period: {P['period']}")
print(f"  goals with coverage: {len(P['cov'])}")
print(f"  hour keys: {len(P['hour'])}, day keys: {len(P['day'])}")
part = [g for g in P["cov"] if P["cov"][g][2] < P["period"][2]]
print(f"  goals with partial coverage: {len(part)} (ref ~26)")
print(f"  goal 398971914 coverage: {P['cov'].get('398971914')} (ref 2026-07-03..09)")