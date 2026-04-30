#!/usr/bin/env python3
"""동일 제품 감지: 전체 영상 임베딩 쌍에서 유사도 높은 것 찾기.

transcribe_worker가 신규 영상 처리 시 실시간으로도 감지하지만,
이 스크립트는 전체 DB를 대상으로 한 번에 재계산한다.

사용법:
  .venv/bin/python find_similar.py                    # 기본 threshold=0.90
  .venv/bin/python find_similar.py --threshold 0.85   # 더 느슨하게
  .venv/bin/python find_similar.py --show             # 결과 출력 (DB 저장 안 함)
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from src.clip_pipeline import blob_to_vec
from src.db import get_conn, init_db


def run(threshold: float = 0.90, show_only: bool = False):
    init_db()
    now = datetime.now(timezone.utc).isoformat()

    with get_conn() as conn:
        rows = conn.execute(
            "SELECT media_id, embedding FROM video_embeddings"
        ).fetchall()

        if len(rows) < 2:
            print(f"임베딩 수 부족 ({len(rows)}개). 최소 2개 필요.")
            return

        print(f"임베딩 {len(rows)}개 로드 → 유사도 계산 중...")
        ids = [r["media_id"] for r in rows]
        mat = np.stack([blob_to_vec(r["embedding"]) for r in rows])

        # 전체 코사인 유사도 행렬 (대각선 제외)
        sim_mat = mat @ mat.T
        np.fill_diagonal(sim_mat, 0)

        pairs = []
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                if sim_mat[i, j] >= threshold:
                    pairs.append((ids[i], ids[j], float(sim_mat[i, j])))

        print(f"threshold={threshold} 이상 쌍: {len(pairs)}건")

        if show_only:
            for a, b, s in sorted(pairs, key=lambda x: -x[2])[:20]:
                print(f"  {s:.4f}  {a}  ↔  {b}")
            return

        if pairs:
            conn.executemany("""
                INSERT OR REPLACE INTO similar_pairs
                    (media_id_a, media_id_b, similarity, detected_at)
                VALUES (?, ?, ?, ?)
            """, [(a, b, s, now) for a, b, s in pairs])
            conn.commit()
            print(f"✅ similar_pairs 테이블에 {len(pairs)}건 저장")

            # 상위 10쌍 미리보기
            print("\n유사도 상위 10쌍:")
            for a, b, s in sorted(pairs, key=lambda x: -x[2])[:10]:
                print(f"  {s:.4f}  {a}  ↔  {b}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--threshold", type=float, default=0.90)
    parser.add_argument("--show", action="store_true", help="DB 저장 없이 결과만 출력")
    args = parser.parse_args()
    run(threshold=args.threshold, show_only=args.show)
