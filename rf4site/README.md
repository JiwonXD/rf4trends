# RF4 관제탑 — 선호 어종 대시보드

근거 문서: PRD.md / SCREENS.md (결정 로그 D-1~D-23)
회원별로 선호 어종을 등록하고, 활성도 순으로 "오늘 잡을만한 어종"을 추천받는 개인 대시보드.

## 무엇인가
- 수집 스레드 + 웹사이트가 한 프로그램(app.py)에서 동작
- `python app.py` 하나면 15분 주기 수집과 대시보드가 같이 켜짐
- 아이디/비밀번호 회원 인증 (비밀번호 bcrypt 해시, 세션 쿠키)
- 활성도 추천은 현재 임시 수식 — 라벨이 쌓이면 ML로 교체 예정 (PRD D-22)

## 설치
```
pip install -r requirements.txt
```

## 실행
```
# (권장) 세션 서명용 비밀키를 본인만의 값으로 지정
export RF4_SECRET="아무도_모르는_긴_랜덤_문자열"
# (선택) admin 계정명 지정 — 기본값 "admin". 이 계정만 라벨 수집 가능
export RF4_ADMIN="admin"
python app.py
```
브라우저에서 http://localhost:8000 → 회원가입 후 선호 어종 등록.
트로피 기준(trophy_weights.csv)은 첫 실행 시 자동 로딩된다.
기존 rf4.db가 있으면 이 폴더에 두면 수집 데이터·계정·라벨이 이어진다.

> RF4_SECRET을 지정하지 않으면 개발용 기본키가 쓰여 세션 쿠키가 위조될 수 있으니,
> 외부 노출(Cloudflare Tunnel 등) 전에는 반드시 설정하세요.

## 안드로이드 태블릿 배포
TERMUX_SETUP.md 참고.

## 구조
- app.py            통합 서버 (웹 라우트 + 15분 수집 스레드 + 하루 1회 정리 + 트로피 자동 로딩)
- auth.py           회원 인증 (가입/로그인/세션, bcrypt 해시)
- collector.py      수집 본체 (3카테고리×10지역=30게시판, 중복 방지, 에러 백오프)
- scoring.py        활성도 계산 + 라벨 피처(ratio 통계, 리셋 경과시간) — 추천 로직은 이 파일만 교체
- labels.py         라벨 수집 (비활성/불명/활성/강한활성 + 학습 피처 스냅샷)
- maintenance.py    7일 초과 데이터 아카이브(archive.db) 후 운영 DB서 삭제
- trophy_weights.csv 트로피 기준 251종 (app.py가 자동 로딩)
- templates/        화면 4종 (로그인 / 대시보드 / 온보딩 / 어종 상세)
- test_app.py       화면 시나리오 검증 (rf4.db를 덮어쓰므로 주의!)
- test_auth.py      회원 인증 시나리오 검증 (rf4.db를 덮어쓰므로 주의!)
- test_labels.py    라벨/아카이브 시나리오 검증 (rf4.db를 덮어쓰므로 주의!)

## 데이터 모델 (rf4.db)
- catches / appearances : 수집 기록 (first_seen 7일 이내만 유지)
- trophies              : 어종별 트로피/레어 기준 무게
- users / favorites     : 회원, 회원별 선호 어종
- labels                : 본인 활성도 판정 + 피처 스냅샷 (영구 보존, 정리 대상 아님)
