"""인스타 벤치시트 웹앱.

실행:
  .venv/bin/streamlit run app.py --server.address 0.0.0.0 --server.port 8501
"""
import time
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

import streamlit as st

from src.job_runner import PROJECT_ROOT, refresh_jobs, running_job, start_job, read_log_tail

KST = timezone(timedelta(hours=9))


def _duration_min(job: dict) -> int | None:
    if job.get("started_at") and job.get("finished_at"):
        delta = datetime.fromisoformat(job["finished_at"]) - datetime.fromisoformat(job["started_at"])
        return max(1, int(delta.total_seconds() // 60))
    return None


st.set_page_config(page_title="인스타 벤치시트", page_icon="📊", layout="centered")
st.title("📊 인스타 벤치시트")

jobs = refresh_jobs()
current = running_job(jobs)

# ── 안내 문구 ───────────────────────────────────────────────────────────────
st.info(
    "수집에 약 15분 소요 · 비용 약 $12 · "
    "**화면을 닫아도 수집은 계속 진행됩니다**"
)

# ── 버튼 + 진행 상태 ────────────────────────────────────────────────────────
if current:
    started = datetime.fromisoformat(current["started_at"])
    now_kst = datetime.now(KST)
    elapsed_min = int((now_kst - started).total_seconds() // 60)

    st.warning(
        f"수집 중 ··· "
        f"(시작시간: {started.strftime('%H:%M')} | 경과: {elapsed_min}분)"
    )
    st.button("벤치시트 만들기", disabled=True, use_container_width=True)
else:
    if st.button("벤치시트 만들기", type="primary", use_container_width=True):
        start_job()
        st.rerun()

# ── 히스토리 ────────────────────────────────────────────────────────────────
st.divider()
st.subheader("히스토리")

if not jobs:
    st.caption("아직 실행 기록이 없습니다.")
else:
    for job in jobs:
        status_label = {"running": "🔄 진행 중", "done": "✅ 완료", "failed": "❌ 실패"}.get(job["status"], job["status"])
        started_str = datetime.fromisoformat(job["started_at"]).strftime("%m-%d %H:%M")
        dur = _duration_min(job)
        dur_str = f"({dur}분)" if dur else ""

        col_info, col_dl, col_log = st.columns([4, 3, 2])

        with col_info:
            st.write(f"**{started_str}**  {status_label}  {dur_str}")

        with col_dl:
            if job["status"] == "done" and job.get("excel_path"):
                excel_path = PROJECT_ROOT / job["excel_path"]
                if excel_path.exists():
                    with open(excel_path, "rb") as f:
                        st.download_button(
                            "📥 엑셀 다운로드",
                            data=f,
                            file_name=excel_path.name,
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            key=f"dl_{job['id']}",
                        )

        with col_log:
            log_tail = read_log_tail(job)
            if log_tail:
                with st.popover("로그"):
                    st.code(log_tail, language=None)

# ── 시간대별 평균 소요시간 ──────────────────────────────────────────────────
done_jobs = [j for j in jobs if j["status"] == "done" and _duration_min(j) is not None]

if len(done_jobs) >= 2:
    st.divider()
    st.subheader("시간대별 평균 소요시간")

    bucket: dict[str, list[int]] = defaultdict(list)
    for job in done_jobs:
        hour = datetime.fromisoformat(job["started_at"]).strftime("%H시")
        bucket[hour].append(_duration_min(job))

    rows = sorted(
        [{"시작 시간대": h, "평균 소요(분)": round(sum(v) / len(v)), "횟수": len(v)} for h, v in bucket.items()],
        key=lambda r: r["시작 시간대"],
    )
    st.table(rows)

# ── 자동 갱신 (수집 중일 때만) ─────────────────────────────────────────────
if current:
    time.sleep(10)
    st.rerun()
