"""계정 상태 자동 평가 (A 조건).

active 계정의 최근 게시물 5개를 조회해 댓글 200+ 가 하나도 없으면 suspended 처리.
게시물이 5개 미만인 계정은 데이터 부족으로 평가 skip.

사용법:
  .venv/bin/python evaluate_accounts.py
  .venv/bin/python evaluate_accounts.py --dry-run   # 실제 변경 없이 결과만 출력
"""
import argparse
from datetime import datetime, timezone

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from src.db import get_conn, get_accounts_by_status, get_recent_posts_per_account, upsert_account

COMMENT_THRESHOLD = 200
MIN_POSTS = 5


def evaluate(dry_run: bool = False):
    now = datetime.now(timezone.utc).isoformat()

    with get_conn() as conn:
        active_accounts = get_accounts_by_status(conn, "active")
        print(f"[평가 시작] active 계정: {len(active_accounts)}개")

        suspended_count = 0
        skipped_count   = 0

        for account_name in active_accounts:
            posts = get_recent_posts_per_account(conn, account_name, limit=MIN_POSTS)

            if len(posts) < MIN_POSTS:
                skipped_count += 1
                continue

            has_quality_post = any(p["comment_count"] >= COMMENT_THRESHOLD for p in posts)

            if not has_quality_post:
                suspended_count += 1
                if dry_run:
                    print(f"  [dry_run] 보류 대상: {account_name}")
                else:
                    upsert_account(
                        conn, account_name,
                        status="suspended",
                        reason=f"최근 {MIN_POSTS}개 게시물 모두 댓글 {COMMENT_THRESHOLD} 미달",
                        source=None,
                        now=now,
                    )

        if not dry_run:
            conn.commit()

    label = "[dry_run] " if dry_run else ""
    print(f"{label}[평가 완료] 보류 처리: {suspended_count}개 / 데이터 부족 skip: {skipped_count}개")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="실제 변경 없이 결과만 출력")
    args = parser.parse_args()
    evaluate(dry_run=args.dry_run)
