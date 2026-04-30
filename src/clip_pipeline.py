"""PySceneDetect + CLIP: 영상 → 핵심 프레임 → 임베딩 → 태그 분류.

이미지 인코더: clip-ViT-B-32 (openai/CLIP)
텍스트 인코더: clip-ViT-B-32-multilingual-v1 (한국어 지원)
두 모델은 같은 임베딩 공간 → 이미지·텍스트 직접 비교 가능.
"""
import io
from pathlib import Path

import cv2
import numpy as np
from PIL import Image
from scenedetect import ContentDetector, detect
from sentence_transformers import SentenceTransformer

# 모델은 프로세스당 한 번만 로드
_img_model: SentenceTransformer | None = None
_text_model: SentenceTransformer | None = None

TAG_FILE = Path(__file__).parent.parent / "data" / "situation_tags.txt"
SIMILARITY_THRESHOLD = 0.90  # 동일 제품 감지 기준
TOP_K_TAGS = 3


def _load_models():
    global _img_model, _text_model
    if _img_model is None:
        _img_model = SentenceTransformer("clip-ViT-B-32")
        _text_model = SentenceTransformer("sentence-transformers/clip-ViT-B-32-multilingual-v1")
    return _img_model, _text_model


def load_tags() -> list[str]:
    """situation_tags.txt에서 태그 리스트 로드. 파일 수정 시 자동 반영."""
    return [
        line.strip()
        for line in TAG_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _extract_frames(mp4_path: Path, max_frames: int = 5) -> list[Image.Image]:
    """PySceneDetect로 장면 전환 감지 → 각 장면 중간 프레임.
    장면이 max_frames 초과 시 균등 샘플링. 감지 실패 시 균등 분할.
    """
    try:
        scenes = detect(str(mp4_path), ContentDetector(threshold=27.0))
    except Exception:
        scenes = []

    cap = cv2.VideoCapture(str(mp4_path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    if not scenes:
        # 장면 전환 없음 → 균등 분할
        step = max(1, total // max_frames)
        midpoints = [step * i + step // 2 for i in range(min(max_frames, total // step))]
    else:
        if len(scenes) > max_frames:
            idxs = np.linspace(0, len(scenes) - 1, max_frames, dtype=int)
            scenes = [scenes[i] for i in idxs]
        midpoints = [
            int((s.get_frames() + e.get_frames()) / 2)
            for s, e in scenes
        ]

    frames = []
    for frame_no in midpoints:
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_no)
        ret, img = cap.read()
        if ret:
            frames.append(Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB)))
    cap.release()
    return frames


def embed_images_from_urls(
    urls: list[str],
    max_images: int = 5,
    timeout: int = 15,
) -> tuple[np.ndarray, int] | tuple[None, int]:
    """CAROUSEL/IMAGE용: URL 리스트 → 이미지 다운로드 → CLIP 임베딩.

    mp4 없이 이미지 URL만 있는 게시물에 사용.
    """
    import requests as _requests
    img_model, _ = _load_models()

    if len(urls) > max_images:
        idxs = np.linspace(0, len(urls) - 1, max_images, dtype=int)
        urls = [urls[i] for i in idxs]

    frames = []
    for url in urls:
        try:
            resp = _requests.get(url, timeout=timeout)
            resp.raise_for_status()
            frames.append(Image.open(io.BytesIO(resp.content)).convert("RGB"))
        except Exception as e:
            print(f"    이미지 다운 실패: {e}")

    if not frames:
        return None, 0

    vecs = img_model.encode(frames, convert_to_numpy=True, normalize_embeddings=True)
    video_vec = vecs.mean(axis=0)
    video_vec = (video_vec / np.linalg.norm(video_vec)).astype(np.float32)
    return video_vec, len(frames)


def embed_video(mp4_path: Path, max_frames: int = 5) -> tuple[np.ndarray, int] | tuple[None, int]:
    """영상 → (512차원 영상 벡터, 프레임 수). 실패 시 (None, 0)."""
    img_model, _ = _load_models()
    frames = _extract_frames(mp4_path, max_frames=max_frames)
    if not frames:
        return None, 0
    vecs = img_model.encode(frames, convert_to_numpy=True, normalize_embeddings=True)
    video_vec = vecs.mean(axis=0)
    video_vec = (video_vec / np.linalg.norm(video_vec)).astype(np.float32)
    return video_vec, len(frames)


def embed_tags(tags: list[str]) -> np.ndarray:
    """태그 텍스트 리스트 → (N, 512) 임베딩 행렬."""
    _, text_model = _load_models()
    return text_model.encode(tags, convert_to_numpy=True, normalize_embeddings=True).astype(np.float32)


def classify_tags(video_vec: np.ndarray, top_k: int = TOP_K_TAGS) -> list[tuple[str, float]]:
    """현재 situation_tags.txt 기준으로 영상 벡터 분류. top_k 반환."""
    tags = load_tags()
    tag_vecs = embed_tags(tags)
    sims = tag_vecs @ video_vec
    idxs = np.argsort(-sims)[:top_k]
    return [(tags[i], float(sims[i])) for i in idxs]


# --- 직렬화 유틸 ---

def vec_to_blob(vec: np.ndarray) -> bytes:
    return vec.astype(np.float32).tobytes()


def blob_to_vec(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)


# --- 유사도 배치 계산 ---

def find_similar(
    new_id: str,
    new_vec: np.ndarray,
    all_rows: list,          # [(media_id, embedding_blob), ...]
    threshold: float = SIMILARITY_THRESHOLD,
) -> list[tuple[str, float]]:
    """new_vec과 all_rows 중 threshold 이상인 쌍 반환. new_id 자신 제외."""
    if not all_rows:
        return []
    ids = [r[0] for r in all_rows if r[0] != new_id]
    if not ids:
        return []
    mat = np.stack([blob_to_vec(r[1]) for r in all_rows if r[0] != new_id])
    sims = mat @ new_vec
    return [
        (ids[i], float(sims[i]))
        for i in range(len(ids))
        if sims[i] >= threshold
    ]
