"""수동 계정 관리: accounts_add.txt, accounts_block.txt → accounts 테이블 반영."""
from datetime import datetime, timezone
from pathlib import Path

from .db import get_conn, upsert_account

ADD_FILE   = Path(__file__).parent.parent / "data" / "accounts_add.txt"
BLOCK_FILE = Path(__file__).parent.parent / "data" / "accounts_block.txt"


def _read_names(path: Path) -> list[str]:
    """텍스트 파일에서 계정명 읽기. # 주석과 빈 줄 제외."""
    if not path.exists():
        return []
    names = []
    for line in path.read_text(encoding="utf-8").splitlines():
        name = line.strip()
        if name and not name.startswith("#"):
            names.append(name)
    return names


def apply_manual_lists():
    """수동 파일을 읽어 accounts 테이블에 반영한다.

    - accounts_add.txt   → status='active' (source='manual')
    - accounts_block.txt → status='blacklisted' (source='manual')
    파일은 수정하지 않는다. 이미 같은 상태인 계정은 ON CONFLICT로 자연 처리.
    """
    now = datetime.now(timezone.utc).isoformat()
    add_names   = _read_names(ADD_FILE)
    block_names = _read_names(BLOCK_FILE)

    if not add_names and not block_names:
        return

    with get_conn() as conn:
        for name in add_names:
            upsert_account(conn, name, "active", "수동 추가", "manual", now)
        for name in block_names:
            upsert_account(conn, name, "blacklisted", "수동 차단", "manual", now)
        conn.commit()

    if add_names:
        print(f"[account_manager] 수동 추가: {len(add_names)}개")
    if block_names:
        print(f"[account_manager] 수동 차단: {len(block_names)}개")
