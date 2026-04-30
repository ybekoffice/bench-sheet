"""transcript + caption → Haiku로 패턴 추출 → patterns 테이블 INSERT.

ringbob patterns.csv 컬럼과 동일 스키마 유지:
  hook_sentence, hook_style, selling_point, persuasion_arc,
  cta_type, situation_tags, hook_type, product_name, product_type, product_category
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import anthropic
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / "config" / ".env")

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.db import get_conn, get_pending_patterns

TRANSCRIPT_DIR = Path(__file__).parent.parent / "data" / "transcripts"
BATCH_LIMIT = 50
MODEL = "claude-haiku-4-5-20251001"

PROMPT = """다음은 인스타그램 쇼핑 영상의 캡션과 대본이다.
아래 항목을 분석해 JSON으로만 출력해라. 다른 말 일절 금지.

캡션:
{caption}

대본:
{transcript}

출력 형식:
{{
  "hook_sentence": "첫 문장 훅",
  "hook_style": "훅 문체 특징 한 줄",
  "selling_point": "핵심 소구점 한 줄",
  "persuasion_arc": "설득 흐름 단계별 요약",
  "cta_type": "행동 유도 유형",
  "situation_tags": "해당 상황 태그 (쉼표 구분, 최대 2개)",
  "hook_type": "극적결과|공감·일상|가성비·경제충격|타인반응|전문가신뢰|충격·반전|희소성·FOMO|불안·경각심 중 하나",
  "product_name": "제품명",
  "product_type": "제품 유형 한 단어",
  "product_category": "대분류 카테고리"
}}"""


def _extract(caption: str, transcript: str) -> dict | None:
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    try:
        msg = client.messages.create(
            model=MODEL,
            max_tokens=512,
            messages=[{"role": "user", "content": PROMPT.format(
                caption=caption or "",
                transcript=transcript,
            )}],
        )
        text = msg.content[0].text.strip()
        return json.loads(text)
    except Exception as e:
        print(f"    Haiku 오류: {e}")
        return None


def run(limit: int = BATCH_LIMIT):
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        rows = get_pending_patterns(conn, limit)
        print(f"[pattern_worker] 처리 대상: {len(rows)}건")

        for row in rows:
            media_id = row["media_id"]
            txt_path = TRANSCRIPT_DIR / f"{media_id}.txt"

            if not txt_path.exists():
                print(f"  ⚠ transcript 없음: {media_id}")
                conn.execute(
                    "UPDATE posts SET pattern_status='failed' WHERE media_id=?",
                    (media_id,)
                )
                conn.commit()
                continue

            transcript = txt_path.read_text(encoding="utf-8")
            print(f"  → {media_id} ({row['account_name']})")
            result = _extract(row["caption"] or "", transcript)

            if result is None:
                conn.execute(
                    "UPDATE posts SET pattern_status='failed' WHERE media_id=?",
                    (media_id,)
                )
            else:
                conn.execute("""
                    INSERT INTO patterns
                        (media_id, account_name, comment_count, hook_sentence, hook_style,
                         selling_point, persuasion_arc, cta_type, situation_tags,
                         hook_type, product_name, product_type, product_category, extracted_at)
                    VALUES
                        (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    media_id,
                    row["account_name"],
                    row["comment_count"],
                    result.get("hook_sentence"),
                    result.get("hook_style"),
                    result.get("selling_point"),
                    result.get("persuasion_arc"),
                    result.get("cta_type"),
                    result.get("situation_tags"),
                    result.get("hook_type"),
                    result.get("product_name"),
                    result.get("product_type"),
                    result.get("product_category"),
                    now,
                ))
                conn.execute(
                    "UPDATE posts SET pattern_status='done' WHERE media_id=?",
                    (media_id,)
                )
                print(f"    ✓ pattern 저장 (hook_type: {result.get('hook_type')})")

            conn.commit()

    print("[pattern_worker] 완료")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=BATCH_LIMIT)
    args = parser.parse_args()
    run(limit=args.limit)
