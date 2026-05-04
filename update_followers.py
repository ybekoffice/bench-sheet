"""팔로워 수 업데이트 스크립트.

accounts 테이블 기준 763개 전체 계정 중 팔로워 수가 없는 것만 Apify로 수집.
결과는 accounts 테이블과 posts 테이블 양쪽에 저장.
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from src.apify_client import fetch_follower_counts
from src.db import get_accounts_needing_followers, get_conn, init_db, update_follower_counts


def run():
    init_db()
    now = datetime.now(timezone.utc).isoformat()

    with get_conn() as conn:
        accounts = get_accounts_needing_followers(conn)
        if not accounts:
            print("[update_followers] 팔로워 수집이 필요한 계정이 없습니다.")
            return

        print(f"[update_followers] 팔로워 미수집 계정: {len(accounts)}개 → Apify 호출 시작")
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
