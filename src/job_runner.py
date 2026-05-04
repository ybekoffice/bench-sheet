"""벤치시트 작업 실행 및 상태 관리.

data/jobs.json 에 작업 히스토리를 기록한다.
"""
import json
import os
import re
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data"
LOGS_DIR = PROJECT_ROOT / "logs"
JOBS_FILE = DATA_DIR / "jobs.json"
PYTHON = str(PROJECT_ROOT / ".venv" / "bin" / "python")
KST = timezone(timedelta(hours=9))

_ANSI = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def strip_ansi(text: str) -> str:
    return _ANSI.sub("", text)


def load_jobs() -> list[dict]:
    if JOBS_FILE.exists():
        try:
            return json.loads(JOBS_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
    return []


def _save_jobs(jobs: list[dict]):
    JOBS_FILE.write_text(json.dumps(jobs, indent=2, ensure_ascii=False), encoding="utf-8")


def _is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    # 좀비 프로세스는 os.kill(0) 을 통과하지만 실제론 종료된 상태
    try:
        result = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(pid)],
            capture_output=True, text=True,
        )
        if "Z" in result.stdout:
            return False
    except Exception:
        pass
    return True


def refresh_jobs() -> list[dict]:
    """running 상태 항목의 실제 완료 여부를 확인하고 jobs 리스트를 반환."""
    jobs = load_jobs()
    changed = False
    for job in jobs:
        if job["status"] != "running":
            continue
        pid = job.get("pid")
        if pid and _is_alive(pid):
            continue  # 아직 실행 중
        # 프로세스가 종료됨 — 엑셀 파일 생성 여부로 성공/실패 판정
        started_ts = datetime.fromisoformat(job["started_at"]).timestamp()
        excel_files = sorted(DATA_DIR.glob("벤치시트_*.xlsx"), key=lambda p: p.stat().st_mtime)
        new_excel = next((p for p in excel_files if p.stat().st_mtime > started_ts), None)
        if new_excel:
            job["status"] = "done"
            job["finished_at"] = datetime.fromtimestamp(new_excel.stat().st_mtime, tz=KST).isoformat()
            job["excel_path"] = str(new_excel.relative_to(PROJECT_ROOT))
        else:
            job["status"] = "failed"
            job["finished_at"] = datetime.now(KST).isoformat()
        changed = True
    if changed:
        _save_jobs(jobs)
    return jobs


def running_job(jobs: list[dict]) -> dict | None:
    return next((j for j in jobs if j["status"] == "running"), None)


def start_job() -> dict:
    """수집 + 엑셀 내보내기를 백그라운드 subprocess로 시작."""
    LOGS_DIR.mkdir(exist_ok=True)
    now = datetime.now(KST)
    job_id = now.strftime("%Y%m%d_%H%M%S")
    log_path = LOGS_DIR / f"run_{job_id}.log"

    with open(log_path, "w", encoding="utf-8") as log_file:
        proc = subprocess.Popen(
            [PYTHON, str(PROJECT_ROOT / "src" / "run_bench.py")],
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,  # 부모(Streamlit) 종료와 무관하게 살아남음
            cwd=str(PROJECT_ROOT),
        )

    job: dict = {
        "id": job_id,
        "started_at": now.isoformat(),
        "finished_at": None,
        "status": "running",
        "pid": proc.pid,
        "log_path": str(log_path.relative_to(PROJECT_ROOT)),
        "excel_path": None,
    }
    jobs = load_jobs()
    jobs.insert(0, job)
    _save_jobs(jobs)
    return job


def read_log_tail(job: dict, lines: int = 50) -> str:
    log_path = job.get("log_path")
    if not log_path:
        return ""
    full_path = PROJECT_ROOT / log_path
    if not full_path.exists():
        return ""
    text = full_path.read_text(encoding="utf-8", errors="replace")
    tail = "\n".join(strip_ansi(text).splitlines()[-lines:])
    return tail
