#!/usr/bin/env python3
"""백로그 워커 진입점: 다운로드+트랜스크립션 → 패턴 추출 순차 실행.

사용법:
  .venv/bin/python run_workers.py            # 기본 (각 워커 50건 처리)
  .venv/bin/python run_workers.py --limit 5  # PoC용 소량 처리
  .venv/bin/python run_workers.py --skip-patterns  # 트랜스크립션만
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--skip-patterns", action="store_true")
    args = parser.parse_args()

    from workers.transcribe_worker import run as transcribe
    transcribe(limit=args.limit)

    if not args.skip_patterns:
        from workers.pattern_worker import run as pattern
        pattern(limit=args.limit)
