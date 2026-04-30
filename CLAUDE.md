# CLAUDE.md (insta-collector)

Instagram 신규 게시물 자동 수집 + 계정 생애주기 관리 파이프라인

> 전역 규칙: ~/.claude/CLAUDE.md
> 작업 공간 규칙: harness/CLAUDE.md

## 실행 방법

```bash
# 가상환경 생성 (최초 1회)
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt

# ── 최초 설정 (1회) ──────────────────────────────────────────
# seed 계정을 candidate으로 리셋 후 퍼널 검증
.venv/bin/python validate_candidates.py --reset --no-comments
# (--no-comments: 댓글 스크래핑 건너뜀 → 통과 계정은 review_required로 분류)
# 댓글 스크래핑까지 포함한 전체 검증:
.venv/bin/python validate_candidates.py --reset

# ── 게시물 수집 ──────────────────────────────────────────────
.venv/bin/python collect_posts.py              # active 계정 전체 1회 수집
.venv/bin/python collect_posts.py --limit 5    # 테스트용 5 계정
.venv/bin/python collect_posts.py --dry-run    # Apify 호출 없이 계정 수만 확인
.venv/bin/python collect_posts.py --every 2    # 2시간마다 반복 (수집 후 자동 평가)

# ── 계정 관리 ────────────────────────────────────────────────
.venv/bin/python evaluate_accounts.py          # active 계정 성과 평가 (수집 후 자동 실행)
.venv/bin/python evaluate_accounts.py --dry-run

.venv/bin/python review_suspended.py           # suspended/review_required 2주 점검
.venv/bin/python review_suspended.py --dry-run

.venv/bin/python discover_accounts.py          # 신규 계정 발굴 (주 1회 권장)
.venv/bin/python discover_accounts.py --limit 5 --no-comments  # 테스트

# ── 데이터 관리 ──────────────────────────────────────────────
.venv/bin/python update_followers.py           # 팔로워 수 갱신 (cron: 매일 자정 KST)
.venv/bin/python export_excel.py               # data/ 폴더에 날짜_시각.xlsx 생성
.venv/bin/python run_workers.py --limit 5      # 영상 분석 (수동 운영)
```

## API 키 설정

`config/.env` 파일에 아래 항목 입력 (config/.env.example 참고):
- `APIFY_TOKEN` — Apify 콘솔 > Settings > Integrations > API token
- `ANTHROPIC_API_KEY` — Haiku 쇼핑 분류 + 패턴 추출용

## 수동 계정 관리

```
data/accounts_add.txt    # 추가할 계정명 (한 줄에 하나, # 주석 지원)
data/accounts_block.txt  # 차단할 계정명 (한 줄에 하나, # 주석 지원)
```
`collect_posts.py` 실행 시 자동 반영됨.

## 프로젝트 목표

`insta_history_report.csv`의 계정에서 출발해 신규 게시물을 수집,
메타데이터(좋아요·댓글·팔로워·링크) 추적 → 고성과 게시물 패턴 분석.
의미없는 계정은 자동 보류·차단하고, 신규 쇼핑 계정은 자동 발굴해 리스트를 동적으로 유지.

## 격리 원칙

- `ringbob-script-mvp/`, `topic-finder/` 두 프로젝트의 파일을 절대 수정하지 않는다
- `topic-finder/insta_history_report.csv`는 계정 리스트 추출 목적으로 **읽기만** 가능
- 모든 신규 데이터(posts.db, transcripts, videos)는 이 프로젝트 안에만 저장

## 계정 생애주기

```
candidate ──[퍼널 통과]──> active ──[댓글 200+ 미달]──> suspended
    │                                                        │
    │  Step 2. 쇼핑계정 아님  ──> blacklisted               └─[2주 점검 통과]──> active
    │  Step 3. 댓글 200+ 없음 ──> suspended
    │  Step 4. CTA 80%+ 확인  ──> active
    │          CTA 80% 미만   ──> review_required (수동 확인 필요)
    │
    └─ 공동구매 계정 → category='group_buy' 태그

수동: accounts_add.txt → active / accounts_block.txt → blacklisted
```

**상태 설명**

| 상태 | 의미 |
|---|---|
| `active` | 매 수집 대상 |
| `candidate` | 퍼널 검증 대기 중 |
| `suspended` | 성과 미달 보류 (2주마다 점검) |
| `review_required` | CTA 패턴 불명확 — 수동 확인 필요 |
| `blacklisted` | 영구 제외 (쇼핑 계정 아님 또는 수동 차단) |

## 파일 역할

| 파일 | 역할 | 상태 |
|---|---|---|
| `collect_posts.py` | 수집 진입점. Apify 호출 → posts.db upsert + 평가 자동 실행 | 구현 완료 |
| `evaluate_accounts.py` | active 계정 성과 평가 → 미달 시 suspended (수집 후 자동 실행) | 구현 완료 |
| `validate_candidates.py` | candidate 계정 3단계 퍼널 검증 (최초 763개 + 신규 후보) | 구현 완료 |
| `review_suspended.py` | suspended/review_required 2주 점검 → 통과 시 active 복귀 | 구현 완료 |
| `discover_accounts.py` | 활성 계정 신규 팔로잉 추적 → 후보 발굴 + 퍼널 검증 | 구현 완료 |
| `update_followers.py` | 전체 계정 팔로워 수 갱신 (Apify 프로필 스크래퍼, 하루 1회) | 구현 완료 |
| `export_excel.py` | posts.db → Excel (72h 이내 + 댓글 300개 이상, 하이퍼링크 포함) | 구현 완료 |
| `run_workers.py` | 워커 진입점. 영상처리 + 패턴 추출 (현재 수동 운영) | 구현 완료 |
| `reclassify_tags.py` | situation_tags.txt 변경 후 전체 재분류 | 구현 완료 |
| `find_similar.py` | 전체 임베딩 대상 동일 제품 감지 | 구현 완료 |
| `src/db.py` | SQLite 스키마 + 공통 쿼리 | 구현 완료 |
| `src/seed.py` | CSV → accounts 초기화 / active 계정 목록 반환 | 구현 완료 |
| `src/account_manager.py` | 수동 파일(accounts_add/block.txt) → accounts 테이블 반영 | 구현 완료 |
| `src/apify_client.py` | Apify Actor 호출 + 결과 파싱 (게시물/팔로잉/댓글) | 구현 완료 |
| `src/instagram_collector.py` | 수집 메인 로직 + 계정당 최신 6개 필터 | 구현 완료 |
| `src/shopping_classifier.py` | Haiku로 쇼핑/공동구매 계정 판별 | 구현 완료 |
| `src/abuse_detector.py` | 댓글 CTA 키워드 패턴 감지 (80%+ 기준) | 구현 완료 |
| `src/candidate_validator.py` | 3단계 퍼널 공통 로직 | 구현 완료 |
| `src/clip_pipeline.py` | PySceneDetect + CLIP 임베딩 + 태그 분류 | 구현 완료 |
| `workers/transcribe_worker.py` | mp4 수명 관리: 다운→Whisper→CLIP→삭제 | 구현 완료 |
| `workers/pattern_worker.py` | transcript → Haiku → patterns 테이블 | 구현 완료 |
| `data/situation_tags.txt` | 태그 설정 파일 (자유롭게 수정 가능) | 구현 완료 |
| `data/accounts_add.txt` | 수동 추가 계정 목록 | 구현 완료 |
| `data/accounts_block.txt` | 수동 차단 계정 목록 | 구현 완료 |

## 수집 설계 원칙

**계정당 최신 6개 고정**
- Apify `resultsLimit: 6` + 72시간 필터(`onlyPostsNewerThan`) 적용
- Apify가 6개 제한을 정확히 보장하지 않아 클라이언트에서 `_keep_latest()`로 재필터링
- 72시간 이전 게시물은 수집 안 함 (비용 절감 ~66%)

**변화율 추적**
- 수집 때마다 `post_snapshots` 테이블에 (media_id, like_count, comment_count, collected_at) 기록
- 2회 이상 수집 후 `get_change_rates(conn, media_id)`로 변화율 조회 가능

**팔로워 수 보호**
- 포스트 스크래퍼는 팔로워 수를 반환하지 않음(항상 0) → upsert 시 기존 값 보존
- 팔로워 수는 `update_followers.py`(프로필 스크래퍼)로만 갱신

## 데이터 위치

```
data/
├── posts.db              # 게시물 메타 + 스냅샷 + 처리 상태 (SQLite)
├── accounts_add.txt      # 수동 추가 계정 목록
├── accounts_block.txt    # 수동 차단 계정 목록
├── videos/               # mp4 임시 (transcribe 후 즉시 삭제)
├── transcripts/          # 텍스트 영구 보관
└── insta_posts_*.xlsx    # 엑셀 내보내기 결과
```

## DB 주요 테이블

| 테이블 | 내용 |
|---|---|
| `accounts` | 계정 생애주기. status / category / source / 변경 이력 |
| `account_followings` | 활성 계정의 팔로잉 스냅샷 (신규 발굴 비교 기준) |
| `posts` | 게시물 메타 + 처리 상태. 계정당 최신 6개 기준 |
| `post_snapshots` | 수집 시마다 좋아요·댓글 스냅샷 (변화율 계산용) |
| `video_embeddings` | CLIP 512차원 임베딩 |
| `video_tags` | 상황 태그 분류 결과 |
| `similar_pairs` | 동일 제품 감지 쌍 (유사도 ≥ 0.90) |
| `patterns` | Haiku 패턴 추출 결과 |

## cron 등록 현황

```
# 현재 등록된 cron (crontab -l 로 확인)
0 15 * * *  update_followers.py     # 매일 한국시간 00:00 (UTC 15:00)

# 아래는 수동 등록 필요 (2주마다, 주 1회)
# 0 15 * * 1,15  review_suspended.py   # 매달 1일·15일 점검
# 0 16 * * 1     discover_accounts.py  # 매주 월요일 발굴
```

## 엑셀 출력 기준

- **게시물 전체 시트**: 72시간 이내 + 댓글 300개 이상, 게시일시 내림차순
- **계정별 요약 시트**: 위 조건에 해당하는 계정, 총댓글 많은 순
- 팔로워수: 계정별 MAX값으로 매칭 (포스트 스크래퍼 0값 무시)
- 게시물링크: 하이퍼링크 적용

## 주의사항

- `APIFY_TOKEN` 없으면 `collect_posts.py` 즉시 오류 종료
- 수집 비용: active 계정 수 × 회당 약 $0.005. 763계정 기준 약 $3~4
- `validate_candidates.py` 전체 실행 시 Haiku 비용 + Apify 댓글 스크래퍼 비용 발생
- `discover_accounts.py` 팔로잉 스크래핑: 활성 계정 전체 기준 약 $4/회
- 팔로워 수는 `update_followers.py` 실행 후에만 채워짐 (한국시간 매일 자정 자동 실행)
- mlx-whisper 최초 실행 시 모델 다운로드 (~3GB). 시간 소요됨
- Instagram CDN video_url은 수 시간 내 만료 → `run_workers.py`는 수집 직후 빠르게 돌릴 것
- `fetch_followings()`의 Apify 액터 파라미터는 콘솔에서 확인 후 조정 필요할 수 있음
