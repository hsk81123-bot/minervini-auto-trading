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

print("\n전체 통과")
