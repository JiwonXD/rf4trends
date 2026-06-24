# 안드로이드 태블릿에 RF4 관제탑 올리기 (Termux)

집 와이파이 안에서 혼자 쓰는 개인 서버 설정. 한 번만 하면 됩니다.

---

## 1단계 — Termux 설치 (플레이스토어 ❌)

플레이스토어 버전은 업데이트가 끊겨서 오류가 납니다. **F-Droid에서 설치하세요.**

1. 태블릿 브라우저에서 https://f-droid.org 접속 → F-Droid 앱(APK) 설치
   (설정에서 "출처를 알 수 없는 앱 설치 허용" 필요)
2. F-Droid 앱을 열고 검색창에 **Termux** → 설치
3. 함께 설치하면 좋은 것: **Termux:Boot** (부팅 시 자동 실행용, 선택)

---

## 2단계 — Termux 기본 설정

Termux를 열고 아래를 순서대로 입력 (한 줄씩):

```bash
pkg update -y && pkg upgrade -y
pkg install -y python rust binutils
termux-setup-storage
```

- `rust`, `binutils`: 일부 파이썬 패키지 설치에 필요 (없으면 에러남)
- `termux-setup-storage`: 태블릿 내부 저장소 접근 권한. 팝업이 뜨면 허용
  → 이후 `~/storage/shared` 가 태블릿의 공용 저장소(다운로드 폴더 등)가 됨

---

## 3단계 — 코드 옮기기 (USB)

PC에서 받은 `rf4site` 폴더(또는 zip)를 태블릿으로:

1. 태블릿을 USB로 PC에 연결 → "파일 전송(MTP)" 모드 선택
2. PC에서 `rf4site` 폴더(또는 rf4site.zip)를 태블릿의 **Download 폴더**에 복사
3. (기존에 모아둔 `rf4.db`가 있다면 rf4site 폴더 안에 같이 넣기 — 그동안 수집한 데이터가 보존됨)
4. USB 분리 후 Termux에서:

```bash
cd ~/storage/shared/Download
# zip으로 옮겼다면 압축 해제:
pkg install -y unzip
unzip rf4site.zip
# 작업 폴더를 홈으로 복사 (저장소 권한 이슈 회피)
cp -r rf4site ~/rf4site
cd ~/rf4site
```

---

## 4단계 — 의존성 설치 & 실행

```bash
pip install -r requirements.txt
python app.py
```

처음 실행하면 화면에 접속 주소가 출력됩니다:
```
이 기기에서:   http://localhost:8000
다른 기기에서: http://<이 태블릿의 IP>:8000
```

**태블릿 IP 확인** (다른 줄에서, 또는 와이파이 설정 화면에서):
```bash
ifconfig wlan0 | grep inet
```
보통 `192.168.0.x` 또는 `192.168.1.x` 형태. 이 주소를 폰/PC 브라우저에 입력하면 접속됩니다.
(같은 와이파이에 연결되어 있어야 함)

20초쯤 뒤 첫 수집이 돌고, 이후 15분마다 자동 수집됩니다.

---

## 5단계 — 24시간 안 꺼지게 (중요)

안드로이드는 배터리 절약을 위해 백그라운드 앱을 잠재웁니다. 막으려면:

1. **Termux wake-lock 켜기**: Termux 알림을 내려서 "Acquire wakelock" 탭
   (또는 실행 전 명령어: `termux-wake-lock`)
2. **배터리 최적화 예외**: 태블릿 설정 → 앱 → Termux → 배터리 → "제한 없음"
3. **충전 연결 유지**: 상시 서버이므로 충전기를 꽂아두는 걸 권장
   (배터리 100% 고정 사용이 걱정되면, 태블릿에 충전 제한 기능이 있는지 확인)

제조사(삼성/샤오미 등)마다 배터리 설정 위치가 조금씩 다릅니다. "삼성 태블릿 백그라운드 앱 제한 해제" 식으로 검색하면 정확한 경로가 나옵니다.

---

## 자주 쓰는 것

- **서버 끄기**: Termux에서 `Ctrl + C`
- **다시 켜기**: `cd ~/rf4site && python app.py`
- **코드 수정본 반영**: USB로 바뀐 파일만 Download에 복사 → `cp ~/storage/shared/Download/<파일> ~/rf4site/`
- **수집 잘 되는지 확인**: 실행 중인 Termux 화면에 15분마다 수집 로그가 찍힘

---

## 문제 해결

- `pip install`에서 에러 → `rust`, `binutils` 설치됐는지 확인 (2단계)
- 다른 기기에서 접속 안 됨 → ① 같은 와이파이인지 ② 태블릿 IP가 맞는지 ③ 공유기의 "AP 격리/게스트 모드"가 꺼져 있는지
- 한참 뒤 수집이 멈춤 → 배터리 최적화 예외(5단계)가 안 걸린 것
