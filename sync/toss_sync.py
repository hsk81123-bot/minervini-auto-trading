# -*- coding: utf-8 -*-
"""토스증권 체결내역 → Firestore 매매기록 동기화 (로컬 실행 전용).

토스 Open API는 허용 IP 등록제라서 클라우드가 아닌 본인 PC에서 실행해야 한다.
(WTS → 설정 → Open API → 허용 IP 관리에 본인 공인 IP 등록 필요)

사용법:
    python sync/toss_sync.py --dry-run   # 쓰지 않고 가져올 내역만 출력 (첫 실행 권장)
    python sync/toss_sync.py             # Firestore에 동기화

설정: sync/config.local.json (sync/config.example.json 참고, git에 커밋되지 않음)
- 이미 동기화된 체결(문서 존재)은 건너뛰므로, 앱에서 보완 입력한
  손절가/셋업 유형이 다시 덮어써지지 않는다.
"""
import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

BASE = "https://openapi.tossinvest.com"
KST = timezone(timedelta(hours=9))
HERE = Path(__file__).resolve().parent


def load_config() -> dict:
    cfg_path = HERE / "config.local.json"
    if not cfg_path.exists():
        sys.exit("sync/config.local.json 이 없습니다. "
                 "sync/config.example.json 을 복사해 값을 채워주세요.")
    return json.load(open(cfg_path, encoding="utf-8"))


def get_token(cfg: dict) -> str:
    r = requests.post(f"{BASE}/oauth2/token", data={
        "grant_type": "client_credentials",
        "client_id": cfg["toss_client_id"],
        "client_secret": cfg["toss_client_secret"],
    }, timeout=30)
    if r.status_code == 403:
        sys.exit("403: 허용 IP 미등록일 가능성이 큽니다. "
                 "WTS → 설정 → Open API → 허용 IP 관리에서 현재 IP를 등록하세요.")
    r.raise_for_status()
    return r.json()["access_token"]


def get_account_seq(token: str, cfg: dict) -> str:
    if cfg.get("account_seq"):
        return str(cfg["account_seq"])
    r = requests.get(f"{BASE}/api/v1/accounts",
                     headers={"Authorization": f"Bearer {token}"}, timeout=30)
    r.raise_for_status()
    data = r.json()
    accounts = data.get("accounts") or data.get("data") or data
    if isinstance(accounts, dict):
        accounts = [accounts]
    if not accounts:
        sys.exit("계좌를 찾을 수 없습니다")
    seq = accounts[0].get("accountSeq") or accounts[0].get("account_seq")
    print(f"계좌 accountSeq: {seq} (다른 계좌를 쓰려면 config에 account_seq 지정)")
    return str(seq)


def fetch_closed_orders(token: str, account_seq: str, days_back: int) -> list[dict]:
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Tossinvest-Account": account_seq,
    }
    params = {
        "status": "CLOSED",
        "from": (datetime.now(KST) - timedelta(days=days_back)).strftime("%Y-%m-%d"),
        "to": datetime.now(KST).strftime("%Y-%m-%d"),
        "limit": 100,
    }
    orders: list[dict] = []
    while True:
        r = requests.get(f"{BASE}/api/v1/orders", headers=headers,
                         params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        orders.extend(data.get("orders", []))
        if not data.get("hasNext") or not data.get("nextCursor"):
            break
        params["cursor"] = data["nextCursor"]
    return orders


def normalize_symbol(symbol: str) -> str:
    """토스 종목코드 → 앱 표기. 한국 'A005930' → '005930', 미국 'AAPL' 그대로."""
    s = symbol.strip().upper()
    if len(s) == 7 and s[0] == "A" and s[1:].isdigit():
        return s[1:]
    return s


def to_trade(o: dict) -> dict | None:
    ex = o.get("execution") or {}
    try:
        qty = float(ex.get("filledQuantity") or 0)
    except (TypeError, ValueError):
        qty = 0
    if qty <= 0:
        return None  # 체결 없이 취소된 주문
    price = ex.get("averageFilledPrice") or o.get("price")
    filled_at = ex.get("filledAt") or o.get("orderedAt")
    try:
        dt = datetime.fromisoformat(filled_at.replace("Z", "+00:00")).astimezone(KST)
        date = dt.strftime("%Y-%m-%d")
    except Exception:
        date = str(filled_at)[:10]
    return {
        "id": f"toss-{o['orderId']}",
        "date": date,
        "ticker": normalize_symbol(o["symbol"]),
        "side": o["side"],  # BUY | SELL
        "shares": qty,
        "price": float(price),
        "stop": None,
        "target": None,
        "setup": "기타",       # 앱에서 보완 입력
        "exitReason": None,   # 앱에서 보완 입력
        "notes": "토스 자동연동",
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="Firestore에 쓰지 않고 출력만")
    ap.add_argument("--days", type=int, default=None,
                    help="며칠 전까지 조회 (기본: config days_back 또는 30)")
    args = ap.parse_args()

    cfg = load_config()
    days_back = args.days or cfg.get("days_back", 30)

    print(f"[1/3] 토스 인증 및 체결내역 조회 (최근 {days_back}일)...")
    token = get_token(cfg)
    account_seq = get_account_seq(token, cfg)
    orders = fetch_closed_orders(token, account_seq, days_back)
    trades = [t for t in (to_trade(o) for o in orders) if t]
    print(f"  종결 주문 {len(orders)}건 중 체결 {len(trades)}건")
    for t in trades:
        print(f"  {t['date']} {t['side']:<4} {t['ticker']:<8} "
              f"{t['shares']}주 @ {t['price']}")

    if args.dry_run:
        print("\n[dry-run] Firestore에 쓰지 않았습니다.")
        return

    print("[2/3] Firestore 연결...")
    import firebase_admin
    from firebase_admin import credentials, firestore
    sa_path = HERE / cfg.get("service_account_path", "serviceAccount.json")
    if not sa_path.exists():
        sys.exit(f"서비스 계정 키가 없습니다: {sa_path}\n"
                 "Firebase 콘솔 → 프로젝트 설정 → 서비스 계정 → 새 비공개 키 생성")
    firebase_admin.initialize_app(credentials.Certificate(str(sa_path)))
    db = firestore.client()
    col = (db.collection("users").document(cfg["firebase_uid"])
             .collection("trades"))

    print("[3/3] 동기화 (기존 문서는 건너뜀 — 수동 보완 입력 보존)...")
    added = skipped = 0
    for t in trades:
        ref = col.document(t["id"])
        if ref.get().exists:
            skipped += 1
            continue
        ref.set(t)
        added += 1
    print(f"완료: 추가 {added}건, 기존 {skipped}건 건너뜀")
    if added:
        print("※ 앱 매매기록에서 새 매수 건의 손절가·셋업 유형을 보완 입력하세요 (R 통계에 필요)")


if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    main()
