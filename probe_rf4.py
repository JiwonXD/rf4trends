# rf4game.kr 주간기록 HTML 구조 정찰 스크립트
# 사용법:
#   pip install requests beautifulsoup4
#   python probe_rf4.py
# 출력 결과 전체를 복사해서 Claude에게 보내주세요.

import requests
from bs4 import BeautifulSoup

URL = "https://rf4game.kr/records/weekly"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

resp = requests.get(URL, headers=HEADERS, timeout=30)
resp.raise_for_status()
soup = BeautifulSoup(resp.text, "html.parser")

print(f"상태코드: {resp.status_code}, HTML 크기: {len(resp.text)} bytes")
print("=" * 60)

# 1) 테이블 후보 찾기
tables = soup.find_all("table")
print(f"<table> 개수: {len(tables)}")

# 2) 첫 번째 데이터 테이블의 원본 HTML 일부 출력 (행 3개)
if tables:
    rows = tables[0].find_all("tr")
    print(f"첫 테이블의 <tr> 개수: {len(rows)}")
    print("-" * 60)
    for row in rows[:4]:
        print(row.prettify())
        print("-" * 60)
else:
    # 테이블 태그가 아닐 수도 있으니 div 기반 구조 탐색
    print("table 태그 없음 → 'records' 관련 클래스를 가진 요소 탐색")
    for el in soup.select("[class*=record], [class*=weekly], [class*=table]")[:5]:
        print(f"태그: {el.name}, 클래스: {el.get('class')}")
        print(str(el)[:1500])
        print("-" * 60)

# 3) 루어(미끼)가 이미지인지 확인: img 태그의 alt/title/src 샘플
imgs = soup.find_all("img")
print(f"\n<img> 개수: {len(imgs)} — 그중 alt 또는 title이 있는 것 샘플 10개:")
shown = 0
for img in imgs:
    if img.get("alt") or img.get("title"):
        print(f"  alt={img.get('alt')!r}  title={img.get('title')!r}  src={img.get('src')!r}")
        shown += 1
        if shown >= 10:
            break

# 4) 국가/지역 전환 링크 탐색
print("\n국가/지역 관련 링크 후보:")
for a in soup.find_all("a", href=True):
    href = a["href"]
    if any(k in href.lower() for k in ("region", "country", "weekly")):
        print(f"  {a.get_text(strip=True)!r} → {href}")
