# change_password.py — 계정 비밀번호 변경 (수동 1회 실행용 유틸)
# 비밀번호 변경 UI가 없어 DB의 bcrypt 해시를 직접 갱신한다.
# 새 비밀번호는 getpass로 입력받아 화면·히스토리에 노출되지 않는다.
#
# 실행: python3 change_password.py <아이디> [DB경로]   (기본: ./rf4.db)

import getpass
import sqlite3
import sys

sys.path.insert(0, "../rf4site")
import auth

username = sys.argv[1] if len(sys.argv) > 1 else "admin"
db = sys.argv[2] if len(sys.argv) > 2 else "rf4.db"

conn = sqlite3.connect(db)
row = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
if not row:
    print(f"[오류] 아이디 '{username}'을 찾을 수 없습니다.")
    sys.exit(1)

password = getpass.getpass(f"'{username}'의 새 비밀번호: ")
err = auth.validate_password(password)
if err:
    print(f"[오류] {err}")
    sys.exit(1)
confirm = getpass.getpass("다시 입력: ")
if password != confirm:
    print("[오류] 두 입력이 일치하지 않습니다.")
    sys.exit(1)

pw_hash = auth._hash_pw(password)
conn.execute("UPDATE users SET password_hash = ? WHERE username = ?", (pw_hash, username))
conn.commit()
conn.close()
print(f"'{username}' 비밀번호가 변경되었습니다.")
