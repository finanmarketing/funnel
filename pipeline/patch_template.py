import os
import shutil
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TPL = os.path.join(BASE_DIR, "pipeline", "dashboard_template.html")
BAK = os.path.join(BASE_DIR, "pipeline", "dashboard_template_v7_backup.html")

CSS_NEW = (
    ".bw{display:flex;align-items:center;gap:8px}"
    ".bout{font-family:var(--mono);font-size:12.5px;font-weight:650;color:#2b3445}"
    ".rv .sub.vis{opacity:.7;margin-left:6px}"
    "</style>"
)

BAR_OLD = ('<div class="bw"><div class="bar" style="width:${w}%">'
           "${v>0?F(v):''}</div></div>")
BAR_NEW = ('<div class="bw"><div class="bar" style="width:${w}%">'
           "${v>0&&w>=25?F(v):''}</div>"
           "${v>0&&w<25?`<span class=\"bout\">${F(v)}</span>`:''}</div>")

HELPER_OLD = ("function V(c,g){const v=c[g];if(!v)return 0;"
              "return seg===0?v[0]:seg===1?v[1]:v[0]-v[1];}")
HELPER_NEW = HELPER_OLD + (
    "function VV(c,g){const v=c[g];if(!v||v.length<4)return 0;"
    "return seg===0?v[2]:seg===1?v[3]:v[2]-v[3];}"
)

EV_OLD = "const v=V(c,g);if(!v)return'';"
EV_NEW = "const v=V(c,g),vv=VV(c,g);if(!v&&!vv)return'';"

PCT_OLD = ("const s=base?`<span class=\"sub\">${P(v,base).toFixed(1)}%"
           "</span>`:'';")
PCT_NEW = ("const den=V(c,gid);const s=den?`<span class=\"sub\">"
           "${P(v,den).toFixed(1)}%</span>`:'';")

RV_OLD = '<span class="rv">${F(v)}${s}</span>'
RV_NEW = ('<span class="rv">${F(v)}${s}'
          "${vv?`<span class=\"sub vis\">· ${F(vv)} виз.</span>`:''}</span>")

HDR_OLD = "События на шаге <span>· в визитах</span>"
HDR_NEW = ("События на шаге <span>· уникальные посетители, "
           "% от побывавших на экране; виз. — визиты с событием</span>")

PATCHES = [
    ("css", "</style>", CSS_NEW),
    ("bar-label", BAR_OLD, BAR_NEW),
    ("helper-VV", HELPER_OLD, HELPER_NEW),
    ("event-values", EV_OLD, EV_NEW),
    ("event-percent", PCT_OLD, PCT_NEW),
    ("event-render", RV_OLD, RV_NEW),
    ("event-header", HDR_OLD, HDR_NEW),
]


def main():
    if not os.path.exists(TPL):
        print(f"FAIL: {TPL} not found")
        return 1
    with open(TPL, "r", encoding="utf-8") as f:
        s = f.read()
    print(f"template: {len(s)} bytes")

    if "function VV(c,g)" in s:
        print("FAIL: template already patched (VV present). Restore backup first.")
        return 1

    for name, old, new in PATCHES:
        n = s.count(old)
        if n != 1:
            print(f"FAIL: patch '{name}' expected 1 occurrence, found {n}")
            return 1

    if not os.path.exists(BAK):
        shutil.copyfile(TPL, BAK)
        print(f"backup created: {BAK}")
    else:
        print(f"backup already exists: {BAK}")

    for name, old, new in PATCHES:
        s = s.replace(old, new, 1)
        print(f"  patched: {name}")

    with open(TPL, "w", encoding="utf-8") as f:
        f.write(s)
    print(f"DONE patch_template: {os.path.getsize(TPL)} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())