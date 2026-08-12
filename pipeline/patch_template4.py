import os
import shutil
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TPL = os.path.join(BASE_DIR, "pipeline", "dashboard_template.html")
BAK = os.path.join(BASE_DIR, "pipeline", "dashboard_template_v14_backup.html")

HTML_OLD = ' <div class="sh"><h3>Прочие события сайта</h3></div>'
HTML_NEW = (' <div class="sh"><h3>Заявка и решение</h3>'
            '<span class="mode" style="border:none;background:none;'
            'color:var(--faint);font-weight:400">'
            'общий этап для новых и повторных клиентов · '
            'доли от экрана выплаты</span></div>\n'
            ' <div class="funnel" id="applyf"></div>\n'
            ' <div class="sh"><h3>Прочие события сайта</h3></div>')

UN_OLD = ("const UNANCH=new Set(['398982288','398982605',"
          "'398983728','398982473','465920058']);")
UN_NEW = ("const UNANCH=new Set(['465920058']);"
          "SL['PAYMENT_PAGE']='Экран выплаты';"
          "SL['REJECT_PAGE']='Страница отказа';")

SIG_OLD = "function chain(list,c,isL){"
SIG_NEW = "function chain(list,c,isL,isF){"

DN_OLD = "const dn0=(mode===1||isL)?prev:(i>0?getv(list[0],0):null);"
DN_NEW = "const dn0=(!isF&&(mode===1||isL))?prev:(i>0?getv(list[0],0):null);"

CC_OLD = ("const cc=cr===null?'none':((mode===1||isL)?"
          "(cr>=50?'good':cr>=25?'mid':'bad'):'none');")
CC_NEW = ("const cc=cr===null?'none':((!isF&&(mode===1||isL))?"
          "(cr>=50?'good':cr>=25?'mid':'bad'):'none');")

TIP_OLD = ("title=\"${(mode===1||isL)?'переход от предыдущего шага':"
           "'доля от «Открыли сайт»'}\"")
TIP_NEW = ("title=\"${isF?'доля от «Экран выплаты»':"
           "(mode===1||isL)?'переход от предыдущего шага':"
           "'доля от «Открыли сайт»'}\"")

CALL_OLD = "document.getElementById('loginf').innerHTML=chain(DATA.login,c,true);"
CALL_NEW = (CALL_OLD +
            "\n document.getElementById('applyf').innerHTML="
            "chain(DATA.apply||[],c,true,true);")

PATCHES = [
    ("html-apply-container", HTML_OLD, HTML_NEW),
    ("unanch-and-labels", UN_OLD, UN_NEW),
    ("chain-signature", SIG_OLD, SIG_NEW),
    ("chain-denominator", DN_OLD, DN_NEW),
    ("chain-color", CC_OLD, CC_NEW),
    ("chain-tooltip", TIP_OLD, TIP_NEW),
    ("render-apply-call", CALL_OLD, CALL_NEW),
]


def main():
    if not os.path.exists(TPL):
        print(f"FAIL: {TPL} not found")
        return 1
    with open(TPL, "r", encoding="utf-8") as f:
        s = f.read()
    print(f"template: {len(s)} chars")

    if 'id="applyf"' in s:
        print("FAIL: already patched. Restore backup first.")
        return 1
    if "a[g]=[0,0,0,0]" not in s:
        print("FAIL: patch_template3.py was not applied. Run it first.")
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
    print(f"DONE patch_template4: {os.path.getsize(TPL)} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())