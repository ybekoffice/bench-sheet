"""collect_posts.py → export_excel.py 순차 실행 래퍼 (벤치시트 생성용).

job_runner.py 에서 start_new_session=True 로 호출한다.
"""
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / "config" / ".env")
load_dotenv(Path.home() / "Desktop" / "harness" / ".env", override=False)

# 수집 시작 시각 기록 — export 시 이 시각 이후 스냅샷만 추출
since = datetime.now(timezone.utc).isoformat()

r = subprocess.run([sys.executable, ROOT / "collect_posts.py"], cwd=ROOT)
if r.returncode != 0:
    sys.exit(r.returncode)

r = subprocess.run([sys.executable, ROOT / "export_excel.py", "--since", since], cwd=ROOT)
if r.returncode != 0:
    sys.exit(r.returncode)

# 완료 후 가장 최근 벤치시트 파일을 텔레그램으로 전송
def _send_telegram(excel_path: Path):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("[텔레그램] 환경변수 없음 — 전송 건너뜀")
        return
    try:
        import requests
        with open(excel_path, "rb") as f:
            resp = requests.post(
                f"https://api.telegram.org/bot{token}/sendDocument",
                data={"chat_id": chat_id, "caption": f"벤치시트 생성 완료 ✅\n{excel_path.name}"},
                files={"document": f},
                timeout=60,
            )
        if resp.ok:
            print(f"[텔레그램] 전송 완료: {excel_path.name}")
        else:
            print(f"[텔레그램] 전송 실패: {resp.text}")
    except Exception as e:
        print(f"[텔레그램] 오류: {e}")

excel_files = sorted((ROOT / "data").glob("벤치시트_*.xlsx"), key=lambda p: p.stat().st_mtime)
if excel_files:
    _send_telegram(excel_files[-1])
