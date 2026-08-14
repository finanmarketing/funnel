import os
import shutil
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TPL = os.path.join(BASE_DIR, "pipeline", "dashboard_template.html")
BAK = os.path.join(BASE_DIR, "pipeline", "dashboard_template_v15_backup.html")

# UNANCH is now empty: 465920058 became a PWA chain step, not an event.
UN_OLD = ("const UNANCH=new Set(['465920058']);"
          "SL['PAYMENT_PAGE']='Экран выплаты';"
          "SL['REJECT_PAGE']='Страница отказа';")
UN_NEW = (
    "const UNANCH=new Set([]);"
    "SL['PAYMENT_PAGE']='Экран выплаты';"
    "SL['REJECT_PAGE']='Страница отказа';"
    "SL['TERMS_CHANGE_PAGE']='Смена условий';"
    "SL['OTHER_OFFERS_PAGE']='Другие предложения';"
    "SL['FORGOT_PASSWORD']='Забыл пароль';"
    "SL['FORGOT_PHONE']='Забыл телефон';"
    "SL['FORGOT_PHONE_COMPLETE']='Телефон восстановлен';"
    "SL['FORGOT_EMAIL']='Забыл e-mail';"
    "SL['FORGOT_EMAIL_COMPLETE']='E-mail восстановлен';"
    "SL['PWA_LAUNCH_WEB']='Запуск из браузера';"
    "SL['PWA_LAUNCH_PWA']='Запуск из приложения';"
    "SL['PWA_INSTALL_AVAILABLE']='Установка предложена';"
    "SL['PWA_INSTALL_ACCEPTED']='Согласились установить';"
    "SL['PWA_INSTALLED']='Приложение установлено';"
    "SL['PWA_INSTALL_DISMISSED']='Отказались от установки';"
)

# Tooltip must name the actual base row, not a hardcoded screen.
TIP_OLD = ("title=\"${isF?'доля от «Экран выплаты»':"
           "(mode===1||isL)?'переход от предыдущего шага':"
           "'доля от «Открыли сайт»'}\"")
TIP_NEW = ("title=\"${isF?('доля от «'+(SL[list[0][0]]||list[0][0])+'»'):"
           "(mode===1||isL)?'переход от предыдущего шага':"
           "'доля от «Открыли сайт»'}\"")

HTML_OLD = ' <div class="funnel" id="applyf"></div>'
HTML_NEW = (
    ' <div class="funnel" id="applyf"></div>\n'
    ' <div class="sh"><h3>Восстановление доступа</h3>'
    '<span class="mode" style="border:none;background:none;'
    'color:var(--faint);font-weight:400">'
    'сценарий доступен с нескольких экранов · доли от «Забыл пароль»'
    '</span></div>\n'
    ' <div class="funnel" id="recoveryf"></div>\n'
    ' <div class="sh"><h3>Запуск и установка приложения</h3>'
    '<span class="mode" style="border:none;background:none;'
    'color:var(--faint);font-weight:400">'
    'способ запуска и воронка установки PWA · доли от «Запуск из браузера»'
    '</span></div>\n'
    ' <div class="funnel" id="pwaf"></div>'
)

CALL_OLD = ("document.getElementById('applyf').innerHTML="
            "chain(DATA.apply||[],c,true,true);")
CALL_NEW = (CALL_OLD +
            "\n document.getElementById('recoveryf').innerHTML="
            "chain(DATA.recovery||[],c,true,true);"
            "\n document.getElementById('pwaf').innerHTML="
            "chain(DATA.pwa||[],c,true,true);")

PATCHES = [
    ("labels-and-unanch", UN_OLD, UN_NEW),
    ("generic-tooltip", TIP_OLD, TIP_NEW),
    ("html-containers", HTML_OLD, HTML_NEW),
    ("render-calls", CALL_OLD, CALL_NEW),
]


def main():
    if not os.path.exists(TPL):
        print(f"FAIL: {TPL} not found")
        return 1
    with open(TPL, "r", encoding="utf-8") as f:
        s = f.read()
    print(f"template: {len(s)} chars")

    if 'id="recoveryf"' in s:
        print("FAIL: already patched. Restore backup first.")
        return 1
    if 'id="applyf"' not in s:
        print("FAIL: patch_template4.py was not applied. Run it first.")
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
    print(f"DONE patch_template5: {os.path.getsize(TPL)} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())