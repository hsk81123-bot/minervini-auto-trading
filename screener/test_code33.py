# -*- coding: utf-8 -*-
"""code33.py 단위 테스트 (네트워크·외부 의존 없음).

    python screener/test_code33.py

실제 수집 데이터(AAPL)로 수작업 검산하고, 적자·결측·이상치 표기를 확인한다.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import code33 as C

ok = lambda m: print("  OK  " + m)
HERE = Path(__file__).resolve().parent.parent / "docs" / "data"

# 1) band_score 경계
assert C.band_score(0.25, "eps_growth") == 30.0
assert C.band_score(0.40, "eps_growth") == 30.0        # 만점 위
assert C.band_score(0.10, "eps_growth") == 0.0         # 0점 기준
assert C.band_score(0.05, "eps_growth") == 0.0         # 아래
assert abs(C.band_score(0.175, "eps_growth") - 15.0) < 1e-9   # 중간 = 절반
assert C.band_score(None, "eps_growth") is None
ok("band_score: 만점·0점 기준과 중간값 선형 보간")

# 2) ratio — 적자 처리
g, st = C.ratio(120, 100)
assert abs(g - 0.2) < 1e-9 and st is None
assert C.ratio(50, -10) == (None, "적자→흑자")
assert C.ratio(-5, -10) == (None, "적자 지속")
g, st = C.ratio(-5, 10)
assert abs(g + 1.5) < 1e-9 and st == "흑자→적자", (g, st)
assert C.ratio(None, 10) == (None, None)
ok("ratio: 적자→흑자 / 적자 지속 / 흑자→적자 구분")

# 3) 전년 동기 짝짓기 — 미국은 종료일(±25일), 한국은 (연도, 분기)
us = {"market": "us", "quarters": [
    {"end": "2026-06-27", "revenue": 100}, {"end": "2026-03-28", "revenue": 90},
    {"end": "2025-06-28", "revenue": 80},  {"end": "2025-03-29", "revenue": 70}]}
pairs = C.quarter_pairs(us)
assert pairs[0][1]["end"] == "2025-06-28", pairs[0]
assert pairs[1][1]["end"] == "2025-03-29"
assert pairs[2][1] is None            # 전년 자료 없음
ok("미국: 종료일 -365일(±25일)로 전년 동기 매칭 (결산일이 매년 달라도 맞는다)")

kr = {"market": "kr", "quarters": [
    {"year": 2026, "quarter": 2, "revenue": 100}, {"year": 2025, "quarter": 2, "revenue": 50}]}
assert C.quarter_pairs(kr)[0][1]["revenue"] == 50
ok("한국: (연도, 분기)로 전년 동기 매칭")

# 4) 가속 — 3개 분기 성장률이 계단식으로 올라가야 True
mk = lambda vals: {"market": "kr", "quarters": [
    {"year": y, "quarter": 1, "net_income": v} for y, v in vals]}
rising = C.quarter_pairs(mk([(2026, 300), (2025, 100), (2024, 50), (2023, 40)]))
is_a, rises, g3 = C.accel(rising, "net_income")
assert is_a is True and rises == 2, (is_a, rises, g3)
ok(f"가속: 성장률 {[round(x,2) for x in g3]} → 계단식 상승 True")
flat = C.quarter_pairs(mk([(2026, 200), (2025, 100), (2024, 50), (2023, 25)]))
is_a, rises, _ = C.accel(flat, "net_income")
assert is_a is False and rises == 0
ok("가속: 성장률이 같으면 False (가속이 아니다)")
short = C.quarter_pairs(mk([(2026, 300), (2025, 100)]))
assert C.accel(short, "net_income")[0] is None
ok("가속: 분기가 모자라면 측정 불가(None)")

# 5) 측정 불가는 0점이 아니라 배점에서 제외
doc = {"market": "kr", "ticker": "X", "quarters": [
    {"year": 2026, "quarter": 2, "revenue": 130, "operating_income": 30, "net_income": 20},
    {"year": 2025, "quarter": 2, "revenue": 100, "operating_income": 20, "net_income": 10}]}
m = C.compute(doc)
assert m["components"]["eps_accel"] is None          # 3분기가 없으니 측정 불가
assert m["components"]["annual_eps_cagr"] is None    # 연간 자료 없음
assert m["coverage"] == 0.75, m["coverage"]
assert m["code33"] == 100, m["components"]           # 측정된 75점을 모두 받음
ok(f"측정 불가 2항목 제외 → 측정 {m['coverage']:.0%}, 남은 배점으로 환산해 {m['code33']}점")

# 6) 측정률이 너무 낮으면 점수를 내지 않는다
thin = {"market": "kr", "ticker": "Y", "quarters": [
    {"year": 2026, "quarter": 2, "revenue": 130},
    {"year": 2025, "quarter": 2, "revenue": 100}]}
mt = C.compute(thin)
assert mt["code33"] is None and mt["coverage"] < C.MIN_COVERAGE
ok(f"측정 {mt['coverage']:.0%} (기준 {C.MIN_COVERAGE:.0%} 미달) → 점수 없음")

# 7) 적자 종목: 비율은 못 내지만 0점으로 처리하고 플래그를 남긴다
loss = {"market": "kr", "ticker": "Z", "quarters": [
    {"year": 2026, "quarter": 2, "revenue": 100, "operating_income": 5, "net_income": 3},
    {"year": 2025, "quarter": 2, "revenue": 80, "operating_income": -5, "net_income": -10}]}
ml = C.compute(loss)
assert ml["eps_growth"] is None and "적자→흑자" in ml["flags"], ml
assert ml["components"]["eps_growth"] == 0.0
ok("적자→흑자: 성장률 None + 플래그 + 해당 배점 0점")

# 8) 수집 단계 이상치가 지표로 전달된다
anom = dict(loss, anomalies=["2026Q2: |순이익|>매출"])
assert "2026Q2: |순이익|>매출" in C.compute(anom)["flags"]
ok("수집 이상치(anomalies)가 지표 flags로 전달")

# 9) 영업이익률 — 매출이 0 이하면 계산하지 않는다
assert C.opm({"revenue": 0, "operating_income": 5}) is None
assert abs(C.opm({"revenue": 100, "operating_income": 25}) - 0.25) < 1e-9
ok("영업이익률: 매출 0 이하는 측정 불가")

# 10) 연간 CAGR — 시작값이 0 이하면 계산하지 않는다
assert C.annual_cagr({"annuals": [{"net_income": 100}, {"net_income": -10}]}, "net_income") is None
c = C.annual_cagr({"annuals": [{"net_income": 200}, {"net_income": 100},
                               {"net_income": 50}]}, "net_income")
assert abs(c - 1.0) < 1e-9, c          # 2배씩 2년 → CAGR 100%
ok("연간 CAGR: 적자 시작은 측정 불가, 2배씩 2년이면 100%")

# 11) 실제 수집 데이터로 수작업 검산 (AAPL)
path = HERE / "fundamentals" / "AAPL.json"
if path.exists():
    raw = json.load(open(path, encoding="utf-8"))
    m = C.compute(raw)
    q = {x["end"]: x for x in raw["quarters"]}
    cur, prev = q[m["as_of"]], q[m["compared_to"]]
    want_eps = cur["eps_diluted"] / prev["eps_diluted"] - 1
    want_rev = cur["revenue"] / prev["revenue"] - 1
    want_opm = (cur["operating_income"] / cur["revenue"]
                - prev["operating_income"] / prev["revenue"])
    assert abs(m["eps_growth"] - want_eps) < 1e-4, (m["eps_growth"], want_eps)
    assert abs(m["revenue_growth"] - want_rev) < 1e-4
    assert abs(m["opm_change"] - want_opm) < 1e-4
    ok(f"AAPL 실데이터: EPS {want_eps:+.2%} 매출 {want_rev:+.2%} "
       f"OPM {want_opm:+.2%}p — 원자료 수작업 계산과 일치")
else:
    print("  --  AAPL.json 없음 → 실데이터 검산 건너뜀")

print("\n전체 통과")
