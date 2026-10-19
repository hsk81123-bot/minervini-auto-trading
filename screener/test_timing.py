# -*- coding: utf-8 -*-
"""timing.py 단위 테스트 (네트워크 없음).

    python screener/test_timing.py

PLAN.md §4~§6 의 정의를 손으로 계산한 값과 맞춘다. 마지막은 '미래를 바꿔도 과거 신호는
그대로'인지 보는 누수 시험이다.
"""
import math
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import timing as T

ok = lambda m: print("  OK  " + m)
close = lambda a, b, tol=1e-9: abs(a - b) <= tol
D = lambda *s: pd.DatetimeIndex(list(s))


def frame(c, tr=None, o=None, rf=0.0, index=None):
    n = len(c)
    index = index if index is not None else pd.bdate_range("2024-01-01", periods=n)
    c = pd.Series(c, index=index, dtype="float64")
    return pd.DataFrame({"O": c if o is None else pd.Series(o, index=index, dtype="float64"),
                         "C": c, "TR": c if tr is None else pd.Series(tr, index=index, dtype="float64"),
                         "RF": pd.Series(rf, index=index, dtype="float64")})


# 1) 13주 국채 할인율 → BEY
assert close(T.bey(0.0), 0.0)
assert close(T.bey(5.0), 0.05134, 1e-4)                 # P = 98.7361, (100/P−1)·365/91
ok("bey: 할인율 5% → 채권수익률 ≈5.13%")

# 2) 일 무위험 수익률: 전일 값 사용, 달력일수만큼 복리, 음수는 0, 결측 채움
idx = D("2024-01-02", "2024-01-03", "2024-01-04", "2024-01-08")        # 마지막은 4일 간격
irx = pd.Series([5.0, 5.0, -0.2, 5.0], index=idx)
rf, info = T.rf_series(irx, idx)
assert rf.iloc[0] == 0.0
assert close(rf.iloc[1], (1 + T.bey(5.0)) ** (1 / 365) - 1)
assert close(rf.iloc[2], (1 + T.bey(5.0)) ** (1 / 365) - 1)            # 전일(5.0) 사용
assert close(rf.iloc[3], 0.0)                                           # 전일 −0.2 → 0 금리
assert info["negative"] == 1
irx2 = pd.Series([5.0, 5.0], index=D("2024-01-02", "2024-01-08"))      # 사이 날짜가 비어도 채운다
rf2, info2 = T.rf_series(irx2, idx)
assert info2["filled"] == 2 and not rf2.isna().any()
raised = False
try:
    T.rf_series(pd.Series([5.0], index=D("2024-01-02")),
                pd.bdate_range("2024-01-01", periods=12))
except ValueError:
    raised = True
assert raised
ok("rf_series: 전일 값·달력일 복리·음수 0·결측 채움/초과 시 오류")

# 3) 월말 판정
me = T.month_end_flags(D("2024-01-30", "2024-01-31", "2024-02-01", "2024-02-29", "2024-03-01"))
assert me.tolist() == [False, True, False, True, True]
ok("month_end_flags: 월 마지막 거래일 + 자료의 마지막 행")

# 4) 변동성
tr = pd.Series([100.0, 110.0, 99.0], index=pd.bdate_range("2024-01-01", periods=3))
v = T.vol_ann(tr, 2)
assert np.isnan(v.iloc[:2]).all()
assert close(v.iloc[2], math.sqrt(0.02) * math.sqrt(252), 1e-9)       # 수익률 .1, −.1 → 표준편차 √.02
ok("vol_ann: 표본표준편차(ddof=1) × √252, 창이 다 차야 값")

# 5) RSI(2) — c=[10,11,10,12]
r = T.rsi2(pd.Series([10.0, 11.0, 10.0, 12.0]))
assert np.isnan(r.iloc[0]) and r.iloc[1] == 100.0
assert close(r.iloc[2], 50.0) and close(r.iloc[3], 100 - 100 / 6)
ok("rsi2: 평균 하락 0 → 100, 이후 Wilder 평활 (50, 83.33)")

# 6) SMA 규칙 — 엄격 부등호, 정의 전은 NaN
s = T.sig_sma(frame([1, 2, 3, 3, 3.0]), n=3)
assert np.isnan(s.iloc[:2]).all()
assert s.iloc[2:].tolist() == [1.0, 1.0, 0.0]        # [2]: 3>2 ; [3]: 3>2.67 ; [4]: 3>3 아님(동률)
s5 = T.sig_sma(frame([1, 2, 3, 3, 3.0]), n=3, off=0.5)
assert s5.iloc[4] == 0.5
ok("sig_sma: 종가 > SMA 엄격 (동률은 꺼짐), 꺼짐 비중 ω")

# 7) Faber — 월말 종가 > 직전 3개 월말 평균(당월 포함), 다음 달 내내 유지
dates = []
for m in range(1, 7):
    dates += [pd.Timestamp(2024, m, 15), pd.Timestamp(2024, m, 28)]
cl = []
for v_ in (10, 11, 12, 11, 10, 13):
    cl += [1.0, float(v_)]                                 # 월중 값은 아무거나 (쓰이지 않아야 함)
f = T.sig_faber(frame(cl, index=pd.DatetimeIndex(dates)), months=3)
assert f.iloc[:5].isna().all()                             # 3월 말(인덱스 5)부터 정의
assert f.iloc[5] == 1.0 and f.iloc[6] == 1.0               # 3월 말 12 > 11 → 1, 4월 중순까지 유지
assert f.iloc[7] == 0.0 and f.iloc[8] == 0.0 and f.iloc[9] == 0.0     # 4월 말 11 < 11.33, 5월 말 10 < 11
assert f.iloc[11] == 1.0                                   # 6월 말 13 > 11.33
ok("sig_faber: 월말 종가 vs 10(여기선 3)개월 월말 평균, 월중에는 값을 바꾸지 않음")

# 8) 절대 모멘텀 — TR 의 n개월 수익률 > 같은 기간 RF 복리(동률은 꺼짐)
dates2 = []
for m in range(1, 5):
    dates2 += [pd.Timestamp(2024, m, 15), pd.Timestamp(2024, m, 28)]
rf_const = 0.0025                                          # 월 2행 → 월 1.0025² , 2개월 4행
tb = (1 + rf_const) ** 4 - 1
for last, want in ((101.0, 0.0), (101.1, 1.0)):            # 1.0% < 1.0038% < 1.1%
    trs = [100.0, 100.0, 100.0, 100.0, 100.0, 100.8, 100.0, last]        # 월말: 인덱스 1·3·5·7
    d = frame(trs, rf=rf_const, index=pd.DatetimeIndex(dates2))
    a = T.sig_absmom(d, months=2)
    assert a.iloc[:5].isna().all()
    assert a.iloc[5] == 0.0                                 # 0.8% < 1.0038%
    assert a.iloc[7] == want, (last, a.iloc[7])
flat = frame([100.0] * 8, rf=0.0, index=pd.DatetimeIndex(dates2))
assert T.sig_absmom(flat, months=2).iloc[7] == 0.0          # 동률 → 꺼짐
assert T.sig_absmom(flat, months=2, off=0.5).iloc[7] == 0.5
assert tb > 0.0100
ok("sig_absmom: TR 수익률 vs RF 복리 — 동률은 꺼짐, 월말 값만 사용")

# 9) 밴드
tg = pd.Series([np.nan, .8, .78, .75, .70, .96, 1.0, .97])
b = T.apply_band(tg, 0.05)
assert np.isnan(b.iloc[0])
assert b.iloc[1:].tolist() == [.8, .8, .75, .7, .96, 1.0, 1.0]
ok("apply_band: 5%p 이상 차이만 반영 (경계 포함), 1.0 복귀는 예외, 첫 직전 비중 1")

# 10) A2 변동성 목표
c = 100.0 * np.cumprod(1 + np.where(np.arange(60) % 2 == 0, 0.02, -0.02))   # σ̂ 큰 구간
d = frame(c)
vt = T.sig_voltarget(d, window=21, target=0.15, band=0.0001)
sg = T.vol_ann(d["TR"], 21)
exp = np.minimum(1.0, 0.15 / sg)
assert vt.iloc[:21].isna().all() and (vt.iloc[21:] <= 1.0).all()
assert np.allclose(vt.iloc[21:], exp.iloc[21:], atol=1e-3)
calm = frame(100.0 * np.cumprod(1 + np.full(40, 0.0002) * np.where(np.arange(40) % 2, 1, -1)))
assert (T.sig_voltarget(calm, window=21).dropna() == 1.0).all()           # 저변동 → 전액
ok("sig_voltarget: min(1, 목표/σ̂), 저변동은 전액, 창 전은 NaN")

# 11) A3 진리표 (sma_n=2, window=2): 끌 때는 '종가<SMA 이고 σ̂>임계' 둘 다
d3 = frame([100, 100, 100, 100, 90, 95.0])
t3 = T.sig_trendvol(d3, sma_n=2, vol_thr=0.20, window=2)
assert np.isnan(t3.iloc[:2]).all()
assert t3.iloc[2] == 1.0 and t3.iloc[3] == 1.0              # σ 0, 종가=SMA
assert t3.iloc[4] == 0.0                                    # 90<95 이고 σ̂≈1.12
assert t3.iloc[5] == 1.0                                    # 95>92.5 (σ̂ 크지만 추세 위)
assert T.sig_trendvol(d3, sma_n=2, vol_thr=0.20, off=0.5, window=2).iloc[4] == 0.5
d3b = frame([100, 100, 100, 99.9])                          # 종가<SMA 이지만 σ̂≈0.011 < 0.2
assert T.sig_trendvol(d3b, sma_n=2, vol_thr=0.20, window=2).iloc[3] == 1.0
ok("sig_trendvol: (종가<SMA ∧ σ̂>임계)일 때만 꺼짐 — 3칸 진리표")

# 12) A4 눌림목 상태기계 (sma_n=3, exit_n=2)
d4 = frame([10, 11, 12, 11, 9, 5, 6, 8.0])
p4 = T.sig_pullback(d4, sma_n=3, rsi_thr=10.0, exit_n=2)
assert np.isnan(p4.iloc[:2]).all()
assert p4.iloc[2:].tolist() == [1, 0, 0, 1, 0, 1]
p4h = T.sig_pullback(d4, sma_n=3, rsi_thr=10.0, exit_n=2, off=0.5)
assert p4h.iloc[2:].tolist() == [1, .5, .5, 1, .5, 1]
d4b = frame([10, 11, 12, 11, 9, 5, 4, 8.0])                # 눌림목 진입 후 종가<2일SMA → 유지
p4b = T.sig_pullback(d4b, sma_n=3, rsi_thr=10.0, exit_n=2)
assert p4b.iloc[5] == 1 and p4b.iloc[6] == 1
ok("sig_pullback: 상승 추세 1 → 추세 밖 RSI(2)<10 에서 진입(1), 종가>단기 SMA 에서 청산")

# 12b) 낙폭 필터 D1~D4 (PLAN §12) — sma_n=4, n=2, x=0.1: 구간 [idx3..6] 의 (G,Q) = (1,1)(1,0)(0,1)(0,0)
dd = frame([50, 60, 100, 120, 105, 107, 90.0])
kw = dict(sma_n=4, x=0.10, n=2)
qs, an, orr, av = (T.sig_ddfilter(dd, mode=m, **kw) for m in ("q", "and", "or", "avg"))
assert qs.iloc[0:1].isna().all() and qs.iloc[1:3].tolist() == [1.0, 1.0]       # Q 는 창 n 이 차면 정의
assert an.iloc[:3].isna().all() and orr.iloc[:3].isna().all() and av.iloc[:3].isna().all()   # G 가 정의되기 전
assert qs.iloc[3:].tolist() == [1, 0, 1, 0]
assert an.iloc[3:].tolist() == [1, 0, 0, 0]
assert orr.iloc[3:].tolist() == [1, 1, 1, 0]
assert av.iloc[3:].tolist() == [1, .5, .5, 0]
assert T.sig_ddfilter(dd, mode="avg", off=0.5, **kw).iloc[3:].tolist() == [1, .75, .75, .5]
assert T.signal(dd, "ddfilter", mode="or", **kw).iloc[3:].tolist() == [1, 1, 1, 0]
ok("sig_ddfilter: Q 만 / G·Q / max / 평균 — (G,Q) 네 조합이 각각 다른 값")

# 13) 신호 분기
d = frame([1, 2, 3.0])
assert T.signal(d, "hold").tolist() == [1, 1, 1] and T.signal(d, "mix", w=0.3).tolist() == [.3] * 3
ok("signal: hold/mix")

# 14) 종가 체결: 신호 d−2 → 체결 d−1 종가 → 수익 d 부터, 비용은 비중 변화에 5bp
trv = [100.0, 110.0, 121.0, 121.0, 133.1, 133.1]
d = frame(trv, rf=0.001)
sg = pd.Series([0, 0, 1, 1, 0, 0.0], index=d.index)
r, w = T.run(d, sg, 5.0, "close")
assert np.isnan(w.iloc[:2]).all() and w.iloc[2:].tolist() == [0, 0, 1, 1]
assert np.isnan(r.iloc[2])                                  # 직전 비중이 없어 비용을 못 구함
assert close(r.iloc[3], 0.001)
assert close(r.iloc[4], 0.1 - 0.0005)                       # 처음 1 이 되는 날: 수익 + 비용
assert close(r.iloc[5], 0.0)
ok("run(close): w[d]=sig[d−2], 수익·현금이자·비용")

# 15) 시가 체결: 신호 d−1 이 d 시가부터. 갭은 전 비중, 장중은 새 비중
o = [100.0, 101.0, 103.0, 99.0]
d = frame([100.0, 102.0, 101.0, 103.0], o=o, rf=0.002)
sg = pd.Series([0, 1, 1, 0.0], index=d.index)
r, w = T.run(d, sg, 5.0, "open")
assert w.tolist()[1:] == [0, 1, 1]
assert close(r.iloc[2], (101.0 / 103.0 - 1) + 0.5 * 0.002 - 0.0005)     # 갭 103/102 은 전 비중 0 으로
assert close(r.iloc[3], 103.0 / 101.0 - 1)                              # 전 비중 1 유지: 하루 수익 그대로
d2 = frame([100.0, 102.0, 101.0, 103.0], tr=[50.0, 51.0, 50.5, 51.5], o=o, rf=0.002)
r2, _ = T.run(d2, sg, 5.0, "open")
assert np.allclose(r.iloc[2:], r2.iloc[2:])                             # TR=C/2 여도 같은 수익
try:
    T.run(frame([1.0, 2.0], o=[np.nan, np.nan]), pd.Series([1.0, 1.0]), 5.0, "open")
    assert False
except ValueError:
    pass
ok("run(open): 갭=전 비중, 장중=새 비중, 현금이자는 평균 비중, 시가 없으면 오류")

# 16) 정적 혼합: 월말 리밸런싱, 사이에는 방치
d = frame([100.0, 110.0, 121.0, 121.0], index=D("2024-01-30", "2024-01-31", "2024-02-01", "2024-02-02"))
m = T.static_mix(d, 0.5, 5.0)
drift = 0.55 / 1.05 - 0.5
assert close(m.iloc[0], 0.0)
assert close(m.iloc[1], 0.05 - 0.0005 * drift)
assert close(m.iloc[2], 0.05)
assert close(m.iloc[3], -0.0005 * drift)
assert np.allclose(T.static_mix(d, 1.0, 5.0), d["TR"].pct_change().fillna(0.0))
ok("static_mix: 월말 종가 리밸런싱 비용 = 5bp × 편차, e=1 은 보유와 같다")

# 17) 지표
dd = pd.Series([0.1, -0.5, 0.1], index=pd.bdate_range("2024-01-01", periods=3))
assert close(T.max_dd(dd), -0.5)
assert close(T.max_dd(pd.Series([-0.2, 0.1], index=dd.index[:2])), -0.2)        # 시작 자산 1 에서 하락
idx = D("2021-01-01", "2021-07-01", "2022-01-01")
rr = pd.Series([0.0, 0.1, 0.1], index=idx)
assert close(T.cagr(rr), 1.21 ** (365.25 / 365) - 1)
sm = T.summarize(rr, pd.Series(0.0, index=idx), pd.Series([1.0, 0.5, 0.5], index=idx))
assert close(sm["exposure"], 2 / 3) and sm["mdd"] == 0.0 and sm["calmar"] == float("inf")
assert close(sm["trades_per_year"], 1 / T.years_between(idx))               # 1→.5 한 번
assert sm["worst_year"][0] in (2021, 2022)
ok("max_dd/cagr/summarize")

# 18) 파일 적재 (CSV → 열 이름, RF 정렬)
with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    ix = pd.bdate_range("2024-01-01", periods=6)
    pd.DataFrame({"Open": [99.0] * 6, "High": 1, "Low": 1, "Close": [100.0] * 6,
                  "Adj Close": [50.0] * 6, "Volume": 1}, index=ix).rename_axis("Date").to_csv(td / "spy.csv")
    pd.DataFrame({"Close": [5.0] * 6}, index=ix).rename_axis("Date").to_csv(td / "irx.csv")
    df = T.load_spy(td)
    assert list(df.columns) == ["O", "C", "TR", "RF"] and df["TR"].iloc[0] == 50.0
    assert df["RF"].iloc[0] == 0.0 and df["RF"].iloc[1] > 0
    text = ("junk\n,Mkt-RF,SMB,HML,RF\n19260701,  0.10,  -0.25,  -0.27,   0.009\n"
            "19260702,  0.45,  -0.33,  -0.06,   0.009\nCopyright\n")
    fr = T.parse_french(text)
    assert len(fr) == 2 and fr["RF"].iloc[0] == 0.009 and fr.index[0] == pd.Timestamp("1926-07-01")
    fr.to_csv(td / "french.csv")
    lf = T.load_french(td)
    assert close(lf["TR"].iloc[1] / lf["TR"].iloc[0] - 1, (0.45 + 0.009) / 100)
    assert lf["O"].isna().all()
ok("load_spy / parse_french / load_french")

# 18b) FRED 파싱·정렬: '.' 결측, 직전 값 채움, 발표 지연 1거래일
txt = "observation_date,BAA10Y\n2024-01-02,2.10\n2024-01-03,.\n2024-01-04,2.30\n2024-01-08,2.50\n"
fs = T.parse_fred(txt)
assert fs.name == "BAA10Y" and fs.isna().tolist() == [False, True, False, False] and fs.iloc[2] == 2.30
ftxt = ("Title: x\nSeries ID:    BAA10Y\nFrequency:   Daily\n\nDATE       VALUE\n"
        "2024-01-02  2.10\n2024-01-03  .\n2024-01-04  2.30\n")
ft = T.parse_fred_txt(ftxt)
assert ft.name == "BAA10Y" and ft.isna().tolist() == [False, True, False] and ft.iloc[2] == 2.30
ix6 = pd.bdate_range("2024-01-02", periods=6)
al = T.macro_aligned(fs, ix6)
assert np.isnan(al.iloc[0])                              # 지연 1일 → 첫날은 값 없음
assert al.iloc[1] == 2.10 and al.iloc[2] == 2.10         # 1/3 은 결측이라 1/2 값을 채움, 1/4 신호는 1/3(채움) 사용
assert al.iloc[3] == 2.30                                # 1/5(금) 신호는 1/4 값
assert al.iloc[4] == 2.30 and al.iloc[5] == 2.50         # 1/8 값은 1/9 신호부터
far = T.macro_aligned(pd.Series([1.0], index=D("2024-01-02")), pd.bdate_range("2024-01-02", periods=12))
assert far.iloc[1:7].eq(1.0).all() and far.iloc[8:].isna().all()     # 채움은 최대 5거래일
with tempfile.TemporaryDirectory() as td:
    for sid in T.FRED_IDS:
        pd.Series([1.0, 2.0, 3.0], index=pd.bdate_range("2024-01-02", periods=3), name=sid).rename_axis("observation_date").to_csv(Path(td) / f"fred_{sid}.csv")
    mm = T.load_macro(pd.bdate_range("2024-01-02", periods=4), Path(td))
    assert list(mm.columns) == list(T.FRED_IDS) and mm["DGS10"].iloc[1:].tolist() == [1.0, 2.0, 3.0]
ok("parse_fred / macro_aligned: 결측·채움 한도·1거래일 지연 / load_macro")

# 19) 누수 시험: k 이후의 가격을 마음대로 바꿔도 sig[:k+1] 은 그대로 (모든 규칙)
rng = np.random.default_rng(7)
n = 700
idx = pd.bdate_range("2019-01-01", periods=n)
tr = 100.0 * np.cumprod(1 + rng.normal(0.0004, 0.012, n))
base = pd.DataFrame({"C": tr * np.linspace(1.0, 0.9, n), "TR": tr}, index=idx)
base["O"] = base["C"] * (1 + rng.normal(0, 0.002, n))
base["RF"] = 0.0001
k = 400
alt = base.copy()
mult = np.exp(np.cumsum(rng.normal(0, 0.05, n - k - 1)))
for col in ("C", "TR", "O"):
    alt.iloc[k + 1:, alt.columns.get_loc(col)] = alt[col].iloc[k + 1:].to_numpy() * mult
cases = [("sma", {}), ("sma", {"n": 150, "off": 0.5}), ("faber", {}), ("absmom", {}),
         ("absmom", {"months": 10, "off": 0.5}), ("voltarget", {}), ("trendvol", {}),
         ("trendvol", {"off": 0.5}), ("ddfilter", {"mode": "q"}), ("ddfilter", {"mode": "and"}),
         ("ddfilter", {"mode": "or", "x": 0.08, "n": 42}), ("ddfilter", {"mode": "avg"}), ("pullback", {}), ("pullback", {"off": 0.5, "exit_n": 3})]
for rule, p in cases:
    s0, s1 = T.signal(base, rule, **p), T.signal(alt, rule, **p)
    a, b = s0.iloc[:k + 1], s1.iloc[:k + 1]
    assert ((a == b) | (a.isna() & b.isna())).all(), (rule, p)
    assert s0.iloc[k + 1:].notna().any()
    w0, w1 = T.run(base, s0, 5.0, "close")[1], T.run(alt, s1, 5.0, "close")[1]
    x, y = w0.iloc[:k + 3], w1.iloc[:k + 3]
    assert ((x == y) | (x.isna() & y.isna())).all()
ok("누수 시험: 미래 가격을 바꿔도 지나간 신호·체결 비중은 그대로 (14개 규칙 설정)")

# 20) 신호가 충분히 정의된다
for rule, p in cases:
    assert len(T.signal(base, rule, **p).dropna()) > 200
print("\n모두 통과")
