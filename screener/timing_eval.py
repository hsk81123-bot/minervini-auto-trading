# -*- coding: utf-8 -*-
"""SPY 타이밍 판정 — PLAN.md §3 C1~C8(+§3.1 종합점수 S), §7 통계, 부록 A 구간을 그대로 구현한다.

사용법 (Actions: timing.yml — 원자료는 Actions 에서만 받을 수 있다):
    python screener/timing_eval.py fetch        # S0: 받기 + 점검 + 해시
    python screener/timing_eval.py frontier     # S2: 베이스라인 프런티어 (SPY · French)
    python screener/timing_eval.py yearly       # 기준선 연도별 표 (기술 통계)
    python screener/timing_eval.py macro        # PLAN §13 금리·신용 신호 M1~M6
    python screener/timing_eval.py macro2       # PLAN §14 신용+금리 인상 결합 M7~M9
    python screener/timing_eval.py annual       # 보유·200일선·M9 의 1년 단위 수익률·MDD 평균/표준편차
    python screener/timing_eval.py defense      # PLAN §12 급락 방어·급등 포착 D1~D4
    python screener/timing_eval.py candidates   # S3: 후보 판정 → 통과한 것만 S4 보류 1회
"""
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import evalkit as E
import timing as T

SEED = 20261017
T_MIN, T_MIN_HOLDOUT = 3.0, 2.0
ERA_FRAC, ERA_MIN_DAYS = 0.8, 126
PLACEBO_N, BOOT_N, BOOT_BLOCK, MIN_SHIFT = 1000, 2000, 252, 252
NEIGHBOR_TOL = 0.015
PLACEBO_PCT = 0.95
NW_LAGS = 8

# 부록 A (고정)
ERAS = {
    "spy": [("1994–99", "1994-05-02", "1999-12-31"), ("2000–02", "2000-01-01", "2002-12-31"),
            ("2003–07", "2003-01-01", "2007-12-31"), ("2008–09", "2008-01-01", "2009-12-31"),
            ("2010–19", "2010-01-01", "2019-12-31"), ("2020~", "2020-01-01", "2099-12-31")],
    "french": [("1927–40", "1927-11-01", "1940-12-31"), ("1941–55", "1941-01-01", "1955-12-31"),
               ("1956–70", "1956-01-01", "1970-12-31"), ("1971–81", "1971-01-01", "1981-12-31"),
               ("1982–92", "1982-01-01", "1992-12-31")]}
CRISES = {
    "spy": [("닷컴 2000-03~2002-10", "2000-03-24", "2002-10-09"),
            ("금융위기 2007-10~2009-03", "2007-10-09", "2009-03-09"),
            ("코로나 2020-02~03", "2020-02-19", "2020-03-23"),
            ("2022 하락장", "2022-01-03", "2022-10-12")],
    "french": [("대공황 1929-09~1932-06", "1929-09-01", "1932-06-30"),
               ("1937-03~1938-03", "1937-03-01", "1938-03-31"),
               ("1973-74", "1973-01-01", "1974-12-31"), ("1987-08~12", "1987-08-01", "1987-12-31")]}
WHIPSAW = [("1994", "1994-02-01", "1994-12-31"), ("1998", "1998-07-17", "1998-10-08"),
           ("2011", "2011-07-22", "2011-10-03"), ("2015-16", "2015-08-18", "2016-02-11"),
           ("2018Q4", "2018-10-03", "2018-12-24")]

# 후보 · 베이스라인 정의 (PLAN §2, §6) — 이웃은 파라미터를 하나씩만 바꾼다
BASE_PARAMS = {"absmom": {"months": 12}, "voltarget": {"window": 21, "target": 0.15, "band": 0.05},
               "trendvol": {"sma_n": 200, "vol_thr": 0.20}, "pullback": {"sma_n": 200, "rsi_thr": 10.0, "exit_n": 5}}
NEIGHBORS = {"absmom": [{"months": 10}, {"months": 14}],
             "voltarget": [{"window": 42}, {"target": 0.12}, {"target": 0.18}],
             "trendvol": [{"sma_n": 150}, {"sma_n": 250}, {"vol_thr": 0.16}, {"vol_thr": 0.24}],
             "pullback": [{"rsi_thr": 5.0}, {"rsi_thr": 15.0}, {"exit_n": 3}, {"exit_n": 7}]}
LABEL = {"absmom": "A1", "voltarget": "A2", "trendvol": "A3", "pullback": "A4"}
OMEGAS = (0.0, 0.5)

# PLAN §12: 급락 방어·급등 포착 D1~D4 (ω=0 만). 이웃은 x, N 을 하나씩만 바꾼다
BASE_DEF = {"sma_n": 200, "x": 0.10, "n": 63}
NEIGH_DEF = [{"x": 0.08}, {"x": 0.12}, {"n": 42}, {"n": 84}]
DEF_MODES = {"D1": "q", "D2": "and", "D3": "or", "D4": "avg"}
SURGES = [("닷컴 반등 2002-10~2003-12", "2002-10-09", "2003-12-31"),
          ("금융위기 반등 2009-03~12", "2009-03-09", "2009-12-31"),
          ("코로나 반등 2020-03~12", "2020-03-23", "2020-12-31"),
          ("2022 반등 2022-10~2023-07", "2022-10-12", "2023-07-31")]


def candidate_specs() -> dict[str, dict]:
    out = {}
    for rule in ("absmom", "voltarget", "trendvol", "pullback"):
        for om in ((None,) if rule == "voltarget" else OMEGAS):
            name = LABEL[rule] if om is None else f"{LABEL[rule]}({om:g})"
            kw = {} if om is None else {"off": om}
            out[name] = {"rule": rule, "params": {**BASE_PARAMS[rule], **kw},
                         "neighbors": [{**BASE_PARAMS[rule], **kw, **nb} for nb in NEIGHBORS[rule]],
                         "omega": om}
    return out


def defense_specs() -> dict[str, dict]:
    out = {}
    for name, mode in DEF_MODES.items():
        base = {**BASE_DEF, "mode": mode}
        out[name] = {"rule": "ddfilter", "params": base, "omega": 0.0,
                     "neighbors": [{**base, **nb} for nb in NEIGH_DEF]}
    return out


# PLAN §13: 금리·신용 신호 M1~M6 (ω=0 만). 이웃은 CS 평균 n, 곡선 θ, 금리 인상 h 를 하나씩만 바꾼다
BASE_MAC = {"sma_n": 200, "cs_n": 200, "theta": 0.0, "h": 1.0}
MAC_MODES = {"M1": "m1", "M2": "m2", "M3": "m3", "M4": "m4", "M5": "m5", "M6": "m6"}
MAC_NEIGH = {"M1": [{"cs_n": 150}, {"cs_n": 250}], "M2": [{"theta": -0.5}, {"theta": 0.5}],
             "M3": [{"h": 0.75}, {"h": 1.25}], "M4": [{"cs_n": 150}, {"cs_n": 250}],
             "M5": [{"cs_n": 150}, {"cs_n": 250}],
             "M6": [{"cs_n": 150}, {"cs_n": 250}, {"theta": -0.5}, {"theta": 0.5}]}


def macro_specs() -> dict[str, dict]:
    out = {}
    for name, mode in MAC_MODES.items():
        base = {**BASE_MAC, "mode": mode}
        out[name] = {"rule": "macro", "params": base, "omega": 0.0,
                     "neighbors": [{**base, **nb} for nb in MAC_NEIGH[name]]}
    return out


# PLAN §14: 신용 + 금리 인상 결합 M7~M9 (새 파라미터 없음, 이웃은 CS n 과 h 를 하나씩만 바꿈)
MAC2_MODES = {"M7": "m7", "M8": "m8", "M9": "m9"}
MAC2_NEIGH = [{"cs_n": 150}, {"cs_n": 250}, {"h": 0.75}, {"h": 1.25}]


def macro2_specs() -> dict[str, dict]:
    out = {}
    for name, mode in MAC2_MODES.items():
        base = {**BASE_MAC, "mode": mode}
        out[name] = {"rule": "macro", "params": base, "omega": 0.0,
                     "neighbors": [{**base, **nb} for nb in MAC2_NEIGH]}
    return out


def baseline_specs() -> dict[str, dict]:
    out = {"B0": {"rule": "hold", "params": {}}, "B4": {"rule": "mix", "params": {"w": 0.5}}}
    for om in OMEGAS:
        out[f"B1({om:g})"] = {"rule": "sma", "params": {"n": 200, "off": om}}
        out[f"B3({om:g})"] = {"rule": "faber", "params": {"months": 10, "off": om}}
    return out


# ---------------------------------------------------------------- 통계
def nw_ols(X: np.ndarray, y: np.ndarray, lags: int = NW_LAGS) -> tuple[np.ndarray, np.ndarray]:
    """OLS 계수와 Newey-West(Bartlett) t 값. X 에는 상수열을 직접 넣는다."""
    n = len(y)
    xtx_inv = np.linalg.inv(X.T @ X)
    b = xtx_inv @ X.T @ y
    u = y - X @ b
    Xu = X * u[:, None]
    S = Xu.T @ Xu
    for l in range(1, lags + 1):
        G = Xu[l:].T @ Xu[:-l]
        S += (1 - l / (lags + 1)) * (G + G.T)
    V = xtx_inv @ S @ xtx_inv * n / (n - X.shape[1])
    return b, b / np.sqrt(np.diag(V))


def weekly(x: pd.Series) -> pd.Series:
    """일 수익률 → 금요일 기준 주간 복리 수익률 (관측이 없는 주는 뺀다)."""
    prod = (1.0 + x).resample("W-FRI").prod() - 1.0
    return prod[x.resample("W-FRI").count() > 0]


def regress(r: pd.Series, mkt: pd.Series, rf: pd.Series) -> dict:
    """주간 초과수익 회귀: 알파(연율)·베타·NW t, Treynor–Mazuy 타이밍 계수 c."""
    y = weekly(r) - weekly(rf)
    x = weekly(mkt) - weekly(rf)
    y, x = y.align(x, join="inner")
    yv, xv = y.to_numpy(), x.to_numpy()
    b, t = nw_ols(np.column_stack([np.ones(len(xv)), xv]), yv)
    b2, t2 = nw_ols(np.column_stack([np.ones(len(xv)), xv, xv ** 2]), yv)
    return {"alpha_ann": float(b[0] * 52), "t_alpha": float(t[0]), "beta": float(b[1]),
            "tm_c": float(b2[2]), "tm_t": float(t2[2]), "weeks": len(xv)}


def stationary_indices(n: int, B: int, mean_block: int, rng: np.random.Generator) -> np.ndarray:
    """Politis–Romano 정상 블록 부트스트랩의 색인 (B×n)."""
    p = 1.0 / mean_block
    restart = rng.random((B, n)) < p
    restart[:, 0] = True
    starts = rng.integers(0, n, size=(B, n))
    idx = np.empty((B, n), dtype=np.int64)
    idx[:, 0] = starts[:, 0]
    for t in range(1, n):
        idx[:, t] = np.where(restart[:, t], starts[:, t], (idx[:, t - 1] + 1) % n)
    return idx


def _mdd_paths(r: np.ndarray) -> np.ndarray:
    """경로(행)마다 최대낙폭 (시작 자산 1)."""
    w = np.cumprod(1.0 + r, axis=1)
    w = np.concatenate([np.ones((w.shape[0], 1)), w], axis=1)
    return (w / np.maximum.accumulate(w, axis=1) - 1.0).min(axis=1)


def bootstrap_diff(rc: pd.Series, rb: pd.Series, B: int = BOOT_N, mean_block: int = BOOT_BLOCK,
                   seed: int = SEED, chunk: int = 250) -> dict:
    """(후보 − B0) 연복리 차이와 최대낙폭 차이의 95% 구간 (같은 날짜 쌍을 함께 다시 뽑음)."""
    rng = np.random.default_rng(seed)
    a, b = rc.to_numpy(), rb.reindex(rc.index).to_numpy()
    n = len(a)
    yrs = T.years_between(rc.index)
    la, lb = np.log1p(a), np.log1p(b)
    cd, md = [], []
    for k in range(0, B, chunk):
        idx = stationary_indices(n, min(chunk, B - k), mean_block, rng)
        cd.append(np.expm1(la[idx].sum(1) / yrs) - np.expm1(lb[idx].sum(1) / yrs))
        md.append(_mdd_paths(a[idx]) - _mdd_paths(b[idx]))
    cd, md = np.concatenate(cd), np.concatenate(md)
    return {"cagr_diff_ci": (float(np.percentile(cd, 2.5)), float(np.percentile(cd, 97.5))),
            "mdd_diff_ci": (float(np.percentile(md, 2.5)), float(np.percentile(md, 97.5)))}


def placebo(w: pd.Series, ret: pd.Series, rf: pd.Series, cost_bp: float, actual_cagr: float,
            B: int = PLACEBO_N, min_shift: int = MIN_SHIFT, seed: int = SEED,
            chunk: int = 200) -> dict:
    """비중 경로를 원형으로 밀어(≥ min_shift 일) 타이밍 정보만 지운 대조의 연복리 분포."""
    rng = np.random.default_rng(seed)
    wv = w.to_numpy()
    rv, fv = ret.to_numpy(), rf.to_numpy()
    n = len(wv)
    yrs = T.years_between(w.index)
    cost = cost_bp / 1e4
    shifts = rng.integers(min_shift, n - min_shift, size=B)
    cg = np.empty(B)
    for k in range(0, B, chunk):
        sh = shifts[k:k + chunk]
        idx = (np.arange(n)[None, :] - sh[:, None]) % n
        ws = wv[idx]
        dw = np.abs(ws - np.roll(ws, 1, axis=1))
        dw[:, 0] = 0.0
        r = ws * rv[None, :] + (1.0 - ws) * fv[None, :] - cost * dw
        cg[k:k + chunk] = np.expm1(np.log1p(r).sum(1) / yrs)
    return {"percentile": float((cg < actual_cagr).mean()), "median": float(np.median(cg)),
            "p95": float(np.percentile(cg, 95))}


def score(sm: dict, sb: dict) -> dict:
    """PLAN §3.1 종합점수: 수익 개선 : 낙폭 감소 = 5 : 5, B0 대비 상대치."""
    s_ret = (sm["cagr"] - sb["cagr"]) / abs(sb["cagr"])
    s_dd = (abs(sb["mdd"]) - abs(sm["mdd"])) / abs(sb["mdd"])
    return {"s_ret": s_ret, "s_dd": s_dd, "S": 0.5 * s_ret + 0.5 * s_dd}


# ---------------------------------------------------------------- 연구 객체
class Study:
    """한 데이터(SPY 또는 French) 위에서 규칙들을 같은 조건으로 계산·판정한다."""

    def __init__(self, df: pd.DataFrame, kind: str, t_min: float = T_MIN):
        self.df, self.kind, self.t_min = df, kind, t_min
        self.start = T.EVAL_START[kind]
        self.has_open = not df["O"].isna().all()
        self.eras, self.crises = ERAS[kind], CRISES[kind]
        self._sig: dict = {}
        self._run: dict = {}

    def sig(self, spec: dict) -> pd.Series:
        key = (spec["rule"], tuple(sorted(spec["params"].items())))
        if key not in self._sig:
            self._sig[key] = T.signal(self.df, spec["rule"], **spec["params"])
        return self._sig[key]

    def run(self, spec: dict, cost: float = T.COST_BP, mode: str = "close"):
        key = (spec["rule"], tuple(sorted(spec["params"].items())), cost, mode)
        if key not in self._run:
            if spec["rule"] == "mix":
                r = T.static_mix(self.df, spec["params"]["w"], cost)
                w = pd.Series(spec["params"]["w"], index=self.df.index)
            else:
                r, w = T.run(self.df, self.sig(spec), cost, mode)
            r, w = r.loc[self.start:], w.loc[self.start:]
            if r.isna().any() or w.isna().any():
                raise ValueError(f"평가 구간에 미정의 값이 있다: {spec} ({r.isna().sum()}개)")
            self._run[key] = (r, w)
        return self._run[key]

    def summary(self, spec: dict, cost: float = T.COST_BP, mode: str = "close") -> dict:
        r, w = self.run(spec, cost, mode)
        return T.summarize(r, self.df["RF"], w)

    def era_cagrs(self, r: pd.Series) -> dict:
        out = {}
        for lab, lo, hi in self.eras:
            x = r.loc[lo:hi]
            if len(x) >= ERA_MIN_DAYS:
                out[lab] = T.cagr(x)
        return out

    def window_mdd(self, r: pd.Series, lo: str, hi: str) -> float | None:
        x = r.loc[lo:hi]
        return T.max_dd(x) if len(x) >= 5 else None

    # ---- 판정 (PLAN §3) -------------------------------------------------
    def judge(self, name: str, spec: dict, with_neighbors: bool = True) -> dict:
        b0 = self.run(baseline_specs()["B0"])[0]
        rc, wc = self.run(spec)
        out = {"name": name, "checks": {}, "detail": {}}
        sm, sb = T.summarize(rc, self.df["RF"], wc), T.summarize(b0, self.df["RF"])
        out["detail"].update(cagr=sm["cagr"], mdd=sm["mdd"], b0_cagr=sb["cagr"], b0_mdd=sb["mdd"],
                             calmar=sm["calmar"], sharpe=sm["sharpe"], exposure=sm["exposure"],
                             trades_per_year=sm["trades_per_year"], worst_year=sm["worst_year"])
        # C1: 종가·시가 체결
        c1 = sm["cagr"] > sb["cagr"]
        if self.has_open:
            ro, _ = self.run(spec, mode="open")
            out["detail"]["cagr_open"] = T.cagr(ro)
            c1 = c1 and out["detail"]["cagr_open"] > sb["cagr"]
        out["checks"]["C1"] = bool(c1)
        # C2: 알파 t + 부트스트랩
        reg = regress(rc, self.df["TR"].pct_change().loc[rc.index], self.df["RF"].loc[rc.index])
        bt = bootstrap_diff(rc, b0)
        out["detail"].update(reg=reg, boot=bt)
        out["checks"]["C2"] = bool(reg["alpha_ann"] > 0 and reg["t_alpha"] >= self.t_min
                                   and bt["cagr_diff_ci"][0] > 0)
        # C3: 시대
        ec, eb = self.era_cagrs(rc), self.era_cagrs(b0)
        won = sum(1 for k in ec if ec[k] > eb[k])
        need = math.ceil(ERA_FRAC * len(ec) - 1e-9)
        out["detail"]["eras"] = {k: (ec[k], eb[k]) for k in ec}
        out["detail"]["eras_won"] = (won, need)
        out["checks"]["C3"] = bool(won >= need)
        # C4: 낙폭 (전체 + 위기 구간, B0 보다 작거나 같음)
        crisis = {}
        for lab, lo, hi in self.crises:
            a, b = self.window_mdd(rc, lo, hi), self.window_mdd(b0, lo, hi)
            if a is not None:
                crisis[lab] = (a, b)
        out["detail"]["crisis"] = crisis
        out["checks"]["C4"] = bool(sm["mdd"] >= sb["mdd"] and all(a >= b for a, b in crisis.values()))
        # C5: 비용 25bp
        r25, _ = self.run(spec, cost=25.0)
        out["detail"]["cagr_25bp"] = T.cagr(r25)
        out["detail"]["S_25bp"] = score(T.summarize(r25, self.df["RF"]), T.summarize(
            self.run(baseline_specs()["B0"], cost=25.0)[0], self.df["RF"]))["S"]
        out["checks"]["C5"] = bool(out["detail"]["cagr_25bp"] > sb["cagr"])
        # C6: Calmar vs B1(같은 ω; A2 는 두 ω 모두), B2
        bs = baseline_specs()
        oms = OMEGAS if spec.get("omega") is None and spec["rule"] == "voltarget" else (
            (spec["params"].get("off", 0.0),))
        b1c = {om: self.summary(bs[f"B1({om:g})"])["calmar"] for om in oms}
        e = float(wc.mean())
        b2 = self.run({"rule": "mix", "params": {"w": e}})[0]
        s2 = T.summarize(b2, self.df["RF"])
        out["detail"].update(b1_calmar=b1c, b2_cagr=s2["cagr"], b2_calmar=s2["calmar"], b2_e=e)
        sc = score(sm, sb)
        s_b1 = {om: score(self.summary(bs[f"B1({om:g})"]), sb)["S"] for om in oms}
        s_b2 = score(s2, sb)["S"]
        out["detail"].update(score=sc, s_b1=s_b1, s_b2=s_b2)
        b1s = self.summary(bs[f"B1({oms[0]:g})"])
        out["detail"]["vs_b1"] = {"cagr": sm["cagr"] - b1s["cagr"], "mdd": abs(b1s["mdd"]) - abs(sm["mdd"])}
        out["checks"]["C6"] = bool(all(sm["calmar"] >= v for v in b1c.values())
                                   and sm["cagr"] >= s2["cagr"] and sm["calmar"] > s2["calmar"]
                                   and all(sc["S"] > v for v in s_b1.values()) and sc["S"] > s_b2)
        # C7: 이웃
        if with_neighbors:
            ncg = []
            for nb in spec["neighbors"]:
                ncg.append(self.summary({"rule": spec["rule"], "params": nb})["cagr"])
            out["detail"]["neighbors"] = ncg
            near = sum(1 for v in ncg if abs(v - sm["cagr"]) <= NEIGHBOR_TOL)
            out["checks"]["C7"] = bool(near >= math.ceil(0.8 * len(ncg) - 1e-9)
                                       and min(ncg) >= sb["cagr"] - NEIGHBOR_TOL)
        else:
            out["checks"]["C7"] = None
        # C8: 플라시보
        pl = placebo(wc, self.df["TR"].pct_change().loc[rc.index], self.df["RF"].loc[rc.index],
                     T.COST_BP, sm["cagr"])
        out["detail"]["placebo"] = pl
        out["checks"]["C8"] = bool(pl["percentile"] >= PLACEBO_PCT)
        out["passed"] = all(v is True for v in out["checks"].values())
        return out


# ---------------------------------------------------------------- 출력
def pct(x, w=8):
    return f"{'-':>{w}}" if x is None else f"{x * 100:+{w - 1}.2f}%"


def print_frontier(st: Study, title: str) -> dict:
    bs = baseline_specs()
    print(f"\n  [{title}] 평가 {st.df.loc[st.start:].index[0].date()} ~ "
          f"{st.df.index[-1].date()} · 편도 5bp · 종가 체결")
    print(f"  {'':<8} {'연복리':>8} {'시가체결':>8} {'MDD':>8} {'변동성':>7} {'Sharpe':>6} "
          f"{'Calmar':>6} {'5:5점수S':>8} {'노출':>5} {'연거래':>6}  최악의 해")
    sb0 = st.summary(bs["B0"])
    rows = {}
    for nm, spec in bs.items():
        s = st.summary(spec)
        so = st.summary(spec, mode="open")["cagr"] if st.has_open else None
        sc = score(s, sb0)
        rows[nm] = dict(s, cagr_open=so, **sc)
        wy = f"{s['worst_year'][0]} {s['worst_year'][1] * 100:+.1f}%"
        print(f"  {nm:<8} {pct(s['cagr'])} {pct(so)} {pct(s['mdd'])} {s['vol'] * 100:6.1f}% "
              f"{s['sharpe']:6.2f} {s['calmar']:6.2f} {sc['S']:+8.3f} {s.get('exposure', 1):5.2f} "
              f"{s.get('trades_per_year', 0):6.1f}  {wy}")
    print(f"  시대별 연복리:  " + "  ".join(f"{lab}" for lab, _, _ in st.eras))
    for nm in ("B0", "B1(0)", "B1(0.5)", "B3(0)", "B3(0.5)", "B4"):
        ec = st.era_cagrs(st.run(bs[nm])[0])
        print(f"    {nm:<8} " + "  ".join(pct(ec.get(lab), 7) for lab, _, _ in st.eras))
    print(f"  위기 구간 낙폭 (시작일 100% 기준):")
    for lab, lo, hi in st.crises:
        print(f"    {lab:<26} " + "  ".join(
            f"{nm} {pct(st.window_mdd(st.run(bs[nm])[0], lo, hi), 7).strip()}"
            for nm in ("B0", "B1(0)", "B1(0.5)", "B3(0)", "B4")))
    return rows


def print_verdict(res: dict) -> None:
    d = res["detail"]
    marks = " ".join(f"{k}:{'O' if v else ('-' if v is None else 'X')}" for k, v in res["checks"].items())
    reg = d["reg"]
    print(f"  {res['name']:<9} 연복리 {pct(d['cagr'])} (시가 {pct(d.get('cagr_open'))}) vs B0 "
          f"{pct(d['b0_cagr'])} · MDD {pct(d['mdd'])} vs {pct(d['b0_mdd'])} · Calmar {d['calmar']:.2f} "
          f"· 노출 {d['exposure']:.2f} · 연 거래 {d['trades_per_year']:.1f}")
    print(f"            알파 {reg['alpha_ann'] * 100:+.2f}% t {reg['t_alpha']:+.2f} β {reg['beta']:.2f} "
          f"TM c {reg['tm_c']:+.2f}(t {reg['tm_t']:+.1f}) · 연수익 차 95% "
          f"[{d['boot']['cagr_diff_ci'][0] * 100:+.2f}%, {d['boot']['cagr_diff_ci'][1] * 100:+.2f}%] · "
          f"MDD 차 95% [{d['boot']['mdd_diff_ci'][0] * 100:+.1f}, {d['boot']['mdd_diff_ci'][1] * 100:+.1f}]%p")
    print(f"            시대 {d['eras_won'][0]}/{d['eras_won'][1]}필요 · 25bp {pct(d['cagr_25bp'])} · "
          f"B2(e={d['b2_e']:.2f}) {pct(d['b2_cagr'])} Calmar {d['b2_calmar']:.2f} · B1 Calmar "
          + "/".join(f"{v:.2f}" for v in d["b1_calmar"].values())
          + f" · 플라시보 {d['placebo']['percentile'] * 100:.0f}번째")
    if "neighbors" in d:
        print(f"            이웃 연복리 " + " ".join(pct(v, 7).strip() for v in d["neighbors"]))
    sc = d["score"]
    print(f"            5:5 점수 S {sc['S']:+.3f} (수익 {sc['s_ret']:+.2f} · 낙폭 {sc['s_dd']:+.2f}) vs B1 "
          + "/".join(f"{v:+.3f}" for v in d["s_b1"].values()) + f" · B2 {d['s_b2']:+.3f} · 25bp S {d['S_25bp']:+.3f}")
    if "vs_b1" in d:
        v = d["vs_b1"]
        print(f"            B1(같은 ω) 대비 연복리 {pct(v['cagr'], 7).strip()} · MDD {v['mdd'] * 100:+.1f}%p(양수=더 얕음)"
              + (" → 둘 다 개선" if v["cagr"] > 0 and v["mdd"] >= 0 else ""))
    print(f"            판정 {marks} → {'통과' if res['passed'] else '탈락'}")


# ---------------------------------------------------------------- 명령
def check_data() -> None:
    """S0 점검: 범위·결측·이상치·IRX·French 와 SPY 의 일치."""
    df = T.load_spy()
    print(f"\n  [S0 데이터 점검]")
    print(f"  SPY {df.index[0].date()} ~ {df.index[-1].date()} · {len(df)}행 · 결측 "
          f"{int(df[['O', 'C', 'TR']].isna().sum().sum())}")
    gaps = df.index.to_series().diff().dt.days
    print(f"  거래일 간격 5일 초과 {int((gaps > 5).sum())}회 (최대 {int(gaps.max())}일)")
    ret = df["TR"].pct_change()
    print(f"  일수익 최대 {ret.max() * 100:+.1f}% · 최소 {ret.min() * 100:+.1f}% · "
          f"TR/C 비율 {df['TR'].iloc[0] / df['C'].iloc[0]:.3f} → {df['TR'].iloc[-1] / df['C'].iloc[-1]:.3f}")
    print(f"  IRX 점검 {df.attrs['irx_info']} · RF 연환산 평균 "
          f"{((1 + df['RF']).prod() ** (1 / T.years_between(df.index)) - 1) * 100:.2f}%")
    for f in ("spy.csv", "irx.csv", "french.csv"):
        p = T.CACHE / f
        if p.exists():
            print(f"  {f}  sha256 {T.sha256(p)}")
    if (T.CACHE / "french.csv").exists():
        fr = T.load_french()
        print(f"  French {fr.index[0].date()} ~ {fr.index[-1].date()} · {len(fr)}행")
        both = pd.concat([fr["TR"].pct_change(), df["TR"].pct_change()], axis=1, join="inner").dropna()
        both.columns = ["french", "spy"]
        o = both
        print(f"  French–SPY 겹치는 {len(o)}일 일수익 상관 {o.corr().iloc[0, 1]:.4f} · 연복리 "
              f"French {T.cagr(o['french']) * 100:.2f}% vs SPY {T.cagr(o['spy']) * 100:.2f}%")
    else:
        print("  French 자료 없음 — 보류는 '참고'로 격하된다 (PLAN §4)")


def check_macro() -> None:
    """금리·신용 대용 자료 점검: 범위·결측·값 범위·SPY 거래일 정렬 (성과는 보지 않는다)."""
    spy = T.load_spy()
    m = T.load_macro(spy.index)
    rates = pd.read_csv(T.CACHE / "macro_rates.csv", index_col=0, parse_dates=True)
    cred = pd.read_csv(T.CACHE / "macro_credit.csv", index_col=0, parse_dates=True)
    start = T.EVAL_START["spy"]
    print(f"\n  [매크로 자료 점검] SPY 거래일 {spy.index[0].date()} ~ {spy.index[-1].date()} 에 정렬 (1거래일 지연)")
    for c in ("R3M", "R10"):
        r = rates[c]
        gaps = r.dropna().index.to_series().diff().dt.days
        print(f"  {c:<4} 재무부 {r.first_valid_index().date()} ~ {r.last_valid_index().date()} · 유효 {int(r.notna().sum())}개 · "
              f"최소 {r.min():.2f} 최대 {r.max():.2f} · 최근 {r.dropna().iloc[-1]:.2f} · 7일 초과 공백 {int((gaps > 7).sum())}회")
    for c in T.CREDIT_TICKERS:
        print(f"  {c:<6} 야후 {cred.index[0].date()} ~ {cred.index[-1].date()} · {len(cred)}행")
    print(f"  CS(ln HY/국채) 범위 {np.log(cred.iloc[:, 0] / cred.iloc[:, 1]).min():.3f} ~ "
          f"{np.log(cred.iloc[:, 0] / cred.iloc[:, 1]).max():.3f}")
    nan = {c: int(m[c].loc[start:].isna().sum()) for c in m.columns}
    print(f"  평가 시작({start}) 이후 정렬 결측: {nan}")
    sma = {c: m[c].rolling(250).mean().loc[start:].isna().sum() for c in ("R3M", "R10", "CS")}
    print(f"  250거래일 평균이 평가 시작 후 미정의인 날 수: {sma}  · 252일 최저(금리) 미정의 "
          f"{int(m['R3M'].rolling(252).min().loc[start:].isna().sum())}")
    for f in ("macro_rates.csv", "macro_credit.csv"):
        print(f"  {f}  sha256 {T.sha256(T.CACHE / f)}")


def cmd_frontier(out: dict) -> None:
    spy = T.load_spy()
    out["frontier_spy"] = print_frontier(Study(spy, "spy"), "S2 프런티어 · SPY")
    if (T.CACHE / "french.csv").exists():
        out["frontier_french"] = print_frontier(Study(T.load_french(), "french"),
                                                "S2 프런티어 · French 시장수익 (보류 구간 자료 — 베이스라인만)")


def yearly_table(st: Study, names: tuple[str, ...], specs: dict | None = None) -> list[dict]:
    """달력 연도별 수익률·그 해 안의 최대낙폭(해 첫날 100%)·SPY 노출·비중 변경 횟수 (기술 통계)."""
    bs = specs or baseline_specs()
    runs = {nm: st.run(bs[nm]) for nm in names}
    years = sorted({d.year for d in runs[names[0]][0].index})
    rows = []
    for y in years:
        row = {"year": y}
        for nm in names:
            r, w = runs[nm]
            ry, wy = r[r.index.year == y], w[w.index.year == y]
            row[nm] = {"ret": float((1.0 + ry).prod() - 1.0), "mdd": T.max_dd(ry),
                       "exposure": float(wy.mean()),
                       "switches": int(((wy - w.shift(1).loc[wy.index]).abs() > 1e-9).sum())}
        rows.append(row)
    return rows


def cmd_yearly(out: dict) -> None:
    st = Study(T.load_spy(), "spy")
    names = ("B0", "B1(0)")
    rows = yearly_table(st, names)
    out["yearly"] = rows
    print(f"\n  [연도별 · SPY 보유(B0) vs 200일선 규칙(B1(0))] 종가 체결 · 편도 5bp · 연 MDD 는 그 해 첫날 100% 기준")
    print(f"  {'연도':<6} {'B0 수익':>8} {'B0 MDD':>8} | {'B1 수익':>8} {'B1 MDD':>8} {'수익차':>7} {'MDD차':>7} {'SPY노출':>7} {'변경':>4}")
    for r in rows:
        b0, b1 = r["B0"], r["B1(0)"]
        print(f"  {r['year']:<6} {pct(b0['ret'])} {pct(b0['mdd'])} | {pct(b1['ret'])} {pct(b1['mdd'])} "
              f"{pct(b1['ret'] - b0['ret'], 7)} {pct(b1['mdd'] - b0['mdd'], 7)} {b1['exposure'] * 100:6.0f}% {b1['switches']:4d}")
    n = len(rows)
    win = sum(1 for r in rows if r["B1(0)"]["ret"] > r["B0"]["ret"])
    shallow = sum(1 for r in rows if r["B1(0)"]["mdd"] >= r["B0"]["mdd"])
    print(f"  B1 이 B0 보다 수익 높은 해 {win}/{n} · 연 MDD 가 같거나 얕은 해 {shallow}/{n}")
    r0, r1 = st.run(baseline_specs()["B0"])[0], st.run(baseline_specs()["B1(0)"])[0]
    print(f"  전체: B0 연복리 {pct(T.cagr(r0)).strip()} MDD {pct(T.max_dd(r0)).strip()} · "
          f"B1 연복리 {pct(T.cagr(r1)).strip()} MDD {pct(T.max_dd(r1)).strip()}")


def window_ret(r: pd.Series, lo: str, hi: str) -> float | None:
    x = r.loc[lo:hi]
    return float((1.0 + x).prod() - 1.0) if len(x) >= 5 else None


def cmd_defense(out: dict) -> None:
    """PLAN §12: D1~D4 를 C1~C8(+C6(c)) 로 판정하고, 급락·급등 구간과 연도별로 B0·B1(0) 와 비교."""
    st = Study(T.load_spy(), "spy")
    bs, ds = baseline_specs(), defense_specs()
    print(f"\n  [S3d 급락 방어·급등 포착 · SPY 설계] PLAN §12 · x={BASE_DEF['x']:.0%} N={BASE_DEF['n']} · 평가 {st.start}~")
    res, passed = {}, []
    for name, spec in ds.items():
        r = st.judge(name, spec)
        print_verdict(r)
        res[name] = r
        if r["passed"]:
            passed.append(name)
    out["defense"] = res
    print(f"\n  요약: 4개 중 통과 {len(passed)}개 {passed}")
    both = [n for n, r in res.items() if r["detail"]["vs_b1"]["cagr"] > 0 and r["detail"]["vs_b1"]["mdd"] >= 0]
    print(f"  (참고) B1(0) 보다 연복리도 높고 MDD 도 얕거나 같은 후보: {both or '없음'}")
    print(f"  5:5 점수 S 순위: " + " > ".join(f"{n} {r['detail']['score']['S']:+.3f}" for n, r in sorted(
        res.items(), key=lambda kv: -kv[1]["detail"]["score"]["S"])) + f" (B1(0) {out_s(st, bs):+.3f})")
    print_tables(st, ("B0", "B1(0)", "D1", "D2", "D3", "D4"), {**bs, **ds}, out, "yearly_defense")
    print(f"\n  [S4 보류 · French 시장수익] 설계 통과 후보만 한 번")
    if not passed:
        print("    설계를 통과한 후보가 없다 — 보류는 계산하지 않는다 (PLAN §12 중단 기준)")
    elif not (T.CACHE / "french.csv").exists():
        print("    French 자료가 없어 보류를 계산할 수 없다 (참고로 격하)")
    else:
        fr = Study(T.load_french(), "french", t_min=T_MIN_HOLDOUT)
        out["defense_holdout"] = {}
        for name in passed:
            r = fr.judge(name, ds[name])
            print_verdict(r)
            out["defense_holdout"][name] = r


def print_tables(st: Study, cols: tuple[str, ...], allspecs: dict, out: dict, key: str) -> None:
    """급락·급등 구간 수익률, 연도별 수익률/MDD, 노출·연 거래 표 (기술 통계)."""
    runs = {c: st.run(allspecs[c])[0] for c in cols}
    for title, wins in (("급락 구간 수익률 (방어: 덜 잃을수록 좋음)", st.crises), ("급등 구간 수익률 (포착: 많이 벌수록 좋음)", SURGES)):
        print(f"\n  [{title}]")
        print(f"  {'구간':<28}" + "".join(f"{c:>9}" for c in cols))
        for lab, lo, hi in wins:
            print(f"  {lab:<28}" + "".join(pct(window_ret(runs[c], lo, hi), 9) for c in cols))
    rows = yearly_table(st, cols, allspecs)
    out[key] = rows
    print(f"\n  [연도별 수익률 / 그 해 MDD]  (1994 는 5/2~, 2026 은 마지막 자료일까지)")
    print(f"  {'연도':<6}" + "".join(f"{c:>15}" for c in cols))
    for r in rows:
        print(f"  {r['year']:<6}" + "".join(f"{r[c]['ret'] * 100:+8.1f}%/{r[c]['mdd'] * 100:+5.0f}%" for c in cols))
    print(f"  {'노출':<6}" + "".join(f"{st.summary(allspecs[c])['exposure'] * 100:14.0f}%" for c in cols))
    print(f"  {'연거래':<5}" + "".join(f"{st.summary(allspecs[c])['trades_per_year']:15.1f}" for c in cols))


def calendar_year_stats(r: pd.Series, min_days: int = 245) -> pd.DataFrame:
    """달력 연도별 수익률과 그 해 안의 최대낙폭(해 첫날 100%). 거래일이 min_days 미만인 해(부분 연도)는 뺀다."""
    rows = {}
    for y, x in r.groupby(r.index.year):
        if len(x) >= min_days:
            rows[y] = {"ret": float((1.0 + x).prod() - 1.0), "mdd": T.max_dd(x)}
    return pd.DataFrame(rows).T


def rolling_stats(r: pd.Series, win: int = 252) -> pd.DataFrame:
    """win 거래일 롤링 창(하루씩 이동, 겹침)의 수익률과 창 안 최대낙폭(창 첫날 100%)."""
    from numpy.lib.stride_tricks import sliding_window_view
    a = r.to_numpy(dtype="float64")
    wealth = np.cumprod(1.0 + sliding_window_view(a, win), axis=1)
    w0 = np.concatenate([np.ones((wealth.shape[0], 1)), wealth], axis=1)
    mdd = (w0 / np.maximum.accumulate(w0, axis=1) - 1.0).min(axis=1)
    return pd.DataFrame({"ret": wealth[:, -1] - 1.0, "mdd": mdd}, index=r.index[win - 1:])


def dist(x: pd.Series) -> dict:
    return {"mean": float(x.mean()), "std": float(x.std(ddof=1)), "median": float(x.median()),
            "min": float(x.min()), "max": float(x.max()), "n": int(len(x))}


def cmd_annual(out: dict) -> None:
    """보유·200일선·M9 의 1년 단위 수익률·MDD 의 평균과 표준편차 (달력 연도 / 롤링 252거래일). 기술 통계."""
    spy = T.load_spy()
    df = spy.join(T.load_macro(spy.index))
    st = Study(df, "spy")
    specs = {"B0 보유": baseline_specs()["B0"], "B1 200일선": baseline_specs()["B1(0)"], "M9": macro2_specs()["M9"]}
    runs = {n: st.run(sp)[0] for n, sp in specs.items()}
    out["annual"] = {}
    for title, fn in (("달력 연도 (완전한 해만: 1995~2025, 해 첫날 100% 기준 MDD)", calendar_year_stats),
                      ("롤링 12개월 (252거래일, 하루씩 이동·겹침, 창 첫날 100% 기준 MDD)", rolling_stats)):
        tabs = {n: fn(r) for n, r in runs.items()}
        out["annual"][title] = {n: {"ret": dist(tb["ret"]), "mdd": dist(tb["mdd"])} for n, tb in tabs.items()}
        k = len(next(iter(tabs.values())))
        print(f"\n  [{title}] 표본 {k}개 · 평가 {st.start}~ · 종가 체결 · 편도 5bp")
        print(f"  {'':<11} {'수익률 평균':>10} {'표준편차':>8} {'중앙값':>8} {'최악':>8} {'최고':>8} │ {'MDD 평균':>9} {'표준편차':>8} {'중앙값':>8} {'최악':>8}")
        for n, tb in tabs.items():
            a, b = dist(tb["ret"]), dist(tb["mdd"])
            print(f"  {n:<11} {pct(a['mean'], 10)} {pct(a['std'], 8)} {pct(a['median'], 8)} {pct(a['min'], 8)} {pct(a['max'], 8)} │ "
                  f"{pct(b['mean'], 9)} {pct(b['std'], 8)} {pct(b['median'], 8)} {pct(b['min'], 8)}")
        base = tabs["B0 보유"]
        for n in ("B1 200일선", "M9"):
            d = tabs[n]["ret"] - base["ret"]
            print(f"  {n} − 보유: 수익률 차이 평균 {pct(d.mean(), 8).strip()} (표준편차 {pct(d.std(ddof=1), 8).strip()}) · "
                  f"보유보다 수익 높은 비율 {(d > 0).mean() * 100:.0f}% · MDD 가 보유보다 얕은 비율 {(tabs[n]['mdd'] > base['mdd']).mean() * 100:.0f}%")
        d = tabs["M9"]["ret"] - tabs["B1 200일선"]["ret"]
        print(f"  M9 − 200일선: 수익률 차이 평균 {pct(d.mean(), 8).strip()} (표준편차 {pct(d.std(ddof=1), 8).strip()}) · "
              f"200일선보다 수익 높은 비율 {(d > 0).mean() * 100:.0f}% · MDD 가 더 얕은 비율 {(tabs['M9']['mdd'] > tabs['B1 200일선']['mdd']).mean() * 100:.0f}%")


def cmd_macro(out: dict, combo: bool = False) -> None:
    """PLAN §13(M1~M6) / §14(M7~M9, combo=True) 를 C1~C8(+C6(c)) 로 판정. 보류(French)는 신호를 계산할 수 없어 없다."""
    spy = T.load_spy()
    df = spy.join(T.load_macro(spy.index))
    st = Study(df, "spy")
    bs, ms = baseline_specs(), (macro2_specs() if combo else macro_specs())
    ref = macro_specs()
    print(f"\n  [S3m {'신용+금리 인상 결합 M7~M9 · PLAN §14' if combo else '금리·신용 신호 · PLAN §13'} · SPY 설계] 평가 {st.start}~ · 신호는 1거래일 지연 자료")
    res, passed = {}, []
    for name, spec in ms.items():
        r = st.judge(name, spec)
        print_verdict(r)
        res[name] = r
        if r["passed"]:
            passed.append(name)
    out["macro2" if combo else "macro"] = res
    print(f"\n  요약: {len(ms)}개 중 통과 {len(passed)}개 {passed}")
    both = [n for n, r in res.items() if r["detail"]["vs_b1"]["cagr"] > 0 and r["detail"]["vs_b1"]["mdd"] >= 0]
    print(f"  (참고) B1(0) 보다 연복리도 높고 MDD 도 얕거나 같은 후보: {both or '없음'}")
    print(f"  5:5 점수 S 순위: " + " > ".join(f"{n} {r['detail']['score']['S']:+.3f}" for n, r in sorted(
        res.items(), key=lambda kv: -kv[1]["detail"]["score"]["S"])) + f" (B1(0) {out_s(st, bs):+.3f})")
    cols = ("B0", "B1(0)") + (("M5", "M6") if combo else ()) + tuple(ms)
    print_tables(st, cols, {**bs, **ref, **ms}, out, "yearly_macro2" if combo else "yearly_macro")
    print("\n  [보류] 재무부 CMT(1990~)·펀드(1991~)는 French 보류 구간(~1992)에 없어 계산할 수 없다 — "
          + ("설계 통과 후보는 '보류 불가, 실시간 모의 대상'으로만 기록한다." if passed else "설계 통과 후보가 없어 여기서 끝."))


def out_s(st: Study, bs: dict) -> float:
    return score(st.summary(bs["B1(0)"]), st.summary(bs["B0"]))["S"]


def cmd_candidates(out: dict) -> None:
    spy = T.load_spy()
    st = Study(spy, "spy")
    print(f"\n  [S3 후보 판정 · SPY 설계] PLAN §3 C1~C8 · 평가 {st.start}~ · ROADMAP 과 같은 잣대")
    res, passed = {}, []
    for name, spec in candidate_specs().items():
        r = st.judge(name, spec)
        print_verdict(r)
        res[name] = r
        if r["passed"]:
            passed.append(name)
    out["candidates"] = res
    print(f"\n  요약: 7개 중 통과 {len(passed)}개 {passed}")
    print(f"  5:5 점수 S 순위 (통과 여부와 별개, 높을수록 좋음): " + " > ".join(
        f"{n} {r['detail']['score']['S']:+.3f}" for n, r in sorted(
            res.items(), key=lambda kv: -kv[1]["detail"]["score"]["S"])))
    print(f"  (참고) 낙폭만 작고 수익은 비슷한 규칙(연복리 B0 −1%p 이내, MDD ≤ B0 의 60%): "
          + (", ".join(n for n, r in res.items()
                       if r["detail"]["cagr"] >= r["detail"]["b0_cagr"] - 0.01
                       and abs(r["detail"]["mdd"]) <= 0.6 * abs(r["detail"]["b0_mdd"])) or "없음"))
    print(f"  휩소 구간 5개 (보조): " + " · ".join(
        f"{lab}" for lab, _, _ in WHIPSAW))
    for lab, lo, hi in WHIPSAW:
        row = "  ".join(f"{nm} {pct(T.cagr(st.run(sp)[0].loc[lo:hi]) if len(st.run(sp)[0].loc[lo:hi]) > 20 else None, 7).strip()}"
                        for nm, sp in (("B0", baseline_specs()["B0"]), ("B1(0)", baseline_specs()["B1(0)"]),
                                       ("B1(.5)", baseline_specs()["B1(0.5)"])))
        print(f"    {lab:<8} {row}")
    # S4: 통과한 후보만, 한 번
    print(f"\n  [S4 보류 · French 시장수익 1926~1992] 설계 통과 후보만 한 번")
    if not passed:
        print(f"    설계를 통과한 후보가 없다 — 보류는 계산하지 않는다 (PLAN §8 S3 중단 기준: "
              f"무레버리지 SPY 타이밍은 B0 를 이기지 못했다)")
    elif not (T.CACHE / "french.csv").exists():
        print("    French 자료가 없어 보류를 계산할 수 없다 (참고로 격하)")
    else:
        fr = Study(T.load_french(), "french", t_min=T_MIN_HOLDOUT)
        out["holdout"] = {}
        for name in passed:
            r = fr.judge(name, candidate_specs()[name])
            print_verdict(r)
            out["holdout"][name] = r


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else ""
    out_json = argv[argv.index("--json") + 1] if "--json" in argv else None
    out: dict = {}
    if cmd == "fetch":
        h = T.fetch()
        print(f"  받음: {h}")
        check_data()
    elif cmd == "fetchmacro":
        print(f"  받음: {T.fetch_macro()}")
        check_macro()
    elif cmd == "checkmacro":
        check_macro()
    elif cmd == "check":
        check_data()
    elif cmd == "frontier":
        cmd_frontier(out)
    elif cmd == "yearly":
        cmd_yearly(out)
    elif cmd == "macro":
        cmd_macro(out)
    elif cmd == "annual":
        cmd_annual(out)
    elif cmd == "macro2":
        cmd_macro(out, combo=True)
    elif cmd == "defense":
        cmd_defense(out)
    elif cmd == "candidates":
        cmd_candidates(out)
    else:
        sys.exit(__doc__)
    if out_json and out:
        Path(out_json).write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str),
                                  encoding="utf-8")
    return 0


if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main(sys.argv[1:]))
