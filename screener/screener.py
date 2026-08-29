# -*- coding: utf-8 -*-
"""
미너비니 트렌드 템플릿 스크리너
유니버스: S&P 500 (추후 전 종목 확장 예정)
출력: docs/data/results.json (GitHub Pages 웹앱이 읽음)
"""
import io
import json
import sys
import time
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

from vcp import detect_vcp

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
DATA_DIR = Path(__file__).resolve().parent.parent / "docs" / "data"
OUT_PATH = DATA_DIR / "results.json"
HISTORY_DIR = DATA_DIR / "history"


# ---------------------------------------------------------------- 유니버스
# 유동성 필터: 미너비니는 저가·저유동성 종목을 피함
MIN_PRICE = 10.0        # 최소 주가 ($)
MIN_AVG_VOL = 100_000   # 최소 50일 평균 거래량 (주)
TOP_EXPORT = 150        # 차트 이력(5년) 내보낼 상위 종목 수 (저장소 크기 관리)

# 종목명에 이 단어가 있으면 보통주가 아님 (워런트/우선주/채권 등)
_EXCLUDE_NAME = ("warrant", " right", " rights", "unit", "preferred",
                 "depositary", "notes", " etn", "%", "trust", "fund")


def get_universe() -> dict[str, str]:
    """NASDAQ Trader 공식 심볼 디렉토리에서 미국 전 상장 보통주 수집
    (NASDAQ + NYSE/AMEX 등 otherlisted). ETF·테스트종목·비보통주 제외."""
    urls = [
        "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt",
        "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt",
    ]
    names: dict[str, str] = {}
    for url in urls:
        r = requests.get(url, headers=UA, timeout=30)
        r.raise_for_status()
        lines = r.text.strip().splitlines()
        header = lines[0].split("|")
        etf_col = header.index("ETF")
        test_col = next(i for i, c in enumerate(header) if "Test Issue" in c)
        for line in lines[1:]:
            if line.startswith("File Creation Time"):
                continue
            f = line.split("|")
            if len(f) <= max(etf_col, test_col):
                continue
            sym, name = f[0].strip(), f[1].strip()
            if f[etf_col].strip() == "Y" or f[test_col].strip() == "Y":
                continue
            # 보통주 심볼만: 영문 1~5자 (+ 선택적 클래스 접미사 .A 등)
            core = sym.replace(".", "").replace("-", "")
            if not core.isalpha() or len(core) > 5:
                continue
            low = name.lower()
            if any(k in low for k in _EXCLUDE_NAME):
                continue
            # 표시용 이름 정리: "- Common Stock", "- Class A Ordinary Shares" 등 제거
            clean = name.split(" - ")[0].strip().rstrip(",")
            names[sym.replace(".", "-")] = clean  # yfinance 형식
    return names


def export_sp500() -> None:
    """S&P 500 구성종목 티커 목록 → docs/data/sp500.json (필터 토글용)."""
    r = requests.get(
        "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
        headers=UA, timeout=30,
    )
    sp500 = pd.read_html(io.StringIO(r.text))[0]
    tickers = sorted(
        sp500["Symbol"].astype(str).str.replace(".", "-", regex=False))
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(DATA_DIR / "sp500.json", "w", encoding="utf-8") as f:
        json.dump(tickers, f)
    print(f"  S&P 500 {len(tickers)} tickers -> sp500.json", flush=True)


# ---------------------------------------------------------------- 데이터 수집
def download_history(tickers: list[str], period: str = "2y",
                     min_len: int = 260) -> dict[str, pd.DataFrame]:
    """일봉을 배치로 다운로드해 {ticker: OHLCV df} 로 반환."""
    data: dict[str, pd.DataFrame] = {}
    batch_size = 100
    for i in range(0, len(tickers), batch_size):
        batch = tickers[i : i + batch_size]
        df = yf.download(
            batch, period=period, interval="1d", auto_adjust=True,
            group_by="ticker", threads=True, progress=False,
        )
        for t in batch:
            try:
                # 최신 거래일에 Volume만 있고 OHLC가 NaN인 결함 행 제거
                sub = df[t].dropna(subset=["Close"])
            except KeyError:
                continue
            if len(sub) >= min_len:
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
        "price_above_150_200": bool(c > m150 and c > m200),
        "ma150_above_ma200": bool(m150 > m200),
        "ma200_rising_1m": bool(m200 > ma200.iloc[-22]),
        "ma50_above_150_200": bool(m50 > m150 and m50 > m200),
        "price_above_ma50": bool(c > m50),
        "above_52w_low_30pct": bool(c >= low52 * 1.30),
        "within_25pct_52w_high": bool(c >= high52 * 0.75),
        "rs_rank_70plus": bool(rs_rank >= 70),
    }
    return {
        "checks": checks,
        "pass_all": all(checks.values()),
        "price": round(float(c), 2),
        "ma50": round(float(m50), 2),
        "ma150": round(float(m150), 2),
        "ma200": round(float(m200), 2),
        "high52": round(float(high52), 2),
        "low52": round(float(low52), 2),
        "pct_off_high": round(float((c / high52 - 1) * 100), 1),
        "pct_above_low": round(float((c / low52 - 1) * 100), 1),
    }


# ---------------------------------------------------------------- 이력 내보내기
def export_history(ticker: str, df: pd.DataFrame, spx_close: pd.Series) -> None:
    """심층 분석 페이지의 3단 차트(RS/SPX/가격·거래량)용 이력 JSON.
    RS 라인 = 종가 / S&P500 종가 (책의 StockCharts 상대강도 라인과 동일 개념)"""
    sub = df
    spx = spx_close.reindex(sub.index).ffill()
    valid = spx.notna()
    sub, spx = sub[valid], spx[valid]
    out = {
        "time": [d.strftime("%Y-%m-%d") for d in sub.index],
        "open": [round(float(x), 2) for x in sub["Open"]],
        "high": [round(float(x), 2) for x in sub["High"]],
        "low": [round(float(x), 2) for x in sub["Low"]],
        "close": [round(float(x), 2) for x in sub["Close"]],
        "volume": [int(x) for x in sub["Volume"]],
        "spx": [round(float(x), 2) for x in spx],
        "rs": [round(float(c / s), 6) for c, s in zip(sub["Close"], spx)],
    }
    with open(HISTORY_DIR / f"{ticker}.json", "w", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"))


# ---------------------------------------------------------------- 메인
def main() -> None:
    print("[1/5] 유니버스 수집 (NASDAQ 심볼 디렉토리, 미국 전 상장 보통주)...",
          flush=True)
    names = get_universe()
    tickers = sorted(names)
    print(f"  {len(tickers)} tickers", flush=True)
    export_sp500()

    print("[2/5] 가격 데이터 다운로드 (2년 일봉)...", flush=True)
    data = download_history(tickers)
    print(f"  {len(data)} tickers with sufficient history", flush=True)
    data = {
        t: df for t, df in data.items()
        if float(df["Close"].iloc[-1]) >= MIN_PRICE
        and float(df["Volume"].iloc[-50:].mean()) >= MIN_AVG_VOL
    }
    print(f"  {len(data)} tickers after liquidity filter "
          f"(price>=${MIN_PRICE:.0f}, vol50>={MIN_AVG_VOL:,})", flush=True)

    print("[3/5] RS 점수 계산 및 백분위 랭킹...", flush=True)
    raw = {t: rs_raw_score(df["Close"]) for t, df in data.items()}
    raw = {t: v for t, v in raw.items() if v is not None}
    rs_rank = (pd.Series(raw).rank(pct=True) * 98 + 1).round(0)  # 1~99

    print("[4/5] 트렌드 템플릿 + VCP 스코어링...", flush=True)
    results = []
    for t, df in data.items():
        if t not in rs_rank.index:
            continue
        tt = trend_template(df, float(rs_rank[t]))
        if not tt["pass_all"]:
            continue
        results.append({
            "ticker": t,
            "name": names.get(t, ""),
            "rs_rank": int(rs_rank[t]),
            **{k: v for k, v in tt.items() if k != "pass_all"},
            "vcp": detect_vcp(df),
        })

    results.sort(key=lambda x: (-x["rs_rank"], -x["vcp"]["score"]))

    print(f"[5/5] 상위 {TOP_EXPORT}종목 가격 이력 내보내기 "
          "(5년, S&P 500 벤치마크 포함)...", flush=True)
    spx = yf.download("^GSPC", period="5y", interval="1d", auto_adjust=True,
                      progress=False)
    spx_close = spx["Close"].dropna()
    if isinstance(spx_close, pd.DataFrame):  # 단일 티커도 MultiIndex로 올 수 있음
        spx_close = spx_close.iloc[:, 0]
    export = results[:TOP_EXPORT]  # 저장소 크기 관리: 나머지는 TradingView 폴백
    data5 = download_history([r["ticker"] for r in export],
                             period="5y", min_len=1)
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    # 이전 실행의 잔여 파일 제거 (통과 종목이 바뀌므로)
    for old in HISTORY_DIR.glob("*.json"):
        old.unlink()
    for r in export:
        export_history(r["ticker"], data5.get(r["ticker"], data[r["ticker"]]),
                       spx_close)

    last_date = max(df.index[-1] for df in data.values())
    out = {
        "generated_at": pd.Timestamp.now().isoformat(),
        "data_date": str(last_date.date()),
        "universe": "US 전 종목 ($10+, 유동성 필터)",
        "universe_size": len(tickers),
        "analyzed": len(rs_rank),
        "passed": len(results),
        "results": results,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n통과 {len(results)}종목 / 분석 {len(rs_rank)}종목 -> {OUT_PATH}")


if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    main()
