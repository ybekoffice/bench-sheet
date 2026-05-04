"""텔레그램 알림 공통 유틸."""
import os
from pathlib import Path

import requests
from dotenv import load_dotenv

_ROOT = Path(__file__).parent.parent
load_dotenv(_ROOT / "config" / ".env")
load_dotenv(Path.home() / "Desktop" / "harness" / ".env", override=False)

_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN", "")
_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
_API     = f"https://api.telegram.org/bot{_TOKEN}"


def send_text(text: str):
    if not _TOKEN or not _CHAT_ID:
        return
    try:
        requests.post(f"{_API}/sendMessage",
                      json={"chat_id": _CHAT_ID, "text": text},
                      timeout=10)
    except Exception as e:
        print(f"[텔레그램] 전송 오류: {e}")


def send_file(path: Path, caption: str = ""):
    if not _TOKEN or not _CHAT_ID:
        return
    try:
        with open(path, "rb") as f:
            requests.post(f"{_API}/sendDocument",
                          data={"chat_id": _CHAT_ID, "caption": caption},
                          files={"document": f},
                          timeout=60)
    except Exception as e:
        print(f"[텔레그램] 파일 전송 오류: {e}")
