# -*- coding: utf-8 -*-
"""금리·신용스프레드 자료 경로 점검 (Actions 전용, 짧은 시간 제한). 성과는 계산하지 않는다.

    python screener/macro_probe.py
"""
import sys
import time

import requests

UA = {"User-Agent": "Mozilla/5.0 (research script)"}
URLS = [
    ("FRED csv BAA10Y", "https://fred.stlouisfed.org/graph/fredgraph.csv?id=BAA10Y"),
    ("FRED csv BAA10Y(cosd)", "https://fred.stlouisfed.org/graph/fredgraph.csv?id=BAA10Y&cosd=1986-01-01"),
    ("FRED txt BAA10Y", "https://fred.stlouisfed.org/data/BAA10Y.txt"),
    ("FRED csv T10Y3M", "https://fred.stlouisfed.org/graph/fredgraph.csv?id=T10Y3M"),
    ("FRED csv DGS10", "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10"),
    ("Treasury 2024 csv", "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
     "daily-treasury-rates.csv/2024/all?type=daily_treasury_yield_curve&field_tdr_date_value=2024&page&_format=csv"),
    ("Treasury 1995 csv", "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
     "daily-treasury-rates.csv/1995/all?type=daily_treasury_yield_curve&field_tdr_date_value=1995&page&_format=csv"),
    ("Fed H15 csv", "https://www.federalreserve.gov/datadownload/Output.aspx?rel=H15&series=bf17364827e38702b42a58cf8eaa3f78"
     "&lastobs=&from=&to=&filetype=csv&label=include&layout=seriescolumn"),
]


def probe_urls() -> None:
    for name, url in URLS:
        t0 = time.time()
        try:
            r = requests.get(url, timeout=(8, 25), headers=UA)
            head = r.text[:90].replace("\n", " | ")
            print(f"  {name:<24} HTTP {r.status_code} {len(r.content):>8}B {time.time() - t0:5.1f}s  {head}", flush=True)
        except Exception as e:
            print(f"  {name:<24} 실패 {type(e).__name__} {time.time() - t0:5.1f}s", flush=True)


def probe_yahoo() -> None:
    import yfinance as yf
    for tk in ("^TNX", "^IRX", "^FVX", "VWEHX", "VFITX", "VWESX", "VFICX", "HYG", "LQD", "JNK", "IEF"):
        try:
            d = yf.download(tk, start="1975-01-01", interval="1d", auto_adjust=False, progress=False)
            if hasattr(d.columns, "get_level_values"):
                d.columns = d.columns.get_level_values(0)
            d = d.dropna(subset=["Close"])
            print(f"  yahoo {tk:<7} {d.index[0].date() if len(d) else '-'} ~ {d.index[-1].date() if len(d) else '-'} {len(d):>6}행", flush=True)
        except Exception as e:
            print(f"  yahoo {tk:<7} 실패 {type(e).__name__}", flush=True)


if __name__ == "__main__":
    print("[URL 점검]")
    probe_urls()
    print("[Yahoo 점검]")
    probe_yahoo()
