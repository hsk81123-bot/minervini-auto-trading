# -*- coding: utf-8 -*-
"""단일 종목 5년 이력 + 분석 메타(VCP·트렌드 템플릿)를 온디맨드로 생성.

사용법: python screener/ondemand.py AAPL
        python screener/ondemand.py 000660.KS

웹앱의 '자체 차트 생성' 버튼이 GitHub Actions(ondemand.yml)로 호출한다.
RS 순위는 전 종목 비교가 필요해 단일 종목으로는 계산하지 않는다(None).
"""
import sys
from pathlib import Path

import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parent))
from screener import CFG, DATA_DIR, export_history, trend_template  # noqa: E402
from vcp import detect_vcp  # noqa: E402


def _download(ticker: str) -> pd.DataFrame:
    df = yf.download(ticker, period="5y", interval="1d", auto_adjust=True,
                     progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df.dropna(subset=["Close"])


def main() -> None:
    ticker = sys.argv[1].strip().upper()
    # 접미사 없는 6자리 한국 코드 → .KQ/.KS 순차 시도
    if ticker.isdigit() and len(ticker) == 6:
        for suf in (".KQ", ".KS"):
            df = _download(ticker + suf)
            if len(df) >= 60:
                ticker += suf
                break
        else:
            sys.exit(f"no data for Korean code {ticker} (.KQ/.KS both empty)")
    else:
        df = _download(ticker)
    market = "kr" if ticker.endswith((".KS", ".KQ")) else "us"
    cfg = CFG[market]

    if len(df) < 60:
        sys.exit(f"not enough data for {ticker} ({len(df)} rows)")

    bench = yf.download(cfg["bench"], period="5y", interval="1d",
                        auto_adjust=True, progress=False)
    bc = bench["Close"].dropna()
    if isinstance(bc, pd.DataFrame):
        bc = bc.iloc[:, 0]

    meta = None
    if len(df) >= 260:  # 1년 미만 상장 종목은 지표 계산 불가 → 차트만
        tt = trend_template(df, rs_rank=0)
        meta = {k: v for k, v in tt.items() if k != "pass_all"}
        meta["checks"]["rs_rank_70plus"] = None  # 단일 종목으로는 측정 불가
        meta["rs_rank"] = None
        meta["vcp"] = detect_vcp(df)
        meta["ondemand"] = True

    hist_dir = DATA_DIR / cfg["hist"]
    hist_dir.mkdir(parents=True, exist_ok=True)
    export_history(ticker, df, bc, hist_dir,
                   extra={"meta": meta} if meta else None)
    print(f"exported {ticker} -> {cfg['hist']}/{ticker}.json "
          f"(meta={'yes' if meta else 'no'})")


if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    main()
