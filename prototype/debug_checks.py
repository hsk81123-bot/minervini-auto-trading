# -*- coding: utf-8 -*-
"""개별 조건별 통과율 확인용 디버그 스크립트 (소수 종목)."""
import sys

import pandas as pd
import yfinance as yf

TICKERS = ["NVDA", "MSFT", "AAPL", "META", "AVGO", "LLY", "COST", "GE",
           "PLTR", "NFLX", "AMZN", "GOOGL", "TSLA", "JPM", "WMT"]

df = yf.download(TICKERS, period="2y", interval="1d", auto_adjust=True,
                 group_by="ticker", threads=True, progress=False)

sys.stdout.reconfigure(encoding="utf-8")
for t in TICKERS:
    sub = df[t].dropna(how="all")
    close, high, low = sub["Close"], sub["High"], sub["Low"]
    n = len(sub)
    c = close.iloc[-1]
    ma50 = close.rolling(50).mean()
    ma150 = close.rolling(150).mean()
    ma200 = close.rolling(200).mean()
    m50, m150, m200 = ma50.iloc[-1], ma150.iloc[-1], ma200.iloc[-1]
    low52 = low.iloc[-252:].min()
    high52 = high.iloc[-252:].max()
    checks = [
        c > m150 and c > m200,
        m150 > m200,
        m200 > ma200.iloc[-22],
        m50 > m150 and m50 > m200,
        c > m50,
        c >= low52 * 1.30,
        c >= high52 * 0.75,
    ]
    print(f"{t:<6} n={n:<4} close={c:>9.2f} ma50={m50:>9.2f} ma150={m150:>9.2f} "
          f"ma200={m200:>9.2f} lo52={low52:>9.2f} hi52={high52:>9.2f} "
          f"checks={''.join('O' if x else 'X' for x in checks)}")
print("\nindex tail:", df.index[-3:].tolist())
