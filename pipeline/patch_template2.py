import os
import shutil
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TPL = os.path.join(BASE_DIR, "pipeline", "dashboard_template.html")
BAK = os.path.join(BASE_DIR, "pipeline", "dashboard_template_v11_backup.html")

HELPERS_OLD = "const F=n=>(n||0).toLocaleString('ru-RU'),P=(a,b)=>b?100*a/b:0;"
HELPERS_NEW = (
    "const UNANCH=new Set(['398982288','398982605','398983728','398982473']);"
    "function DIMBASE(g){"
    "if(st.level==='all'){let t=0;for(const m in DATA.month){const v=DATA.month[m][g];"
    "if(v)t+=seg===0?v[0]:seg===1?v[1]:v[0]-v[1];}return t;}"
    "const m=curMonth();const v=m&&DATA.month[m]?DATA.month[m][g]:null;"
    "return v?(seg===0?v[0]:seg===1?v[1]:v[0]-v[1]):0;}"
    + HELPERS_OLD
)

CR_OLD = "const cr=prev!==null&&prev>0?P(v,prev):null;"
CR_NEW = ("const dn0=(mode===1||isL)?prev:(i>0?getv(list[0],0):null);"
          "const cr=dn0!==null&&dn0>0?P(v,dn0):null;")

CC_OLD = "const cc=cr===null?'none':cr>=50?'good':cr>=25?'mid':'bad';"
CC_NEW = ("const cc=cr===null?'none':((mode===1||isL)?"
          "(cr>=50?'good':cr>=25?'mid':'bad'):'none');")

CRD_OLD = '<div class="cr ${cc}">${cr===null?\'—\':cr.toFixed(1)+\'%\'}</div>'
CRD_NEW = ('<div class="cr ${cc}" title="${(mode===1||isL)?'
           "'переход от предыдущего шага':'доля от «Открыли сайт»'}\">"
           "${cr===null?'—':cr.toFixed(1)+'%'}</div>")

DEN_OLD = ("const w=nm.startsWith('⚠')?' w':'';const den=V(c,gid);"
           "const s=den?`<span class=\"sub\">${P(v,den).toFixed(1)}%</span>`:'';")
DEN_NEW = ("const w=nm.startsWith('⚠')?' w':'';const ua=UNANCH.has(g);"
           "const den=ua?0:V(c,gid);"
           "const s=den?`<span class=\"sub\">${P(v,den).toFixed(1)}%</span>`:"
           "(ua?`<span class=\"sub\" style=\"color:#d8455c\">экран уточняется"
           "</span>`:'');")

DIMT_OLD = "const t=vs.reduce((s,x)=>s+x[1],0)||1;"
DIMT_NEW = "const t=DIMBASE(gid)||vs.reduce((s,x)=>s+x[1],0)||1;"

DIMH_OLD = ("Разрезы посетителей шага <span>· ${D.lbl}"
            "${seg?', по всей аудитории':''}</span>")
DIMH_NEW = ("Разрезы посетителей шага <span>· ${D.lbl}"
            "${seg?', по всей аудитории':''} · % от посетителей шага; "
            "клиент может попасть в несколько источников</span>")

PATCHES = [
    ("helpers-DIMBASE", HELPERS_OLD, HELPERS_NEW),
    ("ladder-denominator", CR_OLD, CR_NEW),
    ("ladder-color", CC_OLD, CC_NEW),
    ("ladder-tooltip", CRD_OLD, CRD_NEW),
    ("unanchored-events", DEN_OLD, DEN_NEW),
    ("dim-denominator", DIMT_OLD, DIMT_NEW),
    ("dim-header", DIMH_OLD, DIMH_NEW),
]


def main():
    if not os.path.exists(TPL):
        print(f"FAIL: {TPL} not found")
        return 1
    with open(TPL, "r", encoding="utf-8") as f:
        s = f.read()
    print(f"template: {len(s)} chars")

    if "function DIMBASE(g)" in s:
        print("FAIL: already patched (DIMBASE present). Restore backup first.")
        return 1
    if "function VV(c,g)" not in s:
        print("FAIL: patch_template.py was not applied. Run it first.")
        return 1

    for name, old, new in PATCHES:
        n = s.count(old)
        if n != 1:
            print(f"FAIL: patch '{name}' expected 1 occurrence, found {n}")
            return 1

    if not os.path.exists(BAK):
        shutil.copyfile(TPL, BAK)
        print(f"backup created: {BAK}")

    for name, old, new in PATCHES:
        s = s.replace(old, new, 1)
        print(f"  patched: {name}")

    with open(TPL, "w", encoding="utf-8") as f:
        f.write(s)
    print(f"DONE patch_template2: {os.path.getsize(TPL)} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())