# -*- coding: utf-8 -*-
"""history.py 단위 테스트 (네트워크 없음).

    python screener/test_history.py

핵심은 재구성이 스크리너와 '같은 리스트'를 내는지다. 합성 종목 30여 개로
screener.py 의 실제 함수(rs_raw_score, trend_template)를 그 날 기준 2년 구간에
돌린 결과와, history.py 의 벡터화 재구성 결과를 비교해 통과 종목과 RS가 정확히
같아야 한다. 신호일 뒤에 분할이 일어나는 종목도 넣는다.
"""
import random
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import history as H
import screener as S

ok = lambda m: print("  OK  " + m)
CFG = S.CFG["us"]

# 1) 분할 배수 — 그 날 '이후' 분할만 곱한다 (분할 당일 가격은 이미 분할 뒤)
idx = pd.date_range("2024-01-01", periods=6, freq="B")
sp = pd.Series([0, 0, 4.0, 0, 10.0, 0], index=idx)
f = H.split_factor_after(sp, idx)
assert list(f) == [40.0, 40.0, 10.0, 10.0, 1.0, 1.0], list(f)
assert list(H.split_factor_after(None, idx)) == [1.0] * 6
rev = pd.Series([0, 0.1, 0, 0, 0, 0], index=idx)        # 1:10 역분할
assert list(H.split_factor_after(rev, idx)) == [0.1, 1, 1, 1, 1, 1]
ok("split_factor_after: 이후 분할만 누적 (4:1·10:1 → 40배, 역분할 0.1배)")

# ---------------------------------------------------------------- 합성 종목
DAYS = pd.bdate_range("2022-01-03", periods=820)       # 2년이 넘는 이력
rng = np.random.default_rng(7)


def make(drift, vol=0.02, price0=50.0, volume=400_000, n=len(DAYS)):
    r = rng.normal(drift, vol, n)
    close = price0 * np.exp(np.cumsum(r))
    hi = close * (1 + np.abs(rng.normal(0, 0.01, n)))
    lo = close * (1 - np.abs(rng.normal(0, 0.01, n)))
    op = close * (1 + rng.normal(0, 0.005, n))
    vol_ = np.maximum(1_000, rng.normal(volume, volume * 0.2, n))
    return pd.DataFrame({"Open": op, "High": hi, "Low": lo, "Close": close,
                         "Volume": vol_}, index=DAYS[-n:])


actual = {}
for i in range(12):
    actual[f"UP{i}"] = make(0.0025 + i * 0.0002)        # 강한 상승 → 통과 후보
for i in range(8):
    actual[f"MID{i}"] = make(0.0008)
for i in range(6):
    actual[f"DN{i}"] = make(-0.0010)
actual["PENNY"] = make(0.003, price0=0.15)              # 상승 중이지만 $10 미만 → 가격 필터 탈락
actual["THIN"] = make(0.003, volume=20_000)             # 거래량 필터 탈락
actual["YOUNG"] = make(0.003, n=200)                    # 이력 260행 미만
SIG = DAYS[700]                                         # 신호일
SPLIT_DAY = DAYS[760]                                   # 신호일 '뒤' 4:1 분할
pre = make(0.0028, price0=30.0)
# 신호일 실제가를 $24 로 맞춘다 → 4:1 소급 조정 후 $6 (가격 필터 경계를 넘나든다)
pre[["Open", "High", "Low", "Close"]] *= 24.0 / pre.loc[SIG, "Close"]
# 실제 거래 가격: 분할일부터 1/4 (주식 수 4배 → 거래량 4배)
pre.loc[pre.index >= SPLIT_DAY, ["Open", "High", "Low", "Close"]] /= 4.0
pre.loc[pre.index >= SPLIT_DAY, "Volume"] *= 4.0
actual["SPLIT"] = pre


def yahoo(frame, split_day=None, ratio=4.0):
    """실제 가격 → yfinance 수정주가(분할 이전 가격을 소급 조정) + 분할 기록."""
    adj = frame.copy()
    adj["Splits"] = 0.0
    if split_day is not None:
        before = adj.index < split_day
        adj.loc[before, ["Open", "High", "Low", "Close"]] /= ratio
        adj.loc[before, "Volume"] *= ratio
        adj.loc[split_day, "Splits"] = ratio
    return adj


adjusted = {t: yahoo(df, SPLIT_DAY if t == "SPLIT" else None)
            for t, df in actual.items()}
# 수정주가로는 SPLIT 이 신호일에 $10 아래로 보여야 테스트 의미가 있다
assert adjusted["SPLIT"].loc[SIG, "Close"] < 10 <= actual["SPLIT"].loc[SIG, "Close"], \
    (adjusted["SPLIT"].loc[SIG, "Close"], actual["SPLIT"].loc[SIG, "Close"])


def seen_on(t, day):
    """그 날 yfinance 가 돌려줬을 일봉 — 그 날까지 일어난 분할만 소급 조정.

    history.split_factor_after 를 쓰지 않고 따로 만든다 (검증 대상과 독립).
    """
    df = actual[t][actual[t].index <= day]
    if t == "SPLIT" and SPLIT_DAY <= day:
        df = yahoo(df, SPLIT_DAY).drop(columns="Splits")
    return df


def screener_reference(day):
    """screener.main 2~4단계를 그 날 기준으로 그대로 — 그 날 본 가격으로."""
    data = {}
    for t in actual:
        df = seen_on(t, day)
        win = df[(df.index > day - pd.Timedelta("730D")) & (df.index <= day)]
        if len(win) < 260:                              # download_history(min_len)
            continue
        if (float(win["Close"].iloc[-1]) >= CFG["min_price"]
                and float(win["Volume"].iloc[-50:].mean()) >= CFG["min_vol"]):
            data[t] = win
    raw = {t: S.rs_raw_score(df["Close"]) for t, df in data.items()}
    raw = {t: v for t, v in raw.items() if v is not None}
    rs = (pd.Series(raw).rank(pct=True) * 98 + 1).round(0)
    return {t: int(rs[t]) for t, df in data.items()
            if t in rs.index and S.trend_template(df, float(rs[t]))["pass_all"]}


def history_rebuild(day):
    rows = []
    for t, df in adjusted.items():
        x = H.indicators(df)
        if day in x.index:
            rows.append(x.loc[day].rename(t))
    cross = pd.DataFrame(rows)
    out = H.screen(cross, CFG)
    return {t: int(v) for t, v in out["rs"].items()}


# 2) 핵심: 같은 날 같은 통과 종목, 같은 RS
for day in (SIG, DAYS[650], SPLIT_DAY, DAYS[-1]):        # 분할 전·당일·후
    ref = screener_reference(day)
    got = history_rebuild(day)
    assert ref == got, (day.date(), sorted(ref.items()), sorted(got.items()))
    assert ref, f"{day.date()}: 통과 종목이 하나도 없으면 비교가 무의미하다"
ref = screener_reference(SIG)
assert "SPLIT" in ref, "분할 전 실제가 $30대 종목은 신호일에 통과해야 한다"
assert not {"PENNY", "THIN", "YOUNG"} & set(ref)
ok(f"재구성 = 스크리너: 분할 전·당일·후 4개 날짜에서 통과 종목·RS 정확히 일치 "
   f"(신호일 통과 {len(ref)}종목)")
ok("분할 보정: 신호일 뒤 4:1 분할 종목이 수정주가($10 미만)에 속지 않고 통과")

# 3) 분할 보정을 끄면 틀린다 — 보정이 실제로 일을 하고 있다는 확인
broken = {t: df.drop(columns="Splits") for t, df in adjusted.items()}
rows = [H.indicators(df).loc[SIG].rename(t) for t, df in broken.items()
        if SIG in df.index]
assert "SPLIT" not in set(H.screen(pd.DataFrame(rows), CFG).index)
ok("분할 기록이 없으면 SPLIT 이 가격 필터에서 잘못 빠진다 (보정이 필요한 이유)")

# 4) 주간 신호일 — ISO 주마다 마지막 거래일 (공휴일 금요일이면 목요일)
cal = pd.DatetimeIndex(["2026-01-05", "2026-01-06", "2026-01-09",
                        "2026-01-12", "2026-01-15",              # 16일 휴장
                        "2026-01-19"])
w = [d.strftime("%Y-%m-%d") for d in H.weekly_dates(cal)]
assert w == ["2026-01-09", "2026-01-15", "2026-01-19"], w
ok("weekly_dates: 주마다 마지막 거래일 (휴장 금요일이면 그 전날)")

# 5) 충실도 비교
a = {"A": {"rs": 99, "vcp": 50}, "B": {"rs": 98, "vcp": 10},
     "C": {"rs": 97, "vcp": 0}, "D": {"rs": 90, "vcp": 0},
     "E": {"rs": 85, "vcp": 0}, "F": {"rs": 80, "vcp": 0}}
r = {"A": {"rs": 99, "vcp": 50}, "B": {"rs": 97, "vcp": 10},
     "C": {"rs": 97, "vcp": 0}, "D": {"rs": 90, "vcp": 0},
     "G": {"rs": 88, "vcp": 0}, "F": {"rs": 80, "vcp": 0}}
c = H.compare(a, r)
assert c["both"] == 5 and abs(c["jaccard"] - 5 / 7) < 1e-12
assert abs(c["rs_exact"] - 4 / 5) < 1e-12 and abs(c["rs_absdiff"] - 0.2) < 1e-12
assert c["top5"] == 4                      # 상위5: A B C D E vs A B C D G
assert c["only_actual"] == ["E"] and c["only_rebuilt"] == ["G"]
assert H.top_set(a) == {"A", "B", "C", "D", "E"}
ok("compare: 자카드·RS 일치율·상위 5 겹침·한쪽에만 있는 종목")

# 6) 시장 국면 — 지수가 200일선 위인 날만 True (200일선이 아직 없으면 False)
b = pd.Series(np.concatenate([np.linspace(100, 200, 250), np.linspace(200, 120, 60)]),
              index=pd.bdate_range("2023-01-02", periods=310))
reg = H.regime_on(b)
days = list(reg)
assert reg[days[0]] is False                   # 200일선 없음
assert reg[days[240]] is True                  # 상승 중
assert reg[days[-1]] is False                  # 급락 뒤
ok("regime_on: S&P 500 이 200일선 위인 날만 매수 허용")

# 7) 연도 수익률·연도별 묶음
yr = H.year_returns(pd.Series([100.0, 110.0, 110.0, 99.0],
                              index=pd.to_datetime(["2024-01-02", "2024-12-31",
                                                    "2025-01-02", "2025-12-31"])))
assert abs(yr["2024"] - 0.10) < 1e-12 and abs(yr["2025"] + 0.10) < 1e-12
tr = [{"cohort": "2024-03-01", "r_multiple": 2.0},
      {"cohort": "2024-05-01", "r_multiple": -1.0},
      {"cohort": "2025-01-10", "r_multiple": -1.0},
      {"cohort": "2025-02-10", "r_multiple": None}]
assert H.by_year(tr) == {"2024": [2.0, -1.0], "2025": [-1.0]}
ok("year_returns / by_year: 연도별 지수 수익률과 거래 묶음 (측정 불가 제외)")

# 8) to_ohlc — simulate 가 읽는 형식, 시작일 이전은 자른다, 날짜 색인 동반
px = {"X": adjusted["UP0"]}
ohlc, index = H.to_ohlc(px, {"X", "NOPE"}, DAYS[810].strftime("%Y-%m-%d"))
assert list(ohlc) == ["X"] and len(ohlc["X"]) == 10
assert set(ohlc["X"][0]) == {"date", "open", "high", "low", "close"}
assert index["X"] == [x["date"] for x in ohlc["X"]]
assert index["X"] == sorted(index["X"])
ok("to_ohlc: simulate 형식 + 날짜 색인, 시작일 이전 자름, 없는 종목 무시")

# 8b) 충실도 진단 — 허용 오차 안 짝짓기, 전날과 똑같은 원점수 비율
assert H.match_count([1.0, 2.0, 3.0], [3.00005, 1.0, 9.0]) == 2
assert H.match_count([1.0, 1.0], [1.0]) == 1                # 한 번씩만 짝짓는다
assert H.match_count([], [1.0]) == 0
assert H.stale_ratio([1.0, 2.0, 3.0, 4.0], [1.0, 2.0, 5.0]) == 0.5
assert H.stale_ratio([1.0], None) is None
ok("match_count / stale_ratio: 모집단 원점수 대조와 지연 데이터 감지")

# 8c) 전 거래일 찾기 — 재구성된 날짜들 중 바로 앞
by = {"2026-09-10": 0, "2026-09-11": 0, "2026-09-14": 0}
assert H.prev_trading("2026-09-14", by) == "2026-09-11"
assert H.prev_trading("2026-09-10", by) is None
ok("prev_trading: 실제 스냅샷이 하루 늦었는지 대조할 전 거래일")

# 9) 3단계 판정 기준은 결과 전에 고정
assert [k for k, _ in H.HISTORY_CHECKS] == ["fidelity", "years_positive"]
assert H.FIDELITY_JACCARD >= 0.9 and H.FIDELITY_TOP5 >= 4
ok("HISTORY_CHECKS: 충실도·연도별 기준이 미리 고정")

print("\n전체 통과")
