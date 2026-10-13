# -*- coding: utf-8 -*-
"""robust.py 단위 테스트 (네트워크 없음).

    python screener/test_robust.py

RS 원점수가 스크리너와 같은 공식인지, 부트스트랩·백분위·제외 점검이 손으로
검산한 값과 맞는지 확인한다.
"""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import robust as R

ok = lambda m: print("  OK  " + m)

# 1) 원점수 공식이 screener.rs_raw_score 와 같다 (다르면 동점 비교가 무의미)
rng = random.Random(1)
closes = [100.0]
for _ in range(299):
    closes.append(closes[-1] * (1 + rng.uniform(-0.03, 0.035)))
try:
    import pandas as pd
    import screener as S
except ImportError:
    print("  SKIP  rs_raw_score 대조 (pandas/yfinance 없음)")
else:
    for n in (253, 260, 300):
        a = R.raw_rs(closes[:n])
        b = S.rs_raw_score(pd.Series(closes[:n]))
        assert abs(a - b) < 1e-12, (n, a, b)
    assert S.rs_raw_score(pd.Series(closes[:252])) is None
    ok("raw_rs: screener.rs_raw_score 와 같은 값 (253·260·300거래일)")
assert R.raw_rs(closes[:252]) is None                   # 1년 미만
c = [1.0] * 253
c[-64] = 0.0
assert R.raw_rs(c) is None                              # 0 이하 가격
c = [10.0] * 253
assert abs(R.raw_rs(c) - 5.0) < 1e-12                   # 보합이면 2+1+1+1
ok("raw_rs: 253거래일 미만·0 이하 가격은 None, 보합이면 5.0")

# 2) 그 날까지의 종가로만 계산한다 (뒤 가격을 쓰면 룩어헤드)
rows = [{"date": f"d{i:03d}", "close": v} for i, v in enumerate(closes)]
d = rows[260]["date"]
assert abs(R.raw_rs_asof(rows, d) - R.raw_rs(closes[:261])) < 1e-12
rows2 = rows[:261] + [{"date": f"d{i:03d}", "close": 1e9}
                      for i in range(261, 300)]
assert R.raw_rs_asof(rows2, d) == R.raw_rs_asof(rows, d)   # 미래가 바뀌어도 같다
ok("raw_rs_asof: 그 날 이후 가격이 바뀌어도 값이 같다 (룩어헤드 없음)")


def snap(dt, rows):
    """rows: {ticker: (rs, vcp)}"""
    return {"date": dt, "rows": {t: {"name": t, "rs": a, "vcp": b,
                                     "price": 10.0, "pivot": 10.0,
                                     "vstop": None}
                                 for t, (a, b) in rows.items()}}


SNAPS = [snap("2026-09-01", {"A": (99, 10), "B": (99, 80), "C": (99, 40),
                             "D": (98, 90), "E": (97, 99)}),
         snap("2026-09-02", {"A": (99, 10)})]          # 마지막은 진입 제외

# 3) 동점 처리 — RS가 먼저, 동점 안에서만 보조 키가 작동한다
vcp = R.tie_cohorts(SNAPS, 2, lambda r, d: -(r["vcp"] or 0))
assert [r["ticker"] for r in vcp[0][1]] == ["B", "C"]
alpha = R.tie_cohorts(SNAPS, 2, lambda r, d: r["ticker"])
assert [r["ticker"] for r in alpha[0][1]] == ["A", "B"]
four = R.tie_cohorts(SNAPS, 4, lambda r, d: r["ticker"])
assert [r["ticker"] for r in four[0][1]] == ["A", "B", "C", "D"]  # D(98)는 VCP 90이어도 뒤
assert len(vcp) == 1
ok("tie_cohorts: RS 순위는 그대로, 동점 안의 순서만 보조 키로 바뀐다")

# 4) 동점 후보 범위 — 상위 N 경계의 RS 이상인 종목 전부
g = R.tie_group(SNAPS, 2)
assert sorted(r["ticker"] for r in g["2026-09-01"]) == ["A", "B", "C"]
g = R.tie_group(SNAPS, 4)
assert sorted(r["ticker"] for r in g["2026-09-01"]) == ["A", "B", "C", "D"]
ok("tie_group: 경계 RS 이상인 종목만 — 동점 처리가 영향을 주는 범위")


def tr(t, r, cohort="c1", status="보유"):
    return {"ticker": t, "r_multiple": r, "cohort": cohort, "status": status,
            "mae": 0.0, "mfe": 0.0}


# 5) 종목 단위 부트스트랩 — 결정적이고, 한 종목 반복을 한 묶음으로 본다
trades = [tr("X", 5.0)] * 10 + [tr("Y", -1.0), tr("Z", -1.0)]
ci = R.cluster_ci(trades, n=500)
ci2 = R.cluster_ci(trades, n=500)
assert ci == ci2                                        # 시드 고정 → 재현
lo, hi = ci
assert lo < 0 < hi, ci          # X 하나가 빠지는 표본이 있으니 하한은 음수
# 거래 단위로 보면 평균 +4.0R 로 확실해 보이지만, 종목은 3개뿐이다
assert abs(R.expectancy(trades) - 4.0) < 1e-12
assert R.cluster_ci([tr("X", 1.0)] * 5) is None          # 묶음 1개면 못 낸다
ok("cluster_ci: X 10회 반복 = 거래 평균 +4R 이어도 종목 단위 하한은 음수")

# 6) 종목당 동일 가중 — 반복 횟수가 표를 늘리지 않는다
assert abs(R.per_ticker_mean(trades) - (5.0 - 1.0 - 1.0) / 3) < 1e-12
assert R.per_ticker_mean([]) is None
ok("per_ticker_mean: 종목마다 한 표")

# 7) 기여 순위와 제외
cb = R.contributions(trades)
assert cb[0][0] == "X" and cb[0][1] == 10 and abs(cb[0][2] - 50.0) < 1e-12
assert R.without(trades, {"X"}) == [tr("Y", -1.0), tr("Z", -1.0)]
ok("contributions/without: R 합계 순, 지정 종목 제외")

# 8) 백분위 — 동점은 절반으로 센다
assert R.percentile_of(5.0, [1, 2, 3, 4]) == 1.0
assert R.percentile_of(0.0, [1, 2, 3, 4]) == 0.0
assert R.percentile_of(2.0, [1, 2, 3, 4]) == 0.375     # (1 + 0.5) / 4
assert R.pct_range(list(range(101))) == (5, 95)
assert R.median_of([3.0, 1.0, 2.0]) == 2.0 and R.median_of([]) is None
ok("percentile_of / pct_range / median_of")

# 9) 판정 기준은 결과 전에 고정 — 5개 모두 코드에 박혀 있다
keys = [k for k, _ in R.CHECKS]
assert keys == ["next_open_positive", "cluster_ci_positive", "beats_random",
                "survives_top2", "survives_top2_vs_random"]
assert R.EDGE_PCTL >= 0.95 and R.N_PERM >= 200 and R.N_BOOT >= 1000
ok("CHECKS: 통과 기준 5개가 미리 고정 (무작위 95백분위, 반복 횟수 하한)")

print("\n전체 통과")
