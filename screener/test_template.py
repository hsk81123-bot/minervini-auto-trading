# -*- coding: utf-8 -*-
"""template_check.py 단위 테스트 (네트워크 없음).

    python screener/test_template.py

동일가중 바구니 수익, 교체 비용, 가격이 끊긴 종목 평가, 현금 주, 판정을 손으로
검산한 값과 맞춘다.
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import spx_validate as S
import template_check as T

ok = lambda m: print("  OK  " + m)

cal = ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05",
       "2024-01-08"]
idx = pd.to_datetime(cal)


def frame(opens, closes=None):
    o = pd.Series(opens, index=idx, dtype="float64")
    c = pd.Series(closes if closes else opens, index=idx, dtype="float64")
    return pd.DataFrame({"Open": o, "High": o, "Low": o, "Close": c})


px = {"A": frame([10, 10, 11, 12, 12, 12]),
      "B": frame([20, 20, 20, 18, 18, 18]),
      # D 는 01-04 부터 거래 없음 (상장폐지) — 마지막 종가 9 로 평가
      "D": frame([10, 10, 10, None, None, None], [10, 10, 9, None, None, None])}
spy_df = frame([100, 100, 101, 102, 103, 104])
spy = S.Spy(spy_df)
p = T.Prices(px, {"A", "B", "D", "ZZ"}, cal)

# 1) 진입일 = 신호 다음 거래일 · 시작/끝 가격
assert p.entry_day("2024-01-01") == "2024-01-02" and p.entry_day("2024-01-08") is None
assert p.start_px("D", "2024-01-04") is None and p.start_px("ZZ", "2024-01-02") is None
assert p.end_px("D", "2024-01-05") == 9.0                     # 끊긴 뒤 마지막 종가
ok("Prices: 다음 거래일 진입 · 시작가 없으면 제외 · 끊긴 종목은 마지막 종가")

# 2) 바구니: 01-01 신호 → 01-02 시가 ~ 01-03 신호의 다음날(01-04) 시가
hold = [("2024-01-01", {"A", "B"}), ("2024-01-03", {"A", "D"}),
        ("2024-01-04", set()), ("2024-01-05", {"B"})]
per = T.baskets(hold, p, spy)
assert [(x["start"], x["end"]) for x in per] == [
    ("2024-01-02", "2024-01-04"), ("2024-01-04", "2024-01-05"),
    ("2024-01-05", "2024-01-08")]
g0 = ((12 / 10 - 1) + (18 / 20 - 1)) / 2                     # (+20% − 10%) / 2
assert abs(per[0]["gross"] - g0) < 1e-12
assert abs(per[0]["net"] - ((1 - T.COST * 1.0) * (1 + g0) - 1)) < 1e-12
assert abs(per[0]["spy"] - (102 / 100 - 1)) < 1e-12
# 두 번째 주: D 는 01-04 시가가 없어 빠지고 A 만 (12 → 12)
assert per[1]["n"] == 1 and per[1]["gross"] == 0.0
wa, wb = 0.5 * 1.2 / (1 + g0), 0.5 * 0.9 / (1 + g0)          # 흘러간 비중
assert abs(per[1]["turnover"] - (abs(1 - wa) + wb)) < 1e-12
# 세 번째 주: 통과 종목 없음 → 현금, 이전 비중 전부 정리
assert per[2]["n"] == 0 and per[2]["gross"] == 0.0 and per[2]["turnover"] == 1.0
assert abs(per[2]["spy"] - (104 / 103 - 1)) < 1e-12
# 제외 목록
assert T.baskets(hold, p, spy, frozenset({"A"}))[0]["n"] == 1
assert set(per[0]["contrib"]) == {"A", "B"} and abs(per[0]["contrib"]["A"] - 0.1) < 1e-12
ok("baskets: 동일가중 시가~시가 · 흘러간 비중 기준 교체 비용 · 현금 주 · 제외")

# 3) 지표
cagr, mdd, tot = T.curve_stats([0.1, -0.2, 0.1], 365)
assert abs(tot - (1.1 * 0.8 * 1.1 - 1)) < 1e-12
assert abs(cagr - ((1 + tot) ** (365.25 / 365) - 1)) < 1e-12
assert abs(mdd - (-0.2)) < 1e-12
assert T.block_ci([0.01] * 3) is None
lo, hi = T.block_ci([0.01] * 20)
assert abs(lo - 0.01) < 1e-12 and abs(hi - 0.01) < 1e-12
xs = [0.01 * ((i % 5) - 2) for i in range(60)]
assert T.block_ci(xs) == T.block_ci(xs)                       # 시드 고정
ok("curve_stats · block_ci (짧으면 None, 상수면 그 값, 재현 가능)")

# 4) 요약·판정
weeks = []
for k in range(104):
    d0 = (pd.Timestamp("2019-01-07") + pd.Timedelta(weeks=k)).strftime("%Y-%m-%d")
    d1 = (pd.Timestamp("2019-01-07") + pd.Timedelta(weeks=k + 1)).strftime("%Y-%m-%d")
    weeks.append({"signal": d0, "start": d0, "end": d1, "n": 3, "gross": 0.004,
                  "net": 0.004, "spy": 0.002, "turnover": 0.1,
                  "contrib": {"X": 0.002, "Y": 0.001, "Z": 0.001}})
m = T.summarize(weeks)
assert m["cagr"] > m["spy_cagr"] and m["ci"][0] > 0 and m["cash_weeks"] == 0
assert set(m["years"]) == {"2019", "2020"} and m["years"]["2019"]["excess"] > 0
assert T.top_contributors(weeks) == ["X", "Y"]
assert T.summarize([]) == {}
ok("summarize: 연복리·SPY·블록 CI·연도별 초과 · top_contributors")

print("\n  전체 통과")
