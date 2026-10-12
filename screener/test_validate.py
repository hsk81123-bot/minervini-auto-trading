# -*- coding: utf-8 -*-
"""validate.py 단위 테스트 (네트워크·git 접근 없음).

    python screener/test_validate.py

검증 방침이 코드로 지켜지는지를 확인한다 — 특히 '판정 보류', '보유 중 제외',
'무작위 대조군 재현성'.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import papertrade as P
import validate as V

ok = lambda m: print("  OK  " + m)


def bar(d, o, h, l, c):
    return {"date": d, "open": o, "high": h, "low": l, "close": c}


# 1) 보유 중 거래를 실현 성과에 섞지 않는다 (방침 7)
tr = [{"status": "손절", "r_multiple": -1.0, "mae": -0.09, "mfe": 0.0},
      {"status": "익절", "r_multiple": 3.0, "mae": -0.02, "mfe": 0.3},
      {"status": "보유", "r_multiple": 9.0, "mae": 0.0, "mfe": 0.9}]
closed, open_ = V.split_closed(tr)
assert len(closed) == 2 and len(open_) == 1
assert abs(P.perf(closed)["expectancy_r"] - 1.0) < 1e-9     # (−1+3)/2
ok("split_closed: 미실현 +9R이 실현 기대값에 섞이지 않는다 (방침 7)")

# 1b) 전량 청산 기준은 보유 중 거래를 창 끝 종가로 닫아 함께 센다 (방침 7)
#     고정 익절이 없는 규칙은 승자가 열려 있어 '실현만'이 과소평가된다.
mk = V.marked(tr)
assert len(mk) == 3
assert abs(P.perf(mk)["expectancy_r"] - (-1.0 + 3.0 + 9.0) / 3) < 1e-9
assert P.perf(mk)["expectancy_r"] > P.perf(closed)["expectancy_r"]
assert V.marked([{"status": "보유", "r_multiple": None}]) == []
ok("marked: 전량 청산 기준 — 규칙끼리 같은 조건에서 비교하기 위한 기준 (방침 7)")

# 2) 표본이 모자라면 판정하지 않는다 (방침 4)
assert "판정 보류" in V.verdict({"measured": 10, "expectancy_r": 5.0})
assert "판정 보류" in V.verdict({"measured": V.MIN_TRADES - 1,
                                 "expectancy_r": 5.0})
assert "양수" in V.verdict({"measured": V.MIN_TRADES, "expectancy_r": 0.5})
assert "미약" in V.verdict({"measured": 99, "expectancy_r": 0.05})
assert "음수" in V.verdict({"measured": 99, "expectancy_r": -0.3})
assert "판정 불가" in V.verdict({"measured": 99})
ok("verdict: 기대값 +5R이라도 10거래면 판정을 내지 않는다 (방침 4)")

# 3) 코호트별 기대값 — 특정 진입일만 좋았는지 드러난다 (방침 1)
tr = [{"cohort": "2026-09-01", "r_multiple": 4.0},
      {"cohort": "2026-09-01", "r_multiple": 2.0},
      {"cohort": "2026-09-02", "r_multiple": -1.0},
      {"cohort": "2026-09-02", "r_multiple": -1.0},
      {"cohort": "2026-09-03", "r_multiple": None}]      # 측정 불가는 빠진다
g = V.by_cohort(tr)
assert g == {"2026-09-01": 3.0, "2026-09-02": -1.0}, g
assert list(g) == sorted(g), "코호트는 날짜 순으로 나와야 한다"
ok("by_cohort: 한 날짜만 좋았던 경우가 분산으로 드러난다 (방침 1)")

# 4) run() — 측정 불가는 거래로 세지 않는다
ohlc = {"AAA": [bar("2026-09-11", 99, 101, 98, 100.0),
                bar("2026-09-14", 99, 100, 91.0, 99.0)]}
cohort = [("2026-09-11", [{"ticker": "AAA", "rs": 99, "vcp": 10},
                          {"ticker": "ZZZ", "rs": 98, "vcp": 5}])]
o = dict(P.DEFAULTS, stop=-8.0, target=25.0)
trades = V.run(cohort, "us", ohlc, o)
assert len(trades) == 1 and trades[0]["ticker"] == "AAA"
assert trades[0]["cohort"] == "2026-09-11" and trades[0]["status"] == "손절"
assert abs(trades[0]["r_multiple"] + 1.0) < 1e-9
ok("run: 가격 없는 종목은 거래 수에 넣지 않는다 (0R로 채우지 않는다)")

# 5) run()은 코호트마다 같은 종목을 따로 센다 (진입일이 다르면 다른 거래)
two = [("2026-09-11", [{"ticker": "AAA", "rs": 99, "vcp": 10}]),
       ("2026-09-14", [{"ticker": "AAA", "rs": 99, "vcp": 10}])]
ohlc2 = {"AAA": [bar("2026-09-11", 99, 101, 98, 100.0),
                 bar("2026-09-14", 99, 101, 98, 100.0),
                 bar("2026-09-15", 99, 100, 91.0, 99.0)]}
tr2 = V.run(two, "us", ohlc2, o)
assert len(tr2) == 2 and {t["cohort"] for t in tr2} == {"2026-09-11",
                                                        "2026-09-14"}
ok("run: 같은 종목도 진입일이 다르면 별개 거래로 집계")

# 6) 격자 설정이 손절·익절을 모두 훑고 '고정 익절 없음'을 포함한다 (방침 3)
assert None in V.GRID_TARGETS, "추격손절만 쓰는 경우가 격자에 있어야 한다"
assert len(V.GRID_STOPS) >= 3 and all(s < 0 for s in V.GRID_STOPS)
assert len(V.GRID_STOPS) * len(V.GRID_TARGETS) >= 9
ok("GRID: 손절 3종 × 익절 4종(익절 없음 포함) — 이웃 칸을 함께 본다")

# 7) 무작위 대조군은 시드로 재현된다 (같은 시드 = 같은 표본)
rows = [{"ticker": f"T{i}", "name": f"T{i}", "rs": 90, "vcp": 0}
        for i in range(20)]
import random
a = random.Random(V.SEED).sample(rows, 5)
b = random.Random(V.SEED).sample(rows, 5)
c = random.Random(V.SEED + 1).sample(rows, 5)
assert [x["ticker"] for x in a] == [x["ticker"] for x in b]
assert [x["ticker"] for x in a] != [x["ticker"] for x in c]
ok("무작위 대조군: 시드 고정으로 재현, 시드를 바꾸면 표본이 바뀐다 (방침 2)")

# 8) 대조군은 상위 N과 같은 '통과 종목' 풀에서 뽑는다 — 통과 필터 기여를 상쇄
#    (표본 수가 풀보다 크면 풀 전체를 쓴다)
small = rows[:3]
assert len(random.Random(V.SEED).sample(small, min(5, len(small)))) == 3
ok("대조군: 통과 종목이 N보다 적으면 있는 만큼만 뽑는다")

# 9) MIN_TRADES 는 10거래로 승률을 말하지 못할 만큼 커야 한다
assert V.MIN_TRADES >= 30
ok(f"MIN_TRADES = {V.MIN_TRADES} — 표본 기준이 상식 수준으로 설정됐다")

# ---------------------------------------------------------------- 피봇 괴리
# 10) extension — 같은 스냅샷의 price/pivot, 피봇이 없으면 None
assert abs(V.extension({"price": 105.0, "pivot": 100.0}) - 0.05) < 1e-12
assert abs(V.extension({"price": 95.0, "pivot": 100.0}) + 0.05) < 1e-12
assert V.extension({"price": 100.0, "pivot": None}) is None
assert V.extension({"price": 100.0, "pivot": 0}) is None       # 피봇 0 = 없음
assert V.extension({"price": None, "pivot": 100.0}) is None
assert V.extension({"price": 100.0}) is None
ok("extension: 피봇 대비 괴리, 피봇 없음·0은 None (0%로 세지 않는다)")

# 11) in_band — 양끝 포함, 한쪽 열린 구간, 피봇 없으면 어느 구간에도 안 듦
r5 = {"price": 105.0, "pivot": 100.0}
assert V.in_band(r5, 0.0, 0.05) and V.in_band(r5, 0.05, None)
assert not V.in_band(r5, 0.0, 0.04) and not V.in_band(r5, None, 0.0)
assert not V.in_band({"price": 1.0, "pivot": None}, None, None)
# 부동소수 잡음으로 경계 종목이 빠지지 않는다 (105/100-1 = 0.0500000000000000444)
assert V.in_band({"price": 110.0, "pivot": 100.0}, 0.0, 0.10)
assert V.in_band({"price": 33.6, "pivot": 32.0}, 0.0, 0.05)
ok("in_band: 경계 포함, 열린 구간 지원, 피봇 없는 종목은 제외")

# 12) bucket_of — 하한 포함·상한 미포함으로 겹치지 않게 나눈다
assert V.bucket_of(-0.01) == "피봇 아래"
assert V.bucket_of(0.0) == "0~+5%"
assert V.bucket_of(0.0499) == "0~+5%" and V.bucket_of(0.05) == "+5~+10%"
assert V.bucket_of(0.10) == "+10~+25%" and V.bucket_of(0.25) == "+25% 이상"
assert V.bucket_of(1.32) == "+25% 이상"
assert V.bucket_of(None) == "피봇 없음"
ok("bucket_of: 구간이 겹치거나 비지 않는다")


def snap(d, rows):
    """rows: {ticker: (rs, vcp, price, pivot)}"""
    return {"date": d, "rows": {
        t: {"name": t, "rs": rs, "vcp": v, "price": pr, "pivot": pv,
            "vstop": None} for t, (rs, v, pr, pv) in rows.items()}}


SNAPS = [
    snap("2026-09-01", {
        "HOT": (99, 50, 200.0, 100.0),   # +100% — 연장
        "WARM": (98, 40, 130.0, 100.0),  # +30%
        "NEAR": (90, 10, 103.0, 100.0),  # +3%  — 돌파 직후
        "EDGE": (85, 10, 105.0, 100.0),  # +5%  — 경계
        "BELOW": (80, 10, 97.0, 100.0),  # -3%  — 돌파 직전
        "NOPIV": (97, 0, 50.0, None),    # 피봇 없음
    }),
    snap("2026-09-02", {"NEAR": (90, 10, 104.0, 100.0)}),
    snap("2026-09-03", {"NEAR": (90, 10, 106.0, 100.0)}),   # 마지막 — 진입 제외
]

# 13) cohorts — 마지막 스냅샷은 진입일로 쓰지 않는다
c = V.cohorts(SNAPS, 2, 1)
assert [d for d, _ in c] == ["2026-09-01", "2026-09-02"]
assert [r["ticker"] for r in c[0][1]] == ["HOT", "WARM"]      # 필터 없음 = RS순
ok("cohorts: 필터 없으면 RS 상위, 마지막 스냅샷 제외")

# 14) 구간 안에서 순위 — '상위 N을 거른다'가 아니라 '구간 안 상위 N'
c = V.cohorts(SNAPS, 2, 1, (0.0, 0.05))
assert [r["ticker"] for r in c[0][1]] == ["NEAR", "EDGE"], c[0][1]
# 순위 상위 2(HOT, WARM)를 먼저 고른 뒤 거르면 0개가 된다 — 그 방식이 아님을 확인
assert all(V.in_band(r, 0.0, 0.05) for r in c[0][1])
ok("cohorts(band): 구간 안 종목 중 RS 상위 N — 실전 진입 규칙과 같다")

# 15) 연장 구간·돌파 직전 구간·피봇 없는 종목
c = V.cohorts(SNAPS, 5, 1, (0.10, None))
assert [r["ticker"] for r in c[0][1]] == ["HOT", "WARM"]
c = V.cohorts(SNAPS, 5, 1, (-0.05, 0.0))
assert [r["ticker"] for r in c[0][1]] == ["BELOW"]
c = V.cohorts(SNAPS, 9, 1, (None, None))                 # 열린 구간
assert "NOPIV" not in [r["ticker"] for r in c[0][1]]     # 피봇 없으면 못 잰다
ok("cohorts(band): 연장·돌파직전 구간 분리, 피봇 없는 종목은 어느 구간에도 없음")

# 16) 후보가 없는 코호트는 빈 목록으로 남는다 (코호트 수는 그대로)
c = V.cohorts(SNAPS, 5, 1, (0.5, 0.6))
assert len(c) == 2 and all(rows == [] for _, rows in c)
ok("cohorts(band): 후보 없는 날도 코호트로 센다 — 커버리지 계산용")

# 17) 무작위 대조군은 같은 구간 풀에서만 뽑고, 시드로 재현된다
r1 = V.random_cohorts(SNAPS, 2, 1, (0.0, 0.05))
r2 = V.random_cohorts(SNAPS, 2, 1, (0.0, 0.05))
assert [[x["ticker"] for x in rows] for _, rows in r1] == \
       [[x["ticker"] for x in rows] for _, rows in r2]
assert {x["ticker"] for x in r1[0][1]} <= {"NEAR", "EDGE"}
ok("random_cohorts(band): 같은 구간 풀에서만 추출, 시드 재현")

# 18) run()은 거래에 진입 시 괴리를 싣는다
ohlc = {"NEAR": [bar("2026-09-01", 100, 104, 99, 103.0),
                 bar("2026-09-02", 103, 105, 102, 104.0)]}
tr = V.run([("2026-09-01", [SNAPS[0]["rows"]["NEAR"] | {"ticker": "NEAR"}])],
           "us", ohlc, dict(P.DEFAULTS, stop=-8.0, target=None))
assert len(tr) == 1 and abs(tr[0]["ext"] - 0.03) < 1e-12
ok("run: 거래마다 진입 시 피봇 괴리(ext)를 남긴다")

# 19) by_bucket — 괴리 구간별로 묶고, 정해진 순서로 낸다
tr = [{"status": "보유", "r_multiple": 2.0, "mae": -0.02, "mfe": 0.2,
       "ext": 0.03, "cohort": "d"},
      {"status": "손절", "r_multiple": -1.0, "mae": -0.09, "mfe": 0.0,
       "ext": 0.40, "cohort": "d"},
      {"status": "손절", "r_multiple": -1.0, "mae": -0.10, "mfe": 0.0,
       "ext": 0.30, "cohort": "d"},
      {"status": "보유", "r_multiple": 0.5, "mae": -0.01, "mfe": 0.1,
       "ext": None, "cohort": "d"}]
bb = dict(V.by_bucket(tr))
assert list(dict(V.by_bucket(tr))) == ["0~+5%", "+25% 이상", "피봇 없음"]
assert abs(bb["+25% 이상"]["expectancy_r"] + 1.0) < 1e-12
assert abs(bb["0~+5%"]["expectancy_r"] - 2.0) < 1e-12
ok("by_bucket: 진입 시 괴리별 성과 — 연장 진입이 나빴는지 직접 잰다")

print("\n전체 통과")
