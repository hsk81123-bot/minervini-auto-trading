# -*- coding: utf-8 -*-
"""후보 규칙 검증 — 가격만 쓰는 후보(C3·C4·C5)를 설계/보류 기간으로 나눠 잰다.

3단계에서 'RS 상위 5'는 7년간 기대값이 거의 0이었고, 같은 트렌드 템플릿 통과
종목에서 무작위로 고른 것보다 나빴다. 여기서는 통과 종목 풀 안에서 다른 방식으로
고르는 후보를 같은 조건으로 잰다. 후보마다 대조군 하나만 바꾼다.

  대조군  통과 종목 중 무작위 5 (200회) · 다음날 시가 · -8% 손절 / 3R 익절
  C3     RS ≥ 80 이고 종가가 50일선 위 10% 이내, RS 순 상위 5
         — 이미 많이 오른 종목을 피한다 (7년 데이터로 처음 검증)
  C4     RS 원점수 ÷ ATR% 순 상위 5, 손절은 ATR 2배(4~15%) / 3R 익절
         — 변동성 대비 강도. 평균 최대역행(-9%)이 -8% 손절보다 커서 노이즈에
           털리던 문제를 손절폭을 변동성에 맞춰 줄인다
  C5     RS ≥ 80 이고 종가가 VCP 피봇 아래 0~5% (아직 돌파 전), RS 순 상위 5.
         신호 뒤 5거래일 안에 '종가 > 피봇 & 거래량 ≥ 50일 평균 1.5배'인 날이
         오면 그 다음날 시가에 산다. 손절은 VCP 저점(2~8%) / 3R 익절.
         — 미너비니의 실제 진입(피봇 돌파). 거래량은 그 날 장 마감 뒤에야 아는
           값이라 장중 체결로 가정하지 않고 다음날 시가에 산다.

펀더멘털 후보 (.cache/us_fund_pit.json.gz 가 있을 때만 — pit_fund.py 참고).
신호일 d 에는 공시일 < d 인 SEC 숫자만 쓴다. 최근 분기가 200일 넘게 지났으면
데이터 없음으로 보고 뺀다. 성장률은 전년 동기 대비, 전년 값이 0 이하면 뺀다.
  C1     최근 분기 EPS +25% 이상 & 매출 +20% 이상, EPS 성장률 순 상위 5
         · -8% / 3R — SEPA 의 '왜 오르는가'
  C2     EPS 성장률이 최근 3개 분기 연속 상승 & 영업이익률이 전년 동기보다 높음,
         RS 순 상위 5 · -8% / 3R — 실적 가속 (코드33 의 본래 뜻)
  C1+C5  C5 와 같되 셋업을 C1 조건을 넘는 종목으로 한정

검증 방식 (결과를 보기 전에 고정):
  설계 기간 2019-02 ~ 2023-12 에서 아래를 모두 넘어야 하고, 넘은 후보만 보류 기간
  2024-01 ~ 끝을 한 번 계산한다(코드가 강제한다 — 탈락한 후보의 보류 기간 숫자는
  계산하지 않는다). 후보가 여럿이라 운으로 통과할 확률을 줄이려고 설계 기간에는
  무작위 대비 99번째 백분위를 요구한다.

  설계  기대값 > 0 · 종목 단위 95% 신뢰구간 하한 > 0 · 무작위 대비 99백분위 이상
        · 기여 상위 2종목을 빼도 무작위 중앙값보다 높음 · 플러스인 해 절반 초과
  보류  같은 항목, 무작위 대비는 95백분위

R-배수는 비용 전 가격 기준이다. 생존 편향(지금 상장된 종목만)은 history.py 와 같다.

사용법:
    python screener/candidates.py [--json candidates.json]
"""
import json
import random
import sys
from pathlib import Path
from statistics import mean

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import history as H
import papertrade as P
import pit_fund as PF
import robust as R
import validate as V

TOP = 5
N_PERM = R.N_PERM
SEED = R.SEED
DESIGN_END = "2023-12-31"        # 이 날까지가 설계 기간 (진입 신호일 기준)
BASE_STOP = 8.0                  # %
TARGET_R = 3.0                   # 익절 = 손절폭 × 3
C3_RS, C3_MAX_EXT = 80, 0.10
C4_ATR_MULT, C4_MIN, C4_MAX = 2.0, 4.0, 15.0
C5_RS, C5_BELOW, C5_DAYS, C5_VOL = 80, 0.05, 5, 1.5
C5_MIN_STOP, C5_MAX_STOP = 2.0, 8.0
C1_EPS, C1_REV = 0.25, 0.20

DESIGN_PCTL, HOLDOUT_PCTL = 0.99, 0.95
# 사후 등록 가설 — 설계 기간에서 탈락했지만 결과를 본 뒤 보류 기간을 한 번 재기로
# 정한 후보. 기준은 같다(5개 모두, 무작위 대비 95백분위). 설계 결과를 보고 골랐으므로
# 통과해도 미리 정한 후보의 통과보다 약한 증거다. 여기에 후보를 더 넣지 않는다.
POSTHOC = {
    "C1+C5 실적+돌파": "2026-10-13 등록 · 설계 기간 99백분위 · CI 하한 -0.11R 만 탈락",
}
CHECKS = (
    ("positive", "기대값 > 0"),
    ("ci", "종목 단위 95% 신뢰구간 하한 > 0"),
    ("beats_random", "무작위 대비 기준 백분위 이상 (설계 99 · 보류 95)"),
    ("survives_top2", "기여 상위 2종목을 빼도 무작위 중앙값보다 높음"),
    ("years", "기대값 플러스인 해가 절반 초과"),
)


# ---------------------------------------------------------------- 보조 지표
def aux_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """후보 규칙에 필요한 날짜별 지표 — 그 날까지의 데이터만 쓴다."""
    c = df["Close"].astype("float64")
    h = df["High"].astype("float64")
    lo = df["Low"].astype("float64")
    pc = c.shift(1)
    tr = pd.concat([h - lo, (h - pc).abs(), (lo - pc).abs()], axis=1).max(axis=1)
    return pd.DataFrame({
        "close": c,
        "ma50": c.rolling(50).mean(),
        "atr_pct": tr.rolling(14).mean() / c,
        # 돌파일 거래량 비교 기준은 '전날까지' 50일 평균 (그 날 거래량이 섞이지 않게)
        "vol50_prev": df["Volume"].astype("float64").rolling(50).mean().shift(1),
        "volume": df["Volume"].astype("float64"),
    }, index=df.index)


def lookup(aux: dict[str, pd.DataFrame], t: str, d: str) -> pd.Series | None:
    x = aux.get(t)
    if x is None:
        return None
    ts = pd.Timestamp(d)
    return x.loc[ts] if ts in x.index else None


# ---------------------------------------------------------------- 후보 선정
def pick_c3(snap: dict, aux: dict) -> list[dict]:
    rows = []
    for t, r in snap["rows"].items():
        a = lookup(aux, t, snap["date"])
        if a is None or r["rs"] < C3_RS or not a["ma50"] > 0:
            continue
        if a["close"] / a["ma50"] - 1 <= C3_MAX_EXT:
            rows.append({"ticker": t, "rs": r["rs"], "raw": r.get("raw") or 0})
    rows.sort(key=lambda x: (-x["rs"], -x["raw"], x["ticker"]))
    return [dict(x, stop=BASE_STOP, entry_date=snap["date"]) for x in rows[:TOP]]


def c4_stop(atr_pct: float) -> float:
    return min(C4_MAX, max(C4_MIN, C4_ATR_MULT * atr_pct * 100))


def pick_c4(snap: dict, aux: dict) -> list[dict]:
    rows = []
    for t, r in snap["rows"].items():
        a = lookup(aux, t, snap["date"])
        if a is None or not a["atr_pct"] > 0 or not r.get("raw"):
            continue
        rows.append({"ticker": t, "rs": r["rs"], "score": r["raw"] / a["atr_pct"],
                     "stop": c4_stop(float(a["atr_pct"]))})
    rows.sort(key=lambda x: (-x["score"], x["ticker"]))
    return [dict(x, entry_date=snap["date"]) for x in rows[:TOP]]


def breakout_day(aux_t: pd.DataFrame, signal: str, pivot: float) -> str | None:
    """신호 다음 C5_DAYS 거래일 안에 '종가 > 피봇 & 거래량 ≥ 전날까지 50일 평균
    × C5_VOL' 인 첫 날. 그 날 장 마감 뒤에야 확인되므로 매수는 그 다음날 시가."""
    after = aux_t[aux_t.index > pd.Timestamp(signal)].iloc[:C5_DAYS]
    for d, a in after.iterrows():
        if a["close"] > pivot and a["vol50_prev"] > 0 \
                and a["volume"] >= C5_VOL * a["vol50_prev"]:
            return d.strftime("%Y-%m-%d")
    return None


def c5_stop(entry_ref: float, vstop: float | None) -> float:
    """VCP 저점까지의 거리(%) — 2~8% 로 묶는다. 저점이 없거나 위에 있으면 8%."""
    if not vstop or vstop >= entry_ref:
        return C5_MAX_STOP
    return min(C5_MAX_STOP, max(C5_MIN_STOP, (1 - vstop / entry_ref) * 100))


def pick_c5(snap: dict, aux: dict, prices: dict, vcp_fn, allow=None
            ) -> tuple[list[dict], int]:
    """(체결된 거래, 셋업 수). 셋업은 RS 순 상위 5, 그중 돌파가 확인된 것만 산다.
    allow(ticker) 가 있으면 그 조건을 넘는 종목만 셋업이 될 수 있다 (C1+C5)."""
    setups = []
    for t, r in snap["rows"].items():
        if r["rs"] < C5_RS or (allow is not None and not allow(t)):
            continue
        piv = r.get("pivot")
        vstop = r.get("vstop")
        if r.get("vcp") is None:              # 재구성 때 VCP 를 안 잰 종목
            v = vcp_fn(prices[t], snap["date"])
            piv, vstop = v.get("pivot"), v.get("stop")
        a = lookup(aux, t, snap["date"])
        if not piv or a is None:
            continue
        if piv * (1 - C5_BELOW) <= a["close"] <= piv:
            setups.append({"ticker": t, "rs": r["rs"], "raw": r.get("raw") or 0,
                           "pivot": float(piv), "vstop": vstop})
    setups.sort(key=lambda x: (-x["rs"], -x["raw"], x["ticker"]))
    setups = setups[:TOP]
    filled = []
    for s in setups:
        d = breakout_day(aux[s["ticker"]], snap["date"], s["pivot"])
        if d:
            filled.append(dict(s, entry_date=d,
                               stop=c5_stop(s["pivot"], s["vstop"])))
    return filled, len(setups)


def c1_ok(f: dict | None) -> bool:
    return (f is not None and f["eps_growth"] is not None
            and f["revenue_growth"] is not None
            and f["eps_growth"] >= C1_EPS and f["revenue_growth"] >= C1_REV)


def c2_ok(f: dict | None) -> bool:
    return (f is not None and f["eps_accel"] is True
            and f["opm_change"] is not None and f["opm_change"] > 0)


def pick_c1(snap: dict, fund_at) -> list[dict]:
    rows = []
    for t, r in snap["rows"].items():
        f = fund_at(t, snap["date"])
        if c1_ok(f):
            rows.append({"ticker": t, "rs": r["rs"], "eps_growth": f["eps_growth"]})
    rows.sort(key=lambda x: (-x["eps_growth"], -x["rs"], x["ticker"]))
    return [dict(x, stop=BASE_STOP, entry_date=snap["date"]) for x in rows[:TOP]]


def pick_c2(snap: dict, fund_at) -> list[dict]:
    rows = [{"ticker": t, "rs": r["rs"], "raw": r.get("raw") or 0}
            for t, r in snap["rows"].items() if c2_ok(fund_at(t, snap["date"]))]
    rows.sort(key=lambda x: (-x["rs"], -x["raw"], x["ticker"]))
    return [dict(x, stop=BASE_STOP, entry_date=snap["date"]) for x in rows[:TOP]]


def fund_lookup(timeline: dict):
    """(종목, 신호일) → 그 날 알 수 있던 지표. 같은 질의가 반복되므로 기억해 둔다."""
    memo: dict = {}

    def at(t: str, d: str) -> dict | None:
        if (t, d) not in memo:
            memo[(t, d)] = PF.lookup(timeline.get(t), d)
        return memo[(t, d)]
    return at


def coverage(snaps: list[dict], fund_at) -> dict[str, tuple[int, int, int, int]]:
    """연도별 (통과 종목·주, 펀더멘털 있음, C1 충족, C2 충족)."""
    out: dict[str, list[int]] = {}
    for s in snaps:
        c = out.setdefault(s["date"][:4], [0, 0, 0, 0])
        for t in s["rows"]:
            f = fund_at(t, s["date"])
            c[0] += 1
            c[1] += f is not None
            c[2] += c1_ok(f)
            c[3] += c2_ok(f)
    return {y: tuple(v) for y, v in sorted(out.items())}


def vcp_lookup():
    """(가격표, 날짜) → 그 날까지 2년 구간의 VCP. 같은 질의는 기억해 둔다."""
    from vcp import detect_vcp
    cache: dict = {}

    def vcp_at(df, d):
        key = (id(df), d)
        if key not in cache:
            ts = pd.Timestamp(d)
            win = df[(df.index > ts - pd.Timedelta(H.WINDOW_2Y)) & (df.index <= ts)]
            cache[key] = detect_vcp(win[["Open", "High", "Low", "Close",
                                         "Volume"]].astype("float64"))
        return cache[key]
    return vcp_at


def build(snaps: list[dict], prices: dict, aux: dict, timeline: dict | None
          ) -> tuple[dict[str, list], dict, list]:
    """(후보 → 코호트, 메타, 현재 규칙 코호트). timeline 이 없으면 C1·C2 는 뺀다."""
    vcp_at = vcp_lookup()
    cands = {
        "C3 덜 오른 강세주": [(s["date"], pick_c3(s, aux)) for s in snaps],
        "C4 변동성 조정": [(s["date"], pick_c4(s, aux)) for s in snaps],
    }
    c5, n_setup = [], 0
    for s in snaps:
        filled, n = pick_c5(s, aux, prices, vcp_at)
        c5.append((s["date"], filled))
        n_setup += n
    cands["C5 피봇 돌파 진입"] = c5
    n_fill = sum(len(p) for _, p in c5)

    fund_doc = None
    if timeline is not None:
        fund_at = fund_lookup(timeline)
        cov = coverage(snaps, fund_at)
        tot = [sum(v[i] for v in cov.values()) for i in range(4)]
        print(f"\n  [펀더멘털 커버리지] 공시일 < 신호일 · 최근 분기 {PF.STALE_DAYS}일 이내")
        print(f"  {'연도':<6} {'통과 종목·주':>12} {'데이터 있음':>10} "
              f"{'C1 충족':>8} {'C2 충족':>8}")
        for y, (n, have, a, b) in list(cov.items()) + [("전체", tuple(tot))]:
            print(f"  {y:<6} {n:>12} {have:>6} ({have / n * 100:3.0f}%) "
                  f"{a:>8} {b:>8}")
        cands["C1 실적 성장"] = [(s["date"], pick_c1(s, fund_at)) for s in snaps]
        cands["C2 실적 가속"] = [(s["date"], pick_c2(s, fund_at)) for s in snaps]
        c15, n15 = [], 0
        for s in snaps:
            filled, n = pick_c5(s, aux, prices, vcp_at,
                                lambda t, d=s["date"]: c1_ok(fund_at(t, d)))
            c15.append((s["date"], filled))
            n15 += n
        cands["C1+C5 실적+돌파"] = c15
        fund_doc = {"coverage": cov, "c15_setups": n15,
                    "c15_filled": sum(len(p) for _, p in c15)}
    else:
        print(f"\n  펀더멘털 타임라인 없음({PF.TIMELINE.name}) — C1·C2 생략")
    # 3단계의 현재 규칙(RS 상위 5)을 같은 청산 조건으로
    ref = [(c, [dict(r, stop=BASE_STOP, entry_date=c) for r in rows])
           for c, rows in V.cohorts(snaps + [snaps[-1]], TOP, 1)]
    return cands, {"c5_setups": n_setup, "c5_filled": n_fill,
                   "fund": fund_doc}, ref


# ---------------------------------------------------------------- 실행·집계
def simulate_picks(cohorts: list[tuple[str, list[dict]]], ohlc, index
                   ) -> list[dict]:
    """후보마다 손절폭이 다르므로 종목별 파라미터로 simulate 를 부른다."""
    out = []
    for cohort, picks in cohorts:
        for p in picks:
            sim = P.simulate(ohlc.get(p["ticker"], []), p["entry_date"],
                             -p["stop"], TARGET_R * p["stop"], None, None, None,
                             "next_open", index.get(p["ticker"]))
            if sim["status"] != "측정 불가":
                out.append({"ticker": p["ticker"], "cohort": cohort, **sim})
    return out


def in_period(trades: list[dict], lo: str, hi: str) -> list[dict]:
    return [t for t in trades if lo <= t["cohort"] <= hi]


def evaluate(trades: list[dict], rnd: list[list[dict]], pctl: float) -> dict:
    """미리 정한 5개 기준. rnd 는 같은 기간 무작위 대조군 거래 목록들."""
    e = R.expectancy(trades)
    ci = R.cluster_ci(trades)
    rnd_e = [x for x in (R.expectancy(t) for t in rnd) if x is not None]
    pct = R.percentile_of(e, rnd_e) if (e is not None and rnd_e) else None
    contrib = R.contributions(trades)
    drop = {c[0] for c in contrib[:2]}
    e2 = R.expectancy(R.without(trades, drop))
    r2 = R.median_of([x for x in (R.expectancy(R.without(t, drop)) for t in rnd)
                      if x is not None])
    years = H.by_year(trades)
    pos = sum(1 for v in years.values() if mean(v) > 0)
    res = {
        "positive": None if e is None else e > 0,
        "ci": None if ci is None else ci[0] > 0,
        "beats_random": None if pct is None else pct >= pctl,
        "survives_top2": None if (e2 is None or r2 is None) else e2 > r2,
        "years": (pos > len(years) / 2) if years else None,
    }
    m = P.perf(V.marked(trades))
    return {"trades": len(V.marked(trades)), "tickers": len(contrib),
            "expectancy": e, "ci": ci, "win": m.get("win_rate"),
            "payoff": m.get("payoff"), "random_median": R.median_of(rnd_e),
            "percentile": pct, "top2_dropped": sorted(drop), "e_top2": e2,
            "random_median_top2": r2, "years_pos": pos, "years_n": len(years),
            "checks": res, "passed": all(v is True for v in res.values())}


def show(label: str, ev: dict, extra: str = "") -> None:
    pc = "-" if ev["percentile"] is None else f"{ev['percentile'] * 100:5.1f}"
    print(f"  {B_wpad(label, 26)} {ev['trades']:>5} {ev['tickers']:>5} "
          f"{P.rfmt(ev['expectancy'])} {R.fmt_ci(ev['ci'])} "
          f"{P.rfmt(ev['random_median'])} {pc:>6} "
          f"{P.rfmt(ev['e_top2'])} {ev['years_pos']}/{ev['years_n']}  "
          f"{'통과' if ev['passed'] else '탈락'}{extra}")


def reasons(evs: dict) -> None:
    for k, ev in evs.items():
        fails = [d for key, d in CHECKS if ev["checks"][key] is not True]
        if fails:
            print(f"    {k} 탈락 이유: {' / '.join(fails)}")


def B_wpad(s, n):
    import backtest as B
    return B.wpad(s, n)


def header() -> None:
    print(f"  {B_wpad('후보', 26)} {'거래':>5} {'종목':>5} {'기대값':>7} "
          f"{'종목단위 95% CI':>18} {'무작위중앙':>8} {'백분위':>6} "
          f"{'상위2제외':>8} {'플러스해':>5}  판정")


def main(argv: list[str]) -> int:
    out_json = argv[argv.index("--json") + 1] if "--json" in argv else None
    data = H.load_prices()
    prices = data["prices"]
    snaps = [s for s in H.load_snaps() if s["weekly"]][:-1]   # 마지막은 진입 불가
    first, last = snaps[0]["date"], snaps[-1]["date"]
    periods = {"설계": (first, DESIGN_END), "보류": ("2024-01-01", last)}

    pool = {t for s in snaps for t in s["rows"]}
    print(f"\n{'=' * 100}")
    print(f"  US 후보 규칙 검증 (C1~C5)  주간 신호 {len(snaps)}개 "
          f"({first} ~ {last}) · 설계 ~{DESIGN_END} / 보류 2024-01-01~")
    print(f"{'=' * 100}")
    print(f"  공통: 트렌드 템플릿 통과 종목 안에서 선정 · 다음날 시가 진입 · 익절 = "
          f"손절폭 × {TARGET_R:.0f} · 무작위 {N_PERM}회 · 시드 {SEED}")
    print(f"  ※ 생존 편향: 지금 상장된 종목뿐 — 결과가 좋게 기운다", flush=True)
    aux = {t: aux_indicators(prices[t]) for t in pool if t in prices}
    ohlc, index = H.to_ohlc(prices, pool, first)

    timeline = PF.load_timeline() if PF.TIMELINE.exists() else None
    cands, meta, ref = build(snaps, prices, aux, timeline)
    n_setup, n_fill, fund_doc = meta["c5_setups"], meta["c5_filled"], meta["fund"]

    # 대조군: 통과 종목 중 무작위 5, 같은 청산
    rnd_all = []
    for i in range(N_PERM):
        rc = V.random_cohorts(snaps + [snaps[-1]], TOP, 1, seed=SEED + i)
        rnd_all.append(simulate_picks(
            [(c, [dict(r, stop=BASE_STOP, entry_date=c) for r in rows])
             for c, rows in rc], ohlc, index))

    trades = {k: simulate_picks(v, ohlc, index) for k, v in cands.items()}
    ref_tr = simulate_picks(ref, ohlc, index)

    doc = {"design_end": DESIGN_END, "first": first, "last": last,
           "c5_setups": n_setup, "c5_filled": n_fill, "fundamentals": fund_doc,
           "design": {}, "holdout": {}}
    lo, hi = periods["설계"]
    rnd_d = [in_period(t, lo, hi) for t in rnd_all]
    print(f"\n  [설계 기간] {lo} ~ {hi} — 기준: 무작위 대비 "
          f"{DESIGN_PCTL * 100:.0f}백분위")
    header()
    show("참고: 현재 규칙(RS 상위5)",
         evaluate(in_period(ref_tr, lo, hi), rnd_d, DESIGN_PCTL))
    passed = []
    for k, tr in trades.items():
        ev = evaluate(in_period(tr, lo, hi), rnd_d, DESIGN_PCTL)
        doc["design"][k] = ev
        extra = (f"  (셋업 {n_setup} 중 돌파 체결 {n_fill})" if k.startswith("C5")
                 else f"  (셋업 {fund_doc['c15_setups']} 중 돌파 체결 "
                      f"{fund_doc['c15_filled']})" if k.startswith("C1+C5")
                 else "")
        show(k, ev, extra)
        if ev["passed"]:
            passed.append(k)
    reasons(doc["design"])

    lo, hi = periods["보류"]
    print(f"\n  [보류 기간] {lo} ~ {hi} — 설계 기간 통과 후보(+ 사후 등록 가설)만 "
          f"한 번 계산 · 기준: 무작위 대비 {HOLDOUT_PCTL * 100:.0f}백분위")
    posthoc = [k for k in POSTHOC if k in trades and k not in passed]
    if not passed:
        print(f"    설계 기간을 통과한 후보가 없다")
    if not passed and not posthoc:
        print(f"    보류 기간은 계산하지 않는다")
    else:
        for k in posthoc:
            print(f"    사후 등록: {k} — {POSTHOC[k]}")
            print(f"      설계 결과를 본 뒤 고른 가설이라 통과해도 약한 증거다")
        rnd_h = [in_period(t, lo, hi) for t in rnd_all]
        header()
        for k in passed + posthoc:
            ev = evaluate(in_period(trades[k], lo, hi), rnd_h, HOLDOUT_PCTL)
            ev["posthoc"] = k in posthoc
            doc["holdout"][k] = ev
            show(k + (" (사후)" if k in posthoc else ""), ev)
        reasons(doc["holdout"])
    print(f"\n  ※ 생존 편향 때문에 모든 숫자는 실제보다 좋게 나왔을 가능성이 크다")

    if out_json:
        Path(out_json).write_text(json.dumps(doc, ensure_ascii=False, indent=2,
                                             default=str), encoding="utf-8")
        print(f"  → {out_json}")
    return 0


if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main(sys.argv[1:]))
