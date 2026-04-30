"""미디어 처리 워커: 타입별 분기 → CLIP 공통 → mp4/이미지 즉시 삭제.

VIDEO:          다운로드 → Whisper → PySceneDetect+CLIP → 삭제
CAROUSEL_ALBUM: 이미지 URL → CLIP (mp4 없음, Whisper 없음)
IMAGE:          이미지 URL → CLIP (mp4 없음, Whisper 없음)

각 단계는 독립적으로 실패할 수 있다.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import mlx_whisper
import requests

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.clip_pipeline import (
    classify_tags, embed_images_from_urls, embed_video,
    find_similar, vec_to_blob,
)
from src.db import (
    get_all_embeddings, get_conn, get_pending_downloads,
    init_db, save_embedding, save_tags,
)

MODEL = "mlx-community/whisper-large-v3-mlx"
LANGUAGE = "ko"
BATCH_LIMIT = 50
DOWNLOAD_TIMEOUT = 60

TRANSCRIPT_DIR = Path(__file__).parent.parent / "data" / "transcripts"
VIDEO_DIR      = Path(__file__).parent.parent / "data" / "videos"
TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
VIDEO_DIR.mkdir(parents=True, exist_ok=True)


def _get_pending_all(conn, limit: int):
    """VIDEO + CAROUSEL_ALBUM + IMAGE 모두 가져온다."""
    return conn.execute("""
        SELECT media_id, media_type, video_url, images_json, account_name
        FROM posts
        WHERE clip_status = 'pending'
          AND (
            (media_type = 'VIDEO' AND download_status = 'pending' AND video_url IS NOT NULL)
            OR
            (media_type IN ('CAROUSEL_ALBUM', 'IMAGE') AND images_json IS NOT NULL)
          )
        ORDER BY media_timestamp DESC
        LIMIT ?
    """, (limit,)).fetchall()


def _download_mp4(url: str, dest: Path) -> bool:
    try:
        with requests.get(url, stream=True, timeout=DOWNLOAD_TIMEOUT) as r:
            r.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
        return True
    except Exception as e:
        print(f"    mp4 다운 실패: {e}")
        return False


def _whisper(mp4_path: Path) -> str | None:
    try:
        result = mlx_whisper.transcribe(
            str(mp4_path),
            path_or_hf_repo=MODEL,
            language=LANGUAGE,
        )
        return result["text"].strip()
    except Exception as e:
        print(f"    Whisper 실패: {e}")
        return None


def _run_clip(conn, media_id: str, vec, n: int, all_embeddings: list, now: str):
    """임베딩 저장 + 태그 분류 + 유사 쌍 감지. 공통 처리."""
    if vec is None:
        conn.execute("UPDATE posts SET clip_status='failed' WHERE media_id=?", (media_id,))
        return

    save_embedding(conn, media_id, vec_to_blob(vec), n, now)
    tag_results = classify_tags(vec)
    save_tags(conn, media_id, tag_results, now)
    conn.execute("UPDATE posts SET clip_status='done' WHERE media_id=?", (media_id,))
    top_tag = tag_results[0][0] if tag_results else "-"
    print(f"    ✓ CLIP ({n}장, top: {top_tag})")

    pairs = find_similar(media_id, vec, all_embeddings)
    if pairs:
        conn.executemany("""
            INSERT OR IGNORE INTO similar_pairs (media_id_a, media_id_b, similarity, detected_at)
            VALUES (?, ?, ?, ?)
        """, [(media_id, b_id, sim, now) for b_id, sim in pairs])
        print(f"    ✓ 유사 영상 {len(pairs)}건")

    all_embeddings.append((media_id, vec_to_blob(vec)))


def run(limit: int = BATCH_LIMIT):
    init_db()
    now = datetime.now(timezone.utc).isoformat()

    with get_conn() as conn:
        rows = _get_pending_all(conn, limit)
        print(f"[media_worker] 처리 대상: {len(rows)}건")
        all_embeddings = get_all_embeddings(conn)

        for row in rows:
            media_id   = row["media_id"]
            media_type = row["media_type"]
            print(f"  → {media_id} ({row['account_name']}) [{media_type}]")

            # ── VIDEO ──────────────────────────────────────
            if media_type == "VIDEO":
                mp4_path = VIDEO_DIR / f"{media_id}.mp4"

                if not _download_mp4(row["video_url"], mp4_path):
                    conn.execute(
                        "UPDATE posts SET download_status='failed' WHERE media_id=?",
                        (media_id,)
                    )
                    conn.commit()
                    continue

                conn.execute(
                    "UPDATE posts SET download_status='done' WHERE media_id=?",
                    (media_id,)
                )

                # Whisper
                text = _whisper(mp4_path)
                if text:
                    (TRANSCRIPT_DIR / f"{media_id}.txt").write_text(text, encoding="utf-8")
                    conn.execute(
                        "UPDATE posts SET transcript_status='done' WHERE media_id=?",
                        (media_id,)
                    )
                    print(f"    ✓ transcript ({len(text)}자)")
                else:
                    conn.execute(
                        "UPDATE posts SET transcript_status='failed' WHERE media_id=?",
                        (media_id,)
                    )

                # CLIP
                try:
                    vec, n = embed_video(mp4_path)
                    _run_clip(conn, media_id, vec, n, all_embeddings, now)
                except Exception as e:
                    print(f"    CLIP 실패: {e}")
                    conn.execute(
                        "UPDATE posts SET clip_status='failed' WHERE media_id=?",
                        (media_id,)
                    )
                finally:
                    mp4_path.unlink(missing_ok=True)

            # ── CAROUSEL_ALBUM / IMAGE ──────────────────────
            else:
                urls = json.loads(row["images_json"] or "[]")
                if not urls:
                    conn.execute(
                        "UPDATE posts SET clip_status='failed' WHERE media_id=?",
                        (media_id,)
                    )
                    conn.commit()
                    continue

                try:
                    vec, n = embed_images_from_urls(urls)
                    _run_clip(conn, media_id, vec, n, all_embeddings, now)
                except Exception as e:
                    print(f"    CLIP 실패: {e}")
                    conn.execute(
                        "UPDATE posts SET clip_status='failed' WHERE media_id=?",
                        (media_id,)
                    )

            conn.commit()

    print("[media_worker] 완료")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=BATCH_LIMIT)
    args = parser.parse_args()
    run(limit=args.limit)
