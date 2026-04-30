"""topic-finder의 insta_history_report.csv에서 계정 리스트 추출 (읽기 전용)."""
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .db import get_conn, upsert_account

CSV_PATH = Path(__file__).parent.parent.parent / "topic-finder" / "insta_history_report.csv"


def init_accounts_from_csv():
    """최초 1회: insta_history_report.csv → accounts 테이블 (source='seed_csv').

    accounts 테이블에 이미 데이터가 있으면 skip (idempotent).
    """
    with get_conn() as conn:
        count = conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
        if count > 0:
            return

        if not CSV_PATH.exists():
            raise FileNotFoundError(f"insta_history_report.csv 없음: {CSV_PATH}")

        df = pd.read_csv(CSV_PATH, usecols=["account_name"])
        accounts = df["account_name"].value_counts().index.tolist()
        now = datetime.now(timezone.utc).isoformat()

        for name in accounts:
            upsert_account(conn, name, "candidate", None, "seed_csv", now)

        conn.commit()
        print(f"[seed] accounts 테이블 초기화 완료: {len(accounts)}개 (candidate)")


def reset_seed_to_candidate():
    """기존 seed_csv active 계정을 candidate으로 리셋 (최초 검증 전 1회 실행)."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        result = conn.execute("""
            UPDATE accounts SET status = 'candidate', status_changed_at = ?,
                                status_reason = 'seed 계정 초기 검증 대상'
            WHERE source = 'seed_csv' AND status = 'active'
        """, (now,))
        conn.commit()
        print(f"[seed] seed_csv 계정 → candidate 리셋: {result.rowcount}개")


def load_active_accounts() -> list[str]:
    """accounts 테이블에서 status='active'인 계정명 목록 반환."""
    with get_conn() as conn:
        return get_conn_active_accounts(conn)


def get_conn_active_accounts(conn) -> list[str]:
    rows = conn.execute(
        "SELECT account_name FROM accounts WHERE status = 'active' ORDER BY account_name"
    ).fetchall()
    return [r[0] for r in rows]


if __name__ == "__main__":
    init_accounts_from_csv()
    accounts = load_active_accounts()
    print(f"활성 계정 수: {len(accounts)}")
    print("상위 10:", accounts[:10])
