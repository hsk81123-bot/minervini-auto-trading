# -*- coding: utf-8 -*-
"""screener.py 데이터 신선도 단위 테스트 (네트워크 없음).

    python screener/test_screener.py

2026-09~10월 실제 스냅샷 28일 중 약 15일이 전 거래일(가끔 이틀 전) 데이터를 오늘
날짜로 커밋했다. 원인: 장 마감 직후 yfinance 최신 일봉이 빈 종목이 많은데,
data_date 를 '종목 마지막 날짜의 최댓값'으로 잡아 일찍 들어온 몇 종목이 전체에
오늘 날짜를 붙였다. 아래는 그 상황을 재현하고 고친 동작을 확인한다.
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import screener as S

ok = lambda m: print("  OK  " + m)

DAYS = pd.bdate_range("2026-09-01", periods=10)
D, D1 = DAYS[-1], DAYS[-2]                     # 오늘(기준일), 전 거래일


def frame(last):
    idx = DAYS[DAYS <= last]
    return pd.DataFrame({"Close": range(1, len(idx) + 1)}, index=idx, dtype=float)


def universe(n_fresh, n_stale):
    d = {f"F{i}": frame(D) for i in range(n_fresh)}
    d.update({f"S{i}": frame(D1) for i in range(n_stale)})
    return d


# 1) 신선도 비율과 '실제 데이터 날짜'
u = universe(10, 90)
assert abs(S.freshness(u, D) - 0.10) < 1e-12
assert S.common_last_date(u) == D1
assert S.freshness({}, D) == 0.0
ok("freshness / common_last_date: 기준일 일봉 비율, 다수가 실제로 끝나는 날")

# 2) 옛 버그 재현 — 최댓값이면 90%가 전날 데이터인데도 오늘 날짜가 붙는다
assert max(df.index[-1] for df in u.values()) == D
ok("버그 재현: max(마지막 날짜) 는 전날 데이터 90% 스냅샷에 오늘 날짜를 붙인다")

# 3) as_of — 기준일 뒤 행을 잘라 한 스냅샷 안에서 날짜가 섞이지 않게
cut = S.as_of(u, D1)
assert all(df.index[-1] == D1 for df in cut.values()) and len(cut) == 100
early = S.as_of({"X": frame(D)}, DAYS[0] - pd.Timedelta(days=1))
assert early == {}                              # 그 날 이전 자료가 없으면 뺀다
ok("as_of: 기준일 이후 행을 잘라 모든 종목이 같은 날짜 기준이 된다")


class Recorder:
    def __init__(self, fixes=None):
        self.waits, self.asked, self.fixes = [], [], fixes or {}

    def wait(self, sec):
        self.waits.append(sec)

    def refetch(self, tickers):
        self.asked.append(sorted(tickers))
        return {t: frame(D) for t in tickers if t in self.fixes}


# 4) 이미 충분히 신선하면 기다리지 않고 기준일 그대로
r = Recorder()
data, day, info = S.ensure_fresh(universe(97, 3), D, r.refetch, r.wait)
assert day == D and not info["stale"] and info["retries"] == 0
assert r.waits == [] and r.asked == []
ok("ensure_fresh: 기준일 일봉 ≥95% 면 그대로 (기다리지 않음)")

# 5) 늦은 종목만 다시 받아 채워지면 기준일로 확정
r = Recorder(fixes={f"S{i}" for i in range(90)})
data, day, info = S.ensure_fresh(universe(10, 90), D, r.refetch, r.wait)
assert day == D and not info["stale"] and info["retries"] == 1
assert r.waits == [S.FRESH_WAIT_SEC]
assert r.asked == [sorted(f"S{i}" for i in range(90))]   # 늦은 종목만
assert all(df.index[-1] == D for df in data.values())
ok("ensure_fresh: 늦은 종목만 다시 받아 채워지면 오늘 날짜로 확정")

# 6) 끝내 안 채워지면 실제로 가진 날짜로 정직하게 표시 + 지연 표시
r = Recorder()
data, day, info = S.ensure_fresh(universe(10, 90), D, r.refetch, r.wait)
assert day == D1, day                                  # 오늘이 아니라 전날
assert info["stale"] and info["retries"] == S.FRESH_RETRIES
assert info["expected_date"] == str(D.date())
assert len(r.waits) == S.FRESH_RETRIES
assert all(df.index[-1] == D1 for df in data.values())  # 일찍 온 10종목도 전날로 자름
ok("ensure_fresh: 끝내 늦으면 data_date=전 거래일, stale 표시, 일찍 온 종목도 잘라 일관성 유지")

# 7) 일부만 채워져 95%를 넘기면 기준일 확정, 남은 지연 종목은 자기 마지막 날 그대로
r = Recorder(fixes={f"S{i}" for i in range(88)})
data, day, info = S.ensure_fresh(universe(10, 90), D, r.refetch, r.wait)
assert day == D and not info["stale"] and abs(info["fresh_ratio"] - 0.98) < 1e-9
ok("ensure_fresh: 98% 채워지면 기준일 확정 (거래정지 등 소수 지연은 허용)")

# 8) 기준 거래일 — 지수와 다수 종목 중 더 최근 쪽 (지수가 늦어도 종목을 자르지 않는다)
bench_late = pd.Series([1.0] * 9, index=DAYS[:9])            # 지수는 D-1 까지
assert S.expected_session(bench_late, universe(97, 3)) == D    # 종목 다수가 D → D
bench_ok = pd.Series([1.0] * 10, index=DAYS)                   # 지수는 D
assert S.expected_session(bench_ok, universe(10, 90)) == D     # 종목이 늦어도 D 기대
assert S.expected_session(pd.Series(dtype=float), universe(10, 90)) == D1  # 지수 실패
r = Recorder()
data, day, info = S.ensure_fresh(universe(97, 3),
                                 S.expected_session(bench_late, universe(97, 3)),
                                 r.refetch, r.wait)
assert day == D and not info["stale"]
ok("expected_session: 지수가 늦은 날에도 최신 종목 데이터를 하루 전으로 자르지 않는다")

print("\n전체 통과")
