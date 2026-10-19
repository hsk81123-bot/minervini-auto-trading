# -*- coding: utf-8 -*-
"""timing_eval.py 단위 테스트 (네트워크 없음).

    python screener/test_timing_eval.py

통계 도구(NW 회귀·블록 부트스트랩·플라시보)와 C1~C8 판정 틀, 그리고 사전 등록한 후보·구간이
PLAN.md 와 같은지 확인한다.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import evalkit as E
import timing as T
import timing_eval as TE

ok = lambda m: print("  OK  " + m)
close = lambda a, b, tol=1e-9: abs(a - b) <= tol
ROOT = Path(__file__).resolve().parent.parent

# 0) 사전 등록: 후보·이웃·구간이 PLAN.md 와 ledger.json 에 적힌 것과 같다
plan = (ROOT / "PLAN.md").read_text(encoding="utf-8")
cs = TE.candidate_specs()
assert list(cs) == ["A1(0)", "A1(0.5)", "A2", "A3(0)", "A3(0.5)", "A4(0)", "A4(0.5)"]
assert sum(len(c["neighbors"]) for c in cs.values()) == 23                 # ledger 의 탐색 13 + 10
for name, c in cs.items():
    for nb in c["neighbors"]:
        diff = [k for k in nb if nb[k] != c["params"].get(k)]
        assert len(diff) == 1, (name, nb)                                    # 한 번에 하나만 바꾼다
assert cs["A3(0.5)"]["params"] == {"sma_n": 200, "vol_thr": 0.20, "off": 0.5}
assert cs["A2"]["params"] == {"window": 21, "target": 0.15, "band": 0.05}
assert cs["A4(0)"]["params"] == {"sma_n": 200, "rsi_thr": 10.0, "exit_n": 5, "off": 0.0}
assert T.EVAL_START == {"spy": "1994-05-02", "french": "1927-11-01"}
for kind in ("spy", "french"):
    assert TE.ERAS[kind][0][1] == T.EVAL_START[kind]
for grp in (TE.CRISES["spy"], TE.WHIPSAW):
    for _, lo, hi in grp:
        assert lo in plan and hi in plan, (lo, hi)
for _, lo, hi in TE.CRISES["french"]:                      # 보류 위기는 PLAN 에 월 단위로 적혀 있다
    assert lo[:7] in plan and hi[:7] in plan and lo.endswith("-01"), (lo, hi)
assert "1927-11-01" in plan and "1994-05-02" in plan
assert (TE.T_MIN, TE.T_MIN_HOLDOUT, TE.NEIGHBOR_TOL, TE.PLACEBO_PCT) == (3.0, 2.0, 0.015, 0.95)
assert (TE.BOOT_BLOCK, TE.BOOT_N, TE.PLACEBO_N, TE.MIN_SHIFT, TE.NW_LAGS) == (252, 2000, 1000, 252, 8)
led = json.loads((ROOT / "screener" / "ledger.json").read_text(encoding="utf-8"))
assert "spy-timing-A1-A4" in json.dumps(led)
bs = TE.baseline_specs()
assert list(bs) == ["B0", "B4", "B1(0)", "B3(0)", "B1(0.5)", "B3(0.5)"]
ok("후보 7개·이웃 23개·구간·임계값이 PLAN.md / ledger.json 과 같다")

# 0a) 급락 방어·급등 포착 D1~D4 가 PLAN §12 / ledger 와 같다
ds = TE.defense_specs()
assert list(ds) == ["D1", "D2", "D3", "D4"]
assert [d["params"]["mode"] for d in ds.values()] == ["q", "and", "or", "avg"]
for name, d in ds.items():
    assert {k: v for k, v in d["params"].items() if k != "mode"} == {"sma_n": 200, "x": 0.10, "n": 63}
    assert d["omega"] == 0.0 and len(d["neighbors"]) == 4
    for nb in d["neighbors"]:
        diff = [k for k in nb if nb[k] != d["params"][k]]
        assert len(diff) == 1 and diff[0] in ("x", "n"), (name, nb)
assert sum(len(d["neighbors"]) for d in ds.values()) == 16
assert "§12" in plan and "x = 0.10" in plan and "N = 63" in plan
assert "spy-timing-defense-D1-D4" in json.dumps(led) and E.ledger_summary(ROOT / "screener" / "ledger.json")["n"] >= 149
for _, lo, hi in TE.SURGES:
    assert lo in plan and hi in plan, (lo, hi)
ok("D1~D4·이웃 16개·급등 구간·장부 149건이 PLAN §12 와 같다")

# 0c) 금리·신용 신호 M1~M6 이 PLAN §13 / ledger 와 같다
ms = TE.macro_specs()
assert list(ms) == ["M1", "M2", "M3", "M4", "M5", "M6"]
assert [d["params"]["mode"] for d in ms.values()] == ["m1", "m2", "m3", "m4", "m5", "m6"]
for name, d in ms.items():
    assert {k: v for k, v in d["params"].items() if k != "mode"} == {"sma_n": 200, "cs_n": 200, "theta": 0.0, "h": 1.0}
    assert d["omega"] == 0.0
    for nb in d["neighbors"]:
        diff = [k for k in nb if nb[k] != d["params"][k]]
        assert len(diff) == 1 and diff[0] in ("cs_n", "theta", "h"), (name, nb)
assert [len(d["neighbors"]) for d in ms.values()] == [2, 2, 2, 2, 2, 4] and sum(len(d["neighbors"]) for d in ms.values()) == 14
assert "§13" in plan and "h = 1.0" in plan and "θ = 0" in plan
assert "spy-timing-macro-M1-M6" in json.dumps(led) and E.ledger_summary(ROOT / "screener" / "ledger.json")["n"] >= 169
ok("M1~M6·이웃 14개·장부 169건이 PLAN §13 과 같다")

# 0b) 종합점수 S (PLAN §3.1): 수익 개선 : 낙폭 감소 = 5 : 5, B0 대비 상대치
sc = TE.score({"cagr": 0.12, "mdd": -0.30}, {"cagr": 0.10, "mdd": -0.50})
assert close(sc["s_ret"], 0.2) and close(sc["s_dd"], 0.4) and close(sc["S"], 0.3)
assert close(TE.score({"cagr": 0.10, "mdd": -0.50}, {"cagr": 0.10, "mdd": -0.50})["S"], 0.0)
assert TE.score({"cagr": 0.025, "mdd": 0.0}, {"cagr": 0.10, "mdd": -0.50})["S"] > 0   # 전액 현금도 양수 → C1·C4 문턱이 따로 필요
assert "§3.1" in plan and "C6(c)" in plan
ok("score: S = 0.5·수익 개선 + 0.5·낙폭 감소")

# 1) NW 회귀가 evalkit 의 검증된 구현과 같다
rng = np.random.default_rng(1)
x = rng.normal(0, 0.02, 400)
y = 0.001 + 0.8 * x + rng.normal(0, 0.01, 400)
b, t = TE.nw_ols(np.column_stack([np.ones(400), x]), y)
ref = E.nw_alpha_beta(list(y), list(x), lags=TE.NW_LAGS)
assert close(b[0] * 52, ref["alpha_ann"], 1e-12) and close(b[1], ref["beta"], 1e-12)
assert close(t[0], ref["t_alpha"], 1e-9) and close(t[1], ref["t_beta"], 1e-9)
ok("nw_ols: evalkit.nw_alpha_beta 와 일치")

# 2) 주간 변환 · 회귀 (TM 계수 복원)
idx = pd.bdate_range("2024-01-01", periods=10)
dr = pd.Series([0.01] * 5 + [0.02] * 5, index=idx)
wk = TE.weekly(dr)
assert len(wk) == 2 and close(wk.iloc[0], 1.01 ** 5 - 1) and close(wk.iloc[1], 1.02 ** 5 - 1)
n = 4000
idx = pd.bdate_range("2000-01-03", periods=n)
mk = pd.Series(rng.normal(0.0003, 0.01, n), index=idx)
rf0 = pd.Series(0.0, index=idx)
w = TE.weekly(mk)
assert len(w) == n // 5
reg = TE.regress(mk * 0.6 + 0.0002, mk, rf0)
assert abs(reg["beta"] - 0.6) < 0.02 and reg["weeks"] == len(w)
assert abs(reg["alpha_ann"] - ((1.0002 ** 5) - 1) * 52) < 0.01 and reg["t_alpha"] > 3
ok("weekly / regress: 주간 복리, 알파·베타 복원")

# 3) 정상 블록 부트스트랩 색인
ix = TE.stationary_indices(2000, 40, 10, np.random.default_rng(3))
assert ix.shape == (40, 2000) and ix.min() >= 0 and ix.max() < 2000
cont = (ix[:, 1:] == (ix[:, :-1] + 1) % 2000).mean()
assert abs(cont - 0.9) < 0.02, cont                                        # 평균 블록 10 → 이어짐 확률 0.9
ok("stationary_indices: 평균 블록 길이 ≈ 지정값")

# 4) 부트스트랩: 같은 수익이면 차이 0, 매일 +0.1%p 더 벌면 하한이 0 위
n = 1500
idx = pd.bdate_range("2010-01-04", periods=n)
rb = pd.Series(rng.normal(0.0004, 0.01, n), index=idx)
z = TE.bootstrap_diff(rb, rb, B=200)
assert z["cagr_diff_ci"] == (0.0, 0.0) and z["mdd_diff_ci"] == (0.0, 0.0)
up = TE.bootstrap_diff(rb + 0.001, rb, B=300)
assert up["cagr_diff_ci"][0] > 0 and up["cagr_diff_ci"][1] > up["cagr_diff_ci"][0]
dn = TE.bootstrap_diff(rb - 0.001, rb, B=300)
assert dn["cagr_diff_ci"][1] < 0
assert np.allclose(TE._mdd_paths(np.array([[0.1, -0.5, 0.1], [-0.2, 0.1, 0.0]])), [-0.5, -0.2])
ok("bootstrap_diff: 동일 → 0, 우위/열위 → 구간이 0 밖, _mdd_paths")

# 5) 플라시보
ret = pd.Series(rng.normal(0.0005, 0.012, n), index=idx)
rf = pd.Series(0.0001, index=idx)
const = pd.Series(1.0, index=idx)
base_cg = np.expm1(np.log1p(ret).sum() / T.years_between(idx))
p0 = TE.placebo(const, ret, rf, 5.0, base_cg - 1e-6, B=100)
p1 = TE.placebo(const, ret, rf, 5.0, base_cg + 1e-6, B=100)
assert p0["percentile"] == 0.0 and p1["percentile"] == 1.0
fore = (ret > 0).astype(float)                              # 완벽한 선견 비중
r_f = fore * ret + (1 - fore) * rf - 0.0005 * fore.diff().abs().fillna(0.0)
cg_f = float(np.expm1(np.log1p(r_f).sum() / T.years_between(idx)))
pf = TE.placebo(fore, ret, rf, 5.0, cg_f, B=200)
assert pf["percentile"] >= 0.99 and cg_f > pf["p95"]
ok("placebo: 상수 비중은 분포가 한 점, 선견 비중은 상위")

# 6) Study — 구간 처리·판정 틀 (평가 시작·시대·위기를 합성 자료에 맞춰 바꿔서)
n = 1600
idx = pd.bdate_range("2015-01-02", periods=n)
trs = 100.0 * np.cumprod(1 + rng.normal(0.0004, 0.011, n))
df = pd.DataFrame({"C": trs * np.linspace(1.0, 0.95, n), "TR": trs}, index=idx)
df["O"] = df["C"] * (1 + rng.normal(0, 0.002, n))
df["RF"] = 0.00008
saved = (dict(T.EVAL_START), dict(TE.ERAS), dict(TE.CRISES))
T.EVAL_START["spy"] = "2016-06-01"
TE.ERAS["spy"] = [("a", "2016-06-01", "2017-12-31"), ("b", "2018-01-01", "2019-12-31"),
                  ("c", "2020-01-01", "2020-01-31"), ("d", "2020-02-01", "2099-12-31")]
TE.CRISES["spy"] = [("c1", "2018-09-01", "2018-12-31"), ("c2", "2021-01-01", "2021-06-30")]
try:
    st = TE.Study(df, "spy")
    r_b0, w_b0 = st.run(bs["B0"])
    assert r_b0.index[0] == pd.Timestamp("2016-06-01") and (w_b0 == 1).all()
    ec = st.era_cagrs(r_b0)
    assert list(ec) == ["a", "b", "d"]                       # 126 거래일 미만인 'c' 는 제외
    assert st.window_mdd(r_b0, "2018-09-01", "2018-09-03") is None
    s_hold = st.summary(bs["B0"])
    assert close(s_hold["cagr"], T.cagr(r_b0))
    # 평가 구간에서 미정의 값이 있으면 거부 (SMA 창이 평가 시작보다 길 때)
    try:
        st.run({"rule": "sma", "params": {"n": 400, "off": 0.0}}, cost=7.0)
        assert False
    except ValueError:
        pass
    # 판정 틀: 보유 자체는 이기지 못하고, 현금(w=0)은 수익이 낮다
    for nm, spec in (("hold", bs["B0"]), ("cash", {"rule": "mix", "params": {"w": 0.0}})):
        res = st.judge(nm, {**spec, "neighbors": [], "omega": 0.0, "params": spec["params"]}, with_neighbors=False)
        assert set(res["checks"]) == {f"C{i}" for i in range(1, 9)}
        assert res["checks"]["C7"] is None and res["passed"] is False
        assert res["checks"]["C1"] is False                  # 같거나 낮다
        assert res["checks"]["C4"] is True                   # 낙폭은 B0 이하
    rd = st.judge("D2", ds["D2"])
    assert set(rd["checks"]) == {f"C{i}" for i in range(1, 9)} and len(rd["detail"]["neighbors"]) == 4
    assert set(rd["detail"]["vs_b1"]) == {"cagr", "mdd"} and set(rd["detail"]["s_b1"]) == {0.0}
    TE.print_verdict(rd)
    wr = TE.window_ret(r_b0, "2018-09-01", "2018-12-31")
    assert close(wr, (1 + r_b0.loc["2018-09-01":"2018-12-31"]).prod() - 1) and TE.window_ret(r_b0, "2018-09-01", "2018-09-03") is None
    res = st.judge("A1(0)", cs["A1(0)"])
    assert set(res["checks"]) == {f"C{i}" for i in range(1, 9)} and "neighbors" in res["detail"]
    assert all(isinstance(v, bool) for v in res["checks"].values())
    assert len(res["detail"]["neighbors"]) == 2 and res["detail"]["eras_won"][1] == 3
    assert "cagr_open" in res["detail"]
    assert close(res["detail"]["score"]["S"], 0.5 * res["detail"]["score"]["s_ret"] + 0.5 * res["detail"]["score"]["s_dd"])
    assert set(res["detail"]["s_b1"]) == {0.0} and "S_25bp" in res["detail"]
    TE.print_verdict(res)
    # 시가 없는 자료(French 형태)는 C1 에서 시가 체결을 건너뛴다
    nf = df.copy()
    nf["O"] = np.nan
    res2 = TE.Study(nf, "spy").judge("A1(0)", cs["A1(0)"], with_neighbors=False)
    assert "cagr_open" not in res2["detail"]
finally:
    T.EVAL_START.clear(); T.EVAL_START.update(saved[0])
    TE.ERAS.clear(); TE.ERAS.update(saved[1])
    TE.CRISES.clear(); TE.CRISES.update(saved[2])
ok("Study: 평가 구간·시대(126일 미만 제외)·판정 C1~C8 틀·시가 없는 자료")

# 6b) 매크로 후보 판정이 합성 자료에서 돈다 (R3M·R10·CS 열이 있는 프레임)
mdf = df.copy()
mdf["CS"] = np.cumsum(rng.normal(0, 0.01, len(mdf)))
mdf["R3M"] = 2.0 + np.abs(np.cumsum(rng.normal(0, 0.02, len(mdf))))
mdf["R10"] = mdf["R3M"] + 1.0 + np.cumsum(rng.normal(0, 0.01, len(mdf)))
T.EVAL_START["spy"] = "2016-06-01"
TE.ERAS["spy"] = [("a", "2016-06-01", "2017-12-31"), ("b", "2018-01-01", "2099-12-31")]
TE.CRISES["spy"] = [("c1", "2018-09-01", "2018-12-31")]
try:
    stm = TE.Study(mdf, "spy")
    rm = stm.judge("M6", ms["M6"])
    assert set(rm["checks"]) == {f"C{i}" for i in range(1, 9)} and len(rm["detail"]["neighbors"]) == 4
    assert all(isinstance(x, bool) for x in rm["checks"].values())
    TE.print_verdict(rm)
finally:
    T.EVAL_START.clear(); T.EVAL_START.update(saved[0])
    TE.ERAS.clear(); TE.ERAS.update(saved[1])
    TE.CRISES.clear(); TE.CRISES.update(saved[2])
ok("macro: 합성 매크로 프레임에서 C1~C8 판정")

# 7) 프런티어 출력이 돈다 (베이스라인 6종)
T.EVAL_START["spy"] = "2016-06-01"
TE.ERAS["spy"] = [("a", "2016-06-01", "2099-12-31")]
TE.CRISES["spy"] = [("c1", "2018-09-01", "2018-12-31")]
try:
    rows = TE.print_frontier(TE.Study(df, "spy"), "시험")
    assert list(rows) == list(bs) and rows["B0"]["cagr_open"] is not None
    assert abs(rows["B4"]["exposure"] - 0.5) < 1e-12
finally:
    T.EVAL_START.clear(); T.EVAL_START.update(saved[0])
    TE.ERAS.clear(); TE.ERAS.update(saved[1])
    TE.CRISES.clear(); TE.CRISES.update(saved[2])
ok("print_frontier: 베이스라인 6종 한 표")

# 8) 연도별 표: 첫 해/마지막 해, 보유 수익률 = 해 안의 일수익 복리, 200일선 규칙의 노출·변경 횟수
T.EVAL_START["spy"] = "2016-06-01"
try:
    st = TE.Study(df, "spy")
    rows = TE.yearly_table(st, ("B0", "B1(0)"))
    assert [r["year"] for r in rows] == sorted({d.year for d in st.run(bs["B0"])[0].index})
    r0 = st.run(bs["B0"])[0]
    y17 = rows[1]
    assert y17["year"] == 2017 and close(y17["B0"]["ret"], (1 + r0[r0.index.year == 2017]).prod() - 1)
    assert all(r["B0"]["exposure"] == 1.0 and r["B0"]["switches"] <= 1 for r in rows)
    assert all(0.0 <= r["B1(0)"]["exposure"] <= 1.0 for r in rows)
    assert sum(r["B1(0)"]["switches"] for r in rows) >= 1
    assert all(r["B1(0)"]["mdd"] <= 0.0 for r in rows)
finally:
    T.EVAL_START.clear(); T.EVAL_START.update(saved[0])
ok("yearly_table: 연도별 수익·MDD·노출·변경 횟수")

print("\n모두 통과")
