# -*- coding: utf-8 -*-
"""트렌드 템플릿 자체가 SPY 를 이기나 — 고르기·손절·익절 없이 필터만 잰다.

포트폴리오: 매주 신호일에 템플릿을 통과한 종목 '전부'를 동일가중으로 보유한다.
  신호 다음 거래일 시가에 바꾸고 다음 신호의 다음 거래일 시가까지 들고 간다.
  통과 종목이 없는 주는 현금(수익 0). 비용 = 편도 5bp × 바꾼 비중.
기준: SPY (배당 포함 수정주가, 같은 시가~시가 구간).

판정 대상 (결과를 보기 전에 고정): S&P 500 풀 — 그 날의 구성종목(위키백과 변경
  이력으로 되돌림) 중 템플릿 통과 종목. 설계 2019-02 ~ 2023-12 에서 모두 넘어야 한다:
    1) 연복리 > SPY 연복리
    2) 주간 초과수익 평균의 블록 부트스트랩(4주 블록) 95% 신뢰구간 하한 > 0
    3) 초과수익(그 해 포트 − SPY)이 플러스인 해가 절반 초과
    4) 기여 상위 2종목을 빼고 다시 짜도 연복리 > SPY
  설계를 통과할 때만 보류 2024-01 ~ 끝을 같은 기준으로 한 번 계산한다.

참고 (판정에 안 씀):
  - S&P 500 구성종목 전체 동일가중 — 동일가중 자체의 효과(2019년 이후 시가총액
    가중에 크게 뒤짐)와 템플릿의 효과를 가른다. 템플릿 − 전체 동일가중 = 필터의 몫
  - 전체 시장(유니버스) 템플릿 통과 동일가중 — 생존 편향이 더 크다

한계: 상장폐지로 가격이 없는 옛 구성종목은 빠진다. 보유 중 가격이 끊긴 종목은 마지막
  종가로 평가한다(실제 청산가를 모름).

사용법 (spx_validate.py members/prices/rebuild 산출물이 있어야 한다):
    python screener/template_check.py [--json template.json]
"""
import json
import random
import sys
from bisect import bisect_right
from datetime import date
from pathlib import Path
from statistics import mean

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import candidates as C
import history as H
import spx_validate as S

COST = S.COST
BLOCK, N_BOOT, SEED = 4, 2000, 20261013
CI_LEVEL = 0.95
CHECKS = (
    ("cagr", "연복리 > SPY 연복리"),
    ("ci", "주간 초과수익 블록 부트스트랩 95% CI 하한 > 0"),
    ("years", "초과수익 플러스인 해가 절반 초과"),
    ("top2", "기여 상위 2종목을 빼도 연복리 > SPY"),
)


# ---------------------------------------------------------------- 수익 계산
class Prices:
    """거래일 달력 위의 시가 / 종가(앞 값으로 채움) 표."""

    def __init__(self, px: dict[str, pd.DataFrame], tickers: set[str],
                 cal: list[str]):
        idx = pd.DatetimeIndex(pd.to_datetime(cal))
        have = sorted(t for t in tickers if t in px)
        self.cal = cal
        self.pos = {d: i for i, d in enumerate(cal)}
        self.open = pd.DataFrame({t: px[t]["Open"].astype("float64")
                                  for t in have}).reindex(idx)
        self.close = pd.DataFrame({t: px[t]["Close"].astype("float64")
                                   for t in have}).reindex(idx).ffill()
        self.cols = {t: i for i, t in enumerate(have)}
        self.o = self.open.to_numpy()
        self.c = self.close.to_numpy()

    def entry_day(self, signal: str) -> str | None:
        i = bisect_right(self.cal, signal)
        return self.cal[i] if i < len(self.cal) else None

    def start_px(self, t: str, d: str) -> float | None:
        j = self.cols.get(t)
        if j is None:
            return None
        v = self.o[self.pos[d], j]
        return float(v) if v == v and v > 0 else None

    def end_px(self, t: str, d: str) -> float | None:
        """그 날 시가. 거래가 없으면(상장폐지·정지) 그 날까지 마지막 종가."""
        j = self.cols[t]
        v = self.o[self.pos[d], j]
        if v == v and v > 0:
            return float(v)
        v = self.c[self.pos[d], j]
        return float(v) if v == v and v > 0 else None


def baskets(holdings: list[tuple[str, set[str]]], p: Prices, spy: S.Spy,
            exclude: frozenset = frozenset()) -> list[dict]:
    """신호마다 동일가중 바구니 → 기간별 {signal, start, end, gross, net, spy, n,
    contrib}. 비용은 직전 바구니(가격 변화로 흘러간 비중)에서 새 비중으로 바꾼 만큼."""
    out = []
    prev_w: dict[str, float] = {}
    for (sig, names), (nxt, _) in zip(holdings, holdings[1:]):
        d0, d1 = p.entry_day(sig), p.entry_day(nxt)
        if d0 is None or d1 is None or d0 >= d1:
            continue
        rets = {}
        for t in names:
            if t in exclude:
                continue
            a = p.start_px(t, d0)
            b = p.end_px(t, d1) if a else None
            if a and b:
                rets[t] = b / a - 1
        n = len(rets)
        w = {t: 1 / n for t in rets} if n else {}
        turnover = sum(abs(w.get(t, 0.0) - prev_w.get(t, 0.0))
                       for t in set(w) | set(prev_w))
        gross = mean(rets.values()) if n else 0.0
        net = (1 - COST * turnover) * (1 + gross) - 1
        s = spy.at(d1, "open") / spy.at(d0, "open") - 1
        out.append({"signal": sig, "start": d0, "end": d1, "n": n,
                    "gross": gross, "net": net, "spy": s, "turnover": turnover,
                    "contrib": {t: r / n for t, r in rets.items()}})
        # 다음 기간 시작 직전의 비중 (가격 변화 반영)
        prev_w = ({t: w[t] * (1 + rets[t]) / (1 + gross) for t in rets}
                  if n and gross > -1 else {})
    return out


# ---------------------------------------------------------------- 지표
def curve_stats(rets: list[float], days: int) -> tuple[float, float, float]:
    """(연복리, 최대낙폭, 누적) — 기간 수익률 목록과 달력 일수."""
    eq, peak, mdd = 1.0, 1.0, 0.0
    for r in rets:
        eq *= 1 + r
        peak = max(peak, eq)
        mdd = min(mdd, eq / peak - 1)
    yrs = days / 365.25
    return (eq ** (1 / yrs) - 1 if yrs > 0 else 0.0), mdd, eq - 1


def block_ci(xs: list[float], block: int = BLOCK, n: int = N_BOOT,
             seed: int = SEED, level: float = CI_LEVEL
             ) -> tuple[float, float] | None:
    """평균의 원형 블록 부트스트랩 신뢰구간 — 주간 수익은 서로 붙어 있어 블록으로 뽑는다."""
    m = len(xs)
    if m < 2 * block:
        return None
    rng = random.Random(seed)
    k = -(-m // block)
    stats = []
    for _ in range(n):
        draw = []
        for _ in range(k):
            s = rng.randrange(m)
            draw.extend(xs[(s + j) % m] for j in range(block))
        stats.append(mean(draw[:m]))
    stats.sort()
    return (stats[int((1 - level) / 2 * n)],
            stats[min(n - 1, int((1 + level) / 2 * n))])


def summarize(per: list[dict]) -> dict:
    if not per:
        return {}
    days = (date.fromisoformat(per[-1]["end"])
            - date.fromisoformat(per[0]["start"])).days
    cagr, mdd, tot = curve_stats([x["net"] for x in per], days)
    s_cagr, s_mdd, s_tot = curve_stats([x["spy"] for x in per], days)
    ex = [x["net"] - x["spy"] for x in per]
    years: dict[str, list[dict]] = {}
    for x in per:
        years.setdefault(x["signal"][:4], []).append(x)
    yr = {}
    for y, xs in sorted(years.items()):
        a = b = 1.0
        for x in xs:
            a *= 1 + x["net"]
            b *= 1 + x["spy"]
        yr[y] = {"port": a - 1, "spy": b - 1, "excess": a - b}
    sd = lambda v: (sum((u - mean(v)) ** 2 for u in v) / (len(v) - 1)) ** 0.5
    sharpe = lambda v: mean(v) / sd(v) * 52 ** 0.5 if len(v) > 1 and sd(v) else None
    return {"from": per[0]["start"], "to": per[-1]["end"], "weeks": len(per),
            "cagr": cagr, "mdd": mdd, "total": tot, "spy_cagr": s_cagr,
            "spy_mdd": s_mdd, "spy_total": s_tot,
            "sharpe": sharpe([x["net"] for x in per]),
            "spy_sharpe": sharpe([x["spy"] for x in per]),
            "excess_week": mean(ex), "ci": block_ci(ex),
            "avg_n": mean(x["n"] for x in per),
            "cash_weeks": sum(1 for x in per if x["n"] == 0),
            "turnover": mean(x["turnover"] for x in per), "years": yr}


def top_contributors(per: list[dict], k: int = 2) -> list[str]:
    g: dict[str, float] = {}
    for x in per:
        for t, c in x["contrib"].items():
            g[t] = g.get(t, 0.0) + c
    return [t for t, _ in sorted(g.items(), key=lambda x: (-x[1], x[0]))[:k]]


def judge(per: list[dict], holdings, p: Prices, spy: S.Spy, lo: str, hi: str
          ) -> dict:
    sub = [x for x in per if lo <= x["signal"] <= hi]
    m = summarize(sub)
    drop = top_contributors(sub)
    hs = [h for h in holdings if lo <= h[0]]
    # 기간 끝 다음 신호까지 있어야 마지막 주 수익이 잡힌다
    re = [x for x in baskets(hs, p, spy, frozenset(drop)) if x["signal"] <= hi]
    m2 = summarize(re)
    pos = sum(1 for v in m["years"].values() if v["excess"] > 0)
    res = {"cagr": m["cagr"] > m["spy_cagr"],
           "ci": None if m["ci"] is None else m["ci"][0] > 0,
           "years": pos > len(m["years"]) / 2,
           "top2": m2["cagr"] > m2["spy_cagr"]}
    return dict(m, top2_dropped=drop, cagr_top2=m2["cagr"], years_pos=pos,
                checks=res, passed=all(v is True for v in res.values()))


# ---------------------------------------------------------------- 출력
def pct(x, w=8) -> str:
    return f"{'-':>{w}}" if x is None else f"{x * 100:+{w - 1}.2f}%"


def row(label: str, m: dict, verdict: str = "") -> None:
    ci = m.get("ci")
    ci_s = "-" if ci is None else f"[{ci[0] * 5200:+.1f}%, {ci[1] * 5200:+.1f}%]"
    sh = "-" if m.get("sharpe") is None else f"{m['sharpe']:.2f}"
    print(f"  {C.B_wpad(label, 30)} {pct(m['cagr'])} {pct(m['spy_cagr'])} "
          f"{pct(m['cagr'] - m['spy_cagr'])} {ci_s:>18} {pct(m['mdd'])} "
          f"{pct(m['spy_mdd'])} {sh:>5} {m['avg_n']:>5.0f}  {verdict}")


def header() -> None:
    print(f"  {C.B_wpad('포트폴리오', 30)} {'연복리':>8} {'SPY':>8} {'차이':>8} "
          f"{'주간 초과×52 95%CI':>18} {'MDD':>8} {'SPY MDD':>8} {'샤프':>5} "
          f"{'평균종목':>5}  판정")


def main(argv: list[str]) -> int:
    out_json = argv[argv.index("--json") + 1] if "--json" in argv else None
    px, data, ex = S.all_prices()
    spy = S.Spy(ex["spy"])
    m = S.load_members()
    sp_snaps, _ = S.load_snaps()
    full = [s for s in H.load_snaps() if s["weekly"]]

    hold_sp = [(s["date"], set(s["rows"])) for s in sp_snaps]
    hold_all = [(s["date"], S.members_at(m["current"], m["changes"], s["date"]))
                for s in sp_snaps]
    hold_full = [(s["date"], set(s["rows"])) for s in full]
    need = set().union(*(h for _, h in hold_sp + hold_all + hold_full))
    p = Prices(px, need, spy.dates)
    per = {"sp": baskets(hold_sp, p, spy), "sp_all": baskets(hold_all, p, spy),
           "full": baskets(hold_full, p, spy)}
    first, last = hold_sp[0][0], hold_sp[-2][0]

    print(f"\n{'=' * 112}")
    print(f"  트렌드 템플릿 자체 vs SPY — 통과 종목 전부 동일가중 · 매주 다음날 시가 "
          f"리밸런싱 · 손절/익절 없음 · 비용 편도 {COST * 1e4:.0f}bp")
    print(f"  신호 {first} ~ {last} · 설계 ~{C.DESIGN_END} / 보류 2024-01-01~ · "
          f"블록 부트스트랩 {BLOCK}주 × {N_BOOT}회")
    print(f"{'=' * 112}")
    doc = {"design": {}, "holdout": {}, "reference": {}}

    lo, hi = first, C.DESIGN_END
    print(f"\n  [설계 기간] {lo} ~ {hi}")
    header()
    ev = judge(per["sp"], hold_sp, p, spy, lo, hi)
    doc["design"]["sp500_template"] = ev
    row("판정: S&P 500 템플릿 통과", ev, "통과" if ev["passed"] else "탈락")
    for key, label in (("sp_all", "참고: S&P 500 전체 동일가중"),
                       ("full", "참고: 전체 시장 템플릿 통과")):
        r = summarize([x for x in per[key] if lo <= x["signal"] <= hi])
        doc["reference"][f"{key}_design"] = r
        row(label, r)
    fails = [d for k, d in CHECKS if ev["checks"][k] is not True]
    print(f"    상위 2종목 {', '.join(ev['top2_dropped'])} 제외 시 연복리 "
          f"{pct(ev['cagr_top2']).strip()} · 초과수익 플러스인 해 "
          f"{ev['years_pos']}/{len(ev['years'])} · 현금 주 {ev['cash_weeks']} · "
          f"주간 교체율 {ev['turnover'] * 100:.0f}%")
    if fails:
        print(f"    탈락 이유: {' / '.join(fails)}")

    lo, hi = "2024-01-01", last
    print(f"\n  [보류 기간] {lo} ~ {hi} — 설계 통과 시에만 계산")
    if not ev["passed"]:
        print(f"    설계 기간 탈락 — 보류 기간은 계산하지 않는다")
    else:
        header()
        ev2 = judge(per["sp"], hold_sp, p, spy, lo, hi)
        doc["holdout"]["sp500_template"] = ev2
        row("판정: S&P 500 템플릿 통과", ev2, "통과" if ev2["passed"] else "탈락")
        for key, label in (("sp_all", "참고: S&P 500 전체 동일가중"),
                           ("full", "참고: 전체 시장 템플릿 통과")):
            r = summarize([x for x in per[key] if lo <= x["signal"] <= hi])
            doc["reference"][f"{key}_holdout"] = r
            row(label, r)
        fails = [d for k, d in CHECKS if ev2["checks"][k] is not True]
        if fails:
            print(f"    탈락 이유: {' / '.join(fails)}")

    # 연도별 (설계 기간만 — 보류는 통과 시에만 본다)
    print(f"\n  [연도별 수익률] 설계 기간")
    ys = sorted(ev["years"])
    print(f"  {C.B_wpad('', 30)} " + " ".join(f"{y:>8}" for y in ys))
    print(f"  {C.B_wpad('S&P 500 템플릿 통과', 30)} "
          + " ".join(pct(ev["years"][y]["port"]) for y in ys))
    for key, label in (("sp_all", "S&P 500 전체 동일가중"),
                       ("full", "전체 시장 템플릿 통과")):
        r = doc["reference"][f"{key}_design"]["years"]
        print(f"  {C.B_wpad(label, 30)} "
              + " ".join(pct(r.get(y, {}).get("port")) for y in ys))
    print(f"  {C.B_wpad('SPY', 30)} "
          + " ".join(pct(ev["years"][y]["spy"]) for y in ys))
    print(f"\n  ※ 상장폐지로 가격 없는 옛 구성종목은 빠진다 — 결과가 좋게 기운다. "
          f"전체 시장 쪽은 생존 편향이 더 크다")

    if out_json:
        Path(out_json).write_text(json.dumps(doc, ensure_ascii=False, indent=2,
                                             default=str), encoding="utf-8")
        print(f"  → {out_json}")
    return 0


if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main(sys.argv[1:]))
