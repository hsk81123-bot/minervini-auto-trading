# -*- coding: utf-8 -*-
"""candidates.py 단위 테스트 (네트워크 없음).

    python screener/test_candidates.py

후보 선정이 그 날까지의 데이터만 쓰는지, 돌파 확인·손절폭 계산이 정한 대로인지,
설계 기간에서 탈락한 후보의 보류 기간을 계산하지 않는지 확인한다.
"""
import gzip
import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import candidates as C
import history as H

ok = lambda m: print("  OK  " + m)


def frame(closes, vols=None, start="2023-01-02", spread=0.01):
    idx = pd.bdate_range(start, periods=len(closes))
    c = pd.Series(closes, index=idx, dtype="float64")
    return pd.DataFrame({"Open": c, "High": c * (1 + spread),
                         "Low": c * (1 - spread), "Close": c,
                         "Volume": pd.Series(vols or [1000.0] * len(closes),
                                             index=idx, dtype="float64")})


# 1) 돌파일 거래량 기준은 '전날까지' 50일 평균 — 그 날 거래량이 섞이지 않는다
vols = [1000.0] * 60
vols[55] = 100000.0
a = C.aux_indicators(frame([10.0] * 60, vols))
d55 = a.index[55]
assert a.loc[d55, "vol50_prev"] == 1000.0
assert a.loc[a.index[56], "vol50_prev"] > 1000.0
assert pd.isna(a["vol50_prev"].iloc[49]) and a["vol50_prev"].iloc[50] == 1000.0
assert abs(a["atr_pct"].iloc[20] - 0.02) < 1e-12           # 고저 ±1% → TR 2%
ok("aux_indicators: 전날까지 50일 평균 거래량 · ATR%")

# 2) 돌파 확인: 종가 > 피봇 이고 거래량 ≥ 1.5배, 신호 다음 5거래일 안의 첫 날
closes = [10.0] * 60 + [10.5, 10.8, 10.9, 11.0, 11.2, 11.5, 11.6]
vols = [1000.0] * 67
vols[61] = 1400.0          # 피봇 위지만 거래량 부족
vols[62] = 1600.0          # 첫 확인일
vols[63] = 5000.0
a = C.aux_indicators(frame(closes, vols))
sig = a.index[59].strftime("%Y-%m-%d")
assert C.breakout_day(a, sig, 10.6) == a.index[62].strftime("%Y-%m-%d")
vols = [1000.0] * 67
vols[59] = 9000.0          # 신호일 당일의 큰 거래량은 돌파가 아니다
a = C.aux_indicators(frame(closes[:60] + [10.0] * 7, vols))
assert C.breakout_day(a, sig, 9.9) is None                 # 종가가 피봇 위지만 거래량 없음
vols = [1000.0] * 67
vols[65] = 5000.0          # 6번째 거래일 — 창 밖
a = C.aux_indicators(frame(closes, vols))
assert C.breakout_day(a, sig, 10.6) is None
ok("breakout_day: 거래량 1.5배 · 신호 다음 5거래일 · 신호일 제외")

# 3) 손절폭 묶기
assert C.c4_stop(0.01) == 4.0 and C.c4_stop(0.03) == 6.0 and C.c4_stop(0.2) == 15.0
assert C.c5_stop(100.0, None) == 8.0                     # 저점 없음
assert C.c5_stop(100.0, 101.0) == 8.0                    # 저점이 피봇 위
assert C.c5_stop(100.0, 99.5) == 2.0                     # 너무 촘촘 → 2%
assert abs(C.c5_stop(100.0, 95.0) - 5.0) < 1e-9
assert C.c5_stop(100.0, 80.0) == 8.0                     # 너무 넓음 → 8%
ok("c4_stop 4~15% · c5_stop 2~8%")

# 4) C3 · C4 선정
idx = pd.bdate_range("2023-01-02", periods=60)
day = idx[-1].strftime("%Y-%m-%d")


def aux_of(close, ma50, atr=0.02):
    return pd.DataFrame({"close": close, "ma50": ma50, "atr_pct": atr,
                         "vol50_prev": 1000.0, "volume": 1000.0}, index=idx)


aux = {"A": aux_of(109, 100), "B": aux_of(111, 100), "C": aux_of(105, 100),
       "D": aux_of(101, 100), "E": aux_of(100, 100, 0.05)}
snap = {"date": day, "rows": {
    "A": {"rs": 90, "raw": 2.0}, "B": {"rs": 99, "raw": 3.0},
    "C": {"rs": 79, "raw": 1.0}, "D": {"rs": 90, "raw": 2.5},
    "E": {"rs": 95, "raw": 4.0}}}
p3 = C.pick_c3(snap, aux)
assert [p["ticker"] for p in p3] == ["E", "D", "A"]       # B 과열 · C RS 미달
assert all(p["stop"] == C.BASE_STOP and p["entry_date"] == day for p in p3)
p4 = C.pick_c4(snap, aux)
# 점수 = 원점수 / ATR%: B 150, D 125, A 100, E 80, C 50
assert [p["ticker"] for p in p4] == ["B", "D", "A", "E", "C"]
assert p4[3]["stop"] == 10.0 and p4[0]["stop"] == 4.0
ok("pick_c3: RS ≥ 80 · 50일선 +10% 이내 · pick_c4: 원점수/ATR 순")

# 5) C5: 셋업은 RS 순 상위 5 를 먼저 고르고, 그중 돌파한 것만 산다
n = 70
base = [10.0] * 60
up = base + [10.4, 10.5, 10.5, 10.5, 10.5, 10.5, 10.5, 10.5, 10.5, 10.5]
flat = base + [9.8] * 10
v_up = [1000.0] * n
v_up[60] = 3000.0
prices = {}
rows = {}
for i, t in enumerate(["S1", "S2", "S3", "S4", "S5", "S6"]):
    brk = t in ("S2", "S6")
    prices[t] = frame(up if brk else flat, v_up if brk else None)
    rows[t] = {"rs": 99 - i, "raw": 1.0, "vcp": 50, "pivot": 10.2, "vstop": 9.69}
rows["S7"] = {"rs": 99, "raw": 1.0, "vcp": 50, "pivot": 11.0, "vstop": 9.0}  # 5% 넘게 아래
prices["S7"] = frame(flat)
rows["S8"] = {"rs": 70, "raw": 1.0, "vcp": 50, "pivot": 10.2, "vstop": 9.69}  # RS 미달
prices["S8"] = frame(up, v_up)
aux5 = {t: C.aux_indicators(df) for t, df in prices.items()}
prices5 = prices
sig = prices["S1"].index[59].strftime("%Y-%m-%d")
called = []
filled, n_setup = C.pick_c5({"date": sig, "rows": rows}, aux5, prices,
                            lambda df, d: called.append(d) or {})
assert n_setup == 5 and not called                        # S6 은 6번째라 셋업 밖
assert [f["ticker"] for f in filled] == ["S2"]
assert filled[0]["entry_date"] == prices["S2"].index[60].strftime("%Y-%m-%d")
assert abs(filled[0]["stop"] - 5.0) < 1e-9                 # 10.2 → 9.69
# 재구성 때 VCP 를 안 잰 종목만 그 날까지의 데이터로 잰다
rows2 = {"S2": {"rs": 90, "raw": 1.0, "vcp": None, "pivot": None, "vstop": None}}
seen = []


def fake_vcp(df, d):
    seen.append((len(df), d))
    return {"pivot": 10.2, "stop": 9.69, "score": 50}


filled, n_setup = C.pick_c5({"date": sig, "rows": rows2}, aux5, prices, fake_vcp)
assert seen == [(n, sig)] and [f["ticker"] for f in filled] == ["S2"]
ok("pick_c5: RS 순 셋업 5 → 돌파 확인 → 다음날 시가 · VCP 저점 손절")

# 6) 종목별 손절폭으로 시뮬레이션 — 익절은 손절폭 × 3
rows_o = [{"date": "2024-01-02", "open": 100, "high": 101, "low": 99, "close": 100},
          {"date": "2024-01-03", "open": 100, "high": 101, "low": 99, "close": 100},
          {"date": "2024-01-04", "open": 101, "high": 113, "low": 100, "close": 112}]
ohlc = {"X": rows_o, "Y": rows_o}
index = {k: [r["date"] for r in v] for k, v in ohlc.items()}
tr = C.simulate_picks([("2024-01-02", [
    {"ticker": "X", "stop": 4.0, "entry_date": "2024-01-02"},
    {"ticker": "Y", "stop": 5.0, "entry_date": "2024-01-02"},
    {"ticker": "Z", "stop": 5.0, "entry_date": "2024-01-02"}])], ohlc, index)
assert [t["ticker"] for t in tr] == ["X", "Y"]             # 가격 없는 Z 는 빠진다
assert tr[0]["status"] == "익절" and abs(tr[0]["r_multiple"] - 3.0) < 1e-9
assert tr[1]["status"] == "보유" and tr[1]["cohort"] == "2024-01-02"
ok("simulate_picks: 종목별 손절폭 · 3R 익절 · 가격 없으면 제외")


# 7) 판정 기준
def T(tk, r, cohort):
    return {"ticker": tk, "cohort": cohort, "r_multiple": r, "status": "보유",
            "ret": r * 0.08, "mae": -0.02, "mfe": 0.05, "days": 10}


good = [T(f"G{i}", 1.0 + (i % 3) * 0.1, f"{2019 + i % 4}-03-01") for i in range(40)]
rnd = [[T(f"R{i}", -0.2 + s * 0.001, f"{2019 + i % 4}-03-01") for i in range(40)]
       for s in range(50)]
ev = C.evaluate(good, rnd, 0.99)
assert ev["passed"] and ev["percentile"] == 1.0 and ev["years_pos"] == 4
bad = good[:-1] + [T("BIG", 200.0, "2019-03-01")]
bad = [dict(t, r_multiple=-1.0) if t["ticker"] != "BIG" else t for t in bad]
ev = C.evaluate(bad, rnd, 0.99)
assert ev["checks"]["positive"] and not ev["checks"]["survives_top2"]
assert ev["checks"]["ci"] is False and not ev["passed"]
assert C.in_period(good, "2020-01-01", "2020-12-31") == \
    [t for t in good if t["cohort"].startswith("2020")]
ok("evaluate: 5개 기준 · 한 종목 의존이면 탈락")

# 8) 펀더멘털 후보 — 조건·순서·C1+C5 한정
F = lambda e, r, o=None, a=None: {"eps_growth": e, "revenue_growth": r,
                                  "opm_change": o, "eps_accel": a}
assert C.c1_ok(F(0.25, 0.20)) and not C.c1_ok(F(0.24, 0.5))
assert not C.c1_ok(F(0.5, None)) and not C.c1_ok(None)
assert C.c2_ok(F(None, None, 0.01, True)) and not C.c2_ok(F(1, 1, 0.0, True))
assert not C.c2_ok(F(1, 1, 0.05, None)) and not C.c2_ok(None)
fund = {"A": F(0.30, 0.25, 0.01, True), "B": F(0.90, 0.30), "C": F(0.30, 0.10),
        "D": F(0.30, 0.40, 0.02, True), "E": None}
asked = []
fund_at = lambda t, d: asked.append(d) or fund.get(t)
p1 = C.pick_c1(snap, fund_at)
assert [p["ticker"] for p in p1] == ["B", "A", "D"]     # EPS 성장 → RS → 티커
assert set(asked) == {day} and p1[0]["stop"] == C.BASE_STOP
assert [p["ticker"] for p in C.pick_c2(snap, fund_at)] == ["D", "A"]  # 같은 RS → 원점수
filled, n_setup = C.pick_c5({"date": sig, "rows": rows}, aux5, prices5,
                            lambda df, d: {}, allow=lambda t: t != "S2")
assert n_setup == 5 and [f["ticker"] for f in filled] == ["S6"]   # S2 대신 S6 이 셋업에
cov = C.coverage([{"date": "2023-05-05", "rows": snap["rows"]}], fund_at)
assert cov == {"2023": (5, 4, 3, 2)}
ok("c1_ok·c2_ok · pick_c1 EPS 성장 순 · pick_c2 RS 순 · C1+C5 셋업 한정 · 커버리지")

# 9) 설계 기간에서 탈락하면 보류 기간을 계산하지 않는다 (끝까지 돌려 본다)
days = pd.bdate_range("2019-01-02", "2024-06-28")
prices = {}
for k in range(8):
    c = pd.Series([50.0 + k] * len(days), index=days)
    prices[f"T{k}"] = pd.DataFrame({"Open": c, "High": c * 1.01, "Low": c * 0.99,
                                    "Close": c, "Volume": 1000.0}, index=days)
weekly = [d for d in days if d.weekday() == 4][60::8]
snaps = [{"date": d.strftime("%Y-%m-%d"), "weekly": True,
          "rows": {f"T{k}": {"rs": 99 - k, "raw": 1.0 + k * 0.01, "vcp": 10,
                             "pivot": 60.0, "vstop": 40.0} for k in range(8)}}
         for d in weekly]
here = Path(__file__).resolve().parent
orig = H.load_prices, H.load_snaps, C.N_PERM, C.PF.TIMELINE
H.load_prices = lambda: {"prices": prices}
H.load_snaps = lambda: snaps
C.N_PERM = 5
out = here / ".test_candidates.json"
tl_path = here / ".test_fund_pit.json.gz"
tl_rows = [[f"{y + 1}-01-01", f"{y}-12-31", 0.5, 0.3, None, None]
           for y in range(2018, 2024)]
# T0~T3 이 해마다 1월 1일에 EPS +50%·매출 +30% 를 공시했다 (가속·마진은 모름)
with gzip.open(tl_path, "wt", encoding="utf-8") as f:
    json.dump({f"T{k}": tl_rows for k in range(4)}, f)
docs, logs = [], []
try:
    for tl in (here / ".없음", tl_path):
        C.PF.TIMELINE = tl
        buf = io.StringIO()
        with redirect_stdout(buf):
            assert C.main(["--json", str(out)]) == 0
        docs.append(json.loads(out.read_text(encoding="utf-8")))
        logs.append(buf.getvalue())
finally:
    H.load_prices, H.load_snaps, C.N_PERM, C.PF.TIMELINE = orig
    out.unlink(missing_ok=True)
    tl_path.unlink(missing_ok=True)
doc = docs[0]
assert set(doc["design"]) == {"C3 덜 오른 강세주", "C4 변동성 조정", "C5 피봇 돌파 진입"}
assert not any(v["passed"] for v in doc["design"].values())     # 보합 → 기대값 0
assert doc["holdout"] == {} and "보류 기간은 계산하지 않는다" in logs[0]
assert doc["fundamentals"] is None and "C1·C2 생략" in logs[0]
doc = docs[1]
assert {"C1 실적 성장", "C2 실적 가속", "C1+C5 실적+돌파"} <= set(doc["design"])
cov = doc["fundamentals"]["coverage"]
# 데이터는 T0~T3 뿐이고, 분기 종료 200일이 지나면 없는 셈이다
assert all(v[1] == v[2] and 0 < v[1] <= v[0] / 2 and v[3] == 0
           for v in cov.values())
for y, v in cov.items():
    expect = sum(C.PF.lookup(tl_rows, s["date"]) is not None
                 for s in snaps[:-1] if s["date"][:4] == y) * 4
    assert v[1] == expect, (y, v, expect)
assert doc["design"]["C1 실적 성장"]["tickers"] == 4
assert doc["design"]["C2 실적 가속"]["trades"] == 0
# 사후 등록 가설은 설계 탈락이어도 보류 기간을 한 번 잰다 — 그 후보만
assert set(doc["holdout"]) == set(C.POSTHOC) and not doc["design"]["C1+C5 실적+돌파"]["passed"]
assert all(v["posthoc"] for v in doc["holdout"].values()) and "사후 등록:" in logs[1]
assert "사후 등록:" not in logs[0]
# 앞선 후보의 숫자는 펀더멘털 후보가 붙어도 그대로다
for k in docs[0]["design"]:
    assert docs[0]["design"][k] == docs[1]["design"][k], k
ok("main: 설계 탈락 → 보류 기간 미계산 (사후 등록 가설만 예외) · 타임라인 있을 때만 C1·C2 · 기존 후보 불변")

print("\n  전체 통과")
