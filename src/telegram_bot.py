"""텔레그램 봇: 매일 10시 벤치시트 알림 + /bench 명령어 처리.

launchd 서비스로 항상 실행된다.
"""
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / "config" / ".env")
load_dotenv(Path.home() / "Desktop" / "harness" / ".env", override=False)

TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
KST     = timezone(timedelta(hours=9))
API     = f"https://api.telegram.org/bot{TOKEN}"

CONFIRM_DATA = "bench_yes"
CANCEL_DATA  = "bench_no"


# ── Telegram API 헬퍼 ─────────────────────────────────────────────────────────

def _post(method: str, **kwargs) -> dict:
    try:
        r = requests.post(f"{API}/{method}", timeout=10, **kwargs)
        return r.json()
    except Exception as e:
        print(f"[텔레그램] {method} 오류: {e}")
        return {}


def send_question(text: str = "벤치시트 만들까요?"):
    _post("sendMessage", json={
        "chat_id": CHAT_ID,
        "text": text,
        "reply_markup": {
            "inline_keyboard": [[
                {"text": "✅ 만들기", "callback_data": CONFIRM_DATA},
                {"text": "❌ 취소",   "callback_data": CANCEL_DATA},
            ]]
        },
    })


def answer_callback(callback_id: str, text: str = ""):
    _post("answerCallbackQuery", json={"callback_query_id": callback_id, "text": text})


def send_text(text: str):
    _post("sendMessage", json={"chat_id": CHAT_ID, "text": text})


def send_file(path: Path, caption: str = ""):
    try:
        with open(path, "rb") as f:
            _post("sendDocument",
                  data={"chat_id": CHAT_ID, "caption": caption},
                  files={"document": f})
    except Exception as e:
        print(f"[텔레그램] 파일 전송 오류: {e}")


# ── 수집 트리거 ───────────────────────────────────────────────────────────────

def _trigger_bench():
    sys.path.insert(0, str(ROOT))
    from src.job_runner import refresh_jobs, running_job, start_job

    jobs = refresh_jobs()
    if running_job(jobs):
        send_text("⚠️ 이미 수집이 진행 중입니다.")
        return

    job = start_job()
    send_text(f"🚀 수집 시작했습니다. 약 15분 후 완료되면 파일을 보내드릴게요.\n(작업 ID: {job['id']})")

    # 완료 대기 후 파일 전송 (별도 스레드)
    import threading
    threading.Thread(target=_wait_and_send, args=(job["id"],), daemon=True).start()


def _wait_and_send(job_id: str):
    sys.path.insert(0, str(ROOT))
    from src.job_runner import refresh_jobs

    for _ in range(120):  # 최대 20분 대기
        time.sleep(10)
        jobs = refresh_jobs()
        job = next((j for j in jobs if j["id"] == job_id), None)
        if not job:
            break
        if job["status"] == "done":
            excel = ROOT / job["excel_path"]
            send_file(excel, caption=f"✅ 벤치시트 완료!\n{excel.name}")
            return
        if job["status"] == "failed":
            send_text("❌ 수집 중 오류가 발생했습니다. 로그를 확인해주세요.")
            return

    send_text("⏰ 20분이 지났는데 완료 여부를 확인하지 못했습니다. 웹앱을 확인해주세요.")


# ── 폴링 루프 ─────────────────────────────────────────────────────────────────

def run():
    if not TOKEN or not CHAT_ID:
        print("[텔레그램 봇] TOKEN 또는 CHAT_ID 없음 — 종료")
        return

    print("[텔레그램 봇] 시작")
    offset = 0
    last_daily_date = None  # 오늘 10시 알림을 보냈는지 추적

    while True:
        # ── 매일 10시 KST 알림 ────────────────────────────────────────────────
        now_kst = datetime.now(KST)
        today   = now_kst.date()
        if now_kst.hour == 10 and last_daily_date != today:
            send_question("📊 오전 10시입니다. 벤치시트 만들까요?")
            last_daily_date = today

        # ── 업데이트 폴링 ─────────────────────────────────────────────────────
        resp = _post("getUpdates", json={"offset": offset, "timeout": 30, "allowed_updates": ["message", "callback_query"]})
        updates = resp.get("result", [])

        for upd in updates:
            offset = upd["update_id"] + 1

            # 콜백 버튼 처리
            if "callback_query" in upd:
                cq   = upd["callback_query"]
                data = cq.get("data", "")
                answer_callback(cq["id"])
                if data == CONFIRM_DATA:
                    _trigger_bench()
                elif data == CANCEL_DATA:
                    send_text("취소했습니다. 필요하면 /bench 로 언제든지 시작할 수 있어요.")

            # 텍스트 명령어 처리
            elif "message" in upd:
                msg  = upd["message"]
                text = msg.get("text", "").strip()
                if text.startswith("/bench"):
                    send_question("벤치시트를 지금 만들까요?")

        time.sleep(1)


if __name__ == "__main__":
    run()
