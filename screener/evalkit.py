# -*- coding: utf-8 -*-
"""후보 평가 공통 도구 — ROADMAP.md 의 성공 기준 5개를 코드로 고정한다.

모든 후보(H1~H4 등)는 '주간 수익 계열'로 이 도구를 통과한다. 후보마다 판정 코드를 새로
짜면 기준이 슬며시 달라지므로, 기준과 임계값은 여기 한 곳에만 둔다.

주간 수익 계열(Frame): 같은 달력의 주마다 시가→다음 신호의 다음날 시가 수익률.
  spy  SPY (배당 포함)             rsp  RSP (S&P 500 동일가중 ETF, 있으면)
  pool 같은 풀 동일가중           <후보> 비용을 뺀 후보 수익률

기준 (ROADMAP.md, 결과를 보기 전에 고정):
  1) 연복리 > SPY            (후보 수익은 생존편향 보정치를 뺀 값)
  2) 알파 Newey-West t ≥ 3.0  (SPY 에 회귀, 알파 > 0)
  3) 시대 블록 5개 중 4개 이상에서 SPY 보다 높은 연복리
  4) 같은 풀 동일가중보다 높은 연복리 (보정 전 값끼리 — 생존편향이 양쪽에 똑같이 걸림)
  5) 낙폭: 전체 최대낙폭이 SPY 보다 2%p 넘게 나쁘지 않고, 위기 구간마다 SPY + 5%p 이내

설계/보류: 설계 2013~ , 보류 2005~2012. 보류는 설계 통과 후보에만 한 번 계산한다.

생존편향 보정치: (같은 풀 동일가중 − RSP) 의 시대별 주간 평균(음수면 0)을 후보 수익에서 뺀다.
RSP 는 실제 지수 구성종목의 실제 수익을 반영하므로 이 차이가 '상장폐지 종목이 빠지고 매주
재조정'한 우리 계산의 부풀림에 가깝다. 모멘텀류는 편향이 더 클 수 있어 하한이다.
"""
import math
import sys
from bisect import bisect_right
from datetime import date
from pathlib import Path
from statistics import NormalDist

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

ERAS = (("2005–08", "2005-01-01", "2008-12-31"),
        ("2009–12", "2009-01-01", "2012-12-31"),
        ("2013–18", "2013-01-01", "2018-12-31"),
        ("2019–23", "2019-01-01", "2023-12-31"),
        ("2024~", "2024-01-01", "2099-12-31"))
CRISES = (("금융위기 2007-10~2009-03", "2007-10-01", "2009-03-31"),
          ("코로나 2020-02~03", "2020-02-01", "2020-03-31"),
          ("2022 하락장", "2022-01-01", "2022-10-31"))
DESIGN = ("2013-01-01", "2099-12-31")
HOLDOUT = ("2005-01-01", "2012-12-31")
T_MIN = 3.0
ERA_FRAC = 0.8                 # 시대 블록의 80% 이상 (5개면 4개)
ERA_MIN_WEEKS = 26
MDD_FULL_TOL = 0.02
MDD_CRISIS_TOL = 0.05
CRISIS_MIN_WEEKS = 4
NW_LAGS = 4
COST_BP = {"large": 5.0, "small": 25.0}     # 편도 (수수료 + 슬리피지 가정)
CHECKS = (
    ("cagr", "연복리 > SPY (보정 후)"),
    ("alpha", f"알파 > 0 이고 Newey-West t ≥ {T_MIN}"),
    ("eras", "시대 블록 80% 이상에서 SPY 보다 높은 연복리"),
    ("pool", "같은 풀 동일가중보다 높은 연복리"),
    ("dd_full", f"최대낙폭이 SPY 보다 {MDD_FULL_TOL * 100:.0f}%p 넘게 나쁘지 않음"),
    ("dd_crisis", f"위기 구간 낙폭이 SPY + {MDD_CRISIS_TOL * 100:.0f}%p 이내"),
)


# ---------------------------------------------------------------- 수익 계열
class Frame:
    """같은 주 달력 위의 수익 계열들."""

    def __init__(self, signal: list[str], d0: list[str], d1: list[str]):
        self.signal, self.d0, self.d1 = signal, d0, d1
        self.r: dict[str, list[float]] = {}

    def add(self, name: str, rets: list[float]) -> "Frame":
        if len(rets) != len(self.signal):
            raise ValueError(f"{name}: 길이 {len(rets)} != 주 수 {len(self.signal)}")
        self.r[name] = list(rets)
        return self

    def idx(self, lo: str, hi: str) -> list[int]:
        return [i for i, s in enumerate(self.signal) if lo <= s <= hi]

    def pick(self, name: str, ix: list[int]) -> list[float]:
        return [self.r[name][i] for i in ix]

    def days(self, ix: list[int]) -> int:
        return (date.fromisoformat(self.d1[ix[-1]])
                - date.fromisoformat(self.d0[ix[0]])).days


def compound(rs: list[float]) -> float:
    eq = 1.0
    for r in rs:
        eq *= 1 + r
    return eq - 1


def cagr(rs: list[float], days: int) -> float | None:
    yrs = days / 365.25
    if not rs or yrs <= 0:
        return None
    return float((1 + compound(rs)) ** (1 / yrs) - 1)


def max_dd(rs: list[float]) -> float:
    eq, peak, mdd = 1.0, 1.0, 0.0
    for r in rs:
        eq *= 1 + r
        peak = max(peak, eq)
        mdd = min(mdd, eq / peak - 1)
    return float(mdd)


def nw_alpha_beta(y: list[float], x: list[float], lags: int = NW_LAGS) -> dict:
    """y = a + b x + e 의 OLS 와 Newey-West(Bartlett) 표준오차. 연율 알파는 주간 알파 × 52."""
    n = len(y)
    if n < 3 * (lags + 1):
        return {}
    Y = np.asarray(y, dtype="float64")
    X = np.column_stack([np.ones(n), np.asarray(x, dtype="float64")])
    xtx_inv = np.linalg.inv(X.T @ X)
    b = xtx_inv @ X.T @ Y
    u = Y - X @ b
    Xu = X * u[:, None]
    S = Xu.T @ Xu
    for l in range(1, lags + 1):
        G = Xu[l:].T @ Xu[:-l]
        S += (1 - l / (lags + 1)) * (G + G.T)
    V = xtx_inv @ S @ xtx_inv * n / (n - 2) if n > 2 else xtx_inv @ S @ xtx_inv
    se = np.sqrt(np.diag(V))
    return {"alpha_week": float(b[0]), "alpha_ann": float(b[0] * 52),
            "beta": float(b[1]), "t_alpha": float(b[0] / se[0]) if se[0] else None,
            "t_beta": float(b[1] / se[1]) if se[1] else None, "n": n}


# ---------------------------------------------------------------- 생존편향 보정
def survivorship_haircut(fr: Frame, pool: str = "pool", rsp: str = "rsp"
                         ) -> list[float]:
    """주마다 빼야 할 값 — 그 주가 속한 시대의 mean(max(pool − rsp, 0)). RSP 가 없으면 0."""
    out = [0.0] * len(fr.signal)
    if rsp not in fr.r or pool not in fr.r:
        return out
    for _, lo, hi in ERAS:
        ix = fr.idx(lo, hi)
        if len(ix) < ERA_MIN_WEEKS:
            continue
        diff = [fr.r[pool][i] - fr.r[rsp][i] for i in ix]
        h = max(0.0, sum(diff) / len(diff))
        for i in ix:
            out[i] = h
    return out


# ---------------------------------------------------------------- 평가
def crisis_dd(fr: Frame, name: str, ix_all: set[int]) -> dict:
    out = {}
    for label, lo, hi in CRISES:
        ix = [i for i in fr.idx(lo, hi) if i in ix_all]
        if len(ix) >= CRISIS_MIN_WEEKS:
            out[label] = max_dd(fr.pick(name, ix))
    return out


def evaluate(fr: Frame, name: str, lo: str = "0000-00-00", hi: str = "9999-12-31",
             haircut: bool = True) -> dict:
    """기간 [lo, hi] 에서 name 을 SPY·같은 풀 동일가중과 비교한다."""
    ix = fr.idx(lo, hi)
    if len(ix) < 2 * (NW_LAGS + 1):
        return {"weeks": len(ix), "error": "주 수 부족"}
    days = fr.days(ix)
    hc = survivorship_haircut(fr) if haircut else [0.0] * len(fr.signal)
    raw = fr.pick(name, ix)
    adj = [r - hc[i] for r, i in zip(raw, ix)]
    spy = fr.pick("spy", ix)
    reg = nw_alpha_beta(adj, spy)
    pool = fr.pick("pool", ix) if "pool" in fr.r else None
    eras = {}
    for label, elo, ehi in ERAS:
        ex = [i for i in ix if elo <= fr.signal[i] <= ehi]
        if len(ex) >= ERA_MIN_WEEKS:
            d = fr.days(ex)
            eras[label] = {"cand": cagr([r - hc[i] for r, i in
                                         zip(fr.pick(name, ex), ex)], d),
                           "spy": cagr(fr.pick("spy", ex), d)}
    need = math.ceil(ERA_FRAC * len(eras) - 1e-9) if eras else 0
    won = sum(1 for e in eras.values() if e["cand"] > e["spy"])
    ixset = set(ix)
    adj_fr = Frame(fr.signal, fr.d0, fr.d1)
    adj_fr.r = {name: [r - h for r, h in zip(fr.r[name], hc)], "spy": fr.r["spy"]}
    cd_c, cd_s = crisis_dd(adj_fr, name, ixset), crisis_dd(adj_fr, "spy", ixset)
    crisis = {k: (cd_c[k], cd_s[k]) for k in cd_c}
    m = {"weeks": len(ix), "from": fr.d0[ix[0]], "to": fr.d1[ix[-1]], "days": days,
         "cagr": cagr(adj, days), "cagr_raw": cagr(raw, days),
         "spy_cagr": cagr(spy, days),
         "rsp_cagr": cagr(fr.pick("rsp", ix), days) if "rsp" in fr.r else None,
         "pool_cagr": cagr(pool, days) if pool else None,
         "mdd": max_dd(adj), "spy_mdd": max_dd(spy), "reg": reg,
         "haircut_ann": (sum(hc[i] for i in ix) / len(ix)) * 52,
         "eras": eras, "eras_won": won, "eras_need": need, "crisis": crisis}
    t = reg.get("t_alpha")
    m["checks"] = {
        "cagr": m["cagr"] > m["spy_cagr"],
        "alpha": bool(t is not None and t >= T_MIN and reg["alpha_week"] > 0),
        "eras": bool(eras) and won >= need,
        "pool": None if pool is None else cagr(raw, days) > m["pool_cagr"],
        "dd_full": m["mdd"] >= m["spy_mdd"] - MDD_FULL_TOL,
        "dd_crisis": all(c >= s - MDD_CRISIS_TOL for c, s in crisis.values()),
    }
    m["checks"] = {k: (None if v is None else bool(v)) for k, v in m["checks"].items()}
    m["passed"] = all(v is True for v in m["checks"].values())
    return m


def verdict(fr: Frame, name: str) -> dict:
    """설계 기간 → 통과 때만 보류 기간 (한 번)."""
    out = {"design": evaluate(fr, name, *DESIGN), "holdout": None}
    if out["design"].get("passed"):
        out["holdout"] = evaluate(fr, name, *HOLDOUT)
    return out


# ---------------------------------------------------------------- 가중치 → 수익
def run_weights(sched: list[tuple[str, dict[str, float]]], prices, cost_bp: float
                ) -> tuple[list[str], list[str], list[str], list[float], list[float]]:
    """(신호일, {종목: 비중}) 목록 → (signal, d0, d1, 총수익, 순수익).

    비중 합이 1 보다 작으면 나머지는 현금(수익 0). 가격이 없는 종목은 빼고 남은 종목에
    원래 합계를 다시 나눈다. 진입 = 신호 다음 거래일 시가, 청산 = 다음 신호의 다음날 시가
    (거래가 없으면 마지막 종가). 비용 = 바꾼 비중 × cost_bp (편도), 흘러간 비중 기준.
    """
    sig, d0s, d1s, gross, net = [], [], [], [], []
    prev: dict[str, float] = {}
    for (s, w), (s2, _) in zip(sched, sched[1:]):
        d0, d1 = prices.entry_day(s), prices.entry_day(s2)
        if d0 is None or d1 is None or d0 >= d1:
            continue
        total = sum(w.values())
        rets = {}
        for t in w:
            a = prices.start_px(t, d0)
            b = prices.end_px(t, d1) if a else None
            if a and b:
                rets[t] = b / a - 1
        have = sum(w[t] for t in rets)
        wn = {t: w[t] * total / have for t in rets} if have > 0 else {}
        g = sum(wn[t] * rets[t] for t in wn)
        turnover = sum(abs(wn.get(t, 0.0) - prev.get(t, 0.0))
                       for t in set(wn) | set(prev))
        n = (1 - cost_bp / 1e4 * turnover) * (1 + g) - 1
        sig.append(s), d0s.append(d0), d1s.append(d1), gross.append(g), net.append(n)
        prev = ({t: wn[t] * (1 + rets[t]) / (1 + g) for t in wn} if g > -1 else {})
    return sig, d0s, d1s, gross, net


def ew(names) -> dict[str, float]:
    names = list(names)
    return {t: 1 / len(names) for t in names} if names else {}


def cap_weights(names, caps: dict[str, float]) -> dict[str, float]:
    ok = [t for t in names if caps.get(t)]
    tot = sum(caps[t] for t in ok)
    return {t: caps[t] / tot for t in ok} if tot > 0 else {}


# ---------------------------------------------------------------- 장부
def ledger_summary(path: Path) -> dict:
    """시도한 가설 수와 그에 맞는 Bonferroni 임계 z (양측 5%)."""
    import json
    d = json.loads(path.read_text(encoding="utf-8"))
    n = (sum(x.get("count", 1) for x in d.get("pre_registered", []))
         + sum(x.get("count", 1) for x in d.get("exploratory", [])))
    z = NormalDist().inv_cdf(1 - 0.025 / max(1, n))
    return {"n": n, "bonferroni_z": z, "t_min": T_MIN}


# ---------------------------------------------------------------- 출력
def pct(x, w=8) -> str:
    return f"{'-':>{w}}" if x is None else f"{x * 100:+{w - 1}.2f}%"


def fmt_t(t) -> str:
    """t 값 표시 — 완전 적합(표준오차 ≈ 0)이면 의미 없는 큰 값이 나오므로 >99 로 줄인다."""
    return "-" if t is None else (">99" if abs(t) > 99 else f"{t:.1f}")


def header() -> None:
    print(f"  {'후보':<26} {'연복리':>8} {'SPY':>8} {'알파(연)':>9} {'t':>5} {'베타':>5} "
          f"{'시대':>5} {'MDD':>8} {'SPY MDD':>8}  판정")


def row(label: str, m: dict, mark: bool = True) -> None:
    if "error" in m:
        print(f"  {label:<26} {m['error']}")
        return
    reg = m["reg"]
    t = reg.get("t_alpha")
    print(f"  {label:<26} {pct(m['cagr'])} {pct(m['spy_cagr'])} "
          f"{pct(reg.get('alpha_ann'), 9)} {fmt_t(t):>5} "
          f"{reg.get('beta', float('nan')):5.2f} {m['eras_won']}/{len(m['eras']):<3} "
          f"{pct(m['mdd'])} {pct(m['spy_mdd'])}  "
          f"{('통과' if m['passed'] else '탈락') if mark else ''}")


def fails(m: dict) -> list[str]:
    return [d for k, d in CHECKS if m["checks"].get(k) is False]
