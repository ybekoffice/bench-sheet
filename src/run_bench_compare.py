"""CLIP vs Gemini 벤치마크.

오늘 수집된 영상 중 댓글 많은 순 N개를 두 방식으로 분석해 결과를 비교 출력한다.

사용법:
  .venv/bin/python src/run_bench_compare.py           # 기본 15개
  .venv/bin/python src/run_bench_compare.py --n 5    # 5개만
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# .env 우선순위: 프로젝트 config/.env → harness 루트 .env
load_dotenv(ROOT / "config" / ".env")
load_dotenv(Path.home() / "Desktop" / "harness" / ".env")

from src.clip_pipeline import classify_tags, embed_video, vec_to_blob
from src.db import get_all_embeddings, get_conn, init_db, save_embedding, save_tags

VIDEO_DIR = ROOT / "data" / "videos"
VIDEO_DIR.mkdir(parents=True, exist_ok=True)
DOWNLOAD_TIMEOUT = 60


# ── 공통: mp4 다운로드 ────────────────────────────────────────────────────────

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


# ── CLIP 분석 ─────────────────────────────────────────────────────────────────

def run_clip(mp4_path: Path) -> dict:
    t0 = time.time()
    vec, n = embed_video(mp4_path, max_frames=5)
    if vec is None:
        return {"ok": False, "elapsed": time.time() - t0}
    tags = classify_tags(vec)
    return {
        "ok": True,
        "elapsed": round(time.time() - t0, 1),
        "frame_count": n,
        "tags": [(tag, round(sim, 3)) for tag, sim in tags],
    }


# ── Gemini 분석 ───────────────────────────────────────────────────────────────

GEMINI_PROMPT = """이 인스타그램 영상을 분석해서 아래 JSON만 출력해라. 다른 말 일절 금지.

{
  "product_name": "제품명 (모르면 null)",
  "product_type": "제품 유형 (예: 튜브형 보습크림, 스틱형 파운데이션)",
  "product_features": "색상·형태·용량·브랜드 등 식별 특징 한 줄",
  "hook_type": "극적결과|공감·일상|가성비·경제충격|타인반응|전문가신뢰|충격·반전|희소성·FOMO|불안·경각심 중 하나",
  "hook_sentence": "영상 첫 문장 또는 첫 자막",
  "content_flow": "전체 흐름 2~3문장 요약",
  "selling_point": "핵심 소구점 한 줄"
}"""


def run_gemini(mp4_path: Path) -> dict:
    from google import genai
    from google.genai import types

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return {"ok": False, "error": "GEMINI_API_KEY 없음", "elapsed": 0}

    client = genai.Client(api_key=api_key)
    t0 = time.time()

    try:
        # 파일 업로드
        t_upload = time.time()
        uploaded = client.files.upload(
            file=str(mp4_path),
            config=types.UploadFileConfig(mime_type="video/mp4"),
        )

        # 처리 완료 대기
        while uploaded.state.name == "PROCESSING":
            time.sleep(1)
            uploaded = client.files.get(name=uploaded.name)

        upload_elapsed = round(time.time() - t_upload, 1)

        # 분석 (503 과부하 시 최대 3회 재시도)
        t_infer = time.time()
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
                    print(f"    Gemini 과부하, {10}초 후 재시도 ({attempt+1}/3)...")
                    time.sleep(10)
                else:
                    raise
        infer_elapsed = round(time.time() - t_infer, 1)

        text = resp.text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]

        data = json.loads(text)
        data["ok"] = True
        data["elapsed"] = round(time.time() - t0, 1)
        data["upload_elapsed"] = upload_elapsed
        data["infer_elapsed"] = infer_elapsed

        # 업로드한 파일 삭제 (Gemini Files API 용량 관리)
        try:
            client.files.delete(name=uploaded.name)
        except Exception:
            pass

        return data

    except Exception as e:
        return {"ok": False, "error": str(e), "elapsed": round(time.time() - t0, 1)}


# ── 출력 ──────────────────────────────────────────────────────────────────────

def _print_result(i: int, row: dict, clip: dict, gemini: dict):
    sep = "─" * 70
    print(f"\n{sep}")
    print(f"[{i}] {row['account_name']}  |  댓글 {row['comment_count']:,}  |  {row['permalink']}")
    print(sep)

    # CLIP
    if clip["ok"]:
        tags_str = "  ".join(f"{t}({s})" for t, s in clip["tags"])
        print(f"  CLIP ({clip['elapsed']}초, {clip['frame_count']}프레임)")
        print(f"    태그: {tags_str}")
    else:
        print(f"  CLIP 실패 ({clip['elapsed']}초)")

    print()

    # Gemini
    if gemini["ok"]:
        print(f"  Gemini ({gemini['elapsed']}초 = 업로드 {gemini.get('upload_elapsed')}초 + 분석 {gemini.get('infer_elapsed')}초)")
        print(f"    제품: {gemini.get('product_name')} / {gemini.get('product_type')}")
        print(f"    특징: {gemini.get('product_features')}")
        print(f"    훅타입: {gemini.get('hook_type')}")
        print(f"    첫문장: {gemini.get('hook_sentence')}")
        print(f"    흐름: {gemini.get('content_flow')}")
        print(f"    소구점: {gemini.get('selling_point')}")
    else:
        print(f"  Gemini 실패 ({gemini['elapsed']}초): {gemini.get('error', '')}")


# ── 메인 ──────────────────────────────────────────────────────────────────────

def run(n: int = 15):
    init_db()

    with get_conn() as conn:
        rows = conn.execute("""
            SELECT media_id, account_name, comment_count, video_url, permalink
            FROM posts
            WHERE media_type = 'VIDEO'
              AND video_url IS NOT NULL
              AND clip_status = 'pending'
              AND first_seen_at >= datetime('now', '-24 hours')
            ORDER BY comment_count DESC
            LIMIT ?
        """, (n,)).fetchall()

    if not rows:
        print("오늘 수집된 처리 가능한 VIDEO가 없습니다.")
        return

    print(f"벤치마크 대상: {len(rows)}개 영상\n")

    clip_times, gemini_times = [], []
    clip_ok, gemini_ok = 0, 0

    for i, row in enumerate(rows, 1):
        media_id = row["media_id"]
        mp4_path = VIDEO_DIR / f"{media_id}.mp4"

        print(f"[{i}/{len(rows)}] 다운로드 중... {row['account_name']}")
        if not _download(row["video_url"], mp4_path):
            print("  → 건너뜀 (URL 만료)\n")
            continue

        clip   = run_clip(mp4_path)
        gemini = run_gemini(mp4_path)

        mp4_path.unlink(missing_ok=True)

        _print_result(i, row, clip, gemini)

        if clip["ok"]:
            clip_times.append(clip["elapsed"])
            clip_ok += 1
        if gemini["ok"]:
            gemini_times.append(gemini["elapsed"])
            gemini_ok += 1

    # 요약
    print(f"\n{'=' * 70}")
    print("요약")
    print(f"  CLIP   성공 {clip_ok}/{len(rows)}  평균 {round(sum(clip_times)/len(clip_times), 1) if clip_times else '-'}초")
    print(f"  Gemini 성공 {gemini_ok}/{len(rows)}  평균 {round(sum(gemini_times)/len(gemini_times), 1) if gemini_times else '-'}초")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=15)
    args = parser.parse_args()
    run(n=args.n)
