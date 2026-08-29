# -*- coding: utf-8 -*-
"""
미너비니 트렌드 템플릿 스크리너 - 프로토타입
유니버스: S&P 500 + NASDAQ-100 (~600종목)
출력: 8개 조건 통과 종목 + VCP 후보 점수, RS 순 정렬
"""
import io
import json
import sys
import time

import numpy as np
import pandas as pd
import requests
import yfinance as yf

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


# ---------------------------------------------------------------- 유니버스
def get_universe() -> list[str]:
    tickers = set()

    r = requests.get(
        "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
        headers=UA, timeout=30,
    )
    sp500 = pd.read_html(io.StringIO(r.text))[0]
    tickers.update(sp500["Symbol"].astype(str).tolist())

    # NOTE: NASDAQ-100은 위키피디아 페이지 구조 변경으로 파싱 불가.
    # 대부분 S&P 500과 겹치므로 프로토타입은 S&P 500만 사용.
    # yfinance는 BRK.B -> BRK-B 형식
    return sorted(t.replace(".", "-").strip() for t in tickers if t and t != "nan")


# ---------------------------------------------------------------- 데이터 수집
def download_history(tickers: list[str]) -> dict[str, pd.DataFrame]:
    """2년치 일봉을 배치로 다운로드해 {ticker: OHLCV df} 로 반환."""
    data: dict[str, pd.DataFrame] = {}
    batch_size = 100
    for i in range(0, len(tickers), batch_size):
        batch = tickers[i : i + batch_size]
        df = yf.download(
            batch, period="2y", interval="1d", auto_adjust=True,
            group_by="ticker", threads=True, progress=False,
        )
        for t in batch:
            try:
                # 최신 거래일에 Volume만 있고 OHLC가 NaN인 결함 행이 있어
                # Close 기준으로 걸러낸다
                sub = df[t].dropna(subset=["Close"])
            except KeyError:
                continue
            if len(sub) >= 260:  # 최소 1년치 + 여유
                data[t] = sub
        print(f"  downloaded {min(i + batch_size, len(tickers))}/{len(tickers)}",
              flush=True)
        time.sleep(0.5)
    return data


# ---------------------------------------------------------------- RS 점수
def rs_raw_score(close: pd.Series) -> float | None:
    """IBD 방식: 최근 3개월 수익률 40% + 이전 3개 분기 각 20% 가중."""
    if len(close) < 253:
        return None
    c = close.iloc[-1]
    try:
        q1, q2, q3, q4 = (close.iloc[-64], close.iloc[-127],
                          close.iloc[-190], close.iloc[-253])
    except IndexError:
        return None
    if min(q1, q2, q3, q4) <= 0:
        return None
    return 2.0 * (c / q1) + (c / q2) + (c / q3) + (c / q4)


# ---------------------------------------------------------------- 트렌드 템플릿
def trend_template(df: pd.DataFrame, rs_rank: float) -> dict:
    close, high, low = df["Close"], df["High"], df["Low"]
    c = close.iloc[-1]
    ma50 = close.rolling(50).mean()
    ma150 = close.rolling(150).mean()
    ma200 = close.rolling(200).mean()
    m50, m150, m200 = ma50.iloc[-1], ma150.iloc[-1], ma200.iloc[-1]

    low52 = low.iloc[-252:].min()
    high52 = high.iloc[-252:].max()

    checks = {
        "1_price_above_150_200": c > m150 and c > m200,
        "2_ma150_above_ma200": m150 > m200,
        "3_ma200_rising_1m": m200 > ma200.iloc[-22],
        "4_ma50_above_150_200": m50 > m150 and m50 > m200,
        "5_price_above_ma50": c > m50,
        "6_above_52w_low_30pct": c >= low52 * 1.30,
        "7_within_25pct_52w_high": c >= high52 * 0.75,
        "8_rs_rank_70plus": rs_rank >= 70,
    }
    return {
        "checks": checks,
        "pass_all": all(checks.values()),
        "price": round(float(c), 2),
        "pct_off_high": round(float((c / high52 - 1) * 100), 1),
        "pct_above_low": round(float((c / low52 - 1) * 100), 1),
    }


# ---------------------------------------------------------------- VCP 후보 점수
def vcp_score(df: pd.DataFrame) -> dict:
    """0~3점. 완전 자동판정이 아닌 '후보' 스코어링.
    (a) 변동성 수축: 최근 10일 가격 범위가 그 이전 30일 범위의 절반 이하
    (b) 거래량 고갈: 최근 10일 평균 거래량 < 50일 평균의 80%
    (c) 베이스 위치: 52주 고점 대비 15% 이내
    """
    close, high, low, vol = df["Close"], df["High"], df["Low"], df["Volume"]
    c = close.iloc[-1]

    range_recent = (high.iloc[-10:].max() - low.iloc[-10:].min()) / c
    prior = df.iloc[-40:-10]
    range_prior = (prior["High"].max() - prior["Low"].min()) / c
    tightening = bool(range_prior > 0 and range_recent <= 0.5 * range_prior)

    vol_dryup = bool(vol.iloc[-10:].mean() < vol.iloc[-50:].mean() * 0.8)

    high52 = high.iloc[-252:].max()
    near_high = bool(c >= high52 * 0.85)

    score = int(tightening) + int(vol_dryup) + int(near_high)
    return {
        "score": score,
        "tightening": tightening,
        "volume_dryup": vol_dryup,
        "near_52w_high": near_high,
        "range_recent_pct": round(float(range_recent * 100), 1),
        "range_prior_pct": round(float(range_prior * 100), 1),
    }


# ---------------------------------------------------------------- 메인
def main() -> None:
    print("[1/4] 유니버스 수집 (S&P 500 + NASDAQ-100)...", flush=True)
    tickers = get_universe()
    print(f"  {len(tickers)} tickers", flush=True)

    print("[2/4] 가격 데이터 다운로드 (2년 일봉)...", flush=True)
    data = download_history(tickers)
    print(f"  {len(data)} tickers with sufficient history", flush=True)

    print("[3/4] RS 점수 계산 및 백분위 랭킹...", flush=True)
    raw = {t: rs_raw_score(df["Close"]) for t, df in data.items()}
    raw = {t: v for t, v in raw.items() if v is not None}
    rs_series = pd.Series(raw)
    rs_rank = (rs_series.rank(pct=True) * 98 + 1).round(0)  # 1~99

    print("[4/4] 트렌드 템플릿 + VCP 스코어링...", flush=True)
    results = []
    for t, df in data.items():
        if t not in rs_rank.index:
            continue
        tt = trend_template(df, float(rs_rank[t]))
        if not tt["pass_all"]:
            continue
        results.append({
            "ticker": t,
            "rs_rank": int(rs_rank[t]),
            "price": tt["price"],
            "pct_off_high": tt["pct_off_high"],
            "pct_above_low": tt["pct_above_low"],
            "vcp": vcp_score(df),
        })

    results.sort(key=lambda x: (-x["rs_rank"], -x["vcp"]["score"]))

    out = {
        "generated_at": pd.Timestamp.now().isoformat(),
        "universe_size": len(tickers),
        "analyzed": len(rs_rank),
        "passed": len(results),
        "results": results,
    }
    with open("prototype/results.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    # 콘솔 요약
    print(f"\n===== 트렌드 템플릿 통과: {len(results)}종목 "
          f"(분석 {len(rs_rank)}종목 중) =====\n")
    hdr = f"{'TICKER':<8}{'RS':>4}{'PRICE':>10}{'OFF-HIGH':>10}{'VCP':>5}  FLAGS"
    print(hdr)
    print("-" * len(hdr))
    for r in results[:30]:
        v = r["vcp"]
        flags = []
        if v["tightening"]:
            flags.append("tight")
        if v["volume_dryup"]:
            flags.append("dryup")
        if v["near_52w_high"]:
            flags.append("nearhigh")
        print(f"{r['ticker']:<8}{r['rs_rank']:>4}{r['price']:>10.2f}"
              f"{r['pct_off_high']:>9.1f}%{v['score']:>5}  {','.join(flags)}")
    print("\n전체 결과: prototype/results.json")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
