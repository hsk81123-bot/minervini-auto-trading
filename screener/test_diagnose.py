# -*- coding: utf-8 -*-
"""diagnose.py 단위 테스트 (네트워크 없음).

    python screener/test_diagnose.py

주간 표, 향후 수익, 가격 이상치 탐지, 종목 기여의 합이 격차와 같은지, 조건 빼기의
기준 변형이 스냅샷과 같은지를 손으로 검산한 값과 맞춘다.
"""
import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import diagnose as D
import spx_validate as S
import template_check as T

ok = lambda m: print("  OK  " + m)

cal = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2024-01-01", periods=12)]
idx = pd.to_datetime(cal)


def frame(opens, closes=None, vol=1000.0):
    o = pd.Series(opens, index=idx, dtype="float64")
    c = pd.Series(closes if closes else opens, index=idx, dtype="float64")
    return pd.DataFrame({"Open": o, "High": o, "Low": o, "Close": c, "Volume": vol})


px = {"A": frame([10.0 + i for i in range(12)]),            # 꾸준히 상승
      "B": frame([20.0] * 12),
      "C": frame([10.0] * 6 + [30.0] + [10.0] * 5)}         # 하루 +200% 뒤 되돌림
spy = S.Spy(frame([100.0 + i for i in range(12)]))
p = T.Prices(px, set(px), cal)
# 신호: 0, 2, 4, 6, 8 번째 날 · 통과: A 는 첫 두 주만, B 는 계속
snaps = [{"date": cal[i], "rows": rows} for i, rows in
         ((0, {"A": {"rs": 90}, "B": {"rs": 80}}), (2, {"A": {"rs": 90}}),
          (4, {"B": {"rs": 80}}), (6, {"B": {"rs": 80}}), (8, {}))]
weeks = D.build_weeks(snaps, lambda d: {"A", "B", "C"}, p, spy)

# 1) 주간 표: 진입은 신호 다음날 시가, 끝은 다음 신호의 다음날 시가
assert [(w["d0"], w["d1"]) for w in weeks] == [(cal[1], cal[3]), (cal[3], cal[5]),
                                               (cal[5], cal[7]), (cal[7], cal[9])]
w0 = weeks[0]
assert abs(w0["rets"]["A"] - (13 / 11 - 1)) < 1e-12 and w0["rets"]["B"] == 0.0
assert w0["pass"] == {"A", "B"} and abs(D.rp(w0) - (13 / 11 - 1) / 2) < 1e-12
assert abs(D.rb(w0) - (13 / 11 - 1) / 3) < 1e-12
assert abs(w0["spy"] - (103 / 101 - 1)) < 1e-12
assert D.rp({"rets": {"A": 0.1}, "pass": set()}) == 0.0          # 현금 주
assert abs(D.ann([0.01] * 52) - (1.01 ** 52 - 1)) < 1e-12 and D.ann([]) is None
ok("build_weeks · rp/rb · 현금 주 · ann")

# 2) 향후 수익: k 주에서 h 주 뒤 끝까지
fw = D.Forward(weeks, p)
f = fw.get(0, 2)
assert abs(f["A"] - (15 / 11 - 1)) < 1e-12                       # cal[1] → cal[5]
assert fw.get(3, 2) is None
past = fw.past(2, 2, {"A"})
assert abs(past["A"] - (15 / 11 - 1)) < 1e-12 and fw.past(0, 1, {"A"}) == {}
ok("Forward.get / past")

# 3) 가격 이상치: C 는 +200% 뒤 되돌림 → 플래그 · A·B 는 아님
fl = D.daily_flags(px, {"A", "B", "C"}, cal[0], cal[-1])
assert set(fl) == {"C"} and fl["C"][0][0] == cal[6]
big = D.biggest_day(px, "A", cal[0], cal[-1])
assert big[0] == cal[1] and abs(big[1] - 0.1) < 1e-12
assert D.classify(True, big) == "데이터 의심"
assert D.classify(False, ("x", 0.2)).startswith("사건")
assert D.classify(False, ("x", 0.05)).startswith("구조")
ok("daily_flags · biggest_day · classify")

# 4) 종목 기여의 합 = Σ 주간 격차 (보유 + 미보유 = 기여)
buf = io.StringIO()
with redirect_stdout(buf):
    s2 = D.section2(weeks, px, set(fl), None)
total = sum(x["contrib"] for x in s2["hurt"] + s2["helped"])
names = {x["ticker"] for x in s2["hurt"] + s2["helped"]}
assert names == {"A", "B", "C"}                                  # 셋 다 나온다
gap = sum(D.rp(w) - D.rb(w) for w in weeks)
assert abs(sum(x["contrib"] for x in s2["hurt"]) - gap) < 1e-12
for x in s2["hurt"]:
    assert abs(x["held"] + x["not_held"] - x["contrib"]) < 1e-12
assert {x["ticker"]: x["class"] for x in s2["hurt"]}["C"] == "데이터 의심"
assert "2-2" in buf.getvalue()
ok("section2: 종목 기여 합 = 격차 · 보유+미보유 · 플래그 종목 분류")

# 5) 조건 빼기: 8개 전부면 스냅샷과 같아야 한다 (일치 주 수를 센다)
cond = {w["signal"]: pd.DataFrame({k: [t in w["pass"] for t in sorted(w["rets"])]
                                   for k, _ in D.CONDS}, index=sorted(w["rets"]))
        for w in weeks}
with redirect_stdout(io.StringIO()):
    ab = D.ablation_report(weeks, cond)
assert ab["match_weeks"] == ab["weeks"] == len(weeks)
assert ab["variants"]["8개 전부 (기준)"][0] == D.ann([D.rp(w) for w in weeks])
ok("ablation_report: 기준 변형 = 스냅샷 통과 종목")

print("\n  전체 통과")
