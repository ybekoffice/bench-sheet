#!/usr/bin/env python3
"""게시물 수집 진입점.

사용법:
  .venv/bin/python collect_posts.py              # 1회 수집 (전체 763 계정)
  .venv/bin/python collect_posts.py --limit 30   # 1회 수집 (30 계정만, 테스트용)
  .venv/bin/python collect_posts.py --every 2    # 2시간마다 반복 수집
  .venv/bin/python collect_posts.py --dry-run    # Apify 호출 없이 계정 수만 확인
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from src.instagram_collector import collect
from evaluate_accounts import evaluate

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit",   type=int,   default=None, help="계정 수 제한 (테스트용)")
    parser.add_argument("--every",   type=float, default=None, help="N시간마다 반복 수집")
    parser.add_argument("--dry-run", action="store_true",      help="Apify 호출 없이 계정 수만 확인")
    args = parser.parse_args()

    if args.every:
        interval_sec = args.every * 3600
        print(f"[반복 수집] {args.every}시간마다 실행. 중단하려면 Ctrl+C")
        while True:
            collect(dry_run=args.dry_run, limit=args.limit)
            if not args.dry_run:
                evaluate()
            print(f"  다음 수집까지 {args.every}시간 대기 중...")
            time.sleep(interval_sec)
    else:
        collect(dry_run=args.dry_run, limit=args.limit)
        if not args.dry_run:
            evaluate()
