import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TPL = os.path.join(BASE_DIR, "pipeline", "dashboard_template.html")

MARKERS = [
    ("patch1: helper VV", "function VV(c,g)"),
    ("patch1: css bout", ".bout{"),
    ("patch1: bar label out", "w<25?`<span class="),
    ("patch1: event percent den", "const den=ua?0:V(c,gid)"),
    ("patch1: visits sub", "sub vis"),
    ("patch1: event header", "уникальные посетители"),
    ("patch2: DIMBASE", "function DIMBASE(g)"),
    ("patch2: UNANCH set", "const UNANCH=new Set"),
    ("patch2: ladder denominator", "dn0=(mode===1||isL)"),
    ("patch2: ladder tooltip", "переход от предыдущего шага"),
    ("patch2: unanchored label", "экран уточняется"),
    ("patch2: dim denominator", "DIMBASE(gid)||vs.reduce"),
    ("patch2: dim header", "% от посетителей шага"),
]


def main():
    with open(TPL, "r", encoding="utf-8") as f:
        s = f.read()
    print(f"template: {len(s)} chars, {os.path.getsize(TPL)} bytes")
    miss = 0
    for name, marker in MARKERS:
        ok = marker in s
        print(f"  {'OK  ' if ok else 'MISS'} {name}")
        if not ok:
            miss += 1
    print(f"\n{'ALL PATCHES PRESENT' if miss == 0 else f'MISSING: {miss}'}")
    return 1 if miss else 0


if __name__ == "__main__":
    sys.exit(main())