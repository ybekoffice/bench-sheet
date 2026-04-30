#!/usr/bin/env python3
"""태그 재분류: situation_tags.txt 변경 후 전체 영상을 새 태그로 재분류.

태그 추가·수정 후 이 스크립트 한 번 실행하면 video_tags 테이블이 갱신됨.
CLIP 임베딩은 그대로 유지. 재처리 필요 없음.

사용법:
  .venv/bin/python reclassify_tags.py
  .venv/bin/python reclassify_tags.py --preview   # DB 쓰지 않고 결과만 출력
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from src.clip_pipeline import classify_tags, blob_to_vec, load_tags
from src.db import get_conn, init_db, save_tags


def run(preview: bool = False):
    init_db()
    now = datetime.now(timezone.utc).isoformat()
    tags = load_tags()
    print(f"현재 태그 수: {len(tags)}개")
    print("태그 목록:", ", ".join(tags))
    print()

    with get_conn() as conn:
        rows = conn.execute(
            "SELECT media_id, embedding FROM video_embeddings"
        ).fetchall()
        print(f"재분류 대상: {len(rows)}건")

        for row in rows:
            media_id = row["media_id"]
            vec = blob_to_vec(row["embedding"])
            results = classify_tags(vec)

            if preview:
                top = results[0] if results else ("-", 0)
                print(f"  {media_id}: {top[0]} ({top[1]:.3f})")
            else:
                save_tags(conn, media_id, results, now)

        if not preview:
            conn.commit()
            print(f"✅ {len(rows)}건 재분류 완료")
        else:
            print(f"\n[preview 모드] DB에 저장하지 않음. 실제 반영하려면 --preview 없이 실행.")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--preview", action="store_true", help="DB 저장 없이 결과만 출력")
    args = parser.parse_args()
    run(preview=args.preview)
