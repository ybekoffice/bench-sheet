"""통합 분석 워커: 다운로드 → Whisper → CLIP → Gemini → 유사 쌍 분류.

transcribe_worker + pattern_worker를 대체한다. 두 신호(Gemini 텍스트 + CLIP 시각)를
조합해 유사 쌍을 same_product / reposted / visual_only로 분류한다.

사용법:
  .venv/bin/python workers/analyze_worker.py                         # 기본 (댓글 300+, 오늘 게시분)
  .venv/bin/python workers/analyze_worker.py --date 2026-04-30      # 날짜 지정
  .venv/bin/python workers/analyze_worker.py --limit 5              # 5개만 (테스트)
  .venv/bin/python workers/analyze_worker.py --no-whisper           # Whisper 생략 (빠른 테스트)
  .venv/bin/python workers/analyze_worker.py --comment-min 100      # 댓글 100+ 로 확대
"""
import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import requests
from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / "config" / ".env")
load_dotenv(Path.home() / "Desktop" / "harness" / ".env")

import mlx_whisper
from src.clip_pipeline import (
    blob_to_vec, classify_tags, embed_video,
    find_similar, vec_to_blob, _load_models,
)
from src.db import (
    get_all_embeddings, get_all_product_text_embeddings,
    get_conn, get_pending_gemini, init_db,
    save_embedding, save_gemini_pattern,
    save_product_text_embedding, save_similar_pair_labeled, save_tags,
)

TRANSCRIPT_DIR = ROOT / "data" / "transcripts"
VIDEO_DIR      = ROOT / "data" / "videos"
TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
VIDEO_DIR.mkdir(parents=True, exist_ok=True)

DOWNLOAD_TIMEOUT   = 60
WORKERS            = 5
TEXT_SIM_THRESHOLD = 0.85
CLIP_SIM_THRESHOLD = 0.90

GEMINI_PROMPT = """이 인스타그램 영상을 분석해서 아래 JSON만 출력해라. 다른 말 일절 금지.

{
  "product_name": "제품명 (모르면 null)",
  "product_type": "제품 유형 (예: 튜브형 보습크림, 스틱형 파운데이션)",
  "product_category": "대분류 카테고리 (예: 스킨케어, 생활용품)",
  "product_features": "색상·형태·용량·브랜드 등 식별 특징 한 줄",
  "product_text": "제품명+유형+특징을 합쳐 임베딩용으로 정규화한 한 줄 (예: 튜브형 보습크림 흰색 100ml 펌프없음 옐로우라벨)",
  "hook_type": "극적결과|공감·일상|가성비·경제충격|타인반응|전문가신뢰|충격·반전|희소성·FOMO|불안·경각심 중 하나",
  "hook_sentence": "영상 첫 문장 또는 첫 자막",
  "hook_style": "훅 문체 특징 한 줄",
  "selling_point": "핵심 소구점 한 줄",
  "persuasion_arc": "설득 흐름 단계별 요약",
  "cta_type": "행동 유도 유형",
  "situation_tags": "해당 상황 태그 (쉼표 구분, 최대 2개)",
  "content_flow": "영상 전체 흐름 2~3문장 요약"
}"""


# ── 다운로드 ──────────────────────────────────────────────────────────────────

def _download(url: str, dest: Path) -> bool:
    try:
        with requests.get(url, stream=True, timeout=DOWNLOAD_TIMEOUT) as r:
            r.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in r.iter_content(8192):
                    f.write(chunk)
        return True
    except Exception as e:
        print(f"    다운로드 실패: {e}")
        return False


# ── Whisper ───────────────────────────────────────────────────────────────────

def _whisper(mp4_path: Path) -> str | None:
    try:
        result = mlx_whisper.transcribe(
            str(mp4_path),
            path_or_hf_repo="mlx-community/whisper-large-v3-mlx",
            language="ko",
        )
        return result["text"].strip()
    except Exception as e:
        print(f"    Whisper 실패: {e}")
        return None


# ── Gemini ────────────────────────────────────────────────────────────────────

def _gemini_analyze(mp4_path: Path) -> dict | None:
    from google import genai
    from google.genai import types

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("    GEMINI_API_KEY 없음")
        return None

    client = genai.Client(api_key=api_key)
    try:
        uploaded = client.files.upload(
            file=str(mp4_path),
            config=types.UploadFileConfig(mime_type="video/mp4"),
        )
        while uploaded.state.name == "PROCESSING":
            time.sleep(1)
            uploaded = client.files.get(name=uploaded.name)

        resp = None
        for attempt in range(3):
            try:
                resp = client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=[uploaded, GEMINI_PROMPT],
                )
                break
            except Exception as e:
                if "503" in str(e) and attempt < 2:
                    print(f"    Gemini 과부하, 10초 후 재시도 ({attempt+1}/3)...")
                    time.sleep(10)
                else:
                    raise

        try:
            client.files.delete(name=uploaded.name)
        except Exception:
            pass

        if resp is None:
            return None

        text = resp.text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return json.loads(text)

    except Exception as e:
        print(f"    Gemini 실패: {e}")
        return None


# ── 텍스트 임베딩 ─────────────────────────────────────────────────────────────

def _embed_text(text: str) -> np.ndarray | None:
    if not text:
        return None
    try:
        _, text_model = _load_models()
        vec = text_model.encode([text], convert_to_numpy=True, normalize_embeddings=True)
        return vec[0].astype(np.float32)
    except Exception as e:
        print(f"    텍스트 임베딩 실패: {e}")
        return None


# ── 유사 쌍 분류 ──────────────────────────────────────────────────────────────

def _classify_match_type(text_sim: float, clip_sim: float) -> str:
    text_high = text_sim >= TEXT_SIM_THRESHOLD
    clip_high  = clip_sim  >= CLIP_SIM_THRESHOLD
    if text_high and not clip_high:
        return "same_product"
    if text_high and clip_high:
        return "reposted"
    return "visual_only"


def _find_text_similar(new_id: str, new_vec: np.ndarray,
                       all_text_rows: list) -> list[tuple[str, float]]:
    if not all_text_rows or new_vec is None:
        return []
    ids = [r[0] for r in all_text_rows if r[0] != new_id]
    if not ids:
        return []
    mat = np.stack([blob_to_vec(r[1]) for r in all_text_rows if r[0] != new_id])
    sims = mat @ new_vec
    return [(ids[i], float(sims[i])) for i in range(len(ids)) if sims[i] >= TEXT_SIM_THRESHOLD]


# ── 영상 1개 처리 ─────────────────────────────────────────────────────────────

def _run_one(row: dict, all_clip_embeddings: list, all_text_embeddings: list,
             use_whisper: bool, now: str) -> dict:
    media_id   = row["media_id"]
    media_type = row["media_type"]
    result     = {"media_id": media_id, "ok": False}

    # CLIP이 이미 완료된 영상은 다운로드 없이 Gemini만 실행
    clip_already_done = row.get("clip_status") == "done"

    mp4_path = VIDEO_DIR / f"{media_id}.mp4"

    # VIDEO: 다운로드 필요 (CLIP 미완료이거나 Whisper 필요할 때)
    need_download = media_type == "VIDEO" and (not clip_already_done or use_whisper)
    if need_download:
        if not _download(row["video_url"], mp4_path):
            return result

    try:
        transcript = None
        clip_vec = None

        # Whisper (VIDEO만, 선택)
        if use_whisper and media_type == "VIDEO" and mp4_path.exists():
            transcript = _whisper(mp4_path)
            if transcript:
                (TRANSCRIPT_DIR / f"{media_id}.txt").write_text(transcript, encoding="utf-8")
                print(f"    ✓ Whisper ({len(transcript)}자)")

        # CLIP (미완료 시에만)
        if not clip_already_done:
            if media_type == "VIDEO" and mp4_path.exists():
                clip_vec, n_frames = embed_video(mp4_path)
            elif media_type in ("CAROUSEL_ALBUM", "IMAGE"):
                from src.clip_pipeline import embed_images_from_urls
                urls = json.loads(row.get("images_json") or "[]")
                clip_vec, n_frames = embed_images_from_urls(urls)

            if clip_vec is not None:
                with get_conn() as conn:
                    save_embedding(conn, media_id, vec_to_blob(clip_vec), n_frames, now)
                    tag_results = classify_tags(clip_vec)
                    save_tags(conn, media_id, tag_results, now)
                    conn.execute("UPDATE posts SET clip_status='done' WHERE media_id=?", (media_id,))
                top_tag = tag_results[0][0] if tag_results else "-"
                print(f"    ✓ CLIP ({n_frames}프레임, top: {top_tag})")
        else:
            print(f"    ↩ CLIP 이미 완료 (건너뜀)")

        # Gemini: VIDEO는 mp4로, 이미지는 Gemini Vision 미지원 → 스킵
        gemini_result = None
        if media_type == "VIDEO":
            # Gemini 분석엔 mp4 필요 → 없으면 다운로드
            if not mp4_path.exists():
                if not _download(row["video_url"], mp4_path):
                    return result
            gemini_result = _gemini_analyze(mp4_path)

        if gemini_result:
            with get_conn() as conn:
                save_gemini_pattern(conn, media_id, row["account_name"],
                                    row["comment_count"], gemini_result, now)
                conn.execute("UPDATE posts SET gemini_status='done' WHERE media_id=?", (media_id,))
            print(f"    ✓ Gemini (hook: {gemini_result.get('hook_type')})")

            # 제품 텍스트 임베딩
            product_text = gemini_result.get("product_text") or ""
            text_vec = _embed_text(product_text)
            if text_vec is not None:
                with get_conn() as conn:
                    save_product_text_embedding(conn, media_id, vec_to_blob(text_vec), now)

                # 유사 쌍 탐지 (텍스트 + CLIP 조합)
                text_pairs = _find_text_similar(media_id, text_vec, all_text_embeddings)
                if text_pairs and clip_vec is not None:
                    clip_pairs_map = {b_id: sim for b_id, sim in
                                      find_similar(media_id, clip_vec, all_clip_embeddings,
                                                   threshold=0.0)}
                    with get_conn() as conn:
                        for b_id, text_sim in text_pairs:
                            clip_sim = clip_pairs_map.get(b_id, 0.0)
                            match_type = _classify_match_type(text_sim, clip_sim)
                            save_similar_pair_labeled(conn, media_id, b_id,
                                                      clip_sim, text_sim, match_type, now)
                            print(f"    ↔ {b_id[:12]}… [{match_type}] text={text_sim:.3f} clip={clip_sim:.3f}")

                all_text_embeddings.append((media_id, vec_to_blob(text_vec)))
        else:
            with get_conn() as conn:
                conn.execute("UPDATE posts SET gemini_status='failed' WHERE media_id=?", (media_id,))

        if clip_vec is not None:
            all_clip_embeddings.append((media_id, vec_to_blob(clip_vec)))

        result["ok"] = True

    finally:
        mp4_path.unlink(missing_ok=True)

    return result


# ── 메인 ──────────────────────────────────────────────────────────────────────

def run(comment_min: int = 300, date_filter: str | None = None,
        limit: int = 500, use_whisper: bool = True):
    init_db()
    now = datetime.now(timezone.utc).isoformat()

    with get_conn() as conn:
        rows = get_pending_gemini(conn, comment_threshold=comment_min,
                                  date_filter=date_filter, limit=limit)
        # clip_status 정보 함께 조회
        clip_done_ids = {r[0] for r in conn.execute(
            "SELECT media_id FROM posts WHERE clip_status='done'"
        ).fetchall()}
        all_clip_embeddings = get_all_embeddings(conn)
        all_text_embeddings = get_all_product_text_embeddings(conn)

    if not rows:
        print("처리 대상 없음.")
        return

    rows = [dict(r) for r in rows]
    for row in rows:
        row["clip_status"] = "done" if row["media_id"] in clip_done_ids else "pending"

    # CLIP 모델 사전 로드 (멀티스레드 충돌 방지)
    if any(r["clip_status"] != "done" for r in rows):
        print("CLIP 모델 로딩 중...")
        _load_models()

    print(f"[analyze_worker] 대상: {len(rows)}개 | Whisper: {'ON' if use_whisper else 'OFF'}")
    print(f"  기존 CLIP 임베딩: {len(all_clip_embeddings)}개")
    print(f"  기존 텍스트 임베딩: {len(all_text_embeddings)}개\n")

    ok_count = 0
    t_start = time.time()

    # Whisper가 GPU를 독점하므로 병렬도를 줄여 충돌 방지
    max_workers = 2 if use_whisper else WORKERS

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(_run_one, row, all_clip_embeddings, all_text_embeddings,
                        use_whisper, now): row
            for row in rows
        }
        for i, future in enumerate(as_completed(futures), 1):
            row = futures[future]
            print(f"[{i}/{len(rows)}] {row['account_name']} | 댓글 {row['comment_count']:,}")
            try:
                res = future.result()
                if res["ok"]:
                    ok_count += 1
            except Exception as e:
                print(f"  오류: {e}")

    elapsed = round(time.time() - t_start)
    print(f"\n[analyze_worker] 완료: {ok_count}/{len(rows)}개 성공 | {elapsed}초")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--comment-min", type=int, default=300)
    parser.add_argument("--date", type=str, default=None, help="게시 날짜 (YYYY-MM-DD, KST 기준)")
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--no-whisper", action="store_true")
    args = parser.parse_args()

    run(
        comment_min=args.comment_min,
        date_filter=args.date,
        limit=args.limit,
        use_whisper=not args.no_whisper,
    )
