"""기존 seed 계정 + candidate 계정 일괄 3단계 퍼널 검증.

사용법:
  .venv/bin/python validate_candidates.py              # candidate 전체 검증 (댓글 스크래핑 포함)
  .venv/bin/python validate_candidates.py --no-comments  # step 4 건너뜀 (→ 모두 review_required)
  .venv/bin/python validate_candidates.py --limit 5    # 테스트용 5개만
  .venv/bin/python validate_candidates.py --reset      # seed 계정 active→candidate 리셋 후 검증
  .venv/bin/python validate_candidates.py --reset-only # 리셋만 하고 종료
"""
import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.db import get_conn, get_accounts_by_status, init_db
from src.apify_client import fetch_recent_posts, fetch_comments
from src.candidate_validator import validate_candidates, print_summary
from src.seed import reset_seed_to_candidate


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset",        action="store_true", help="seed 계정 → candidate 리셋 후 검증")
    parser.add_argument("--reset-only",   action="store_true", help="리셋만 하고 종료")
    parser.add_argument("--no-comments",  action="store_true", help="step4 댓글 스크래핑 건너뜀")
    parser.add_argument("--limit",        type=int, default=None, help="처리 계정 수 제한 (테스트용)")
    args = parser.parse_args()

    init_db()

    if args.reset or args.reset_only:
        reset_seed_to_candidate()
        if args.reset_only:
            return

    now = datetime.now(timezone.utc).isoformat()

    with get_conn() as conn:
        candidates = get_accounts_by_status(conn, "candidate")

    if not candidates:
        print("처리할 candidate 계정이 없습니다.")
        print("  seed 계정이 active 상태라면 --reset 옵션을 사용하세요.")
        return

    if args.limit:
        candidates = candidates[:args.limit]

    print(f"[검증 시작] candidate: {len(candidates)}개")
    if args.no_comments:
        print("  ※ --no-comments: step4 건너뜀 → 통과 계정은 모두 review_required")

    fetch_comments_fn = None if args.no_comments else fetch_comments

    with get_conn() as conn:
        result = validate_candidates(
            conn,
            candidates,
            now,
            fetch_posts_fn=fetch_recent_posts,
            fetch_comments_fn=fetch_comments_fn,
        )

    print_summary(result)


if __name__ == "__main__":
    main()
