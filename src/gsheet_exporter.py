"""수집 결과를 구글시트에 탭으로 기록.

사용법:
  .venv/bin/python src/gsheet_exporter.py
  .venv/bin/python src/gsheet_exporter.py --since "2026-04-30T04:39:00+00:00"
"""
import argparse
import os
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

import gspread
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials

PROJECT_ROOT = Path(__file__).parent.parent
DB_PATH = PROJECT_ROOT / "data" / "posts.db"
KST = timezone(timedelta(hours=9))

load_dotenv(PROJECT_ROOT / "config" / ".env")
SHEET_URL = os.getenv("GSHEET_URL", "")

SCOPES = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

HEADERS = [
    "계정명", "팔로워수", "좋아요", "댓글수", "댓글증가(증가율%)",
    "미디어타입", "캡션", "게시물링크", "게시일시", "최초수집일시", "이번수집일시",
]

LINK_COL_IDX = HEADERS.index("게시물링크") + 1  # 1-based


def _credentials_path() -> Path:
    files = list((PROJECT_ROOT / "config").glob("*.json"))
    if not files:
        raise FileNotFoundError("config/ 에 서비스 계정 JSON 파일이 없습니다.")
    return files[0]


def _connect_sheet():
    creds = Credentials.from_service_account_file(str(_credentials_path()), scopes=SCOPES)
    gc = gspread.authorize(creds)
    return gc.open_by_url(SHEET_URL)


def _fmt_dt(s: str | None) -> str:
    if not s:
        return ""
    try:
        return datetime.fromisoformat(s).astimezone(KST).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return s or ""


def _fetch_data(conn: sqlite3.Connection, since: str, now_str: str) -> list[list]:
    rows = conn.execute("""
        SELECT DISTINCT
            p.account_name,
            COALESCE(fc.follower_count, 0)  AS follower_count,
            p.like_count,
            p.comment_count,
            prev.comment_count              AS prev_comment_count,
            p.media_type,
            p.caption,
            p.permalink,
            p.media_timestamp,
            p.first_seen_at
        FROM posts p
        INNER JOIN post_snapshots ps
            ON p.media_id = ps.media_id AND ps.collected_at >= :since
        LEFT JOIN (
            SELECT ps2.media_id, ps2.comment_count
            FROM post_snapshots ps2
            INNER JOIN (
                SELECT media_id, MAX(collected_at) AS max_at
                FROM post_snapshots
                WHERE collected_at < :since
                GROUP BY media_id
            ) latest
                ON ps2.media_id = latest.media_id
               AND ps2.collected_at = latest.max_at
        ) prev ON p.media_id = prev.media_id
        LEFT JOIN (
            SELECT account_name, MAX(follower_count) AS follower_count
            FROM posts
            WHERE follower_count > 0
            GROUP BY account_name
        ) fc ON p.account_name = fc.account_name
        WHERE p.comment_count >= 300
        ORDER BY p.comment_count DESC
    """, {"since": since}).fetchall()

    result = []
    for r in rows:
        prev_cnt = r[4]
        if prev_cnt is None:
            delta_str = "신규"
        else:
            delta = r[3] - prev_cnt
            rate  = round(delta / prev_cnt * 100) if prev_cnt > 0 else 0
            sign  = "+" if delta >= 0 else ""
            delta_str = f"{sign}{delta}({sign}{rate}%)"

        result.append([
            r[0],                # 계정명
            int(r[1]),           # 팔로워수
            int(r[2] or 0),      # 좋아요
            int(r[3] or 0),      # 댓글수
            delta_str,           # 댓글증가(증가율%)
            r[5] or "",          # 미디어타입
            r[6] or "",          # 캡션
            r[7] or "",          # 게시물링크 (나중에 HYPERLINK 수식으로 교체)
            _fmt_dt(r[8]),       # 게시일시
            _fmt_dt(r[9]),       # 최초수집일시
            now_str,             # 이번수집일시
        ])
    return result


def export(since: str):
    if not SHEET_URL:
        print("[구글시트] GSHEET_URL 미설정 — 건너뜀")
        return

    now_kst  = datetime.now(KST)
    now_str  = now_kst.strftime("%Y-%m-%d %H:%M")
    tab_name = now_kst.strftime("%m-%d %H:%M")

    conn = sqlite3.connect(DB_PATH)
    data = _fetch_data(conn, since, now_str)
    conn.close()

    if not data:
        print("[구글시트] 조건에 맞는 데이터 없음 — 탭 생성 건너뜀")
        return

    sh = _connect_sheet()

    # 같은 이름 탭이 있으면 삭제 후 재생성
    try:
        sh.del_worksheet(sh.worksheet(tab_name))
    except gspread.exceptions.WorksheetNotFound:
        pass

    ws = sh.add_worksheet(title=tab_name, rows=len(data) + 1, cols=len(HEADERS), index=0)

    # 데이터 일괄 기록
    ws.update([HEADERS] + data, value_input_option="USER_ENTERED")

    # 헤더 스타일: 굵게 + 회색 배경
    col_end = chr(64 + len(HEADERS))
    ws.format(f"A1:{col_end}1", {
        "textFormat": {"bold": True},
        "backgroundColor": {"red": 0.851, "green": 0.851, "blue": 0.851},
    })

    # 신규 행: 전체 행 연두색
    new_indices = [i + 2 for i, row in enumerate(data) if row[4] == "신규"]
    # 캐러셀 행: 미디어타입 셀만 하늘색
    carousel_col_letter = chr(64 + HEADERS.index("미디어타입") + 1)
    carousel_indices = [i + 2 for i, row in enumerate(data) if row[5] == "CAROUSEL_ALBUM"]

    formats = []
    for r in new_indices:
        formats.append({
            "range": f"A{r}:{col_end}{r}",
            "format": {"backgroundColor": {"red": 0.851, "green": 0.953, "blue": 0.831}},
        })
    for r in carousel_indices:
        formats.append({
            "range": f"{carousel_col_letter}{r}",
            "format": {"backgroundColor": {"red": 0.698, "green": 0.878, "blue": 0.953}},
        })
    if formats:
        ws.batch_format(formats)

    # 게시물링크 열: HYPERLINK 수식 적용
    link_col_letter = chr(64 + LINK_COL_IDX)
    link_updates = []
    for i, row in enumerate(data, start=2):
        url = row[LINK_COL_IDX - 1]
        if url and url.startswith("http"):
            link_updates.append({
                "range": f"{link_col_letter}{i}",
                "values": [[f'=HYPERLINK("{url}","링크")']],
            })
    if link_updates:
        ws.batch_update(link_updates, value_input_option="USER_ENTERED")

    # 행 높이 고정 + 전체 클립 + 숫자 열 천단위 포맷
    num_col_indices = [HEADERS.index(h) for h in ("팔로워수", "좋아요", "댓글수")]
    sh.batch_update({"requests": [
        {
            "updateDimensionProperties": {
                "range": {
                    "sheetId": ws.id,
                    "dimension": "ROWS",
                    "startIndex": 1,
                    "endIndex": len(data) + 1,
                },
                "properties": {"pixelSize": 21},
                "fields": "pixelSize",
            }
        },
        {
            "repeatCell": {
                "range": {"sheetId": ws.id, "startRowIndex": 1},
                "cell": {"userEnteredFormat": {"wrapStrategy": "CLIP"}},
                "fields": "userEnteredFormat.wrapStrategy",
            }
        },
        *[
            {
                "repeatCell": {
                    "range": {
                        "sheetId": ws.id,
                        "startRowIndex": 1,
                        "startColumnIndex": col_i,
                        "endColumnIndex": col_i + 1,
                    },
                    "cell": {"userEnteredFormat": {"numberFormat": {"type": "NUMBER", "pattern": "#,##0"}}},
                    "fields": "userEnteredFormat.numberFormat",
                }
            }
            for col_i in num_col_indices
        ],
    ]})

    print(f"[구글시트] 탭 '{tab_name}' 생성 완료 — {len(data)}행 (신규 {len(new_indices)}건 / 캐러셀 {len(carousel_indices)}건)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", type=str, default=None,
                        help="이 시각(ISO) 이후 수집된 게시물만 추출. 미입력 시 오늘 00:00 기준")
    args = parser.parse_args()

    since = args.since or datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0,
    ).isoformat()
    export(since)
