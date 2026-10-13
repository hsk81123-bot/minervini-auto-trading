# -*- coding: utf-8 -*-
"""모의매매 — 과거 추천 상위 N을 동일 금액으로 사고 매도 규칙을 적용한다.

입력: git 이력의 docs/data/results[_kr].json (backtest.py의 스냅샷 로직 재사용)
출력: 종목별 체결 내역 + 손익비·기대값 + 총 수익률

설계 근거:
- 진입 종목은 '그 날 커밋된 스냅샷'의 상위 N이다. 지금 리스트에서 거꾸로
  고르면 이미 오른 종목을 뽑는 셈이 된다 (backtest.py와 같은 이유).
- 성과는 수익률이 아니라 R-배수로 본다. 1R = 진입가에서 최초 손절선까지의
  거리다. 손절을 -5%로 걸든 -8%로 걸든 R로 환산하면 규칙끼리 비교할 수 있고,
  '손익비 3:1'이라는 말이 설계값이 아니라 실측값이 된다.
- 전략의 생존 조건은 승률이 아니라 기대값 E[R] = 평균 R이다. 승률 40%에
  손익비 3:1이면 기대값은 양수다. 승률만 보면 거꾸로 읽는다.
- 고정 익절은 승자를 자른다. 미너비니는 손절을 좁게 걸고 상승은 열어 둔 뒤
  추격손절로 따라간다. --target 없이 --trail 로 굴리는 쪽이 원래 방식이다.
- 오늘의 손절선은 '어제까지'의 고가로만 정한다. 당일 고가로 추격손절을 올리면
  장중 고점을 미리 안다고 가정하는 셈이 된다 (룩어헤드).
- 손절·익절은 장중에 걸린다. 종가만 보면 당일 -8%까지 빠졌다 -3%로 끝난 날을
  놓치므로 일봉의 고가·저가로 판정한다.
- 갭을 넘어 체결되지는 않는다. 시가가 이미 기준을 지나 열렸으면 시가로 채운다
  (스톱 주문의 실제 동작). 그래서 실제 손실은 손절선보다 커질 수 있다.
- 같은 날 저가가 손절선, 고가가 익절선을 모두 건드리면 일봉으로는 순서를 알 수
  없다. 손절을 먼저 적용하고 그런 날은 따로 표시한다 (보수적인 쪽).
- MAE/MFE(보유 중 최대 역행·순행)를 남긴다. 손절선이 노이즈에 걸리는지,
  익절선이 상승을 자르는지는 이 두 숫자로만 판단할 수 있다.
- 수수료·세금을 반영한다. 0으로 두면 회전율이 높은 규칙이 과대평가된다.
  한국은 매도 시 증권거래세가 따로 붙는다.
- 미국 종목은 원화 환산을 같이 낸다. 매도 판정은 현지 통화 가격으로 한다
  — 해외주식 스톱은 보통 현지가로 걸기 때문이다.
- 가격은 수정종가 기준(분할·배당 반영)이다. 실제 체결가와 소수 단위가 다를 수
  있다. 슬리피지 미반영.

사용법:
    python screener/papertrade.py                              # 기본: -8% / +25% (3.1:1)
    python screener/papertrade.py --stop -8 --target 25
    python screener/papertrade.py --stop -8 --trail -15        # 추격손절 (익절 없음)
    python screener/papertrade.py --stop -8 --trail -15 --breakeven 10
    python screener/papertrade.py --time-stop 20               # N일 무반응이면 청산
    python screener/papertrade.py --entry close                # 신호 당일 종가 진입(비교용)
    python screener/papertrade.py --days 30 --top 5 --capital 10000000
    python screener/papertrade.py --market us --json pt.json
"""
import json
import sys
from datetime import date
from pathlib import Path
from statistics import mean, median

sys.path.insert(0, str(Path(__file__).resolve().parent))
import backtest as B

FX_TICKER = "KRW=X"          # USD/KRW

# 거래비용 (bp = 0.01%). 한국 증권거래세는 2026년 코스피·코스닥 0.18% 기준.
FEE_BPS = 5.0                # 편도 위탁수수료
TAX_BPS = {"kr": 18.0, "us": 0.0}   # 매도 시에만

DEFAULTS = {"days": 30, "top": 5, "capital": 10_000_000,
            "stop": -8.0, "target": 25.0, "trail": None,
            "breakeven": None, "time-stop": None, "entry": "next_open"}
ENTRY_MODES = ("next_open", "close")


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


def price(v: float) -> str:
    """가격 표기 — 원화 수천원대와 달러 소수점을 한 함수로 읽히게."""
    return f"{v:,.0f}" if abs(v) >= 1000 else f"{v:,.2f}"


def at_or_before(series: dict[str, float], d: str) -> float | None:
    if not series:
        return None
    prior = [k for k in series if k <= d]
    return series[max(prior)] if prior else None


# ---------------------------------------------------------------- 체결 판정
def simulate(rows: list[dict], entry_date: str, stop: float,
             target: float | None = None, trail: float | None = None,
             breakeven: float | None = None,
             time_stop: int | None = None,
             entry_mode: str = "next_open") -> dict:
    """일봉을 앞으로 걸으며 매도 규칙을 판정한다.

    stop/target/trail/breakeven 은 % 단위. stop·trail 은 음수, target·
    breakeven 은 양수. trail 은 보유 중 최고가 기준 하락폭이다.

    entry_date 는 신호가 나온 스냅샷 날짜다. entry_mode:
      next_open (기본) 신호 다음 거래일 시가에 산다. 스크리너는 장 마감 뒤에
                       돌기 때문에 이것이 실제로 살 수 있는 첫 가격이다.
                       산 날의 고가·저가는 매수 뒤의 일이라 그 날부터 판정한다.
      close            신호가 나온 날 종가에 산다. 신호를 만든 가격에 사는
                       셈이라 실제로는 불가능하다 — 비교용으로만 남긴다.
                       산 날의 고가·저가는 매수 전의 일이라 판정에서 뺀다.
    """
    if entry_mode == "next_open":
        idx = next((i for i, r in enumerate(rows) if r["date"] > entry_date),
                   None)
        if idx is None:
            return {"status": "측정 불가", "reason": "신호 다음 거래일 가격 없음"}
        entry = rows[idx]["open"]
        first = idx                 # 시가에 샀으므로 그 날 장중부터 판정
    elif entry_mode == "close":
        idx = next((i for i, r in enumerate(rows) if r["date"] == entry_date),
                   None)
        if idx is None:
            # 진입일에 거래가 없었다 — 그 이후 첫 거래일 종가로 산다.
            idx = next((i for i, r in enumerate(rows)
                        if r["date"] > entry_date), None)
        if idx is None:
            return {"status": "측정 불가", "reason": "진입일 이후 가격 없음"}
        entry = rows[idx]["close"]
        first = idx + 1             # 종가에 샀으므로 그 날 고저는 매수 전
    else:
        raise ValueError(f"entry_mode 는 next_open|close (받은 값: {entry_mode})")

    if entry <= 0:
        return {"status": "측정 불가", "reason": "진입가 이상"}
    e_date = rows[idx]["date"]
    init_stop = entry * (1 + stop / 100)
    risk = entry - init_stop                 # 1R
    tgt = entry * (1 + target / 100) if target else None
    # 보유 중 최고가는 진입가에서 출발한다 (매수 전 고가는 내 것이 아니다).
    peak = entry
    mae = mfe = 0.0

    for n, r in enumerate(rows[first:], start=first - idx):
        # 오늘의 손절선은 어제까지의 고가로만 정한다 (당일 고가는 아직 모른다).
        level, kind = init_stop, "손절"
        if trail is not None:
            t = peak * (1 + trail / 100)
            if t > level:
                level, kind = t, "추격손절"
        if breakeven is not None and peak >= entry * (1 + breakeven / 100):
            if entry > level:
                level, kind = entry, "추격손절"

        mae = min(mae, r["low"] / entry - 1.0)
        mfe = max(mfe, r["high"] / entry - 1.0)

        # 갭 체결은 시가에서 끝난다. 그 날의 저가·고가는 체결 뒤의 일이므로
        # '순서 모호'(both_hit) 표시를 붙이지 않는다.
        if r["open"] <= level:
            return _exit(entry, risk, e_date, r["date"], r["open"], kind,
                         "갭하락 시가 체결", False, mae, mfe)
        if tgt and r["open"] >= tgt:
            return _exit(entry, risk, e_date, r["date"], r["open"], "익절",
                         "갭상승 시가 체결", False, mae, mfe)
        # 장중 체결은 일봉으로 순서를 알 수 없는 경우가 있다 — 그때만 표시한다.
        both = bool(tgt) and r["low"] <= level and r["high"] >= tgt
        if r["low"] <= level:
            return _exit(entry, risk, e_date, r["date"], level, kind,
                         f"저가 {price(r['low'])} ≤ {kind}선 {price(level)}",
                         both, mae, mfe)
        if tgt and r["high"] >= tgt:
            return _exit(entry, risk, e_date, r["date"], tgt, "익절",
                         f"고가 {price(r['high'])} ≥ 익절선 {price(tgt)}",
                         both, mae, mfe)
        if time_stop and n >= time_stop:
            return _exit(entry, risk, e_date, r["date"], r["close"],
                         "시간청산", f"{time_stop}일 경과 — 종가 청산",
                         False, mae, mfe)
        peak = max(peak, r["high"])

    last = rows[-1]
    return {"status": "보유", "reason": "매도 조건 미도달",
            "entry_date": e_date, "entry": entry, "risk": risk,
            "exit_date": last["date"], "exit": last["close"],
            "ret": last["close"] / entry - 1.0,
            "r_multiple": (last["close"] - entry) / risk if risk else None,
            "mae": mae, "mfe": mfe, "both_hit": False,
            "days": (date.fromisoformat(last["date"])
                     - date.fromisoformat(e_date)).days}


def _exit(entry: float, risk: float, e_date: str, d: str, px: float,
          status: str, reason: str, both: bool, mae: float,
          mfe: float) -> dict:
    return {"status": status, "reason": reason, "entry_date": e_date,
            "entry": entry, "risk": risk, "exit_date": d, "exit": px,
            "ret": px / entry - 1.0,
            "r_multiple": (px - entry) / risk if risk else None,
            "mae": mae, "mfe": mfe, "both_hit": both,
            "days": (date.fromisoformat(d) - date.fromisoformat(e_date)).days}


# ---------------------------------------------------------------- 성과 지표
def perf(sims: list[dict]) -> dict:
    """손익비와 기대값. 승률보다 이 둘이 전략의 생존 조건이다."""
    rs = [s["r_multiple"] for s in sims
          if s.get("r_multiple") is not None]
    d: dict = {"trades": len(sims), "measured": len(rs)}
    if not rs:
        return d
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    d["win_rate"] = len(wins) / len(rs)
    d["expectancy_r"] = mean(rs)          # E[R] — 거래당 기대 R
    d["median_r"] = median(rs)
    d["avg_win_r"] = mean(wins) if wins else None
    d["avg_loss_r"] = mean(losses) if losses else None
    # 손익비 = 평균이익 / |평균손실|. 한쪽이 없으면 정의되지 않는다.
    d["payoff"] = (mean(wins) / abs(mean(losses))
                   if wins and losses and mean(losses) != 0 else None)
    d["worst_r"], d["best_r"] = min(rs), max(rs)
    d["avg_mae"] = mean([s["mae"] for s in sims if "mae" in s])
    d["avg_mfe"] = mean([s["mfe"] for s in sims if "mfe" in s])
    return d


# ---------------------------------------------------------------- 포트폴리오
def pick(market: str, days: int, top: int) -> tuple[list[dict], str]:
    """한 달 전 스냅샷의 상위 N (웹앱과 같은 RS→VCP 정렬)."""
    snaps = B.snapshots(market, days)
    if not snaps:
        return [], ""
    rows = sorted(({"ticker": t, **v} for t, v in snaps[0]["rows"].items()),
                  key=B.rank_key)
    return rows[:top], snaps[0]["date"]


def cost_mult(market: str) -> tuple[float, float]:
    """(매수 배수, 매도 배수) — 수수료와 매도 시 거래세."""
    buy = 1 + FEE_BPS / 10_000
    sell = 1 - (FEE_BPS + TAX_BPS.get(market, 0.0)) / 10_000
    return buy, sell


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
        # 환율을 못 받으면 현지 통화 수익률만 남긴다 (1:1로 가정하지 않는다).
        pos["qty"] = pos["krw_out"] = pos["krw_ret"] = None
        return
    buy, sell = cost_mult(pos["market"])
    pos["fx_in"], pos["fx_out"] = fx_in, fx_out
    pos["qty"] = per / (sim["entry"] * fx_in * buy)
    pos["krw_out"] = pos["qty"] * sim["exit"] * fx_out * sell
    pos["krw_ret"] = pos["krw_out"] / per - 1.0


# ---------------------------------------------------------------- 출력
def won(v: float | None) -> str:
    return "-" if v is None else f"{round(v):,}원"


def rfmt(v: float | None) -> str:
    return "    -  " if v is None else f"{v:+6.2f}R"


def rules_line(o: dict) -> str:
    parts = ["진입 " + ("신호 다음날 시가" if o.get("entry", "next_open")
                        == "next_open" else "신호 당일 종가(실행 불가, 비교용)")]
    parts.append(f"손절 {o['stop']:+.1f}%")
    if o["target"]:
        parts.append(f"익절 {o['target']:+.1f}% "
                     f"(설계 손익비 {abs(o['target'] / o['stop']):.1f}:1)")
    else:
        parts.append("고정 익절 없음")
    if o["trail"] is not None:
        parts.append(f"추격손절 최고가 {o['trail']:+.1f}%")
    if o["breakeven"] is not None:
        parts.append(f"본전손절 {o['breakeven']:+.1f}% 도달 시")
    if o["time-stop"]:
        parts.append(f"시간청산 {o['time-stop']}일")
    return " · ".join(parts)


def report(positions: list[dict], o: dict, asof: str) -> dict:
    per = o["capital"] / len(positions) if positions else 0.0
    print(f"\n{'=' * 92}")
    print(f"  모의매매  진입 {positions[0]['sim'].get('entry_date', '?')} "
          f"→ 평가 {asof}")
    print(f"{'=' * 92}")
    print(f"  원금 {won(o['capital'])} → {len(positions)}종목 × {won(per)} "
          f"(소수점 매수)")
    print(f"  매도 규칙: {rules_line(o)}")
    print(f"  판정: 일봉 고가·저가, 갭은 시가 체결 · 손절선은 전일까지의 "
          f"고가로만 갱신")
    print(f"  비용: 편도 수수료 {FEE_BPS:.0f}bp + 매도 거래세 "
          f"한국 {TAX_BPS['kr']:.0f}bp / 미국 {TAX_BPS['us']:.0f}bp "
          f"· 슬리피지 미반영\n")

    print(f"  {B.wpad('종목', 22)} {'진입가':>10} {'체결가':>10} "
          f"{B.wpad('결과', 10)} {'현지':>8} {'R':>7} {'MAE':>7} {'MFE':>7} "
          f"{'평가금액':>12} 일")
    print(f"  {'-' * 90}")
    tot_in = tot_out = 0.0
    for p in positions:
        s = p["sim"]
        if s["status"] == "측정 불가":
            print(f"  {B.wpad(p['label'], 22)} 측정 불가 — {s['reason']}")
            continue
        tot_in += p["krw_in"]
        tot_out += p["krw_out"] if p["krw_out"] is not None else p["krw_in"]
        print(f"  {B.wpad(p['label'], 22)} {price(s['entry']):>10} "
              f"{price(s['exit']):>10} {B.wpad(s['status'], 10)} "
              f"{B.pct(s['ret'])} {rfmt(s['r_multiple'])} "
              f"{B.pct(s['mae'])[:7]} {B.pct(s['mfe'])[:7]} "
              f"{won(p['krw_out']):>12} {s['days']:>2}")
    print(f"  {'-' * 90}")
    total_ret = (tot_out / tot_in - 1.0) if tot_in else None
    print(f"  {B.wpad('합계 (비용 반영)', 22)} {B.pct(total_ret)}  "
          f"{won(tot_out)}  손익 {won(tot_out - tot_in)}")

    sims = [p["sim"] for p in positions if p["sim"]["status"] != "측정 불가"]
    m = perf(sims)
    print(f"\n  [규칙 성과] R-배수 기준 — 1R = 진입가에서 최초 손절선까지")
    if "expectancy_r" in m:
        print(f"  기대값 {rfmt(m['expectancy_r'])}/거래 · 중앙값 "
              f"{rfmt(m['median_r'])} · 승률 {m['win_rate'] * 100:.1f}%")
        print(f"  실측 손익비 "
              + ("정의 불가 (한쪽이 없다)" if m["payoff"] is None
                 else f"{m['payoff']:.2f}:1")
              + f"  (평균이익 {rfmt(m['avg_win_r'])} / 평균손실 "
              + f"{rfmt(m['avg_loss_r'])})")
        print(f"  최고 {rfmt(m['best_r'])} · 최저 {rfmt(m['worst_r'])} · "
              f"보유 중 평균 최대역행 {B.pct(m['avg_mae'])} / 최대순행 "
              f"{B.pct(m['avg_mfe'])}")

    print()
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

    kinds: dict[str, int] = {}
    for s in sims:
        kinds[s["status"]] = kinds.get(s["status"], 0) + 1
    print("\n  " + " · ".join(f"{k} {v}종목" for k, v in sorted(kinds.items())))
    return {"asof": asof, "rules": {k: o[k] for k in DEFAULTS},
            "total_return": total_ret, "total_value": tot_out,
            "cost": {"fee_bps": FEE_BPS, "tax_bps": TAX_BPS},
            "perf": m, "positions": positions}


# ---------------------------------------------------------------- 진입점
def parse_args(argv: list[str]) -> tuple[dict, list[str], str | None]:
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
        elif a == "--no-target":
            o["target"] = None
        elif a == "--entry" and i + 1 < len(argv):
            i += 1
            if argv[i] not in ENTRY_MODES:
                sys.exit(f"--entry 는 {'|'.join(ENTRY_MODES)} (받은 값: {argv[i]})")
            o["entry"] = argv[i]
        elif a.lstrip("-") in o and i + 1 < len(argv):
            key = a.lstrip("-")
            i += 1
            raw = argv[i]
            if raw in ("", "none", "off"):
                o[key] = None
            else:
                o[key] = (int(raw) if key in ("days", "top", "capital",
                                              "time-stop") else float(raw))
        else:
            sys.exit(f"알 수 없는 인자: {a}\n{__doc__}")
        i += 1
    if o["stop"] is None or o["stop"] >= 0:
        sys.exit(f"--stop 은 음수여야 한다 (받은 값: {o['stop']})")
    if o["trail"] is not None and o["trail"] >= 0:
        sys.exit(f"--trail 은 음수여야 한다 (받은 값: {o['trail']})")
    return o, markets, out_json


def main(argv: list[str]) -> int:
    o, markets, out_json = parse_args(argv)
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
                            o["stop"], o["target"], o["trail"],
                            o["breakeven"], o["time-stop"], o["entry"])
        value(p, o["capital"] / len(positions), fx)

    asof = max((p["sim"].get("exit_date", "") for p in positions), default="")
    doc = report(positions, o, asof)
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
