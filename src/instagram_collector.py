"""수집 메인 로직: Apify 호출 → posts.db upsert → 스냅샷 기록."""
import sys
from datetime import datetime, timezone
from pathlib import Path

from .account_manager import apply_manual_lists
from .apify_client import fetch_recent_posts
from .db import get_conn, init_db, save_snapshot, upsert_post
from .notifier import send_text
from .seed import init_accounts_from_csv, load_active_accounts

# 한 번에 Apify에 넘기는 최대 계정 수 (Actor 메모리 한계 방어)
BATCH_SIZE = 50
POSTS_PER_ACCOUNT = 6


def _keep_latest(posts: list[dict]) -> list[dict]:
    """계정별 최신 POSTS_PER_ACCOUNT개만 남긴다.

    Apify가 resultsLimit을 계정당 정확히 적용하지 않을 수 있어 클라이언트에서 재필터링.
    """
    from collections import defaultdict
    buckets: dict[str, list[dict]] = defaultdict(list)
    for p in posts:
        buckets[p["account_name"]].append(p)
    result = []
    for account_posts in buckets.values():
        account_posts.sort(key=lambda p: p["media_timestamp"], reverse=True)
        result.extend(account_posts[:POSTS_PER_ACCOUNT])
    return result


def collect(dry_run: bool = False, limit: int | None = None):
    """신규 게시물을 수집해 posts.db에 저장한다.

    dry_run=True이면 Apify를 호출하지 않고 계정 목록만 출력.
    limit: 대상 계정 수 제한 (PoC용).
    """
    init_db()
    apply_manual_lists()
    init_accounts_from_csv()
    now = datetime.now(timezone.utc).isoformat()
    accounts = load_active_accounts()
    if limit:
        accounts = accounts[:limit]

    print(f"[수집 시작] 대상 계정: {len(accounts)}개")
    send_text(f"🚀 수집 시작 — {len(accounts)}개 계정")

    if dry_run:
        print("[dry_run] Apify 호출 없이 종료. 계정 목록 상위 10:", accounts[:10])
        return

    new_count = updated_count = 0
    batches = [accounts[i:i + BATCH_SIZE] for i in range(0, len(accounts), BATCH_SIZE)]

    with get_conn() as conn:
        for i, batch in enumerate(batches, 1):
            print(f"  배치 {i}/{len(batches)}: {len(batch)}개 계정 조회 중...")
            posts = _keep_latest(fetch_recent_posts(batch))

            existing_ids = set(
                row[0] for row in conn.execute(
                    f"SELECT media_id FROM posts WHERE media_id IN ({','.join('?'*len(posts))})",
                    [p["media_id"] for p in posts]
                ).fetchall()
            ) if posts else set()

            for p in posts:
                upsert_post(conn, p)
                save_snapshot(conn, p["media_id"], p["like_count"], p["comment_count"], now)
                if p["media_id"] in existing_ids:
                    updated_count += 1
                else:
                    new_count += 1

            pct = round(i / len(batches) * 100)
            print(f"  배치 {i}/{len(batches)} 완료 ({pct}%)")
            if pct in (25, 50, 75):
                send_text(f"📦 수집 {pct}% 완료 ({i}/{len(batches)} 배치)")

        conn.commit()

    print(f"[수집 완료] 신규: {new_count}건, 지표갱신: {updated_count}건")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=None, help="계정 수 제한 (PoC용)")
    args = parser.parse_args()
    collect(dry_run=args.dry_run, limit=args.limit)
