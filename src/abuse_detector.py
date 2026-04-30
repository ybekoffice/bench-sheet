"""댓글 리스트에서 CTA 키워드 패턴 감지.

쇼핑 계정은 CTA로 특정 키워드를 댓글로 남기도록 유도한다.
→ 댓글의 80%+ 가 동일 키워드(또는 유사 변형) 포함 = 정상 CTA 패턴 (active)
→ 80% 미만 = 수동 확인 필요 (review_required)
"""
import re
from collections import Counter


def _normalize(text: str) -> str:
    """한국어·영어만 남기고 소문자 변환. 이모지·숫자·특수문자 제거."""
    text = re.sub(r'[^\w\s가-힣a-zA-Z]', ' ', text)
    return re.sub(r'\s+', ' ', text).strip().lower()


def _edit_distance(a: str, b: str) -> int:
    """Levenshtein 거리 계산. 길이 차이 3 초과면 즉시 99 반환."""
    if abs(len(a) - len(b)) > 3:
        return 99
    dp = list(range(len(b) + 1))
    for ca in a:
        new_dp = [dp[0] + 1]
        for j, cb in enumerate(b):
            new_dp.append(min(
                dp[j] + (0 if ca == cb else 1),
                new_dp[j] + 1,
                dp[j + 1] + 1,
            ))
        dp = new_dp
    return dp[-1]


def detect_cta_pattern(comments: list[str], threshold: float = 0.8) -> dict:
    """댓글 리스트에서 CTA 키워드 패턴을 감지한다.

    Args:
        comments:  댓글 텍스트 리스트
        threshold: is_cta=True 판정 기준 비율 (기본 0.8 = 80%)

    Returns:
        is_cta:   True → 80%+ 동일 키워드 (정상 CTA) → active 대상
                  False → 80% 미만 → review_required 대상
        keyword:  감지된 CTA 키워드
        ratio:    키워드 포함 댓글 비율
    """
    if not comments:
        return {"is_cta": False, "keyword": "", "ratio": 0.0}

    normalized = [_normalize(c) for c in comments]

    # 2글자 이상 단어만 집계
    all_words: list[str] = []
    for nc in normalized:
        all_words.extend(w for w in nc.split() if len(w) >= 2)

    if not all_words:
        return {"is_cta": False, "keyword": "", "ratio": 0.0}

    top_word = Counter(all_words).most_common(1)[0][0]

    # 각 댓글에 top_word와 편집거리 2 이내인 단어가 있으면 매칭
    match_count = sum(
        1 for nc in normalized
        if any(_edit_distance(w, top_word) <= 2 for w in nc.split())
    )
    ratio = match_count / len(normalized)

    return {
        "is_cta":  ratio >= threshold,
        "keyword": top_word,
        "ratio":   round(ratio, 3),
    }
