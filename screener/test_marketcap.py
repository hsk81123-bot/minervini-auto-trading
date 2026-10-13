# -*- coding: utf-8 -*-
"""marketcap.py 단위 테스트 (네트워크 없음).

    python screener/test_marketcap.py

배당 조정을 순방향으로 적용한 값에서 실제 가격이 정확히 복원되는지, 분할·발행주식수가
섞인 시총이 손 계산과 같은지, 공시일 이전 값을 쓰지 않는지 확인한다.
"""
import random
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import marketcap as M

ok = lambda m: print("  OK  " + m)

# 1) 배당 조정 복원: 실제 종가 → Yahoo 식으로 순방향 조정 → 복원
rng = random.Random(5)
n = 400
raw = [50.0]
for _ in range(n - 1):
    raw.append(raw[-1] * (1 + rng.gauss(0.0004, 0.012)))
raw = np.array(raw)
div = np.zeros(n)
for k in range(30, n, 63):                                   # 분기 배당
    div[k] = 0.012 * raw[k - 1] * rng.uniform(0.7, 1.3)
div[0] = 0.0
adj = raw.copy()
for k in np.nonzero(div > 0)[0]:
    adj[:k] *= 1 - div[k] / raw[k - 1]                       # k 이전 모든 날에 곱한다
back = M.unadjust_close(adj, div)
assert np.max(np.abs(back / raw - 1)) < 1e-12, np.max(np.abs(back / raw - 1))
assert adj[0] < raw[0] * 0.96                                # 누적 배당이 실제로 큼
assert np.allclose(M.unadjust_close(adj, np.zeros(n)), adj)  # 배당 없으면 그대로
assert np.allclose(M.unadjust_close(raw[-3:], np.array([0, 0, 0.0])), raw[-3:])
ok("unadjust_close: 순방향 조정(분기 배당 13회)에서 실제 종가를 1e-12 로 복원")

# 2) 시총: 분할 + 발행주식수 + 배당 조정
days = pd.bdate_range("2023-01-02", periods=300)
cl_raw = np.full(300, 100.0)
cl_raw[150:] = 25.0                                          # 4:1 분할 후 가격 (분할 조정 전 단위)
# 야후식 분할 조정: 분할 전 가격을 4 로 나눈다
c_split = cl_raw.copy()
c_split[:150] = 25.0
dv = np.zeros(300)
dv[200] = 0.5                                                # 분할 뒤 배당 $0.5
adj2 = c_split.copy()
adj2[:200] *= 1 - 0.5 / c_split[199]
spl = np.zeros(300)
spl[150] = 4.0
df = pd.DataFrame({"Close": adj2, "Dividends": dv, "Splits": spl}, index=days)
shares = {"X": [[days[10].strftime("%Y-%m-%d"), days[5].strftime("%Y-%m-%d"), 1_000.0],
                [days[250].strftime("%Y-%m-%d"), days[245].strftime("%Y-%m-%d"), 4_100.0]]}
caps = M.Caps({"X": df}, shares)
d = lambda i: days[i].strftime("%Y-%m-%d")
# 분할 전 (i=100): 실제 가격 $100, 주식수 1000 (분할 전 기준) → 100,000
assert abs(caps.cap("X", d(100)) - 100_000) < 1e-6
# 분할 뒤 (i=160): 실제 $25, 주식수 1000×4 → 100,000 (시총은 분할로 안 변한다)
assert abs(caps.cap("X", d(160)) - 100_000) < 1e-6
# 새 공시 뒤 (i=260): 주식수 4100 (이미 분할 후), 가격 $25 → 102,500
assert abs(caps.cap("X", d(260)) - 102_500) < 1e-6
assert caps.cap("X", d(10)) is None                          # 공시일 당일은 아직 모른다
assert caps.cap("X", d(11)) is not None
assert caps.cap("Y", d(100)) is None                         # 가격/주식수 없는 종목
assert caps.undone == 1 and caps.adjusted_only == 0
# 배당 열이 없으면 조정 가격 그대로 (옛 캐시) — 표시만 다르다
caps2 = M.Caps({"X": df.drop(columns="Dividends")}, shares)
assert caps2.cap("X", d(100)) < 100_000 and caps2.adjusted_only == 1
got = caps.caps(["X", "Y"], d(260))
assert set(got) == {"X"}
ok("Caps.cap: 분할(전·후) · 새 공시 · 공시일 당일 제외 · 배당 되돌림 · 열 없으면 조정가")

# 3) 주식 종류가 여럿인 회사: 풀에 있는 종류 수로 나눠 두 번 세지 않는다
shares2 = {"G1": shares["X"], "G2": shares["X"], "H": shares["X"]}
px2 = {"G1": df, "G2": df, "H": df}
base = M.Caps(px2, shares2).caps(["G1", "G2", "H"], d(160))
split = M.Caps(px2, shares2, cik_of={"G1": "0001", "G2": "0001", "H": "0002"}).caps(
    ["G1", "G2", "H"], d(160))
assert base["G1"] == base["G2"] == base["H"] == 100_000
assert split["G1"] == split["G2"] == 50_000 and split["H"] == 100_000
one = M.Caps(px2, shares2, cik_of={"G1": "0001", "G2": "0001"}).caps(["G1", "H"], d(160))
assert one["G1"] == 100_000                                   # 풀에 한 종류뿐이면 그대로
ok("Caps.caps: 같은 CIK 의 여러 종류는 풀에 있는 수로 나눔")

# 4) 첫 XBRL 공시 이전: backfill 이면 첫 값을 분할 배수로 되돌려 쓴다 (미래 정보 — 근사 비중용)
early = {"X": [[d(170), d(165), 4_000.0]]}                      # 분할(150) 뒤 공시: 주식수 4000
assert M.Caps({"X": df}, early).cap("X", d(50)) is None
bf = M.Caps({"X": df}, early, backfill_days=400)
assert abs(bf.cap("X", d(50)) - 100_000) < 1e-6                # 4000 / 4(분할) × $100
assert bf.backfilled == 1 and bf.cap("X", d(171)) is not None and bf.backfilled == 1
assert M.Caps({"X": df}, early, backfill_days=1).cap("X", d(50)) is None
assert abs(bf.price("X", d(100)) - 100.0) < 1e-9               # 분할 전 실제 가격
assert abs(bf.price("X", d(160)) - 25.0) < 1e-9 and bf.price("X", "2000-01-01") is None
ok("backfill: 첫 공시 이전은 분할 배수로 되돌린 첫 값 · 기한 밖이면 None · price()")

# 5) 고립된 이상치(단위 오류) 제거 — 분할로 인한 정당한 점프·완만한 추세는 둔다
rows = [[f"2020-0{i}-01", f"2019-1{i}-01", v] for i, v in
        enumerate([1000.0, 1010.0, 1_000_000.0, 990.0, 985.0, 980.0], 1)]
kept, k = M.drop_share_outliers(rows, [1.0] * 6)
assert k == 1 and [r[2] for r in kept] == [1000.0, 1010.0, 990.0, 985.0, 980.0]
rows = [["a", "a", 1000.0], ["b", "b", 1010.0], ["c", "c", 4040.0], ["d", "d", 4000.0],
        ["e", "e", 3990.0]]
kept, k = M.drop_share_outliers(rows, [4.0, 4.0, 1.0, 1.0, 1.0])   # 4:1 분할 — 정상
assert k == 0 and len(kept) == 5
trend = [["x", "x", 1000.0 * 1.5 ** i] for i in range(6)]            # 해마다 50% 증가
assert M.drop_share_outliers(trend, [1.0] * 6)[1] == 0
assert M.drop_share_outliers(rows[:3], [1.0] * 3)[1] == 0            # 점이 적으면 손대지 않음
# Caps 가 실제로 이 값을 빼고 쓰는지: 한 점이 1000배인 종목
# 분할(150) 전 공시는 분할 전 단위(250), 후 공시는 1000 — 조정하면 모두 같은 값이어야 한다
bad = [[days[i].strftime("%Y-%m-%d"), days[i - 5].strftime("%Y-%m-%d"),
        250.0 if i - 5 < 150 else 1_000.0] for i in range(30, 300, 30)]
cb = M.Caps({"X": df}, {"X": bad})
assert abs(cb.cap("X", d(160)) - 25_000) < 1e-6                   # 250주 × 4 × $25
assert cb.dropped == 0                                               # 분할 점프는 정상
bad[3][2] = 1_000_000.0
cb2 = M.Caps({"X": df}, {"X": bad})
cb2.cap("X", d(160))
assert cb2.dropped == 1
dbg = cb2.debug("X", d(160))
assert dbg["ticker"] == "X" and dbg["n_div"] == 1 and dbg["cap"] is not None
ok("drop_share_outliers: 고립된 단위 오류만 제거 · 분할·추세·점 부족은 둠 · Caps 연결 · debug()")

print("\n  전체 통과")
