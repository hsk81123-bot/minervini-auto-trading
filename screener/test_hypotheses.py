# -*- coding: utf-8 -*-
"""hypotheses.py 단위 테스트 (네트워크 없음).

    python screener/test_hypotheses.py

사전 등록한 규칙(H1 12-1 모멘텀 상위 10%, H2 템플릿 + SPY 보충, H3 시총 × 모멘텀 틸트)이
ROADMAP 에 적은 대로 동작하는지 손으로 계산한 값과 맞춘다.
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hypotheses as Hy
import template_check as T

ok = lambda m: print("  OK  " + m)

# 0) 사전 등록 값이 ROADMAP 과 같다
assert (Hy.MOM_LONG, Hy.MOM_SKIP, Hy.TOP_FRAC, Hy.H2_N_MIN, Hy.H3_LAMBDA, Hy.Z_CLIP) == \
    (252, 21, 0.10, 20, 0.25, 2.0)
road = (Path(__file__).resolve().parent.parent / "ROADMAP.md").read_text(encoding="utf-8")
for needle in ("상위 10%", "20 이상이면", "0.25", "±2"):
    assert needle in road, needle
ok("사전 등록 파라미터가 ROADMAP.md 와 같다")

# 1) 월 첫 신호일
assert Hy.monthly_flags(["2024-01-05", "2024-01-12", "2024-02-02", "2024-02-09",
                         "2025-01-03"]) == [True, False, True, False, True]
ok("monthly_flags")

# 2) 모멘텀 12-1: 손 계산
cal = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2023-01-02", periods=300)]
idx = pd.to_datetime(cal)
mk = lambda f: pd.DataFrame({"Open": pd.Series([f(k) for k in range(300)], index=idx),
                             "Close": pd.Series([f(k) for k in range(300)], index=idx)})
px = {"A": mk(lambda k: 100.0 + k), "B": mk(lambda k: 200.0 - 0.1 * k),
      "C": mk(lambda k: 50.0)}
late = mk(lambda k: 100.0)
late.iloc[:100] = float("nan")                         # 100번째 날에 상장 → 이력 부족
px["L"] = late
halt = mk(lambda k: 100.0 + k)
halt.iloc[299] = float("nan")                          # 신호일에 거래 없음
px["H"] = halt
p = T.Prices(px, set(px), cal)
m = Hy.momentum(p, cal[299], ["A", "B", "C", "L", "H", "ZZ"])
assert abs(m["A"] - (378 / 147 - 1)) < 1e-12           # 종가[278] / 종가[47] − 1
assert abs(m["B"] - ((200 - 27.8) / (200 - 4.7) - 1)) < 1e-12 and m["C"] == 0.0
assert set(m) == {"A", "B", "C"}                       # L(이력 부족) · H(거래 없음) · ZZ 제외
assert Hy.momentum(p, cal[100], ["A"]) == {}           # 252거래일 전 데이터 없음
ok("momentum: 12-1 손 계산 · 이력 부족·거래 없음·없는 종목 제외")

# 3) 상위 10% (올림, 최소 1)
mom = {f"T{k:02d}": float(k) for k in range(25)}
assert Hy.top_fraction(mom) == ["T24", "T23", "T22"]               # ceil(2.5) = 3
assert Hy.top_fraction({"A": 1.0}) == ["A"] and Hy.top_fraction({}) == []
assert Hy.top_fraction({"B": 1.0, "A": 1.0}, 0.5) == ["A"]         # 동점은 티커 순
ok("top_fraction: 올림 · 최소 1종목 · 동점은 티커 순")

# 4) H2: 통과 종목이 적을수록 SPY 로 채운다
w = Hy.h2_weights([f"S{k}" for k in range(10)])
assert abs(w["SPY"] - 0.5) < 1e-12 and abs(w["S0"] - 0.05) < 1e-12 and abs(sum(w.values()) - 1) < 1e-12
w = Hy.h2_weights([f"S{k}" for k in range(20)])
assert "SPY" not in w and abs(w["S0"] - 0.05) < 1e-12
w = Hy.h2_weights([f"S{k}" for k in range(40)])
assert "SPY" not in w and abs(w["S0"] - 0.025) < 1e-12
assert Hy.h2_weights([]) == {"SPY": 1.0}
w = Hy.h2_weights(["A"])
assert abs(w["A"] - 0.05) < 1e-12 and abs(w["SPY"] - 0.95) < 1e-12
ok("h2_weights: n=0 → SPY 100% · n=10 → 50/50 · n≥20 → SPY 없음")

# 5) H3 틸트
caps = {f"T{k:02d}": 100.0 + k for k in range(30)}
mom = {f"T{k:02d}": float(k) for k in range(30)}
base = Hy.E.cap_weights(list(caps), caps)
w0 = Hy.tilt_weights(caps, mom, lam=0.0)
assert all(abs(w0[t] - base[t]) < 1e-12 for t in base)             # λ=0 이면 시총 가중 그대로
w = Hy.tilt_weights(caps, mom)
assert abs(sum(w.values()) - 1) < 1e-12 and all(v >= 0 for v in w.values())
assert w["T29"] / base["T29"] > 1 > w["T00"] / base["T00"]         # 모멘텀 높으면 올리고 낮으면 내림
assert 0 < Hy.active_share(w, base) < 0.2                          # 약간만 가감
# 클립: z 가 아무리 커도 최대 1 + 0.25 × 2 = 1.5 배 (정규화 전)
mom2 = dict(mom)
mom2["T29"] = 1e9
w2 = Hy.tilt_weights(caps, mom2)
raw = {t: caps[t] * (1 + 0.25 * max(-2, min(2, (mom2[t] - sum(mom2.values()) / 30) /
                                              (sum((x - sum(mom2.values()) / 30) ** 2
                                                   for x in mom2.values()) / 30) ** 0.5)))
       for t in caps}
tot = sum(raw.values())
assert all(abs(w2[t] - raw[t] / tot) < 1e-12 for t in caps)
# 종목이 적으면 순수 시총 가중 · 모멘텀 없는 종목은 z=0
few = {"A": 1.0, "B": 3.0}
assert Hy.tilt_weights(few, {"A": 1.0, "B": 2.0}) == {"A": 0.25, "B": 0.75}
nom = Hy.tilt_weights(caps, {t: v for t, v in mom.items() if t != "T10"})
ms = [v for t, v in mom.items() if t != "T10"]
mu = sum(ms) / len(ms)
sd = (sum((x - mu) ** 2 for x in ms) / len(ms)) ** 0.5
raw = {t: caps[t] * (1 + 0.25 * (0.0 if t == "T10" else
                                  max(-2, min(2, (mom[t] - mu) / sd)))) for t in caps}
tot = sum(raw.values())
assert all(abs(nom[t] - raw[t] / tot) < 1e-12 for t in caps)       # 모멘텀 없는 종목은 z=0
assert Hy.active_share({"A": 1.0}, {"B": 1.0}) == 1.0 and Hy.active_share(base, base) == 0.0
ok("tilt_weights: λ=0 → 시총 가중 · 모멘텀 방향 · ±2 클립 · 종목 적으면 시총 가중 · active_share")


# 6) 스케줄: 월 첫 주만 H1·H3 에 비중, 나머지는 None(거래 없음) · H2 는 매주
class FakeCaps:
    def caps(self, names, d):
        return {t: 100.0 + i for i, t in enumerate(sorted(names))}


sigs = [cal[260], cal[267], cal[274], cal[281]]
snaps = [{"date": s, "rows": {"A": {}, "B": {}}} for s in sigs]
members = {s: {"A", "B", "C"} for s in sigs}
ctx = {"p": p, "caps": FakeCaps(), "members": members, "snaps": snaps}
sched, st = Hy.schedules(ctx)
flags = Hy.monthly_flags(sigs)
assert [w is not None for _, w in sched["h1"]] == flags == [w is not None for _, w in sched["h3"]]
assert all(w is not None for _, w in sched["h2"])
assert sched["h2"][0][1]["SPY"] > 0.8 and "A" in sched["h2"][0][1]   # 통과 2종목 → 거의 SPY
assert sched["h1"][0][1] == {"A": 1.0} and st["h1_avg_names"] == 1.0   # 3종목의 10% → 1종목
ok("schedules: H1·H3 는 월 첫 신호일만, 나머지는 거래 없음 · H2 는 매주")

print("\n  전체 통과")
