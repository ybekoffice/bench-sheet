"""활성 계정의 신규 팔로잉을 추적해 새 쇼핑 계정을 발굴한다.

흐름:
  1. active 계정의 팔로잉 목록을 Apify로 수집
  2. 이전 스냅샷(account_followings)과 비교 → 신규 팔로잉 추출
  3. 이미 accounts 테이블에 있는 계정 제외
  4. 나머지를 candidate으로 등록
  5. 3단계 퍼널 검증 (validate_candidates)
  6. account_followings 스냅샷 갱신

사용법:
  .venv/bin/python discover_accounts.py
  .venv/bin/python discover_accounts.py --limit 5       # 추적 대상 active 계정 5개만
  .venv/bin/python discover_accounts.py --no-comments   # step4 건너뜀 (→ review_required)
  .venv/bin/python discover_accounts.py --snapshot-only # 팔로잉 스냅샷 갱신만 (검증 없음)
"""
import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.db import (
    get_conn, get_accounts_by_status, get_followings_snapshot,
    init_db, upsert_account, upsert_following,
)
from src.apify_client import fetch_followings, fetch_recent_posts, fetch_comments
from src.candidate_validator import validate_candidates, print_summary

BATCH_SIZE = 50


def _update_followings_snapshot(conn, account_name: str, current: list[str], now: str):
    for following in current:
        upsert_following(conn, account_name, following, now)


def discover(
    limit: int | None = None,
    no_comments: bool = False,
    snapshot_only: bool = False,
):
    init_db()
    now = datetime.now(timezone.utc).isoformat()

    with get_conn() as conn:
        active_accounts = get_accounts_by_status(conn, "active")

    if limit:
        active_accounts = active_accounts[:limit]

    print(f"[발굴 시작] 팔로잉 추적 대상: {len(active_accounts)}개 active 계정")

    # ── Step 1: 팔로잉 목록 수집 ─────────────────────────────────────────
    print("  Apify로 팔로잉 목록 수집 중...")
    followings_map = fetch_followings(active_accounts, batch_size=BATCH_SIZE)
    print(f"  팔로잉 수집 완료: {sum(len(v) for v in followings_map.values())}개 팔로잉")

    # ── Step 2: 신규 팔로잉 추출 + 스냅샷 갱신 ───────────────────────────
    new_candidates: set[str] = set()

    with get_conn() as conn:
        existing_accounts = set(
            r[0] for r in conn.execute("SELECT account_name FROM accounts").fetchall()
        )

        for account_name, current_followings in followings_map.items():
            prev_snapshot = get_followings_snapshot(conn, account_name)
            new_followings = set(current_followings) - prev_snapshot

            if new_followings:
                print(f"  {account_name}: 신규 팔로잉 {len(new_followings)}개 발견")

            # 스냅샷 갱신 (신규 포함 전체 기록)
            _update_followings_snapshot(conn, account_name, current_followings, now)

            # 이미 관리 중인 계정 제외
            fresh = new_followings - existing_accounts
            new_candidates.update(fresh)

        conn.commit()

    new_list = sorted(new_candidates)
    print(f"\n신규 후보: {len(new_list)}개 (기존 관리 계정 제외)")

    if not new_list:
        print("발굴된 신규 후보 없음. 종료.")
        return

    if snapshot_only:
        print("--snapshot-only: 검증 없이 종료.")
        return

    # ── Step 3: candidate 등록 ───────────────────────────────────────────
    with get_conn() as conn:
        for name in new_list:
            upsert_account(conn, name, "candidate", "팔로잉 추적 발굴", "discovery", now)
        conn.commit()
    print(f"candidate 등록: {len(new_list)}개")

    # ── Step 4~6: 3단계 퍼널 검증 ────────────────────────────────────────
    print("\n[퍼널 검증 시작]")
    fetch_comments_fn = None if no_comments else fetch_comments

    with get_conn() as conn:
        result = validate_candidates(
            conn,
            new_list,
            now,
            fetch_posts_fn=fetch_recent_posts,
            fetch_comments_fn=fetch_comments_fn,
        )

    print_summary(result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit",         type=int, default=None,
                        help="팔로잉 추적 대상 active 계정 수 제한 (테스트용)")
    parser.add_argument("--no-comments",   action="store_true",
                        help="step4 댓글 스크래핑 건너뜀 (→ 통과 계정 모두 review_required)")
    parser.add_argument("--snapshot-only", action="store_true",
                        help="팔로잉 스냅샷 갱신만 하고 퍼널 검증 없이 종료")
    args = parser.parse_args()

    discover(
        limit=args.limit,
        no_comments=args.no_comments,
        snapshot_only=args.snapshot_only,
    )
