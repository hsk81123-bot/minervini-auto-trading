# -*- coding: utf-8 -*-
"""VCP 감지 정답지 테스트 — 책 <그림 7-5> NFLX 2009-10-23 (27주 베이스) 재현."""
import json
import sys
from pathlib import Path

import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "screener"))
from vcp import detect_vcp  # noqa: E402

CASES = [
    ("NFLX", "2008-09-01", "2009-10-24"),  # 책 사례: 고전적 VCP
    ("NFLX", "2009-03-01", "2009-08-01"),  # 베이스 중반 시점 (덜 완성된 상태)
]

sys.stdout.reconfigure(encoding="utf-8")
for ticker, start, end in CASES:
    df = yf.download(ticker, start=start, end=end, auto_adjust=True,
                     progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.dropna(subset=["Close"])
    r = detect_vcp(df)
    print(f"\n===== {ticker} {start} ~ {end} (기준일 종가 "
          f"${float(df['Close'].iloc[-1]):.2f}) =====")
    print(f"score={r['score']} count={r['count']} pivot={r['pivot']} "
          f"stop={r['stop']} dryup={r['dryup']} vol_declining={r['vol_declining']} "
          f"base_days={r['base_days']}")
    for i, c in enumerate(r["contractions"], 1):
        print(f"  T{i}: {c['hi_date']} ${c['high']} → {c['lo_date']} ${c['low']}"
              f"  깊이 -{c['depth_pct']}%  거래량비 {c['vol_ratio']}")
