# -*- coding: utf-8 -*-
"""evalkit.py 단위 테스트 (네트워크 없음).

    python screener/test_evalkit.py

연복리·낙폭·Newey-West 회귀를 독립 계산과 맞추고, 합성 후보가 기준별로 정확히
통과/탈락하는지, 설계 탈락 후보의 보류 기간이 계산되지 않는지 확인한다.
"""
import json
import random
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import evalkit as E
import template_check as T

ok = lambda m: print("  OK  " + m)

# 1) 기본 통계
assert abs(E.compound([0.1, -0.2, 0.1]) - (1.1 * 0.8 * 1.1 - 1)) < 1e-12
assert abs(E.cagr([0.1], 365.25) - 0.1) < 1e-12 and E.cagr([], 100) is None
assert abs(E.cagr([0.21], 2 * 365.25) - 0.1) < 1e-12              # 2년에 21% = 연 10%
assert abs(E.max_dd([0.1, -0.2, 0.1, -0.1]) - (0.8712 / 1.1 - 1)) < 1e-12  # 1.1 고점 → 0.8712
assert E.max_dd([0.1, 0.1]) == 0.0
ok("compound · cagr · max_dd")

# 2) 회귀: 알파·베타는 최소제곱과 같고, lags=0 의 베타 표준오차는 손으로 낸 값과 같고,
#    양의 자기상관 잡음이면 Newey-West 가 표준오차를 키운다
rng = random.Random(3)
n = 600
x = [rng.gauss(0.002, 0.02) for _ in range(n)]
e, prev = [], 0.0
for _ in range(n):
    prev = 0.8 * prev + rng.gauss(0, 0.004)
    e.append(prev)
y = [0.001 + 1.5 * a + b for a, b in zip(x, e)]
r0 = E.nw_alpha_beta(y, x, lags=0)
X = np.column_stack([np.ones(n), x])
coef = np.linalg.lstsq(X, np.asarray(y), rcond=None)[0]
assert abs(r0["alpha_week"] - coef[0]) < 1e-12 and abs(r0["beta"] - coef[1]) < 1e-12
assert abs(r0["alpha_ann"] - coef[0] * 52) < 1e-10
xm = np.asarray(x) - np.mean(x)
res = np.asarray(y) - X @ coef
se_beta = np.sqrt(np.sum(xm ** 2 * res ** 2) / np.sum(xm ** 2) ** 2 * n / (n - 2))
assert abs(r0["beta"] / r0["t_beta"] - se_beta) < 1e-10
r4 = E.nw_alpha_beta(y, x, lags=4)
assert abs(r4["beta"] - r0["beta"]) < 1e-12
assert abs(r4["beta"] / r4["t_beta"]) > abs(r0["beta"] / r0["t_beta"])
assert E.nw_alpha_beta([0.1] * 5, [0.1] * 5) == {}                   # 표본 부족
ok("nw_alpha_beta: OLS 와 일치 · 손 계산한 표준오차 · 자기상관이면 표준오차가 커짐")

# 3) 합성 주간 달력 2005~2026
start = date(2005, 1, 7)
signal, d0, d1 = [], [], []
d = start
while d < date(2026, 9, 25):
    signal.append(d.isoformat())
    d0.append((d + timedelta(days=3)).isoformat())
    d1.append((d + timedelta(days=10)).isoformat())
    d += timedelta(days=7)
N = len(signal)
crisis = set()
for _, lo, hi in E.CRISES:
    ix = [i for i, s in enumerate(signal) if lo <= s <= hi]
    crisis |= set(ix[: min(6, len(ix))])
spy = [(-0.03 if i in crisis else 0.002 + 0.008 * np.sin(i / 7)) for i in range(N)]
rng = random.Random(11)
good = [r + 0.0015 + rng.gauss(0, 0.003) for r in spy]              # 알파 > 0, 베타 ≈ 1
pool = [r + 0.0004 for r in spy]
rsp = [r + 0.0002 for r in spy]


def frame(**series):
    f = E.Frame(signal, d0, d1).add("spy", spy).add("pool", pool).add("rsp", rsp)
    for k, v in series.items():
        f.add(k, v)
    return f


fr = frame(good=good)
hc = E.survivorship_haircut(fr)
assert all(abs(h - 0.0002) < 1e-12 for h in hc)                      # pool − rsp = 0.0002
assert E.survivorship_haircut(E.Frame(signal, d0, d1).add("pool", pool)) == [0.0] * N
fr2 = frame()
fr2.r["pool"] = [r - 0.001 for r in spy]                             # 우리가 RSP 보다 낮으면 0
assert E.survivorship_haircut(fr2) == [0.0] * N
ok("survivorship_haircut: 시대별 (풀 − RSP) 평균, 음수면 0, RSP 없으면 0")

m = E.evaluate(fr, "good")
assert m["passed"] and all(v is True for v in m["checks"].values()), m["checks"]
assert m["eras_won"] == 5 and m["eras_need"] == 4 and len(m["crisis"]) == 3
assert m["reg"]["t_alpha"] > E.T_MIN and 0.9 < m["reg"]["beta"] < 1.1
assert abs(m["haircut_ann"] - 0.0002 * 52) < 1e-9
assert m["cagr"] < m["cagr_raw"]                                     # 보정이 수익을 깎는다
ok("evaluate: 알파 있는 후보는 6개 기준 모두 통과 · 보정이 수익을 깎음")

# 4) 기준별로 정확히 하나씩 떨어지는 후보
lev = [1.6 * r + rng.gauss(0, 0.002) for r in spy]                   # 베타 1.6, 알파 0
m = E.evaluate(frame(lev=lev), "lev")
assert m["checks"]["alpha"] is False and m["checks"]["dd_full"] is False
assert not m["passed"]
below = [r - 0.0006 for r in good]                                   # 보정 뒤 SPY 보다 낮음
m = E.evaluate(frame(below=[r - 0.0030 for r in good]), "below")
assert m["checks"]["cagr"] is False and m["checks"]["alpha"] is False
# 시대 블록: 2024~ 에서만 이기는 후보
late = [r + (0.003 if s >= "2024-01-01" else -0.0008) for r, s in zip(spy, signal)]
m = E.evaluate(frame(late=late), "late")
assert m["eras_won"] == 1 and m["checks"]["eras"] is False
# 낙폭만 나쁜 후보: 수익은 높지만 위기 구간에서 훨씬 더 떨어진다
deep = [(1.0 + 0.0 if i not in crisis else 1.0) * r for i, r in enumerate(good)]
deep = [(-0.08 if i in crisis else r + 0.004) for i, r in enumerate(good)]
m = E.evaluate(frame(deep=deep), "deep")
assert m["checks"]["dd_crisis"] is False and m["checks"]["dd_full"] is False
# 같은 풀 동일가중에 지는 후보 (SPY 는 이김)
weak = [r + 0.0001 for r in spy]
fw = frame(weak=weak)
fw.r["pool"] = [r + 0.003 for r in spy]
fw.r["rsp"] = [r + 0.003 for r in spy]
m = E.evaluate(fw, "weak")
assert m["checks"]["pool"] is False
ok("evaluate: 레버리지 · 보정 후 열세 · 한 시대만 우위 · 위기 낙폭 · 풀에 짐 → 각 기준에서 탈락")

# 5) 설계 → 보류: 설계 통과 때만 보류를 계산한다
v = E.verdict(frame(good=good, lev=lev), "good")
assert v["design"]["passed"] and v["holdout"] is not None
assert v["holdout"]["from"] < "2013-01-01" and v["holdout"]["to"] <= "2013-01-18"
v = E.verdict(frame(lev=lev), "lev")
assert not v["design"]["passed"] and v["holdout"] is None
assert E.evaluate(frame(good=good), "good", "2026-08-01")["error"] == "주 수 부족"
ok("verdict: 설계 통과 → 보류 계산 · 설계 탈락 → 보류 None")

# 6) 가중치 → 수익 (손 계산)
cal = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2024-01-01", periods=8)]
idx = pd.to_datetime(cal)
fm = lambda opens: pd.DataFrame({"Open": pd.Series(opens, index=idx, dtype="float64"),
                                 "Close": pd.Series(opens, index=idx, dtype="float64")})
px = {"A": fm([10, 10, 11, 12, 12, 12, 12, 12]), "B": fm([20, 20, 20, 18, 18, 18, 18, 18])}
p = T.Prices(px, {"A", "B", "Z"}, cal)
sched = [(cal[0], {"A": 0.5, "B": 0.5}), (cal[2], {"A": 1.0}), (cal[4], {})]
sig, a0, a1, gross, net = E.run_weights(sched, p, 10.0)
assert sig == [cal[0], cal[2]] and a0 == [cal[1], cal[3]] and a1 == [cal[3], cal[5]]
g0 = 0.5 * 0.2 + 0.5 * (-0.1)
assert abs(gross[0] - g0) < 1e-12
assert abs(net[0] - ((1 - 0.001 * 1.0) * (1 + g0) - 1)) < 1e-12
wa, wb = 0.5 * 1.2 / (1 + g0), 0.5 * 0.9 / (1 + g0)
assert gross[1] == 0.0 and abs(net[1] - (-0.001 * (abs(1 - wa) + wb))) < 1e-12
# 가격 없는 종목은 빼고 남은 종목에 합계를 다시 나눈다 · 현금 비중은 그대로 현금
sg, _, _, gr, _ = E.run_weights([(cal[0], {"A": 0.5, "Z": 0.5}), (cal[2], {})], p, 0.0)
assert abs(gr[0] - 0.2) < 1e-12
sg, _, _, gr, _ = E.run_weights([(cal[0], {"A": 0.5}), (cal[2], {})], p, 0.0)
assert abs(gr[0] - 0.1) < 1e-12                                       # 50% 현금
assert E.ew(["A", "B"]) == {"A": 0.5, "B": 0.5} and E.ew([]) == {}
cw = E.cap_weights(["A", "B", "Z"], {"A": 3.0, "B": 1.0})
assert cw == {"A": 0.75, "B": 0.25}
ok("run_weights: 시가~시가 · 흘러간 비중 기준 비용 · 가격 없는 종목 · 현금 · ew · cap_weights")

# 7) 장부
with tempfile.TemporaryDirectory() as td:
    pth = Path(td) / "ledger.json"
    pth.write_text(json.dumps({"pre_registered": [{"count": 8}],
                               "exploratory": [{"count": 12}, {"count": 20}]}))
    s = E.ledger_summary(pth)
    assert s["n"] == 40 and 3.0 < s["bonferroni_z"] < 3.4 and s["t_min"] == 3.0
ok("ledger_summary: 시도 횟수와 Bonferroni z")

print("\n  전체 통과")
