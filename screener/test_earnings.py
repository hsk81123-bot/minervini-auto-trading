# -*- coding: utf-8 -*-
"""earnings.py 단위 테스트 (네트워크 없음).

    python screener/test_earnings.py

8-K Item 2.02 파싱, 발표일 창, SUE(손 계산), 보유 구간, 이벤트 스터디의 수익률 계산을
확인한다. ROADMAP 에 고정한 H4 파라미터가 코드 상수와 같은지도 본다.
"""
import sys
from datetime import date, timedelta
from pathlib import Path
from statistics import stdev

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import earnings as Ea
import spx_validate as S
import template_check as T

ok = lambda m: print("  OK  " + m)

# 0) 사전 등록 값
assert (Ea.SUE_MIN, Ea.SIGMA_MIN, Ea.MIN_PREV, Ea.N_PREV, Ea.WIN_LO, Ea.WIN_HI,
        Ea.HOLD_DAYS, Ea.EVAL_FROM) == (2.0, 0.02, 6, 8, 15, 75, 84, "2014-01-01")
road = (Path(__file__).resolve().parent.parent / "ROADMAP.md").read_text(encoding="utf-8")
for needle in ("SUE ≥ 2.0", "(15일, 75일]", "`σ ≥ 0.02`", "발표일 + 84일", "2014-01-01"):
    assert needle in road, needle
ok("사전 등록 파라미터가 ROADMAP.md 와 같다")

# 1) 8-K Item 2.02 만 (정정·다른 항목 제외)
blk = {"form": ["8-K", "8-K", "10-Q", "8-K/A", "8-K", "8-K"],
       "filingDate": ["2020-02-01", "2020-03-01", "2020-04-01", "2020-05-01", "2020-06-01",
                      "2020-07-01"],
       "items": ["2.02,9.01", "5.02", "", "2.02", "7.01, 2.02 ,9.01", "2.020"]}
assert Ea.parse_filings(blk) == ["2020-02-01", "2020-06-01"]
assert Ea.parse_filings({}) == []
ok("parse_filings: 8-K 의 Item 2.02 만 (정정·다른 항목·공백 포함 항목 처리)")

# 2) 발표일 창: 분기말 뒤 (15, 75] 일의 첫 제출
dts = ["2020-04-10", "2020-04-20", "2020-05-05", "2020-07-20"]
assert Ea.announce_date("2020-03-31", dts) == "2020-04-20"        # 4/10 은 15일 이내라 제외
assert Ea.announce_date("2020-03-31", ["2020-04-15"]) is None      # 정확히 15일은 제외
assert Ea.announce_date("2020-03-31", ["2020-04-16"]) == "2020-04-16"
assert Ea.announce_date("2020-03-31", ["2020-06-14"]) == "2020-06-14"   # 75일 포함
assert Ea.announce_date("2020-03-31", ["2020-06-15"]) is None
assert Ea.announce_date("2020-03-31", []) is None
ok("announce_date: (15, 75] 일 창의 첫 제출일")

# 3) SUE: 손 계산 (독립 계산과 대조)
seed = [1.0, 1.2, 0.9, 1.1]
d = [3 * x for x in (0.10, 0.12, 0.08, 0.11, 0.09, 0.10, 0.13, 0.07, 0.10, 0.11, 0.09,
                     0.50)]                                                # k = 4..15 (σ ≈ 0.04)
eps = list(seed)
for k, dk in zip(range(4, 16), d):
    eps.append(eps[k - 4] + dk)
ends = [(date(2020, 3, 31) + timedelta(days=round(91.31 * k))).isoformat() for k in range(16)]
qs = [{"end": e, "eps_diluted": v} for e, v in zip(ends, eps)]
sue = Ea.sue_series(qs)
assert set(sue) == {ends[k] for k in range(10, 16)}                # 이전 Δ 가 6개 이상일 때부터
prev = d[3:11]                                                     # k=7..14
assert abs(sue[ends[15]] - d[11] / stdev(prev)) < 1e-9
assert abs(sue[ends[10]] - d[6] / stdev(d[0:6])) < 1e-9            # k=10: 이전 6개 (k=4..9)
assert sue[ends[15]] > Ea.SUE_MIN
# σ 가 0.02 미만이면 제외 · 분기 공백이 있으면 제외 · EPS 없는 분기는 무시
flat_q = [{"end": e, "eps_diluted": 1.0 + 0.1 * (k // 4)} for k, e in enumerate(ends)]
assert Ea.sue_series(flat_q) == {}
gap = [q for k, q in enumerate(qs) if k not in (9, 10)]
assert all(e not in Ea.sue_series(gap) for e in ends[11:13])
assert Ea.sue_series([{"end": e, "eps_diluted": None} for e in ends]) == {}
ok("sue_series: 손 계산 일치 · 이전 6개 미만/σ<0.02/분기 공백이면 제외")

# 4) 이벤트와 보유 구간
dates_t = [(date.fromisoformat(e) + timedelta(days=30)).isoformat() for e in ends]
evs = Ea.events_for(qs, dates_t)
assert [e["end"] for e in evs] == [ends[k] for k in range(10, 16)]
assert evs[-1]["f"] == dates_t[15] and evs[-1]["sue"] > 2
idx = Ea.index_events({"A": evs, "B": []})
f = evs[-1]["f"]
assert Ea.active_names(idx, f, ["A", "B", "Z"]) == ["A"]                         # 발표일 당일부터
before = (date.fromisoformat(f) - timedelta(days=1)).isoformat()
assert "A" not in Ea.active_names(idx, before, ["A"])                            # 발표 전엔 없음
last = (date.fromisoformat(f) + timedelta(days=83)).isoformat()
after = (date.fromisoformat(f) + timedelta(days=84)).isoformat()
assert Ea.active_names(idx, last, ["A"]) == ["A"] and Ea.active_names(idx, after, ["A"]) == []
assert Ea.active_names(idx, f, ["B", "Z"]) == []                                 # 구성종목만
low = [dict(e, sue=1.9) for e in evs]
assert Ea.active_names(Ea.index_events({"A": low}), f, ["A"]) == []              # SUE < 2 제외
ok("events_for · active_names: 발표일 당일~83일 보유, 84일째 해제, SUE<2·비구성종목 제외")

# 5) 이벤트 스터디: 발표 다음 거래일 시가 → h 거래일 뒤 시가의 (종목 − SPY)
cal = [x.strftime("%Y-%m-%d") for x in pd.bdate_range("2024-01-01", periods=120)]
ix = pd.to_datetime(cal)
mk = lambda f_: pd.DataFrame({"Open": pd.Series([f_(k) for k in range(120)], index=ix),
                              "Close": pd.Series([f_(k) for k in range(120)], index=ix)})
px = {"A": mk(lambda k: 100.0 * 1.01 ** k), "B": mk(lambda k: 100.0 * 0.99 ** k)}
p = T.Prices(px, {"A", "B"}, cal)
spy = S.Spy(mk(lambda k: 100.0 * 1.001 ** k))
events = {"A": [{"f": cal[10], "end": "2023-12-31", "sue": 3.0}],
          "B": [{"f": cal[10], "end": "2023-12-31", "sue": -2.5},
                {"f": "2013-05-01", "end": "2013-03-31", "sue": 5.0}]}
rows = Ea.event_study(events, p, spy, lambda d_: {"A", "B"}, since="2014-01-01")
assert len(rows) == 2                                             # 2013 이벤트는 제외
ra = next(r for r in rows if r["ticker"] == "A")
assert ra["group"] == "hi" and abs(ra[5] - ((1.01 ** 5 - 1) - (1.001 ** 5 - 1))) < 1e-12
assert abs(ra[60] - ((1.01 ** 60 - 1) - (1.001 ** 60 - 1))) < 1e-12   # 진입 = cal[11] 시가 기준 비율
rb = next(r for r in rows if r["ticker"] == "B")
assert rb["group"] == "lo" and rb[20] < 0
# 발표 반응: 발표일(cal[10]) 전날 종가 → 다음날 종가
assert abs(ra["react"] - ((1.01 ** 2 - 1) - (1.001 ** 2 - 1))) < 1e-12
assert abs(rb["react"] - ((0.99 ** 2 - 1) - (1.001 ** 2 - 1))) < 1e-12
j = p.cols["A"]
assert Ea.reaction(p, spy, j, cal[0]) is None and Ea.reaction(p, spy, j, cal[-1]) is None
only = Ea.event_study(events, p, spy, lambda d_: {"A"}, since="2014-01-01")
assert [r["ticker"] for r in only] == ["A"]                        # 그 날 구성종목만
tab = Ea.study_table(rows)
assert tab[("hi", 5)]["n"] == 1 and tab[("lo", 60)]["mean"] < 0 and ("mid", 5) not in tab
assert tab[("hi", "react")]["mean"] > 0 > tab[("lo", "react")]["mean"]
assert Ea.bucket(2.0) == "hi" and Ea.bucket(-2.0) == "lo" and Ea.bucket(1.99) == "mid"
ok("event_study: 발표 다음 거래일 시가 기준 초과수익 · 구성종목·기간 필터 · 그룹 표")

print("\n  전체 통과")
