import os
import shutil
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TPL = os.path.join(BASE_DIR, "pipeline", "dashboard_template.html")
BAK = os.path.join(BASE_DIR, "pipeline", "dashboard_template_v13_backup.html")

RAW_OLD = "if(!a[g])a[g]=[0,0];a[g][0]+=v[0];a[g][1]+=v[1];"
RAW_NEW = ("if(!a[g])a[g]=[0,0,0,0];a[g][0]+=v[0];a[g][1]+=v[1];"
           "if(v.length>3){a[g][2]+=v[2];a[g][3]+=v[3];}")

UN_OLD = ("const UNANCH=new Set(['398982288','398982605',"
          "'398983728','398982473']);")
UN_NEW = ("const UNANCH=new Set(['398982288','398982605',"
          "'398983728','398982473','465920058']);")

NOTE_OLD = "${rows||'<span class=\"note\">За период не зафиксировано</span>'}"
NOTE_NEW = ("${rows||`<span class=\"note\">${st.level==='hour'?"
            "'на почасовом уровне события не рассчитываются':"
            "'за период не зафиксировано'}</span>`}")

DBL_OLD = "${(100*(1-ok/dbn)).toFixed(0)}"
DBL_NEW = "${(100*(1-V(c,REGOK)/dbn)).toFixed(0)}"

SEG_OLD = ("document.getElementById('segnote').textContent=seg===0?'':\n"
           "  seg===1?'показаны только те, кто впервые на сайте':"
           "'показаны только вернувшиеся посетители';")
SEG_NEW = ("document.getElementById('segnote').textContent=(seg===0?'':\n"
           "  seg===1?'показаны только те, кто впервые на сайте':"
           "'показаны только вернувшиеся посетители')+\n"
           "  (st.level==='all'?(seg===0?'':' · ')+"
           "'за весь период числа суммируются по месяцам: клиент, "
           "заходивший в разные месяцы, учтён несколько раз':'');")

PATCHES = [
    ("raw-visits-aggregation", RAW_OLD, RAW_NEW),
    ("unanchored-add-browser-entry", UN_OLD, UN_NEW),
    ("hour-events-note", NOTE_OLD, NOTE_NEW),
    ("db-gap-uses-raw", DBL_OLD, DBL_NEW),
    ("all-level-warning", SEG_OLD, SEG_NEW),
]


def main():
    if not os.path.exists(TPL):
        print(f"FAIL: {TPL} not found")
        return 1
    with open(TPL, "r", encoding="utf-8") as f:
        s = f.read()
    print(f"template: {len(s)} chars")

    if "a[g]=[0,0,0,0]" in s:
        print("FAIL: already patched. Restore backup first.")
        return 1
    if "function DIMBASE(g)" not in s:
        print("FAIL: patch_template2.py was not applied. Run it first.")
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
    print(f"DONE patch_template3: {os.path.getsize(TPL)} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())