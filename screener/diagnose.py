# -*- coding: utf-8 -*-
"""왜 트렌드 템플릿이 SPY 에 졌나 — 원인 분해와 이상치 분석 (진단 · 판정 아님).

template_check.py 의 결과(S&P 500 시점별 구성종목 중 템플릿 통과 종목 전부 동일가중,
2019-02~2023-12 연복리 -1.25%, 같은 구성종목 전체 동일가중 +12.47%, SPY +13.66%)를
나눠 본다. 이미 내린 판정은 바꾸지 않는다. 여기서 본 변형은 '탐색'이며 여러 개를
본 만큼 다중 비교 문제가 있다 — 새 규칙으로 쓰려면 앞으로의 데이터로 다시 검증한다.

  0) 버그 배제    우리 '전체 동일가중'을 RSP(동일가중 ETF)와 같은 구간으로 대조 ·
                 무작위 주 손 검산 · 가격 이상치 탐지와 제외 재계산
  1) 격차 분해    선별(통과 종목의 미래 수익) · 진입/이탈 타이밍 · 시장 국면 ·
                 섹터·규모 (Brinson: 배분 + 선택)
  2) 이상치       종목 기여 상·하위 · 극단 주 · 거래 단위(-8%/3R) 극단값,
                 각각 데이터 의심 / 사건(하루 ±15%) / 구조(점진) 로 분류
  3) 탐색         보유 기간 · 상위 N 집중 · 지수 200일선 타이밍 · 템플릿 조건 빼기

수익은 비용 전(gross) 시가~시가다 (비용은 template_check 에서 반영했고 격차에 비해 작다).
템플릿 바구니(rp)와 전체 동일가중(rb)은 같은 주, 같은 가격으로 잰다 — 차이는 고른 종목뿐.

사용법 (spx_validate members/prices/rebuild 산출물 필요):
    python screener/diagnose.py [--json diagnose.json] [--periods 2005-01-01:2018-12-31,...]
    [--no-trades] [--no-ablation]
"""
import json
import random
import sys
from bisect import bisect_right
from datetime import date
from pathlib import Path
from statistics import mean, median

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import candidates as C
import history as H
import spx_validate as S
import template_check as T
import validate as V

HORIZONS = (1, 4, 13, 26)
SEED = 20261013
SPIKE, BIG_DAY, EVENT_DAY = 0.40, 0.50, 0.15
EVENTS = (
    ("2020 급락", "2020-02-19", "2020-03-23"),
    ("2020 V자 반등", "2020-03-24", "2020-08-31"),
    ("2020-11 백신 로테이션", "2020-11-02", "2021-03-31"),
    ("2022 하락장", "2022-01-03", "2022-10-12"),
    ("2022-10 이후 반등", "2022-10-13", "2023-12-29"),
    ("2024 이후", "2024-01-01", "2099-12-31"),
)
CONDS = (
    ("c1", "종가 > 150·200일선"),
    ("c2", "150일선 > 200일선"),
    ("c3", "200일선 1개월 상승"),
    ("c4", "50일선 > 150·200일선"),
    ("c5", "종가 > 50일선"),
    ("c6", "52주 저점 +30% 이상"),
    ("c7", "52주 고점 -25% 이내"),
    ("c8", "RS ≥ 70"),
)


# ---------------------------------------------------------------- 기본 표
def build_weeks(snaps: list[dict], mem_fn, p: T.Prices, spy: S.Spy) -> list[dict]:
    """주마다 {signal, d0, d1, rets(구성종목 수익), pass(통과 ∩ 가격 있음), spy}."""
    weeks = []
    for k in range(len(snaps) - 1):
        sig = snaps[k]["date"]
        d0, d1 = p.entry_day(sig), p.entry_day(snaps[k + 1]["date"])
        if d0 is None or d1 is None or d0 >= d1:
            continue
        rets = {}
        for t in mem_fn(sig):
            a = p.start_px(t, d0)
            b = p.end_px(t, d1) if a else None
            if a and b:
                rets[t] = b / a - 1
        passed = set(snaps[k]["rows"]) & set(rets)
        weeks.append({"signal": sig, "d0": d0, "d1": d1, "rets": rets,
                      "pass": passed, "rows": snaps[k]["rows"],
                      "spy": spy.at(d1, "open") / spy.at(d0, "open") - 1})
    return weeks


def ew(rets: dict[str, float], names) -> float | None:
    xs = [rets[t] for t in names if t in rets]
    return mean(xs) if xs else None


def rp(w: dict) -> float:
    """템플릿 바구니 수익 — 통과 종목이 없으면 현금(0)."""
    return ew(w["rets"], w["pass"]) or 0.0


def rb(w: dict) -> float:
    return ew(w["rets"], w["rets"]) or 0.0


def ann(rs: list[float]) -> float | None:
    """주간 수익 목록 → 연복리 (주 수 기준)."""
    if not rs:
        return None
    eq = 1.0
    for r in rs:
        eq *= 1 + r
    return eq ** (52 / len(rs)) - 1


def in_win(w: dict, lo: str, hi: str) -> bool:
    return lo <= w["signal"] <= hi


def pct(x, w: int = 8) -> str:
    return f"{'-':>{w}}" if x is None else f"{x * 100:+{w - 1}.2f}%"


def pp(x, w: int = 8) -> str:
    """연 %p (산술 합 기준)."""
    return f"{'-':>{w}}" if x is None else f"{x * 100:+{w - 3}.2f}%p"


def wp(s: str, n: int) -> str:
    return C.B_wpad(s, n)


class Forward:
    """신호 k 에서 h 주 뒤까지의 종목별 수익 (다음날 시가 → h 번째 다음 신호의 다음날 시가)."""

    def __init__(self, weeks: list[dict], p: T.Prices):
        self.weeks, self.p = weeks, p
        self.memo: dict = {}

    def get(self, k: int, h: int) -> dict[str, float] | None:
        if k + h > len(self.weeks):
            return None
        key = (k, h)
        if key not in self.memo:
            w = self.weeks[k]
            end = self.weeks[k + h - 1]["d1"]
            out = {}
            for t in w["rets"]:
                a = self.p.start_px(t, w["d0"])
                b = self.p.end_px(t, end) if a else None
                if a and b:
                    out[t] = b / a - 1
            self.memo[key] = out
        return self.memo[key]

    def past(self, k: int, h: int, names) -> dict[str, float]:
        """신호 k 직전 h 주 동안의 수익 (이미 얼마나 올랐나)."""
        if k - h < 0:
            return {}
        a_d, b_d = self.weeks[k - h]["d0"], self.weeks[k]["d0"]
        out = {}
        for t in names:
            a = self.p.start_px(t, a_d)
            b = self.p.end_px(t, b_d) if a else None
            if a and b:
                out[t] = b / a - 1
        return out


def block_ci(xs: list[float], block: int) -> tuple[float, float] | None:
    return T.block_ci(xs, block=max(1, block))


# ---------------------------------------------------------------- 0) 버그 배제
def daily_flags(px: dict, tickers, lo: str, hi: str) -> dict[str, list]:
    """하루 종가 변화가 ±50% 넘거나, +40% 뒤 5일 안에 -25% 되돌림(또는 반대)인 날."""
    out: dict[str, list] = {}
    lo_t, hi_t = pd.Timestamp(lo), pd.Timestamp(hi)
    for t in tickers:
        df = px.get(t)
        if df is None:
            continue
        c = df["Close"].astype("float64")
        c = c[(c.index >= lo_t) & (c.index <= hi_t)].dropna()
        if len(c) < 3:
            continue
        r = c.pct_change()
        fwd5 = c.shift(-5) / c - 1
        bad = r[(r.abs() > BIG_DAY)
                | ((r > SPIKE) & (fwd5 < -0.25)) | ((r < -SPIKE) & (fwd5 > 0.25))]
        if len(bad):
            out[t] = [(d.strftime("%Y-%m-%d"), float(v)) for d, v in bad.items()]
    return out


def biggest_day(px: dict, t: str, lo: str, hi: str) -> tuple[str, float] | None:
    df = px.get(t)
    if df is None:
        return None
    c = df["Close"].astype("float64")
    c = c[(c.index >= pd.Timestamp(lo)) & (c.index <= pd.Timestamp(hi))].dropna()
    r = c.pct_change().dropna()
    if r.empty:
        return None
    d = r.abs().idxmax()
    return d.strftime("%Y-%m-%d"), float(r[d])


def classify(flagged: bool, big: tuple[str, float] | None) -> str:
    if flagged:
        return "데이터 의심"
    if big and abs(big[1]) >= EVENT_DAY:
        return "사건(하루 ±15%↑)"
    return "구조(점진)"


def section0(weeks, px, spy, rsp) -> tuple[dict, set]:
    print(f"\n  [0] 버그 배제")
    out = {}
    # (a) RSP 대조 — 우리 '전체 동일가중'이 실제 동일가중 ETF 와 비슷한가
    if rsp is not None:
        years: dict[str, list] = {}
        for w in weeks:
            r = rsp.at(w["d1"], "open") / rsp.at(w["d0"], "open") - 1
            years.setdefault(w["signal"][:4], []).append((rb(w), r, w["spy"]))
        print(f"    (a) 같은 시가~시가 구간 연도별 — 우리 전체 동일가중 vs RSP "
              f"(동일가중 ETF, 분기 리밸런싱) vs SPY")
        print(f"      {'연도':<6} {'우리 EW':>9} {'RSP':>9} {'차이':>9} {'SPY':>9}")
        rows = {}
        for y, xs in sorted(years.items()):
            a = ann_total([x[0] for x in xs])
            b = ann_total([x[1] for x in xs])
            s = ann_total([x[2] for x in xs])
            rows[y] = (a, b, s)
            print(f"      {y:<6} {pct(a, 9)} {pct(b, 9)} {pct(a - b, 9)} {pct(s, 9)}")
        diffs = [abs(a - b) for a, b, _ in rows.values()]
        print(f"      → 연도별 |차이| 평균 {mean(diffs) * 100:.1f}%p, 최대 "
              f"{max(diffs) * 100:.1f}%p (생존 편향·리밸런싱 주기 차이로 우리 쪽이 "
              f"조금 높게 나오는 것이 정상)")
        out["rsp"] = rows
    else:
        print(f"    (a) RSP 가격을 받지 못해 대조 생략")
    # (b) 손 검산 — 같은 주를 원시 가격표로 다시 계산
    rng = random.Random(SEED)
    picks = rng.sample(range(len(weeks)), min(5, len(weeks)))
    diffs = []
    for k in picks:
        w = weeks[k]
        xs = []
        for t in w["pass"]:
            o = px[t]["Open"].astype("float64")
            a = o.get(pd.Timestamp(w["d0"]))
            b = o.get(pd.Timestamp(w["d1"]))
            if b is None or not b == b:
                cl = px[t]["Close"].astype("float64")
                b = cl[cl.index <= pd.Timestamp(w["d1"])].dropna().iloc[-1]
            xs.append(b / a - 1)
        diffs.append(abs((mean(xs) if xs else 0.0) - rp(w)))
    print(f"    (b) 무작위 {len(picks)}주 손 검산: 바구니 수익 최대 차이 "
          f"{max(diffs):.2e} ({', '.join(weeks[k]['signal'] for k in sorted(picks))})")
    out["handcheck_max_diff"] = max(diffs)
    # (c) 미래 데이터 — 진입은 반드시 신호 다음 거래일
    ok = all(w["d0"] > w["signal"] for w in weeks)
    print(f"    (c) 진입일 > 신호일: {'모든 주 확인' if ok else '위반 있음!'} · 통과 판정은 "
          f"신호일까지의 가격만 쓴다 (history.indicators — test_history 가 스크리너와 "
          f"같은 값임을 확인)")
    out["lookahead_ok"] = ok
    # (d) 가격 이상치
    names = set().union(*(w["rets"].keys() for w in weeks))
    flags = daily_flags(px, names, weeks[0]["d0"], weeks[-1]["d1"])
    big_w = sorted(((r, t, w["signal"]) for w in weeks for t, r in w["rets"].items()
                    if r > 1.0 or r < -0.6), key=lambda x: -abs(x[0]))
    print(f"    (d) 가격 이상치: 하루 ±{BIG_DAY * 100:.0f}% 초과 또는 급등락 후 되돌림 "
          f"종목 {len(flags)}개 · 주간 +100% 초과/-60% 미만 {len(big_w)}건")
    for r, t, sig in big_w[:10]:
        print(f"        {t:<6} {sig}  주간 {pct(r)}  {'(일간 플래그)' if t in flags else ''}")
    flagged = set(flags)
    clean = [dict(w, rets={t: r for t, r in w["rets"].items() if t not in flagged},
                  **{"pass": w["pass"] - flagged}) for w in weeks]
    a0, b0 = ann([rp(w) for w in weeks]), ann([rb(w) for w in weeks])
    a1, b1 = ann([rp(w) for w in clean]), ann([rb(w) for w in clean])
    print(f"        플래그 종목을 양쪽에서 빼면: 템플릿 {pct(a0).strip()} → "
          f"{pct(a1).strip()} · 전체 동일가중 {pct(b0).strip()} → {pct(b1).strip()} · "
          f"격차 {pp(a0 - b0).strip()} → {pp(a1 - b1).strip()}")
    out.update({"flagged": {t: v[:3] for t, v in flags.items()},
                "weekly_extremes": big_w[:20],
                "cagr_before": (a0, b0), "cagr_without_flagged": (a1, b1)})
    return out, flagged


def ann_total(rs: list[float]) -> float:
    eq = 1.0
    for r in rs:
        eq *= 1 + r
    return eq - 1


# ---------------------------------------------------------------- 1) 격차 분해
def section1(weeks, fw: Forward, sectors, dv) -> dict:
    out = {}
    K = len(weeks)
    gap = [rp(w) - rb(w) for w in weeks]
    print(f"\n  [1] 격차 분해 — 템플릿 바구니(rp) − 같은 주 전체 동일가중(rb), "
          f"주간 산술 평균 × 52 = 연 {pp(mean(gap) * 52).strip()}")
    out["gap_annual"] = mean(gap) * 52

    # 1-1 선별: 통과 종목이 미래에 더 오르나
    print(f"\n    1-1 선별 — 통과 vs 미통과 구성종목의 향후 수익 (시가 기준, 주 평균의 평균)")
    print(f"      {'기간':<6} {'통과':>9} {'미통과':>9} {'차이':>9} "
          f"{'블록 95% CI':>22} {'통과 우위 주':>10}")
    sel = {}
    for h in HORIZONS:
        sp, a_, b_ = [], [], []
        for k in range(K):
            f = fw.get(k, h)
            if f is None:
                continue
            P = [f[t] for t in weeks[k]["pass"] if t in f]
            N = [f[t] for t in f if t not in weeks[k]["pass"]]
            if P and N:
                a_.append(mean(P))
                b_.append(mean(N))
                sp.append(mean(P) - mean(N))
        ci = block_ci(sp, h)
        ci_s = "-" if ci is None else f"[{ci[0] * 100:+.2f}%, {ci[1] * 100:+.2f}%]"
        win = sum(1 for x in sp if x > 0) / len(sp) if sp else None
        sel[h] = {"pass": mean(a_), "non": mean(b_), "spread": mean(sp), "ci": ci,
                  "win": win, "n": len(sp)}
        print(f"      {h:>3}주   {pct(mean(a_), 9)} {pct(mean(b_), 9)} "
              f"{pct(mean(sp), 9)} {ci_s:>22} {win * 100:9.0f}%")
    out["selection"] = sel
    # 연도별 4주 차이
    by_y: dict[str, list] = {}
    for k in range(K):
        f = fw.get(k, 4)
        if f is None:
            continue
        P = [f[t] for t in weeks[k]["pass"] if t in f]
        N = [f[t] for t in f if t not in weeks[k]["pass"]]
        if P and N:
            by_y.setdefault(weeks[k]["signal"][:4], []).append(mean(P) - mean(N))
    print(f"      4주 차이 연도별: " + " · ".join(f"{y} {pct(mean(v)).strip()}"
                                            for y, v in sorted(by_y.items())))
    out["selection_4w_by_year"] = {y: mean(v) for y, v in by_y.items()}

    # 1-2 진입/이탈 타이밍
    print(f"\n    1-2 진입·이탈 — 새로 통과한 종목 / 계속 통과 / 이번 주 탈락한 종목의 "
          f"향후 수익 − 같은 주 전체 평균")
    grp = {"신규 진입": {}, "계속 통과": {}, "탈락": {}}
    pastg = {"신규 진입": [], "계속 통과": [], "탈락": []}
    contrib = {"신규 진입": [], "계속 통과": []}
    for k in range(1, K):
        cur, prev = weeks[k]["pass"], weeks[k - 1]["pass"]
        sets = {"신규 진입": cur - prev, "계속 통과": cur & prev,
                "탈락": (prev - cur) & set(weeks[k]["rets"])}
        for h in (1, 4, 13):
            f = fw.get(k, h)
            if f is None:
                continue
            base = mean(f.values())
            for g, names in sets.items():
                xs = [f[t] for t in names if t in f]
                if xs:
                    grp[g].setdefault(h, []).append(mean(xs) - base)
        allp = fw.past(k, 4, weeks[k]["rets"])
        if allp:
            base = mean(allp.values())
            for g, names in sets.items():
                xs = [allp[t] for t in names if t in allp]
                if xs:
                    pastg[g].append(mean(xs) - base)
        n = len(cur)
        if n:
            r, b = weeks[k]["rets"], rb(weeks[k])
            for g in ("신규 진입", "계속 통과"):
                names = sets[g]
                if names:
                    contrib[g].append(len(names) / n * (ew(r, names) - b))
                else:
                    contrib[g].append(0.0)
    print(f"      {'묶음':<10} {'직전 4주':>9} {'다음 1주':>9} {'다음 4주':>9} "
          f"{'다음 13주':>9} {'주당 종목':>8}")
    timing = {}
    for g in grp:
        cnt = mean(len({"신규 진입": weeks[k]["pass"] - weeks[k - 1]["pass"],
                        "계속 통과": weeks[k]["pass"] & weeks[k - 1]["pass"],
                        "탈락": (weeks[k - 1]["pass"] - weeks[k]["pass"])
                        & set(weeks[k]["rets"])}[g]) for k in range(1, K))
        vals = [mean(grp[g][h]) if grp[g].get(h) else None for h in (1, 4, 13)]
        timing[g] = {"past4": mean(pastg[g]) if pastg[g] else None,
                     "fwd": dict(zip((1, 4, 13), vals)), "per_week": cnt}
        print(f"      {wp(g, 10)} {pct(timing[g]['past4'], 9)} "
              + " ".join(pct(v, 9) for v in vals) + f" {cnt:8.0f}")
    print(f"      격차 기여(연): 신규 진입 {pp(mean(contrib['신규 진입']) * 52).strip()} · "
          f"계속 통과 {pp(mean(contrib['계속 통과']) * 52).strip()} "
          f"(나머지는 현금 주)")
    out["timing"] = timing
    out["timing_contrib"] = {g: mean(v) * 52 for g, v in contrib.items()}

    # 1-3 시장 국면
    print(f"\n    1-3 시장 국면 — 주를 다섯 묶음으로 나눠 연율 격차 (산술 × 52)")
    feats = []
    for k, w in enumerate(weeks):
        breadth = len(w["pass"]) / len(w["rets"]) if w["rets"] else 0
        trail = (ann_total([x["spy"] for x in weeks[k - 13:k]]) if k >= 13 else None)
        feats.append((breadth, trail, rp(w) - rb(w), rp(w) - w["spy"]))
    regime = {}
    for idx, name in ((0, "통과 비율(시장 폭)"), (1, "SPY 직전 13주 수익")):
        xs = sorted((f for f in feats if f[idx] is not None), key=lambda f: f[idx])
        q = len(xs) // 5
        print(f"      {name}")
        rows = []
        for i in range(5):
            part = xs[i * q:(i + 1) * q] if i < 4 else xs[4 * q:]
            lo_v, hi_v = part[0][idx], part[-1][idx]
            a = mean(f[2] for f in part) * 52
            b = mean(f[3] for f in part) * 52
            rows.append({"lo": lo_v, "hi": hi_v, "vs_ew": a, "vs_spy": b})
            print(f"        {i + 1}분위 [{lo_v * 100:+6.1f}% ~ {hi_v * 100:+6.1f}%]  "
                  f"vs 전체EW {pp(a).strip():>9} · vs SPY {pp(b).strip():>9}")
        regime[name] = rows
    out["regime"] = regime
    print(f"      사건 구간 (그 구간 신호의 주간 수익을 이어 붙인 누적)")
    print(f"        {wp('구간', 22)} {'주':>4} {'템플릿':>9} {'전체EW':>9} {'SPY':>9}")
    ev = {}
    for name, lo, hi in EVENTS:
        ws = [w for w in weeks if in_win(w, lo, hi)]
        if not ws:
            continue
        a, b, s = (ann_total([f(w) for w in ws]) for f in (rp, rb, lambda w: w["spy"]))
        ev[name] = (len(ws), a, b, s)
        print(f"        {wp(name, 22)} {len(ws):>4} {pct(a, 9)} {pct(b, 9)} {pct(s, 9)}")
    out["events"] = ev

    # 1-4 섹터·규모 Brinson
    def brinson(key_fn, label):
        alloc: dict[str, float] = {}
        selc: dict[str, float] = {}
        wsum: dict[str, list] = {}
        cash = 0.0
        for k, w in enumerate(weeks):
            r = w["rets"]
            b_all = rb(w)
            groups: dict[str, list] = {}
            for t in r:
                groups.setdefault(key_fn(k, t), []).append(t)
            n_b, n_p = len(r), len(w["pass"])
            if n_p == 0:
                cash += -b_all
                continue
            for g, names in groups.items():
                pn = [t for t in names if t in w["pass"]]
                wb_, wp_ = len(names) / n_b, len(pn) / n_p
                rbg = mean(r[t] for t in names)
                alloc[g] = alloc.get(g, 0.0) + (wp_ - wb_) * (rbg - b_all)
                if pn:
                    selc[g] = selc.get(g, 0.0) + wp_ * (mean(r[t] for t in pn) - rbg)
                wsum.setdefault(g, []).append((wp_, wb_))
        print(f"\n    1-4 {label} — 배분(비중 차이 × 그 묶음 성과) + 선택(묶음 안에서 "
              f"고른 종목 성과), 연 %p")
        print(f"      {wp('묶음', 24)} {'템플릿 비중':>10} {'전체 비중':>9} {'배분':>9} "
              f"{'선택':>9} {'합':>9}")
        res = {}
        for g in sorted(wsum, key=lambda g: (alloc.get(g, 0) + selc.get(g, 0))):
            a = alloc.get(g, 0.0) / K * 52
            s_ = selc.get(g, 0.0) / K * 52
            wpm = mean(x[0] for x in wsum[g])
            wbm = mean(x[1] for x in wsum[g])
            res[g] = {"w_template": wpm, "w_all": wbm, "allocation": a, "selection": s_}
            print(f"      {wp(g, 24)} {wpm * 100:9.1f}% {wbm * 100:8.1f}% {pp(a, 9)} "
                  f"{pp(s_, 9)} {pp(a + s_, 9)}")
        ta = sum(v["allocation"] for v in res.values())
        ts = sum(v["selection"] for v in res.values())
        print(f"      {wp('합계', 24)} {'':>10} {'':>9} {pp(ta, 9)} {pp(ts, 9)} "
              f"{pp(ta + ts, 9)}  (현금 주 {pp(cash / K * 52).strip()})")
        return {"groups": res, "allocation": ta, "selection": ts,
                "cash": cash / K * 52}

    out["sector"] = brinson(lambda k, t: sectors.get(t, "미상(옛 구성종목)"),
                            "섹터 (GICS, 위키백과 지금 표 기준)")

    def size_key(k, t):
        q = dv[k].get(t)
        return "미상" if q is None else f"{q + 1}분위(거래대금{'↑' if q == 4 else ''})"
    out["size"] = brinson(size_key, "규모 (50일 평균 거래대금 5분위, 5분위가 가장 큼)")
    return out


def dollar_volume_quintiles(weeks, px) -> list[dict[str, int]]:
    cache: dict[str, pd.Series] = {}
    out = []
    for w in weeks:
        ts = pd.Timestamp(w["signal"])
        vals = {}
        for t in w["rets"]:
            if t not in cache:
                df = px[t]
                cache[t] = (df["Close"].astype("float64")
                            * df["Volume"].astype("float64")).rolling(50).mean()
            s = cache[t]
            i = s.index.searchsorted(ts, side="right") - 1
            if i >= 0 and s.iloc[i] == s.iloc[i]:
                vals[t] = float(s.iloc[i])
        order = sorted(vals, key=vals.get)
        n = len(order)
        out.append({t: min(4, i * 5 // n) for i, t in enumerate(order)} if n else {})
    return out


# ---------------------------------------------------------------- 2) 이상치
def section2(weeks, px, flagged, trades) -> dict:
    out = {}
    K = len(weeks)
    lo, hi = weeks[0]["d0"], weeks[-1]["d1"]
    # 2-1 종목 기여
    tot: dict[str, float] = {}
    held: dict[str, float] = {}
    nweeks: dict[str, int] = {}
    life: dict[str, float] = {}
    for w in weeks:
        r = w["rets"]
        n_b, n_p = len(r), len(w["pass"])
        for t, x in r.items():
            wp_ = 1 / n_p if t in w["pass"] else 0.0
            c = (wp_ - 1 / n_b) * x
            tot[t] = tot.get(t, 0.0) + c
            if t in w["pass"]:
                held[t] = held.get(t, 0.0) + c
                nweeks[t] = nweeks.get(t, 0) + 1
            life[t] = (1 + life.get(t, 0.0)) * (1 + x) - 1
    s = sum(tot.values())
    print(f"\n  [2] 이상치")
    print(f"    2-1 종목별 격차 기여 (Σ주 (템플릿 비중 − 전체 비중) × 수익, 합계 "
          f"{pp(s).strip()} = 연 {pp(s / K * 52).strip()})")
    print(f"      보유 = 템플릿에 있던 주의 기여, 미보유 = 없던 주의 기여"
          f"(음수면 '놓친 상승', 양수면 '피한 하락')")

    def show(rows, title):
        print(f"      {title}")
        print(f"        {'종목':<6} {'기여':>9} {'보유':>9} {'미보유':>9} {'보유주':>5} "
              f"{'기간 수익':>10}  {'최대 하루 변동':<20} 분류")
        lst = []
        for t, c in rows:
            big = biggest_day(px, t, lo, hi)
            cls = classify(t in flagged, big)
            h = held.get(t, 0.0)
            lst.append({"ticker": t, "contrib": c, "held": h, "not_held": c - h,
                        "weeks_held": nweeks.get(t, 0), "life": life.get(t),
                        "big_day": big, "class": cls})
            big_s = "-" if big is None else f"{big[0]} {big[1] * 100:+.0f}%"
            print(f"        {t:<6} {pp(c, 9)} {pp(h, 9)} {pp(c - h, 9)} "
                  f"{nweeks.get(t, 0):>5} {pct(life.get(t), 10)}  {big_s:<20} {cls}")
        return lst

    ranked = sorted(tot.items(), key=lambda x: x[1])
    out["hurt"] = show(ranked[:20], "격차를 키운 종목 하위 20 (템플릿에 불리)")
    out["helped"] = show(ranked[::-1][:10], "격차를 줄인 종목 상위 10 (템플릿에 유리)")
    for lst in (out["hurt"],):
        cnt: dict[str, int] = {}
        for x in lst:
            cnt[x["class"]] = cnt.get(x["class"], 0) + 1
        print(f"      하위 20 분류: " + " · ".join(f"{k} {v}" for k, v in cnt.items()))
    miss = sum(x["not_held"] for x in out["hurt"])
    print(f"      하위 20 의 기여 중 미보유(놓친 상승) {pp(miss).strip()} · 보유 "
          f"{pp(sum(x['held'] for x in out['hurt'])).strip()}")
    # 하위 k 종목을 양쪽에서 빼면
    print(f"      불리한 종목 k개를 양쪽 바구니에서 빼고 다시 잰 연복리")
    print(f"        {'k':>3} {'템플릿':>9} {'전체EW':>9} {'격차':>9}")
    rm = {}
    for k_ in (k for k in (0, 1, 2, 5, 10, 20, 50) if k < len(ranked) // 2):
        drop = {t for t, _ in ranked[:k_]}
        ws = [dict(w, rets={t: r for t, r in w["rets"].items() if t not in drop},
                   **{"pass": w["pass"] - drop}) for w in weeks]
        a, b = ann([rp(w) for w in ws]), ann([rb(w) for w in ws])
        rm[k_] = (a, b)
        print(f"        {k_:>3} {pct(a, 9)} {pct(b, 9)} {pp(a - b, 9)}")
    out["remove_top"] = rm

    # 2-2 극단 주
    gaps = sorted(((rp(w) - rb(w), w) for w in weeks), key=lambda x: x[0])
    print(f"\n    2-2 격차가 가장 컸던 주")
    print(f"        {'신호일':<11} {'통과수':>5} {'템플릿':>8} {'전체EW':>8} {'SPY':>8} "
          f"{'격차':>8}")
    for g, w in gaps[:10]:
        print(f"        {w['signal']:<11} {len(w['pass']):>5} {pct(rp(w))} {pct(rb(w))} "
              f"{pct(w['spy'])} {pct(g)}")
    print(f"        … 템플릿이 가장 앞선 주")
    for g, w in gaps[::-1][:5]:
        print(f"        {w['signal']:<11} {len(w['pass']):>5} {pct(rp(w))} {pct(rb(w))} "
              f"{pct(w['spy'])} {pct(g)}")
    out["worst_weeks"] = [(w["signal"], g) for g, w in gaps[:10]]
    out["best_weeks"] = [(w["signal"], g) for g, w in gaps[::-1][:10]]
    print(f"      극단 주를 빼고 다시 잰 연복리 (세 계열에서 같은 주를 뺀다)")
    print(f"        {wp('뺀 주', 28)} {'템플릿':>9} {'전체EW':>9} {'SPY':>9} {'템플릿-SPY':>10}")
    trims = {}
    for name, sel in (("없음", lambda i, n: True),
                      ("격차 최악 1%", lambda i, n: i >= int(n * 0.01)),
                      ("격차 최악 5%", lambda i, n: i >= int(n * 0.05)),
                      ("격차 양쪽 1%", lambda i, n: int(n * 0.01) <= i < n - int(n * 0.01)),
                      ("격차 양쪽 5%", lambda i, n: int(n * 0.05) <= i < n - int(n * 0.05))):
        ws = [w for i, (_, w) in enumerate(gaps) if sel(i, len(gaps))]
        a, b, sp_ = ann([rp(w) for w in ws]), ann([rb(w) for w in ws]), \
            ann([w["spy"] for w in ws])
        trims[name] = (a, b, sp_)
        print(f"        {wp(name, 28)} {pct(a, 9)} {pct(b, 9)} {pct(sp_, 9)} "
              f"{pp(a - sp_, 10)}")
    out["trim"] = trims

    # 2-3 거래 단위 (현재 규칙: RS 상위 5, -8% / 3R)
    if trades:
        rs_ = sorted(t["r_multiple"] for t in trades if t.get("r_multiple") is not None)
        maes = sorted(t["mae"] for t in trades if t.get("mae") is not None)
        q = lambda xs, f: xs[min(len(xs) - 1, int(f * (len(xs) - 1)))]
        stops = [t for t in trades if t["status"] == "손절"]
        gap_st = [t for t in stops if "시가" in t.get("reason", "")]
        print(f"\n    2-3 거래 단위 — 현재 규칙(RS 상위 5) · 다음날 시가 · -8% / 3R · "
              f"S&P 500 풀 {len(trades)}건")
        print(f"      R 분위: " + " · ".join(f"{int(f * 100)}% {q(rs_, f):+.2f}R"
                                         for f in (0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99)))
        print(f"      최대역행(MAE) 분위: " + " · ".join(
            f"{int(f * 100)}% {q(maes, f) * 100:+.1f}%" for f in (0.05, 0.25, 0.5, 0.75)))
        print(f"      손절 {len(stops)}건 ({len(stops) / len(trades) * 100:.0f}%) · 그중 갭으로 "
              f"손절선 아래 체결 {len(gap_st)}건 (평균 "
              f"{mean(t['r_multiple'] for t in gap_st) if gap_st else 0:+.2f}R) · 익절 "
              f"{sum(1 for t in trades if t['status'] == '익절')}건 · 평균 "
              f"{mean(rs_):+.2f}R · 중앙 {median(rs_):+.2f}R")
        top = sorted(trades, key=lambda t: t.get("r_multiple") or 0)
        lst = []
        for title, rows in (("가장 나쁜 10건", top[:10]), ("가장 좋은 10건", top[::-1][:10])):
            print(f"      {title}")
            for t in rows:
                big = biggest_day(px, t["ticker"], t["entry_date"], t["exit_date"])
                cls = classify(t["ticker"] in flagged, big)
                big_s = "-" if big is None else f"{big[0]} {big[1] * 100:+.0f}%"
                lst.append({k: t.get(k) for k in ("ticker", "cohort", "entry_date",
                                                  "exit_date", "r_multiple", "reason")}
                           | {"class": cls, "big_day": big})
                print(f"        {t['ticker']:<6} {t['cohort']} → {t['exit_date']} "
                      f"{t['r_multiple']:+6.2f}R  {t.get('reason', '')[:24]:<24} "
                      f"{big_s:<18} {cls}")
        sum_top = sum(t["r_multiple"] for t in top[::-1][:10])
        print(f"      상위 10건이 전체 R 합 {sum(rs_):+.1f}R 중 {sum_top:+.1f}R")
        out["trades"] = {"n": len(trades), "mean_r": mean(rs_), "median_r": median(rs_),
                         "stop_share": len(stops) / len(trades),
                         "gap_stops": len(gap_st), "extremes": lst}
    return out


# ---------------------------------------------------------------- 3) 탐색
def section3(weeks, fw: Forward, spy_df, ablation) -> dict:
    out = {}
    K = len(weeks)
    print(f"\n  [3] 탐색 — 판정 아님. 여러 변형을 본 만큼 다중 비교 문제가 있다")
    # 3-1 보유 기간
    print(f"    3-1 보유 기간 — N 주마다 통과 종목을 사서 N 주 보유 (연복리)")
    print(f"      {'N':>3} {'템플릿':>9} {'전체EW':>9} {'SPY':>9}")
    hold = {}
    for n in HORIZONS:
        a_, b_, s_ = [], [], []
        for k in range(0, K - n + 1, n):
            f = fw.get(k, n)
            P = [f[t] for t in weeks[k]["pass"] if t in f]
            a_.append(mean(P) if P else 0.0)
            b_.append(mean(f.values()) if f else 0.0)
            s_.append(ann_total([w["spy"] for w in weeks[k:k + n]]))
        g = lambda xs: (ann_total(xs) + 1) ** (52 / (len(xs) * n)) - 1
        hold[n] = (g(a_), g(b_), g(s_))
        print(f"      {n:>3} {pct(g(a_), 9)} {pct(g(b_), 9)} {pct(g(s_), 9)}")
    out["holding"] = hold
    # 3-2 상위 N 집중 (RS 순, 손절 없음)
    print(f"    3-2 RS 상위 N 만 동일가중 (매주, 손절·익절 없음)")
    conc = {}
    for n in (5, 20, 50):
        rs_ = []
        for w in weeks:
            order = sorted(w["pass"], key=lambda t: (-w["rows"][t]["rs"],
                                                     -(w["rows"][t].get("raw") or 0), t))
            rs_.append(ew(w["rets"], order[:n]) or 0.0)
        conc[n] = ann(rs_)
        print(f"      상위 {n:<3} {pct(ann(rs_))}")
    print(f"      전부      {pct(ann([rp(w) for w in weeks]))} · SPY "
          f"{pct(ann([w['spy'] for w in weeks])).strip()}")
    out["concentration"] = conc
    # 3-3 지수 200일선 타이밍
    c = spy_df["Close"]
    ma = c.rolling(200).mean()
    on = {d.strftime("%Y-%m-%d"): bool(v) for d, v in (c > ma).items()}

    keys = sorted(on)
    flag = []
    for w in weeks:
        i = bisect_right(keys, w["signal"]) - 1
        flag.append(on[keys[i]] if i >= 0 else True)
    a = ann([rp(w) if f else 0.0 for w, f in zip(weeks, flag)])
    b = ann([rp(w) if f else w["spy"] for w, f in zip(weeks, flag)])
    print(f"    3-3 SPY 가 200일선 아래면: 현금 {pct(a).strip()} · SPY 보유 "
          f"{pct(b).strip()} (위인 주 {sum(flag)}/{len(flag)})")
    out["timing_200dma"] = {"cash": a, "spy": b, "on_weeks": sum(flag)}
    # 3-4 조건 빼기
    if ablation:
        out["ablation"] = ablation_report(weeks, ablation)
    return out


def conditions(px: dict, sig_dates: list[str], cfg: dict) -> dict[str, pd.DataFrame]:
    """신호일마다 모집단 전체의 템플릿 8개 조건 (RS 는 history.screen 과 같은 식)."""
    sig = pd.DatetimeIndex(pd.to_datetime(sig_dates))
    parts = []
    for t, df in px.items():
        x = H.indicators(df)
        x = x[x.index.isin(sig)]
        if not x.empty:
            parts.append(x.assign(ticker=t))
    long = pd.concat(parts)
    out = {}
    for d, day in long.groupby(level=0):
        e = H.population(day.set_index("ticker"), cfg)
        if e.empty:
            continue
        e["rs"] = (e["raw"].rank(pct=True) * 98 + 1).round(0)
        c, m50, m150, m200 = e["close"], e["ma50"], e["ma150"], e["ma200"]
        out[d.strftime("%Y-%m-%d")] = pd.DataFrame({
            "c1": (c > m150) & (c > m200), "c2": m150 > m200,
            "c3": m200 > e["ma200_22"], "c4": (m50 > m150) & (m50 > m200),
            "c5": c > m50, "c6": c >= e["low52"] * 1.30,
            "c7": c >= e["high52"] * 0.75, "c8": e["rs"] >= 70})
    return out


def ablation_report(weeks, cond: dict[str, pd.DataFrame]) -> dict:
    keys = [k for k, _ in CONDS]
    same, tot = 0, 0
    res = {}

    def basket(w, need):
        df = cond.get(w["signal"])
        if df is None:
            return set()
        m = df.loc[df.index.isin(list(w["rets"]))]
        if need:
            m = m[m[list(need)].all(axis=1)]
        return set(m.index)
    for w in weeks:
        b = basket(w, keys)
        tot += 1
        same += b == w["pass"]
    print(f"    3-4 템플릿 조건 빼기 — 재계산한 통과 종목이 스냅샷과 같은 주 {same}/{tot}")
    print(f"      {wp('변형', 30)} {'연복리':>9} {'평균 종목':>8}")
    base_all = ann([rb(w) for w in weeks])
    for name, need in [("8개 전부 (기준)", keys)] + \
            [(f"빼기: {lbl}", [k for k in keys if k != key]) for key, lbl in CONDS] + \
            [(f"이것만: {lbl}", [key]) for key, lbl in CONDS]:
        rs_, ns = [], []
        for w in weeks:
            b = basket(w, need)
            ns.append(len(b))
            rs_.append(ew(w["rets"], b) or 0.0)
        res[name] = (ann(rs_), mean(ns))
        print(f"      {wp(name, 30)} {pct(ann(rs_), 9)} {mean(ns):8.0f}")
    print(f"      {wp('조건 없음 (유동성 통과 구성종목)', 30)} {pct(base_all, 9)}")
    return {"match_weeks": same, "weeks": tot, "variants": res}


# ---------------------------------------------------------------- 실행
def ref_trades(snaps, px) -> list[dict]:
    pool = {t for s in snaps for t in s["rows"]}
    ohlc, index = H.to_ohlc(px, pool, snaps[0]["date"])
    ref = [(c, [dict(r, stop=C.BASE_STOP, entry_date=c) for r in rows])
           for c, rows in V.cohorts(snaps + [snaps[-1]], C.TOP, 1)]
    return C.simulate_picks(ref, ohlc, index)


def fetch_rsp(start: str) -> S.Spy | None:
    try:
        import yfinance as yf
        df = yf.download("RSP", start=start, interval="1d", auto_adjust=True,
                         progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df[["Open", "Close"]].dropna().astype("float64")
        return S.Spy(df) if len(df) else None
    except Exception as e:                       # 네트워크 실패 — 대조만 생략
        print(f"  RSP 받기 실패: {e}", flush=True)
        return None


def period_summary(weeks, periods) -> dict:
    print(f"\n  [기간별 요약] 주간 수익을 이어 붙인 연복리 (비용 전)")
    print(f"    {wp('기간', 26)} {'주':>4} {'템플릿':>9} {'전체EW':>9} {'SPY':>9}")
    out = {}
    for lo, hi in periods:
        ws = [w for w in weeks if in_win(w, lo, hi)]
        if not ws:
            continue
        a, b, s = ann([rp(w) for w in ws]), ann([rb(w) for w in ws]), \
            ann([w["spy"] for w in ws])
        out[f"{lo}~{hi}"] = (len(ws), a, b, s)
        print(f"    {wp(f'{lo} ~ {hi}', 26)} {len(ws):>4} {pct(a, 9)} {pct(b, 9)} "
              f"{pct(s, 9)}")
    return out


def main(argv: list[str]) -> int:
    out_json = argv[argv.index("--json") + 1] if "--json" in argv else None
    periods = [("2019-01-01", "2023-12-31"), ("2024-01-01", "2099-12-31")]
    if "--periods" in argv:
        periods = [tuple(x.split(":")) for x in
                   argv[argv.index("--periods") + 1].split(",")]
    px, data, ex = S.all_prices()
    spy = S.Spy(ex["spy"])
    m = S.load_members()
    snaps, _ = S.load_snaps()
    mem = lambda d: S.members_at(m["current"], m["changes"], d)
    need = set().union(*(mem(s["date"]) for s in snaps))
    p = T.Prices(px, need, spy.dates)
    weeks = build_weeks(snaps, mem, p, spy)
    fw = Forward(weeks, p)
    sectors = m.get("sectors", {})

    print(f"\n{'=' * 104}")
    print(f"  진단: 트렌드 템플릿(S&P 500 시점별 구성종목 중 통과 종목 전부 동일가중) vs "
          f"같은 구성종목 전체 동일가중 vs SPY")
    print(f"  {weeks[0]['signal']} ~ {weeks[-1]['signal']} · {len(weeks)}주 · 비용 전 "
          f"시가~시가 · 구성종목 출처 {m.get('source', 'changes')} · 섹터 정보 "
          f"{len(sectors)}종목(지금 구성종목)")
    print(f"  전체 기간 연복리: 템플릿 {pct(ann([rp(w) for w in weeks])).strip()} · "
          f"전체 동일가중 {pct(ann([rb(w) for w in weeks])).strip()} · SPY "
          f"{pct(ann([w['spy'] for w in weeks])).strip()}")
    print(f"{'=' * 104}", flush=True)
    doc = {"weeks": len(weeks), "first": weeks[0]["signal"],
           "last": weeks[-1]["signal"]}
    doc["periods"] = period_summary(weeks, periods)
    rsp = fetch_rsp(data["start"])
    doc["s0"], flagged = section0(weeks, px, spy, rsp)
    dv = dollar_volume_quintiles(weeks, px)
    doc["s1"] = section1(weeks, fw, sectors, dv)
    trades = None if "--no-trades" in argv else ref_trades(snaps, px)
    doc["s2"] = section2(weeks, px, flagged, trades)
    abl = None
    if "--no-ablation" not in argv:
        print(f"\n  (조건 빼기용 지표 계산 중…)", flush=True)
        abl = conditions(px, [w["signal"] for w in weeks], H.screener_cfg())
    doc["s3"] = section3(weeks, fw, ex["spy"], abl)
    print(f"\n  ※ 진단은 판정을 바꾸지 않는다. 상장폐지로 가격 없는 옛 구성종목은 빠져 있다.")
    if out_json:
        Path(out_json).write_text(json.dumps(doc, ensure_ascii=False, indent=1,
                                             default=str), encoding="utf-8")
        print(f"  → {out_json}")
    return 0


if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main(sys.argv[1:]))
