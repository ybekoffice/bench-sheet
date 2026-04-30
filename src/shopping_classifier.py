"""Haiku를 이용한 쇼핑 계정 판별 + 공동구매 여부 분류."""
import json
import os
import time
from pathlib import Path

import anthropic
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / "config" / ".env")
load_dotenv(Path(__file__).parent.parent.parent.parent / ".env", override=False)

MODEL = "claude-haiku-4-5-20251001"

_PROMPT = """다음은 인스타그램 계정의 최근 게시물 캡션 {n}개다.

{captions}

이 계정이 아래 기준에 해당하는지 판단해라. JSON으로만 출력해라. 다른 말 금지.

기준:
- is_shopping: 매일 또는 매우 자주 쇼핑 상품(생활용품, 식품, 의류, 뷰티, 인테리어 등)을 소개·판매하는 계정이면 true
- is_group_buy: 공동구매(공구)를 주로 진행하는 계정이면 true

출력:
{{"is_shopping": true/false, "is_group_buy": true/false}}"""


def classify_shopping(account_name: str, captions: list[str]) -> dict:
    """최근 게시물 캡션으로 쇼핑 계정 여부 판별.

    Returns:
        is_shopping:  매일 쇼핑 상품 소개 계정이면 True
        is_group_buy: 공동구매 계정이면 True (is_shopping=True인 경우에만 의미 있음)
    """
    if not captions:
        return {"is_shopping": False, "is_group_buy": False}

    captions_text = "\n".join(
        f"캡션 {i + 1}: {(c or '(없음)')[:300]}" for i, c in enumerate(captions)
    )

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    prompt = _PROMPT.format(n=len(captions), captions=captions_text)

    for attempt in range(1, 4):
        try:
            msg = client.messages.create(
                model=MODEL,
                max_tokens=64,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = msg.content[0].text.strip()
            if not raw:
                raise ValueError("empty response")
            result = json.loads(raw)
            return {
                "is_shopping":  bool(result.get("is_shopping", False)),
                "is_group_buy": bool(result.get("is_group_buy", False)),
            }
        except Exception as e:
            print(f"    [shopping_classifier] Haiku 오류 ({account_name}) 시도 {attempt}/3: {e}")
            if attempt < 3:
                time.sleep(2 ** attempt)  # 2s, 4s

    return None
