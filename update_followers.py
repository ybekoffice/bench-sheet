"""팔로워 수 일일 업데이트 스크립트.

posts DB에 있는 모든 계정의 팔로워 수를 Apify 프로필 스크래퍼로 가져와 일괄 갱신.
하루 1회 실행 권장 (cron: 매일 새벽 3시 등).
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from src.apify_client import fetch_follower_counts
from src.db import get_all_account_names, get_conn, init_db, update_follower_counts


def run():
    init_db()
    now = datetime.now(timezone.utc).isoformat()

    with get_conn() as conn:
        accounts = get_all_account_names(conn)
        if not accounts:
            print("[update_followers] DB에 계정이 없습니다.")
            return

        print(f"[update_followers] 팔로워 수 업데이트 시작: {len(accounts)}개 계정")
        counts = fetch_follower_counts(accounts)

        if not counts:
            print("[update_followers] Apify에서 결과가 없습니다.")
            return

        update_follower_counts(conn, counts, now)
        conn.commit()

    found = len(counts)
    print(f"[update_followers] 완료: {found}/{len(accounts)}개 계정 팔로워 수 갱신")


if __name__ == "__main__":
    run()
