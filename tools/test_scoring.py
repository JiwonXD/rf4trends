# 미끼 패밀리 정규화(bait_family, D-49) 단위 검증
import sys, os as _os
sys.stdout.reconfigure(encoding="utf-8")  # 한국 Windows 콘솔(cp949)에서 em-dash 출력 크래시 방지
sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), '..', 'rf4site'))

from scoring import bait_family

fails = []
def check(label, cond):
    print(('PASS' if cond else 'FAIL'), label)
    if not cond: fails.append(label)

CASES = [
    ("Active W-Stick7.0-05", "active w-stick -05"), ("Active W-Stick 2.5-05", "active w-stick -05"),
    ("AngryWalker S8-001", "angrywalker -001"), ("Funky Minnow F11-002", "funky minnow -002"),
    ("Balsa Crank 80F-003", "balsa crank -003"), ("Veikko 25g-011", "veikko -011"),
    ("Hijacker slim 7SP-002", "hijacker slim -002"), ("Nasty Worm 4.5-001", "nasty worm -001"),
    ("Nasty worm 7-001", "nasty worm -001"), ("Spiker #2 016", "spiker -016"),
    ("Hornet #3 005", "hornet -005"), ("연어 팝업 20", "연어 팝업"), ("굴 16", "굴"),
    ("꿀 반죽", "꿀 반죽"), ("Jiggmeister DC 1000", "jiggmeister dc"),
    ("Stor Fisk M25-600 #17", "stor fisk -#17"), ("Pilker №2-300 RD", "pilker №2 -rd"),
    ("Super Grub 4 CLR-B", "super grub -clr-b"), ("Icon Fat m-001", "icon fat -001"),
    ("Furry T01", "furry t01"), ("UL Popper-001", "ul popper-001"),
    ("Orig Walker-002", "orig walker-002"), ("지렁이", "지렁이"),
    ("핫 체리 수용성 20; 뉴트럴 25", "뉴트럴; 핫 체리 수용성"),
    ("뉴트럴 25; 핫 체리 수용성 20", "뉴트럴; 핫 체리 수용성"),
    ("파리; 파리", "파리; 파리"), ("Natural Squid - 23 - 07", "natural squid -07"),
]

for bait, expected in CASES:
    check(f"bait_family({bait!r}) == {expected!r}", bait_family(bait) == expected)

print("="*40)
print("실패", len(fails), "건" if fails else "— 전체 통과")
