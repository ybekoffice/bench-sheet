"""Apify Instagram Scraper Actor 래퍼."""
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from apify_client import ApifyClient
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / "config" / ".env")

ACTOR_ID = "apify/instagram-scraper"
RESULTS_PER_ACCOUNT = 6
FILTER_HOURS = 72


def _client() -> ApifyClient:
    token = os.environ.get("APIFY_TOKEN")
    if not token:
        raise EnvironmentError("APIFY_TOKEN 환경변수가 없습니다. config/.env를 확인하세요.")
    return ApifyClient(token)


def fetch_recent_posts(usernames: list[str], hours_filter: int | None = FILTER_HOURS) -> list[dict]:
    """계정 리스트의 최신 게시물을 Apify로 가져온다.

    hours_filter=None이면 시간 제한 없이 수집 (review_suspended 등에서 사용).
    반환 형태: posts.db upsert에 바로 쓸 수 있는 dict 리스트.
    """
    client = _client()
    direct_urls = [f"https://www.instagram.com/{u}/" for u in usernames]

    run_input: dict = {
        "directUrls": direct_urls,
        "resultsType": "posts",
        "resultsLimit": RESULTS_PER_ACCOUNT,
    }
    if hours_filter is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_filter)
        run_input["onlyPostsNewerThan"] = cutoff.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    run = client.actor(ACTOR_ID).call(run_input=run_input)

    items = client.dataset(run["defaultDatasetId"]).list_items().items
    now = datetime.now(timezone.utc).isoformat()

    posts = []
    for item in items:
        media_type = _normalize_type(item.get("type", ""))
        posts.append({
            "media_id":        str(item.get("id") or item.get("shortCode", "")),
            "account_name":    item.get("ownerUsername", ""),
            "caption":         item.get("caption", ""),
            "like_count":      item.get("likesCount") or 0,
            "comment_count":   item.get("commentsCount") or 0,
            "follower_count":  item.get("ownerFollowersCount") or item.get("followersCount") or 0,
            "media_timestamp": item.get("timestamp", ""),
            "permalink":       item.get("url", ""),
            "media_type":      media_type,
            "video_url":       item.get("videoUrl") if media_type == "VIDEO" else None,
            "images_json":     _extract_images_json(item, media_type),
            "first_seen_at":   now,
        })
    return posts


def _extract_images_json(item: dict, media_type: str) -> str | None:
    """CAROUSEL_ALBUM/IMAGE 게시물의 이미지 URL 목록을 JSON 문자열로 반환."""
    import json
    if media_type == "VIDEO":
        return None
    urls = item.get("images") or []
    if not urls:
        display = item.get("displayUrl")
        if display:
            urls = [display]
    return json.dumps(urls) if urls else None


def fetch_follower_counts(usernames: list[str], batch_size: int = 100) -> dict[str, int]:
    """계정 리스트의 팔로워 수를 Apify 프로필 스크래퍼로 가져온다.

    반환 형태: {account_name: follower_count}
    배치 단위(100개)로 나눠 호출해 Apify 메모리 한도 초과 방지.
    """
    client = _client()
    result = {}

    for i in range(0, len(usernames), batch_size):
        batch = usernames[i:i + batch_size]
        run = client.actor("apify/instagram-profile-scraper").call(run_input={
            "usernames": batch,
        })
        items = client.dataset(run["defaultDatasetId"]).list_items().items
        for item in items:
            username = item.get("username") or item.get("ownerUsername", "")
            followers = item.get("followersCount") or item.get("followersCount") or 0
            if username:
                result[username] = followers
        print(f"  프로필 배치 {i // batch_size + 1}: {len(items)}개 완료")

    return result


def fetch_comments(permalinks: list[str], limit_per_post: int = 100) -> dict[str, list[str]]:
    """게시물 URL 리스트의 댓글 텍스트를 Apify로 가져온다.

    반환 형태: {permalink: [comment_text, ...]}
    """
    if not permalinks:
        return {}
    client = _client()
    run = client.actor("apify/instagram-comment-scraper").call(run_input={
        "directUrls": permalinks,
        "resultsLimit": limit_per_post,
    })
    items = client.dataset(run["defaultDatasetId"]).list_items().items
    result: dict[str, list[str]] = {}
    for item in items:
        url   = item.get("url") or item.get("postUrl", "")
        text  = item.get("text") or item.get("commentText", "")
        if url and text:
            result.setdefault(url, []).append(text)
    return result


def fetch_followings(usernames: list[str], batch_size: int = 50) -> dict[str, list[str]]:
    """활성 계정의 팔로잉 목록을 Apify로 가져온다.

    반환 형태: {account_name: [following_name, ...]}
    주의: Apify 'apify/instagram-profile-scraper' 액터의 팔로잉 지원 여부를
          Apify 콘솔에서 확인 필요. 지원하지 않으면 별도 actor로 교체.
    """
    if not usernames:
        return {}
    client = _client()
    result: dict[str, list[str]] = {}

    for i in range(0, len(usernames), batch_size):
        batch = usernames[i:i + batch_size]
        run = client.actor("apify/instagram-profile-scraper").call(run_input={
            "usernames": batch,
            "resultsType": "following",
        })
        items = client.dataset(run["defaultDatasetId"]).list_items().items
        for item in items:
            owner = item.get("username") or item.get("ownerUsername", "")
            following = item.get("followingUsername") or item.get("username", "")
            if owner and following and owner != following:
                result.setdefault(owner, []).append(following)
        print(f"  팔로잉 배치 {i // batch_size + 1}: {len(items)}개 완료")

    return result


def _normalize_type(raw: str) -> str:
    raw = raw.upper()
    if raw in ("VIDEO", "REEL"):
        return "VIDEO"
    if raw in ("SIDECAR", "CAROUSEL_ALBUM"):
        return "CAROUSEL_ALBUM"
    return "IMAGE"
