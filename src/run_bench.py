"""collect_posts.py → export_excel.py 순차 실행 래퍼 (벤치시트 생성용).

job_runner.py 에서 start_new_session=True 로 호출한다.
"""
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.notifier import send_file, send_text

# 수집 시작 시각 기록 — export 시 이 시각 이후 스냅샷만 추출
since = datetime.now(timezone.utc).isoformat()

r = subprocess.run([sys.executable, ROOT / "collect_posts.py"], cwd=ROOT)
if r.returncode != 0:
    send_text("❌ 수집 중 오류가 발생했습니다. 로그를 확인해주세요.")
    sys.exit(r.returncode)

r = subprocess.run([sys.executable, ROOT / "export_excel.py", "--since", since], cwd=ROOT)
if r.returncode != 0:
    send_text("❌ 엑셀 생성 중 오류가 발생했습니다. 로그를 확인해주세요.")
    sys.exit(r.returncode)

excel_files = sorted((ROOT / "data").glob("벤치시트_*.xlsx"), key=lambda p: p.stat().st_mtime)
if excel_files:
    excel = excel_files[-1]
    send_file(excel, caption=f"✅ 벤치시트 완료!\n{excel.name}")
    print(f"[텔레그램] 전송 완료: {excel.name}")
