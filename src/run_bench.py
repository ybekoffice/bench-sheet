"""collect_posts.py → export_excel.py 순차 실행 래퍼 (벤치시트 생성용).

job_runner.py 에서 start_new_session=True 로 호출한다.
"""
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent

# 수집 시작 시각 기록 — export 시 이 시각 이후 스냅샷만 추출
since = datetime.now(timezone.utc).isoformat()

r = subprocess.run([sys.executable, ROOT / "collect_posts.py"], cwd=ROOT)
if r.returncode != 0:
    sys.exit(r.returncode)

r = subprocess.run([sys.executable, ROOT / "export_excel.py", "--since", since], cwd=ROOT)
sys.exit(r.returncode)
