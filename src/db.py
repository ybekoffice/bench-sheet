"""SQLite 스키마 + 공통 쿼리."""
import os
import sqlite3
from pathlib import Path

_default_db = Path(__file__).parent.parent / "data" / "posts.db"
DB_PATH = Path(os.environ["POSTS_DB"]) if "POSTS_DB" in os.environ else _default_db


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _migrate(conn: sqlite3.Connection):
    """기존 DB에 새 컬럼/테이블이 없으면 추가."""
    existing_posts = {r[1] for r in conn.execute("PRAGMA table_info(posts)").fetchall()}
    if "clip_status" not in existing_posts:
        conn.execute("ALTER TABLE posts ADD COLUMN clip_status TEXT DEFAULT 'pending'")
    if "images_json" not in existing_posts:
        conn.execute("ALTER TABLE posts ADD COLUMN images_json TEXT")
    if "follower_count" not in existing_posts:
        conn.execute("ALTER TABLE posts ADD COLUMN follower_count INTEGER")
    if "gemini_status" not in existing_posts:
        conn.execute("ALTER TABLE posts ADD COLUMN gemini_status TEXT DEFAULT 'pending'")

    existing_patterns = {r[1] for r in conn.execute("PRAGMA table_info(patterns)").fetchall()}
    if existing_patterns:
        if "product_features" not in existing_patterns:
            conn.execute("ALTER TABLE patterns ADD COLUMN product_features TEXT")
        if "product_text" not in existing_patterns:
            conn.execute("ALTER TABLE patterns ADD COLUMN product_text TEXT")
        if "content_flow" not in existing_patterns:
            conn.execute("ALTER TABLE patterns ADD COLUMN content_flow TEXT")

    existing_pairs = {r[1] for r in conn.execute("PRAGMA table_info(similar_pairs)").fetchall()}
    if existing_pairs:
        if "match_type" not in existing_pairs:
            conn.execute("ALTER TABLE similar_pairs ADD COLUMN match_type TEXT")
        if "text_similarity" not in existing_pairs:
            conn.execute("ALTER TABLE similar_pairs ADD COLUMN text_similarity REAL")

    existing_accounts = {r[1] for r in conn.execute("PRAGMA table_info(accounts)").fetchall()}
    if existing_accounts and "category" not in existing_accounts:
        conn.execute("ALTER TABLE accounts ADD COLUMN category TEXT")
    if existing_accounts and "follower_count" not in existing_accounts:
        conn.execute("ALTER TABLE accounts ADD COLUMN follower_count INTEGER DEFAULT 0")
    if existing_accounts and "follower_updated_at" not in existing_accounts:
        conn.execute("ALTER TABLE accounts ADD COLUMN follower_updated_at TEXT")
    # posts 테이블의 기존 팔로워 수를 accounts로 복사 (컬럼 신규 추가 직후 1회)
    conn.execute("""
        UPDATE accounts SET follower_count = (
            SELECT MAX(follower_count) FROM posts WHERE posts.account_name = accounts.account_name
        )
        WHERE follower_count IS NULL OR follower_count = 0
    """)

    conn.commit()


def init_db():
    with get_conn() as conn:
        _migrate(conn)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS posts (
                media_id              TEXT PRIMARY KEY,
                account_name          TEXT NOT NULL,
                caption               TEXT,
                like_count            INTEGER,
                comment_count         INTEGER,
                follower_count        INTEGER,
                media_timestamp       TEXT,
                permalink             TEXT,
                media_type            TEXT,
                video_url             TEXT,
                images_json           TEXT,
                first_seen_at         TEXT NOT NULL,
                last_metric_update_at TEXT,
                download_status       TEXT DEFAULT 'pending',
                transcript_status     TEXT DEFAULT 'pending',
                clip_status           TEXT DEFAULT 'pending',
                pattern_status        TEXT DEFAULT 'pending'
            );
            CREATE INDEX IF NOT EXISTS idx_posts_ts       ON posts(media_timestamp);
            CREATE INDEX IF NOT EXISTS idx_posts_dl       ON posts(download_status);
            CREATE INDEX IF NOT EXISTS idx_posts_tr       ON posts(transcript_status);
            CREATE INDEX IF NOT EXISTS idx_posts_cl       ON posts(clip_status);
            CREATE INDEX IF NOT EXISTS idx_posts_type     ON posts(media_type);

            -- CLIP 영상 임베딩 (512차원 float32 벡터)
            CREATE TABLE IF NOT EXISTS video_embeddings (
                media_id    TEXT PRIMARY KEY REFERENCES posts(media_id),
                embedding   BLOB NOT NULL,
                frame_count INTEGER,
                created_at  TEXT NOT NULL
            );

            -- 태그 분류 결과 (재분류 시 삭제 후 재삽입)
            CREATE TABLE IF NOT EXISTS video_tags (
                media_id      TEXT NOT NULL REFERENCES posts(media_id),
                tag           TEXT NOT NULL,
                similarity    REAL NOT NULL,
                rank          INTEGER NOT NULL,
                classified_at TEXT NOT NULL,
                PRIMARY KEY (media_id, tag)
            );
            CREATE INDEX IF NOT EXISTS idx_vtags_tag ON video_tags(tag);

            -- 동일 제품 감지 쌍 (similarity > threshold)
            CREATE TABLE IF NOT EXISTS similar_pairs (
                media_id_a  TEXT NOT NULL,
                media_id_b  TEXT NOT NULL,
                similarity  REAL NOT NULL,
                detected_at TEXT NOT NULL,
                PRIMARY KEY (media_id_a, media_id_b)
            );
            CREATE INDEX IF NOT EXISTS idx_simpairs_a ON similar_pairs(media_id_a);
            CREATE INDEX IF NOT EXISTS idx_simpairs_b ON similar_pairs(media_id_b);

            CREATE TABLE IF NOT EXISTS patterns (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                media_id         TEXT NOT NULL REFERENCES posts(media_id),
                account_name     TEXT,
                comment_count    INTEGER,
                hook_sentence    TEXT,
                hook_style       TEXT,
                selling_point    TEXT,
                persuasion_arc   TEXT,
                cta_type         TEXT,
                situation_tags   TEXT,
                hook_type        TEXT,
                product_name     TEXT,
                product_type     TEXT,
                product_category TEXT,
                extracted_at     TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_patterns_mid  ON patterns(media_id);
            CREATE INDEX IF NOT EXISTS idx_patterns_hook ON patterns(hook_type);

            -- 제품 설명 텍스트 임베딩 (same_product 유사도 비교용)
            CREATE TABLE IF NOT EXISTS product_text_embeddings (
                media_id   TEXT PRIMARY KEY REFERENCES posts(media_id),
                embedding  BLOB NOT NULL,
                created_at TEXT NOT NULL
            );

            -- 제품 텍스트 유사 쌍 (Gemini product_text 기반)
            CREATE TABLE IF NOT EXISTS text_similar_pairs (
                media_a     TEXT NOT NULL,
                media_b     TEXT NOT NULL,
                text_sim    REAL NOT NULL,
                detected_at TEXT NOT NULL,
                PRIMARY KEY (media_a, media_b)
            );
            CREATE INDEX IF NOT EXISTS idx_tpairs_a ON text_similar_pairs(media_a);
            CREATE INDEX IF NOT EXISTS idx_tpairs_b ON text_similar_pairs(media_b);

            -- 시각 유사 쌍 (CLIP 임베딩 기반)
            CREATE TABLE IF NOT EXISTS visual_similar_pairs (
                media_a     TEXT NOT NULL,
                media_b     TEXT NOT NULL,
                clip_sim    REAL NOT NULL,
                detected_at TEXT NOT NULL,
                PRIMARY KEY (media_a, media_b)
            );
            CREATE INDEX IF NOT EXISTS idx_vpairs_a ON visual_similar_pairs(media_a);
            CREATE INDEX IF NOT EXISTS idx_vpairs_b ON visual_similar_pairs(media_b);

            -- 수집 때마다 좋아요·댓글 스냅샷 (변화율 계산용)
            CREATE TABLE IF NOT EXISTS post_snapshots (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                media_id      TEXT NOT NULL REFERENCES posts(media_id),
                like_count    INTEGER,
                comment_count INTEGER,
                collected_at  TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_snapshots_mid ON post_snapshots(media_id, collected_at);

            -- 계정 생애주기 관리
            CREATE TABLE IF NOT EXISTS accounts (
                account_name      TEXT PRIMARY KEY,
                status            TEXT NOT NULL DEFAULT 'active',
                added_at          TEXT NOT NULL,
                status_changed_at TEXT,
                status_reason     TEXT,
                source            TEXT,
                last_reviewed_at  TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_accounts_status ON accounts(status);

            -- 신규 계정 발굴용 팔로잉 스냅샷
            CREATE TABLE IF NOT EXISTS account_followings (
                account_name    TEXT NOT NULL,
                following_name  TEXT NOT NULL,
                first_seen_at   TEXT NOT NULL,
                PRIMARY KEY (account_name, following_name)
            );
            CREATE INDEX IF NOT EXISTS idx_following_name ON account_followings(following_name);
        """)


def upsert_post(conn: sqlite3.Connection, row: dict):
    """신규면 INSERT, 기존이면 지표(like/comment/follower)만 갱신."""
    conn.execute("""
        INSERT INTO posts
            (media_id, account_name, caption, like_count, comment_count, follower_count,
             media_timestamp, permalink, media_type, video_url, images_json,
             first_seen_at, last_metric_update_at)
        VALUES
            (:media_id, :account_name, :caption, :like_count, :comment_count, :follower_count,
             :media_timestamp, :permalink, :media_type, :video_url, :images_json,
             :now, :now)
        ON CONFLICT(media_id) DO UPDATE SET
            like_count            = excluded.like_count,
            comment_count         = excluded.comment_count,
            follower_count        = CASE WHEN excluded.follower_count > 0 THEN excluded.follower_count ELSE follower_count END,
            last_metric_update_at = excluded.last_metric_update_at
    """, {**row, "now": row["first_seen_at"]})


def get_pending_downloads(conn: sqlite3.Connection, limit: int = 50) -> list[sqlite3.Row]:
    return conn.execute("""
        SELECT media_id, video_url, account_name
        FROM posts
        WHERE media_type = 'VIDEO'
          AND download_status = 'pending'
          AND video_url IS NOT NULL
        ORDER BY media_timestamp DESC
        LIMIT ?
    """, (limit,)).fetchall()


def get_pending_transcripts(conn: sqlite3.Connection, limit: int = 50) -> list[sqlite3.Row]:
    return conn.execute("""
        SELECT media_id, account_name
        FROM posts
        WHERE transcript_status = 'pending'
          AND download_status = 'done'
        ORDER BY media_timestamp DESC
        LIMIT ?
    """, (limit,)).fetchall()


def get_pending_patterns(conn: sqlite3.Connection, limit: int = 50) -> list[sqlite3.Row]:
    return conn.execute("""
        SELECT p.media_id, p.account_name, p.comment_count, p.caption
        FROM posts p
        WHERE p.transcript_status = 'done'
          AND p.pattern_status = 'pending'
        ORDER BY p.media_timestamp DESC
        LIMIT ?
    """, (limit,)).fetchall()


def get_pending_clips(conn: sqlite3.Connection, limit: int = 50) -> list[sqlite3.Row]:
    return conn.execute("""
        SELECT media_id, video_url, account_name
        FROM posts
        WHERE media_type = 'VIDEO'
          AND download_status = 'pending'
          AND video_url IS NOT NULL
        ORDER BY media_timestamp DESC
        LIMIT ?
    """, (limit,)).fetchall()


def get_all_embeddings(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """유사도 계산용: 전체 영상 임베딩 반환."""
    return conn.execute(
        "SELECT media_id, embedding FROM video_embeddings"
    ).fetchall()


def save_embedding(conn: sqlite3.Connection, media_id: str, vec: bytes, frame_count: int, now: str):
    conn.execute("""
        INSERT OR REPLACE INTO video_embeddings (media_id, embedding, frame_count, created_at)
        VALUES (?, ?, ?, ?)
    """, (media_id, vec, frame_count, now))


def save_snapshot(conn: sqlite3.Connection, media_id: str, like_count: int, comment_count: int, now: str):
    """수집 시점의 좋아요·댓글 수를 스냅샷으로 기록."""
    conn.execute("""
        INSERT INTO post_snapshots (media_id, like_count, comment_count, collected_at)
        VALUES (?, ?, ?, ?)
    """, (media_id, like_count, comment_count, now))


def get_change_rates(conn: sqlite3.Connection, media_id: str) -> dict | None:
    """최근 2개 스냅샷 기준 좋아요·댓글 변화율 반환. 스냅샷이 2개 미만이면 None."""
    rows = conn.execute("""
        SELECT like_count, comment_count, collected_at
        FROM post_snapshots
        WHERE media_id = ?
        ORDER BY collected_at DESC
        LIMIT 2
    """, (media_id,)).fetchall()

    if len(rows) < 2:
        return None

    latest, prev = rows[0], rows[1]
    like_delta    = latest["like_count"]    - prev["like_count"]
    comment_delta = latest["comment_count"] - prev["comment_count"]

    return {
        "like_delta":    like_delta,
        "comment_delta": comment_delta,
        "prev_at":       prev["collected_at"],
        "latest_at":     latest["collected_at"],
    }


def get_all_account_names(conn: sqlite3.Connection) -> list[str]:
    """posts 테이블에 있는 고유 계정명 목록 반환."""
    rows = conn.execute("SELECT DISTINCT account_name FROM posts WHERE account_name != ''").fetchall()
    return [r[0] for r in rows]


def get_accounts_needing_followers(conn: sqlite3.Connection) -> list[str]:
    """팔로워 수가 0이거나 없는 accounts 테이블 계정 전체 반환."""
    rows = conn.execute("""
        SELECT account_name FROM accounts
        WHERE follower_count IS NULL OR follower_count = 0
        ORDER BY account_name
    """).fetchall()
    return [r[0] for r in rows]


def update_follower_counts(conn: sqlite3.Connection, counts: dict[str, int], now: str):
    """계정별 팔로워 수를 accounts 테이블과 posts 테이블에 일괄 업데이트."""
    conn.executemany("""
        UPDATE accounts SET follower_count = ?, follower_updated_at = ?
        WHERE account_name = ?
    """, [(count, now, account) for account, count in counts.items()])
    conn.executemany("""
        UPDATE posts SET follower_count = ?, last_metric_update_at = ?
        WHERE account_name = ?
    """, [(count, now, account) for account, count in counts.items()])


def save_tags(conn: sqlite3.Connection, media_id: str, results: list[tuple[str, float]], now: str):
    conn.execute("DELETE FROM video_tags WHERE media_id = ?", (media_id,))
    conn.executemany("""
        INSERT INTO video_tags (media_id, tag, similarity, rank, classified_at)
        VALUES (?, ?, ?, ?, ?)
    """, [(media_id, tag, sim, rank + 1, now) for rank, (tag, sim) in enumerate(results)])


# ── 계정 생애주기 ────────────────────────────────────────────────────────────

def upsert_account(conn: sqlite3.Connection, account_name: str, status: str,
                   reason: str | None, source: str, now: str):
    """accounts 테이블에 계정을 INSERT하거나 상태를 갱신한다.

    신규면 INSERT. 기존이면 status·reason·status_changed_at만 업데이트.
    """
    conn.execute("""
        INSERT INTO accounts (account_name, status, added_at, status_changed_at, status_reason, source)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(account_name) DO UPDATE SET
            status            = excluded.status,
            status_reason     = excluded.status_reason,
            status_changed_at = excluded.status_changed_at
    """, (account_name, status, now, now, reason, source))


def get_accounts_by_status(conn: sqlite3.Connection, status: str) -> list[str]:
    """지정 status인 계정명 목록 반환."""
    rows = conn.execute(
        "SELECT account_name FROM accounts WHERE status = ? ORDER BY account_name",
        (status,)
    ).fetchall()
    return [r[0] for r in rows]


def get_recent_posts_per_account(conn: sqlite3.Connection,
                                  account_name: str, limit: int = 5) -> list[sqlite3.Row]:
    """계정의 최근 게시물을 최신순으로 반환."""
    return conn.execute("""
        SELECT media_id, comment_count, media_timestamp
        FROM posts
        WHERE account_name = ?
        ORDER BY media_timestamp DESC
        LIMIT ?
    """, (account_name, limit)).fetchall()


def upsert_following(conn: sqlite3.Connection, account_name: str,
                     following_name: str, now: str):
    """팔로잉 스냅샷 저장. 이미 있으면 무시."""
    conn.execute("""
        INSERT OR IGNORE INTO account_followings (account_name, following_name, first_seen_at)
        VALUES (?, ?, ?)
    """, (account_name, following_name, now))


def get_followings_snapshot(conn: sqlite3.Connection, account_name: str) -> set[str]:
    """저장된 팔로잉 목록 반환 (신규 팔로잉 탐지용 비교 기준)."""
    rows = conn.execute(
        "SELECT following_name FROM account_followings WHERE account_name = ?",
        (account_name,)
    ).fetchall()
    return {r[0] for r in rows}


def get_recent_captions(conn: sqlite3.Connection,
                         account_name: str, limit: int = 3) -> list[str]:
    """계정의 최근 게시물 캡션 반환 (쇼핑 분류용)."""
    rows = conn.execute("""
        SELECT caption FROM posts
        WHERE account_name = ? AND caption IS NOT NULL AND caption != ''
        ORDER BY media_timestamp DESC
        LIMIT ?
    """, (account_name, limit)).fetchall()
    return [r[0] for r in rows]


def get_high_comment_permalinks(conn: sqlite3.Connection,
                                 account_name: str,
                                 min_comments: int = 200,
                                 limit: int = 3) -> list[str]:
    """댓글 min_comments 이상인 게시물의 permalink 반환 (어뷰징 검사용)."""
    rows = conn.execute("""
        SELECT permalink FROM posts
        WHERE account_name = ? AND comment_count >= ? AND permalink IS NOT NULL
        ORDER BY comment_count DESC
        LIMIT ?
    """, (account_name, min_comments, limit)).fetchall()
    return [r[0] for r in rows]


def update_account_category(conn: sqlite3.Connection,
                             account_name: str, category: str | None, now: str):
    """계정의 category 컬럼만 갱신."""
    conn.execute(
        "UPDATE accounts SET category = ?, status_changed_at = ? WHERE account_name = ?",
        (category, now, account_name)
    )


# ── Gemini 분석 워커용 ────────────────────────────────────────────────────────

def get_pending_gemini(conn: sqlite3.Connection,
                       comment_threshold: int = 300,
                       date_filter: str | None = None,
                       limit: int = 200) -> list[sqlite3.Row]:
    """Gemini 분석 대기 중인 영상 목록 반환.

    date_filter: 'YYYY-MM-DD' 형식. 해당 날짜(KST)에 게시된 것만.
    """
    if date_filter:
        return conn.execute("""
            SELECT media_id, media_type, video_url, images_json,
                   account_name, comment_count, permalink
            FROM posts
            WHERE gemini_status = 'pending'
              AND comment_count >= ?
              AND (
                    (media_type = 'VIDEO' AND video_url IS NOT NULL)
                    OR
                    (media_type IN ('CAROUSEL_ALBUM', 'IMAGE') AND images_json IS NOT NULL)
                  )
              AND date(media_timestamp, '+9 hours') = ?
            ORDER BY comment_count DESC
            LIMIT ?
        """, (comment_threshold, date_filter, limit)).fetchall()
    return conn.execute("""
        SELECT media_id, media_type, video_url, images_json,
               account_name, comment_count, permalink
        FROM posts
        WHERE gemini_status = 'pending'
          AND comment_count >= ?
          AND (
                (media_type = 'VIDEO' AND video_url IS NOT NULL)
                OR
                (media_type IN ('CAROUSEL_ALBUM', 'IMAGE') AND images_json IS NOT NULL)
              )
        ORDER BY comment_count DESC
        LIMIT ?
    """, (comment_threshold, limit)).fetchall()


def save_gemini_pattern(conn: sqlite3.Connection, media_id: str,
                        account_name: str, comment_count: int,
                        result: dict, now: str):
    """Gemini 분석 결과를 patterns 테이블에 저장."""
    conn.execute("""
        INSERT OR REPLACE INTO patterns
            (media_id, account_name, comment_count,
             hook_sentence, hook_style, selling_point, persuasion_arc,
             cta_type, situation_tags, hook_type,
             product_name, product_type, product_category,
             product_features, product_text, content_flow,
             extracted_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        media_id, account_name, comment_count,
        result.get("hook_sentence"), result.get("hook_style"),
        result.get("selling_point"), result.get("persuasion_arc"),
        result.get("cta_type"), result.get("situation_tags"),
        result.get("hook_type"), result.get("product_name"),
        result.get("product_type"), result.get("product_category"),
        result.get("product_features"), result.get("product_text"),
        result.get("content_flow"), now,
    ))


def save_product_text_embedding(conn: sqlite3.Connection,
                                media_id: str, embedding: bytes, now: str):
    """제품 설명 텍스트 임베딩 저장."""
    conn.execute("""
        INSERT OR REPLACE INTO product_text_embeddings (media_id, embedding, created_at)
        VALUES (?, ?, ?)
    """, (media_id, embedding, now))


def get_all_product_text_embeddings(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """유사도 비교용: 전체 제품 텍스트 임베딩 반환."""
    return conn.execute(
        "SELECT media_id, embedding FROM product_text_embeddings"
    ).fetchall()


def save_text_pair(conn: sqlite3.Connection,
                   media_a: str, media_b: str, text_sim: float, now: str):
    """제품 텍스트 유사 쌍 저장."""
    a, b = (media_a, media_b) if media_a < media_b else (media_b, media_a)
    conn.execute("""
        INSERT OR REPLACE INTO text_similar_pairs (media_a, media_b, text_sim, detected_at)
        VALUES (?, ?, ?, ?)
    """, (a, b, text_sim, now))


def save_visual_pair(conn: sqlite3.Connection,
                     media_a: str, media_b: str, clip_sim: float, now: str):
    """시각 유사 쌍 저장."""
    a, b = (media_a, media_b) if media_a < media_b else (media_b, media_a)
    conn.execute("""
        INSERT OR REPLACE INTO visual_similar_pairs (media_a, media_b, clip_sim, detected_at)
        VALUES (?, ?, ?, ?)
    """, (a, b, clip_sim, now))


def get_classified_pairs(conn: sqlite3.Connection,
                         text_threshold: float = 0.75,
                         clip_threshold: float = 0.90) -> list[sqlite3.Row]:
    """텍스트·시각 두 신호를 조합해 match_type 라벨을 붙여 반환.

    match_type:
      reposted     — text HIGH + clip HIGH (같은 영상 도용)
      same_product — text HIGH + clip LOW  (같은 제품, 다른 영상)
    """
    return conn.execute("""
        SELECT
            t.media_a, t.media_b,
            t.text_sim,
            COALESCE(v.clip_sim, 0.0) AS clip_sim,
            CASE
                WHEN t.text_sim >= ? AND COALESCE(v.clip_sim, 0.0) >= ? THEN 'reposted'
                ELSE 'same_product'
            END AS match_type,
            t.detected_at
        FROM text_similar_pairs t
        LEFT JOIN visual_similar_pairs v
            ON (t.media_a = v.media_a AND t.media_b = v.media_b)
        WHERE t.text_sim >= ?
        ORDER BY t.text_sim DESC
    """, (text_threshold, clip_threshold, text_threshold)).fetchall()


def save_similar_pair_labeled(conn: sqlite3.Connection,
                               media_id_a: str, media_id_b: str,
                               clip_sim: float, text_sim: float,
                               match_type: str, now: str):
    """match_type 라벨과 함께 유사 쌍 저장."""
    conn.execute("""
        INSERT OR REPLACE INTO similar_pairs
            (media_id_a, media_id_b, similarity, text_similarity, match_type, detected_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (media_id_a, media_id_b, clip_sim, text_sim, match_type, now))
