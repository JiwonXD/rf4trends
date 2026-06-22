# 라이트/바텀라이트/해양 게시판이 통합 내용을 반환하는 원인 진단
# 사용법: python diagnose_rf4.py
# 출력 전체를 복사해서 보내주세요.

import requests
from bs4 import BeautifulSoup

CATS = ["records", "recordslight", "bottomlight", "sea", "ultralight"]

# collector와 동일한 헤더 (문제 재현용)
HEADERS_OLD = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

# 실제 브라우저에 가까운 헤더 (해결 후보)
HEADERS_NEW = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/126.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9",
}


def check(label, headers):
    print(f"\n===== {label} =====")
    sess = requests.Session()
    for cat in CATS:
        url = f"https://rf4game.kr/{cat}/weekly/region/GL/"
        r = sess.get(url, headers=headers, timeout=30)
        soup = BeautifulSoup(r.text, "html.parser")
        title = soup.title.get_text(strip=True) if soup.title else "?"
        first = soup.select_one(".records_subtable .row.header")
        species = first.select_one(".col.fish").get_text(strip=True) if first else "?"
        weight = first.select_one(".col.weight").get_text(strip=True) if first else "?"
        redirect = f" [리다이렉트: {r.url}]" if r.url.rstrip("/") != url.rstrip("/") else ""
        history = f" (경유 {len(r.history)}회)" if r.history else ""
        print(f"{cat:14s} | {title:30s} | 1위: {species} {weight}{redirect}{history}")


check("기존 헤더 (collector와 동일)", HEADERS_OLD)
check("브라우저형 헤더", HEADERS_NEW)
