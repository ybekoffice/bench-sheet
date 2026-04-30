"""3단계 퍼널 검증 공통 로직.

신규 발굴 계정과 기존 763 seed 계정 모두 이 퍼널을 거친다.

퍼널 순서:
  Step 2. 쇼핑 계정인가? (캡션 → Haiku)
           NO  → blacklisted (쇼핑 계정 아님)
           YES, group_buy → category='group_buy'
  Step 3. 댓글 200+ 게시물 있나? (최근 5개)
           없음 → suspended
  Step 4. CTA 키워드 패턴인가? (댓글 스크래핑 → abuse_detector)
           80%+ 동일 키워드 → active
           80% 미만         → review_required
"""
from datetime import datetime, timezone

from .abuse_detector import detect_cta_pattern
from .db import (
    get_accounts_by_status,
    get_high_comment_permalinks,
    get_recent_captions,
    get_recent_posts_per_account,
    update_account_category,
    upsert_account,
    upsert_post,
    save_snapshot,
)
from .shopping_classifier import classify_shopping

COMMENT_THRESHOLD = 200
MIN_POSTS_FOR_EVAL = 5


def _set_status(conn, account_name: str, status: str, reason: str, now: str):
    upsert_account(conn, account_name, status, reason, None, now)


def validate_candidates(
    conn,
    candidates: list[str],
    now: str,
    fetch_posts_fn=None,
    fetch_comments_fn=None,
) -> dict[str, list[str]]:
    """3단계 퍼널로 candidate 계정들을 검증한다.

    Args:
        candidates:       검증할 account_name 리스트
        now:              ISO 타임스탬프
        fetch_posts_fn:   fn(usernames) → list[dict]. DB 데이터 부족 시 Apify 호출.
                          None이면 DB 데이터만 사용 (캡션 없으면 해당 계정 skip).
        fetch_comments_fn: fn(permalinks) → dict[url, list[str]]. 어뷰징 검사용.
                           None이면 step 4 skip → 모두 review_required 처리.

    Returns:
        결과별 account_name 리스트:
        {"active": [], "blacklisted": [], "suspended": [], "review_required": [], "skipped": []}
    """
    result: dict[str, list[str]] = {
        "active": [], "blacklisted": [], "suspended": [],
        "review_required": [], "skipped": [],
    }

    total = len(candidates)
    for idx, account_name in enumerate(candidates, 1):
        print(f"  [{idx}/{total}] {account_name}", end=" → ")

        # ── Step 2: 쇼핑 계정 여부 ───────────────────────────────────────
        captions = get_recent_captions(conn, account_name, limit=3)

        if not captions and fetch_posts_fn:
            posts = fetch_posts_fn([account_name])
            for p in posts:
                upsert_post(conn, p)
                save_snapshot(conn, p["media_id"], p["like_count"], p["comment_count"], now)
            conn.commit()
            captions = get_recent_captions(conn, account_name, limit=3)

        if not captions:
            print("skip (캡션 없음)")
            result["skipped"].append(account_name)
            continue

        shopping = classify_shopping(account_name, captions)

        if shopping is None:
            print("skip (Haiku 오류 — 재시도 필요)")
            result["skipped"].append(account_name)
            continue

        if not shopping["is_shopping"]:
            print("blacklisted (쇼핑 계정 아님)")
            _set_status(conn, account_name, "blacklisted", "쇼핑 계정 아님", now)
            result["blacklisted"].append(account_name)
            conn.commit()
            continue

        if shopping["is_group_buy"]:
            update_account_category(conn, account_name, "group_buy", now)
            conn.commit()

        # ── Step 3: 성과 (댓글 200+) ─────────────────────────────────────
        posts_rows = get_recent_posts_per_account(conn, account_name, limit=MIN_POSTS_FOR_EVAL)

        if len(posts_rows) < MIN_POSTS_FOR_EVAL and fetch_posts_fn:
            extra = fetch_posts_fn([account_name])
            for p in extra:
                upsert_post(conn, p)
                save_snapshot(conn, p["media_id"], p["like_count"], p["comment_count"], now)
            conn.commit()
            posts_rows = get_recent_posts_per_account(conn, account_name, limit=MIN_POSTS_FOR_EVAL)

        has_quality = any(p["comment_count"] >= COMMENT_THRESHOLD for p in posts_rows)

        if not has_quality:
            print("suspended (댓글 200+ 없음)")
            _set_status(conn, account_name, "suspended", "최근 5개 게시물 댓글 200 미달", now)
            result["suspended"].append(account_name)
            conn.commit()
            continue

        # ── Step 4: CTA 패턴 (어뷰징 검사) ──────────────────────────────
        if fetch_comments_fn is None:
            print("review_required (댓글 스크래퍼 미설정)")
            _set_status(conn, account_name, "review_required", "댓글 패턴 수동 확인 필요", now)
            result["review_required"].append(account_name)
            conn.commit()
            continue

        permalinks = get_high_comment_permalinks(conn, account_name, min_comments=COMMENT_THRESHOLD)
        if not permalinks:
            print("review_required (permalink 없음)")
            _set_status(conn, account_name, "review_required", "댓글 패턴 수동 확인 필요", now)
            result["review_required"].append(account_name)
            conn.commit()
            continue

        comments_map = fetch_comments_fn(permalinks[:1])  # 가장 댓글 많은 게시물 1개
        all_comments: list[str] = []
        for clist in comments_map.values():
            all_comments.extend(clist)

        cta = detect_cta_pattern(all_comments)

        if cta["is_cta"]:
            print(f"active (CTA 키워드: '{cta['keyword']}', 비율: {cta['ratio']:.0%})")
            _set_status(conn, account_name, "active", None, now)
            result["active"].append(account_name)
        else:
            print(f"review_required (키워드 비율 {cta['ratio']:.0%} < 80%)")
            _set_status(conn, account_name, "review_required",
                        f"CTA 비율 {cta['ratio']:.0%} — 수동 확인 필요", now)
            result["review_required"].append(account_name)

        conn.commit()

    return result


def print_summary(result: dict[str, list[str]]):
    total = sum(len(v) for v in result.values())
    print("\n── 검증 결과 ──────────────────────────────")
    print(f"  전체:          {total}개")
    print(f"  active:        {len(result['active'])}개")
    print(f"  blacklisted:   {len(result['blacklisted'])}개  (쇼핑 계정 아님)")
    print(f"  suspended:     {len(result['suspended'])}개  (성과 미달 → 2주 점검 대상)")
    print(f"  review_required: {len(result['review_required'])}개  (수동 확인 필요)")
    print(f"  skipped:       {len(result['skipped'])}개  (데이터 부족)")
    print("───────────────────────────────────────────")
