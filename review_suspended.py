"""보류(suspended) + 수동확인(review_required) 계정 2주 점검.

최신 게시물 6개를 Apify로 가져와 댓글 200+ 가 1개라도 있으면 active 복귀.
없으면 last_reviewed_at만 갱신.

사용법:
  .venv/bin/python review_suspended.py
  .venv/bin/python review_suspended.py --limit 5    # 테스트용 5개만
  .venv/bin/python review_suspended.py --dry-run    # 실제 변경 없이 결과 출력만
"""
import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.db import (
    get_conn, get_accounts_by_status, get_recent_posts_per_account,
    init_db, upsert_account, upsert_post, save_snapshot,
)
from src.apify_client import fetch_recent_posts

COMMENT_THRESHOLD = 200
BATCH_SIZE = 50


def review(limit: int | None = None, dry_run: bool = False):
    init_db()
    now = datetime.now(timezone.utc).isoformat()

    with get_conn() as conn:
        suspended      = get_accounts_by_status(conn, "suspended")
        review_req     = get_accounts_by_status(conn, "review_required")

    targets = suspended + review_req
    if limit:
        targets = targets[:limit]

    print(f"[점검 시작] suspended: {len(suspended)}개, review_required: {len(review_req)}개")
    print(f"  대상: {len(targets)}개" + (" (dry_run)" if dry_run else ""))

    if not targets:
        print("점검 대상 없음.")
        return

    revived_count = 0
    unchanged_count = 0

    batches = [targets[i:i + BATCH_SIZE] for i in range(0, len(targets), BATCH_SIZE)]

    with get_conn() as conn:
        for b_idx, batch in enumerate(batches, 1):
            print(f"  배치 {b_idx}/{len(batches)}: {len(batch)}개 조회 중...")

            if not dry_run:
                posts = fetch_recent_posts(batch, hours_filter=None)
                for p in posts:
                    upsert_post(conn, p)
                    save_snapshot(conn, p["media_id"], p["like_count"], p["comment_count"], now)
                conn.commit()

            for account_name in batch:
                recent = get_recent_posts_per_account(conn, account_name, limit=6)
                has_quality = any(p["comment_count"] >= COMMENT_THRESHOLD for p in recent)

                if dry_run:
                    status = "→ active 복귀 예정" if has_quality else "→ 보류 유지"
                    print(f"    [dry_run] {account_name}: {status}")
                    continue

                if has_quality:
                    upsert_account(conn, account_name, "active", "점검 통과 (댓글 200+ 확인)", None, now)
                    revived_count += 1
                    print(f"    ✓ {account_name}: active 복귀")
                else:
                    conn.execute(
                        "UPDATE accounts SET last_reviewed_at = ? WHERE account_name = ?",
                        (now, account_name)
                    )
                    unchanged_count += 1

            if not dry_run:
                conn.commit()

    if not dry_run:
        print(f"\n[점검 완료] active 복귀: {revived_count}개 / 보류 유지: {unchanged_count}개")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit",   type=int, default=None, help="처리 계정 수 제한 (테스트용)")
    parser.add_argument("--dry-run", action="store_true",    help="실제 변경 없이 결과 출력만")
    args = parser.parse_args()
    review(limit=args.limit, dry_run=args.dry_run)
