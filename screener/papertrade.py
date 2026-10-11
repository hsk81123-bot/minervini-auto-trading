# -*- coding: utf-8 -*-
"""모의매매 — 과거 추천 상위 N을 동일 금액으로 사고 손절·익절 규칙을 적용한다.

입력: git 이력의 docs/data/results[_kr].json (backtest.py의 스냅샷 로직 재사용)
출력: 종목별 체결 내역 + 총 수익률

설계 근거:
- 진입 종목은 '그 날 커밋된 스냅샷'의 상위 N이다. 지금 리스트에서 거꾸로
  고르면 이미 오른 종목을 뽑는 셈이 된다 (backtest.py와 같은 이유).
- 손절·익절은 장중에 걸린다. 종가만 보면 당일 -8%까지 빠졌다 -3%로 끝난 날을
  놓치므로 일봉의 고가·저가로 판정한다.
- 갭을 넘어 체결되지는 않는다. 시가가 이미 기준을 지나 열렸으면 시가로 채운다
  (스톱 주문의 실제 동작). 그렇지 않으면 기준가에 체결된 것으로 본다.
- 같은 날 저가가 손절선, 고가가 익절선을 모두 건드리면 일봉으로는 순서를 알 수
  없다. 손절을 먼저 적용하고 그런 날은 따로 표시한다 (보수적인 쪽).
- 미국 종목은 원화 환산을 같이 낸다. 손절·익절 판정은 현지 통화 가격으로 한다
  — 해외주식 스톱은 보통 현지가로 걸기 때문이다. 환율 효과는 따로 보여준다.
- 가격은 수정종가 기준(분할·배당 반영)이다. 실제 체결가와 소수 단위가 다를 수
  있다. 수수료·슬리피지·세금 미반영.

사용법:
    python screener/papertrade.py                       # 1개월 전 진입, 양 시장 상위 5
    python screener/papertrade.py --days 30 --top 5
    python screener/papertrade.py --capital 10000000 --stop -5 --target 10
    python screener/papertrade.py --market us
    python screener/papertrade.py --json pt.json
"""
import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import backtest as B

FX_TICKER = "KRW=X"          # USD/KRW
DEFAULTS = {"days": 30, "top": 5, "capital": 10_000_000,
            "stop": -5.0, "target": 10.0}


# ---------------------------------------------------------------- 가격
def fetch_ohlc(tickers: list[str], start: str) -> dict[str, list[dict]]:
    """{ticker: [{date, open, high, low, close}, ...]} 날짜 오름차순."""
    import time

    import yfinance as yf

    uniq = sorted(set(tickers))
    out: dict[str, list[dict]] = {}
    for i in range(0, len(uniq), 100):
        batch = uniq[i:i + 100]
        df = yf.download(batch, start=start, interval="1d", auto_adjust=True,
                         group_by="ticker", threads=True, progress=False)
        for t in batch:
            try:
                sub = df[t].dropna(subset=["Close"])
            except (KeyError, TypeError):
                continue
            rows = [{"date": str(idx.date()), "open": float(r["Open"]),
                     "high": float(r["High"]), "low": float(r["Low"]),
                     "close": float(r["Close"])}
                    for idx, r in sub.iterrows()]
            if rows:
                out[t] = rows
        time.sleep(0.5)
    return out


def fx_series(start: str) -> dict[str, float]:
    """USD/KRW 종가. 받지 못하면 빈 dict (원화 환산을 생략한다)."""
    return {r["date"]: r["close"]
            for r in fetch_ohlc([FX_TICKER], start).get(FX_TICKER, [])}


def at_or_before(series: dict[str, float], d: str) -> float | None:
    if not series:
        return None
    prior = [k for k in series if k <= d]
    return series[max(prior)] if prior else None


# ---------------------------------------------------------------- 체결 판정
def simulate(rows: list[dict], entry_date: str, stop_pct: float,
             target_pct: float) -> dict:
    """일봉을 앞으로 걸으며 손절·익절을 판정한다.

    rows는 진입일을 포함한 날짜 오름차순 일봉. 진입일 당일은 종가에 샀다고
    보므로 판정에서 제외한다.
    """
    idx = next((i for i, r in enumerate(rows) if r["date"] == entry_date), None)
    if idx is None:
        # 진입일에 거래가 없었다 — 그 이후 첫 거래일 종가로 산다.
        idx = next((i for i, r in enumerate(rows) if r["date"] > entry_date),
                   None)
    if idx is None:
        return {"status": "측정 불가", "reason": "진입일 이후 가격 없음"}

    entry = rows[idx]["close"]
    if entry <= 0:
        return {"status": "측정 불가", "reason": "진입가 이상"}
    stop = entry * (1 + stop_pct / 100)
    target = entry * (1 + target_pct / 100)

    for r in rows[idx + 1:]:
        both = r["low"] <= stop and r["high"] >= target
        if r["open"] <= stop:
            return _exit(entry, rows[idx]["date"], r["date"], r["open"],
                         "손절", "갭하락 시가 체결", both)
        if r["open"] >= target:
            return _exit(entry, rows[idx]["date"], r["date"], r["open"],
                         "익절", "갭상승 시가 체결", both)
        if r["low"] <= stop:
            return _exit(entry, rows[idx]["date"], r["date"], stop,
                         "손절", f"저가 {r['low']:.4g} ≤ 손절선", both)
        if r["high"] >= target:
            return _exit(entry, rows[idx]["date"], r["date"], target,
                         "익절", f"고가 {r['high']:.4g} ≥ 익절선", both)

    last = rows[-1]
    return {"status": "보유", "reason": "손절·익절 모두 미도달",
            "entry_date": rows[idx]["date"], "entry": entry,
            "exit_date": last["date"], "exit": last["close"],
            "ret": last["close"] / entry - 1.0,
            "days": (date.fromisoformat(last["date"])
                     - date.fromisoformat(rows[idx]["date"])).days,
            "both_hit": False}


def _exit(entry: float, entry_date: str, d: str, price: float, status: str,
          reason: str, both: bool) -> dict:
    return {"status": status, "reason": reason, "entry_date": entry_date,
            "entry": entry, "exit_date": d, "exit": price,
            "ret": price / entry - 1.0,
            "days": (date.fromisoformat(d)
                     - date.fromisoformat(entry_date)).days,
            "both_hit": both}


# ---------------------------------------------------------------- 포트폴리오
def pick(market: str, days: int, top: int) -> tuple[list[dict], str]:
    """한 달 전 스냅샷의 상위 N (웹앱과 같은 RS→VCP 정렬)."""
    snaps = B.snapshots(market, days)
    if not snaps:
        return [], ""
    rows = sorted(({"ticker": t, **v} for t, v in snaps[0]["rows"].items()),
                  key=B.rank_key)
    return rows[:top], snaps[0]["date"]


def value(pos: dict, per: float, fx: dict[str, float]) -> None:
    """포지션에 수량·평가금액을 채운다. 미국은 환율로 원화 환산한다."""
    sim = pos["sim"]
    pos["krw_in"] = per
    if sim["status"] == "측정 불가":
        pos["krw_out"] = pos["krw_ret"] = pos["qty"] = None
        return
    fx_in = 1.0 if pos["market"] == "kr" else at_or_before(fx,
                                                           sim["entry_date"])
    fx_out = 1.0 if pos["market"] == "kr" else at_or_before(fx,
                                                            sim["exit_date"])
    if not fx_in or not fx_out:
        # 환율을 못 받으면 현지 통화 수익률만 남긴다.
        pos["qty"] = pos["krw_out"] = pos["krw_ret"] = None
        return
    pos["fx_in"], pos["fx_out"] = fx_in, fx_out
    pos["qty"] = per / (sim["entry"] * fx_in)
    pos["krw_out"] = pos["qty"] * sim["exit"] * fx_out
    pos["krw_ret"] = pos["krw_out"] / per - 1.0


# ---------------------------------------------------------------- 출력
def won(v: float | None) -> str:
    return "-" if v is None else f"{round(v):,}원"


def report(positions: list[dict], capital: float, stop: float, target: float,
           asof: str) -> dict:
    per = capital / len(positions) if positions else 0.0
    print(f"\n{'=' * 86}")
    print(f"  모의매매  진입 {positions[0]['sim'].get('entry_date', '?')} "
          f"→ 평가 {asof}")
    print(f"{'=' * 86}")
    print(f"  원금 {won(capital)} → {len(positions)}종목 × {won(per)} "
          f"(소수점 매수)")
    print(f"  매도 기준: {stop:+.1f}% 손절 / {target:+.1f}% 익절 "
          f"— 일봉 고가·저가로 판정, 갭은 시가 체결")
    print(f"  수수료·슬리피지·세금 미반영 · 미국은 환율 반영 원화 기준\n")

    head = (f"  {B.wpad('종목', 22)} {'진입가':>11} {'체결/현재':>11} "
            f"{B.wpad('상태', 8)} {'현지':>8} {'원화':>8} {'평가금액':>13} 일")
    print(head)
    print(f"  {'-' * 84}")
    tot_in = tot_out = 0.0
    for p in positions:
        s = p["sim"]
        if s["status"] == "측정 불가":
            print(f"  {B.wpad(p['label'], 22)} {'측정 불가 — ' + s['reason']}")
            continue
        tot_in += p["krw_in"]
        tot_out += p["krw_out"] if p["krw_out"] is not None else p["krw_in"]
        print(f"  {B.wpad(p['label'], 22)} {s['entry']:>11,.2f} "
              f"{s['exit']:>11,.2f} {B.wpad(s['status'], 8)} "
              f"{B.pct(s['ret'])} {B.pct(p['krw_ret'])} "
              f"{won(p['krw_out']):>13} {s['days']:>2}")
    print(f"  {'-' * 84}")
    total_ret = (tot_out / tot_in - 1.0) if tot_in else None
    print(f"  {B.wpad('합계', 22)} {'':>11} {'':>11} {B.wpad('', 8)} "
          f"{'':>9} {B.pct(total_ret)} {won(tot_out):>13}")
    print(f"  손익 {won(tot_out - tot_in)}\n")

    for p in positions:
        s = p["sim"]
        if s["status"] == "측정 불가":
            continue
        line = (f"  · {p['label']}: {s['status']}"
                + (f" ({s['exit_date']})" if s["status"] != "보유" else "")
                + f" — {s['reason']}")
        if s.get("both_hit"):
            line += " ※ 같은 날 손절선·익절선을 모두 건드렸다 (손절 우선 적용)"
        print(line)

    sold = [p for p in positions if p["sim"]["status"] in ("손절", "익절")]
    held = [p for p in positions if p["sim"]["status"] == "보유"]
    print(f"\n  체결 {len(sold)}종목 (손절 "
          f"{sum(1 for p in sold if p['sim']['status'] == '손절')} · 익절 "
          f"{sum(1 for p in sold if p['sim']['status'] == '익절')}) · "
          f"보유 {len(held)}종목")
    return {"asof": asof, "capital": capital, "per_position": per,
            "stop_pct": stop, "target_pct": target,
            "total_return": total_ret, "total_value": tot_out,
            "positions": positions}


# ---------------------------------------------------------------- 진입점
def main(argv: list[str]) -> int:
    o = dict(DEFAULTS)
    markets, out_json = ["us", "kr"], None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--market" and i + 1 < len(argv):
            i += 1
            markets = [argv[i]]
        elif a == "--json" and i + 1 < len(argv):
            i += 1
            out_json = argv[i]
        elif a.lstrip("-") in o and i + 1 < len(argv):
            key = a.lstrip("-")
            i += 1
            o[key] = (int(argv[i]) if key in ("days", "top", "capital")
                      else float(argv[i]))
        else:
            sys.exit(f"알 수 없는 인자: {a}\n{__doc__}")
        i += 1
    if any(m not in B.CFG for m in markets):
        sys.exit(f"--market 은 us|kr (받은 값: {markets})")

    positions, entry_dates = [], []
    for m in markets:
        rows, d = pick(m, o["days"], o["top"])
        if not rows:
            print(f"  {m}: 최근 {o['days']}일 스냅샷이 없다")
            continue
        entry_dates.append(d)
        for r in rows:
            positions.append({
                "market": m, "ticker": r["ticker"], "name": r["name"],
                "label": f"{r['ticker']} {r['name']}",
                "rs": r["rs"], "vcp": r["vcp"], "entry_snapshot": d,
            })
    if not positions:
        return 1

    start = min(entry_dates)
    print(f"  진입 {start} · {len(positions)}종목 일봉 수집 …", flush=True)
    ohlc = fetch_ohlc([p["ticker"] for p in positions], start)
    fx = fx_series(start)
    if not fx:
        print("  경고: 환율을 받지 못했다 — 현지 통화 수익률만 낸다", flush=True)

    for p in positions:
        p["sim"] = simulate(ohlc.get(p["ticker"], []), p["entry_snapshot"],
                            o["stop"], o["target"])
        value(p, o["capital"] / len(positions), fx)

    asof = max((p["sim"].get("exit_date", "") for p in positions), default="")
    doc = report(positions, float(o["capital"]), o["stop"], o["target"], asof)
    if out_json:
        path = Path(out_json)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=2)
        print(f"  → {path}")
    return 0


if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main(sys.argv[1:]))
