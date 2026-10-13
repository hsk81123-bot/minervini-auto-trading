# -*- coding: utf-8 -*-
"""사전 등록 가설 H1·H2·H3 (ROADMAP.md 단계 2) — 파라미터는 구현 전에 ROADMAP 에 고정했다.

공통: 풀 = 그 날 S&P 500 구성종목(시점별) · 진입 = 신호 다음 거래일 시가 · 편도 5bp.
모멘텀 12-1 = 종가[t−21거래일] / 종가[t−252거래일] − 1 (배당 포함 수정주가).

  H1 정석 모멘텀      월 첫 신호일에 모멘텀 상위 10% 동일가중, 다음 달 첫 신호일까지 보유
  H2 SPY 코어 + 템플릿 위성  매주. 통과 종목 n ≥ 20 이면 통과 종목 100%,
                      n < 20 이면 통과 종목 n/20 · 나머지 SPY
  H3 시총 가중 틸트   월 첫 신호일에 비중 ∝ 시총 × (1 + 0.25·z), z = 모멘텀 z-점수(±2)

판정은 evalkit.verdict (설계 2013~ → 통과 때만 보류 2005–12). 결과를 본 뒤 파라미터를
바꾸지 않는다 — 바꾸려면 새 가설로 ledger.json 에 추가한다.

사용법 (spx_validate members/prices/rebuild, pit_fund extract 산출물 필요):
    python screener/hypotheses.py [--json hypotheses.json]
"""
import json
import math
import sys
from bisect import bisect_right
from pathlib import Path
from statistics import mean, pstdev

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import baseline as B
import evalkit as E

MOM_LONG, MOM_SKIP = 252, 21          # 거래일
TOP_FRAC = 0.10                        # H1
H2_N_MIN = 20                          # H2
H3_LAMBDA, Z_CLIP = 0.25, 2.0          # H3
MIN_Z_NAMES = 20                       # z-점수를 낼 최소 종목 수 (그 밑이면 순수 시총 가중)
SPY_TICKER = "SPY"
HYPOTHESES = (("h1", "H1 정석 모멘텀"), ("h2", "H2 SPY 코어+템플릿 위성"),
              ("h3", "H3 시총 가중 틸트"))


def monthly_flags(signals: list[str]) -> list[bool]:
    """그 달의 첫 신호일이면 True (리밸런싱 날)."""
    out, seen = [], set()
    for s in signals:
        key = s[:7]
        out.append(key not in seen)
        seen.add(key)
    return out


def momentum(p, signal: str, names) -> dict[str, float]:
    """신호일 기준 12-1 모멘텀. 273거래일 미만 이력이거나 신호일에 거래가 없던 종목은 뺀다."""
    i = bisect_right(p.cal, signal) - 1
    if i < MOM_LONG:
        return {}
    out = {}
    for t in names:
        j = p.cols.get(t)
        if j is None or not p.o[i, j] == p.o[i, j]:        # 신호일에 거래 없음
            continue
        a, b = p.c[i - MOM_LONG, j], p.c[i - MOM_SKIP, j]
        if a == a and b == b and a > 0:
            out[t] = float(b / a - 1)
    return out


def top_fraction(mom: dict[str, float], frac: float = TOP_FRAC) -> list[str]:
    n = len(mom)
    if n == 0:
        return []
    k = max(1, math.ceil(frac * n))
    return [t for t, _ in sorted(mom.items(), key=lambda x: (-x[1], x[0]))[:k]]


def h2_weights(passes: list[str], n_min: int = H2_N_MIN) -> dict[str, float]:
    n = len(passes)
    if n == 0:
        return {SPY_TICKER: 1.0}
    share = min(1.0, n / n_min)
    w = {t: share / n for t in passes}
    if share < 1.0:
        w[SPY_TICKER] = 1.0 - share
    return w


def tilt_weights(caps_d: dict[str, float], mom: dict[str, float],
                 lam: float = H3_LAMBDA, clip: float = Z_CLIP) -> dict[str, float]:
    names = [t for t in caps_d if caps_d[t] > 0]
    ms = [mom[t] for t in names if t in mom]
    if len(ms) < MIN_Z_NAMES or pstdev(ms) == 0:
        return E.cap_weights(names, caps_d)
    mu, sd = mean(ms), pstdev(ms)
    w = {}
    for t in names:
        z = max(-clip, min(clip, (mom[t] - mu) / sd)) if t in mom else 0.0
        w[t] = caps_d[t] * max(0.0, 1.0 + lam * z)
    tot = sum(w.values())
    return {t: v / tot for t, v in w.items()}


def active_share(w: dict[str, float], base: dict[str, float]) -> float:
    return 0.5 * sum(abs(w.get(t, 0.0) - base.get(t, 0.0)) for t in set(w) | set(base))


def schedules(ctx: dict) -> tuple[dict, dict]:
    """세 가설의 (신호일, 비중 또는 None) 목록과 부가 통계."""
    p, caps, members, snaps = ctx["p"], ctx["caps"], ctx["members"], ctx["snaps"]
    sigs = [s["date"] for s in snaps]
    flags = monthly_flags(sigs)
    h1, h2, h3, act, nsel = [], [], [], [], []
    for s, rebal in zip(snaps, flags):
        sig, mem = s["date"], members[s["date"]]
        if rebal:
            mom = momentum(p, sig, mem)
            sel = top_fraction(mom)
            h1.append((sig, E.ew(sel) if sel else {}))
            nsel.append(len(sel))
            c = caps.caps(mem, sig)
            w = tilt_weights(c, mom)
            h3.append((sig, w))
            if w:
                act.append(active_share(w, E.cap_weights(list(c), c)))
        else:
            h1.append((sig, None))
            h3.append((sig, None))
        h2.append((sig, h2_weights(list(s["rows"]))))
    return {"h1": h1, "h2": h2, "h3": h3}, {
        "h1_avg_names": mean(nsel) if nsel else 0.0,
        "h3_active_share": mean(act) if act else 0.0}


def build(ctx: dict) -> tuple[E.Frame, dict]:
    fr = ctx["fr"]
    px = dict(ctx["px"])
    px[SPY_TICKER] = ctx["spy_df"]
    names = set(ctx["p"].cols) | {SPY_TICKER}
    import template_check as T
    p = T.Prices(px, names, ctx["p"].cal)
    sched, stats = schedules(ctx)
    fr2 = E.Frame(fr.signal, fr.d0, fr.d1)
    for k, v in fr.r.items():
        fr2.add(k, v)
    drag = {}
    for k, sc in sched.items():
        sig, d0, d1, gross, net = E.run_weights(sc, p, ctx["cost"])
        if (sig, d0, d1) != (fr.signal, fr.d0, fr.d1):
            raise RuntimeError(f"{k}: 주 달력이 기준선과 다르다")
        fr2.add(k, net)
        drag[k] = mean(g - n for g, n in zip(gross, net)) * 52
    stats["cost_drag"] = drag
    return fr2, stats


def report(fr: E.Frame, stats: dict) -> dict:
    doc: dict = {"stats": stats, "verdict": {}}
    print(f"\n{'=' * 104}")
    print(f"  사전 등록 가설 H1~H3 (ROADMAP 단계 2) · 설계 {E.DESIGN[0]}~ → 통과 때만 보류 "
          f"{E.HOLDOUT[0]}~{E.HOLDOUT[1]}")
    print(f"  H1 평균 보유 종목 {stats['h1_avg_names']:.0f}개 · H3 평균 액티브 셰어 "
          f"{stats['h3_active_share'] * 100:.1f}% · 연 비용 부담 "
          + " · ".join(f"{k.upper()} {v * 100:.2f}%" for k, v in stats["cost_drag"].items()))
    print(f"{'=' * 104}")
    print(f"\n  [참고 기준선 · 설계 기간]")
    print(f"  {'':<26} {'연복리':>8} {'변동성':>7} {'MDD':>8} {'베타':>5}")
    for k, lbl in (("spy", "SPY"), ("rsp", "RSP"), ("pool", "풀 동일가중"),
                   ("pool_cw", "풀 시총가중")):
        if k in fr.r:
            b = B.basic(fr, k, *E.DESIGN)
            print(f"  {lbl:<26} {E.pct(b['cagr'])} {b['vol'] * 100:6.1f}% {E.pct(b['mdd'])} "
                  f"{'-' if b['beta'] is None else f'{b['beta']:5.2f}':>5}")
    print(f"\n  [후보 판정] ROADMAP 성공 기준 6항목 (보정 후)")
    E.header()
    for k, lbl in HYPOTHESES:
        v = E.verdict(fr, k)
        doc["verdict"][k] = v
        E.row(lbl, v["design"])
        d = v["design"]
        if "checks" in d:
            f = E.fails(d)
            if f:
                print(f"      탈락 이유: {' / '.join(f)}")
            print(f"      시대: " + " · ".join(
                f"{el} {E.pct(e['cand']).strip()} vs SPY {E.pct(e['spy']).strip()}"
                for el, e in d["eras"].items()))
            print(f"      위기: " + " · ".join(
                f"{cl} {E.pct(c).strip()} vs {E.pct(sp).strip()}"
                for cl, (c, sp) in d["crisis"].items()))
            print(f"      같은 풀 동일가중 {E.pct(d['pool_cagr']).strip()} · 보정치 연 "
                  f"{d['haircut_ann'] * 100:.2f}%p · 보정 전 연복리 {E.pct(d['cagr_raw']).strip()}")
        if v["holdout"]:
            E.row(lbl + " (보류)", v["holdout"])
            f = E.fails(v["holdout"]) if "checks" in v["holdout"] else []
            if f:
                print(f"      탈락 이유: {' / '.join(f)}")
        else:
            print(f"      설계 기간 탈락 — 보류 기간은 계산하지 않는다")
    print(f"\n  ※ 생존편향 보정은 하한이다. 2005–2010 시총은 근사다 (baseline 참고).")
    return doc


def main(argv: list[str]) -> int:
    out_json = argv[argv.index("--json") + 1] if "--json" in argv else None
    fr, meta, ctx = B.build_frame()
    fr2, stats = build(ctx)
    doc = report(fr2, stats)
    if out_json:
        Path(out_json).write_text(json.dumps(doc, ensure_ascii=False, indent=1,
                                             default=str), encoding="utf-8")
        print(f"  → {out_json}")
    return 0


if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main(sys.argv[1:]))
