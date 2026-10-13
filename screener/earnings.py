# -*- coding: utf-8 -*-
"""H4 실적 서프라이즈 드리프트 (ROADMAP 단계 2) — 파라미터는 구현 전에 ROADMAP 에 고정했다.

  EPS     분기 희석 EPS (SEC XBRL 최신 공시본, pit_fund.full_quarters)
  SUE     (EPS_q − EPS_{q−4}) / σ,  σ = 직전 8개 분기의 같은 계절 차분의 표본표준편차
          (최소 6개, σ ≥ 0.02). 분기 사이에 큰 공백이 있으면 제외
  발표일  분기말 뒤 (15일, 75일] 안의 첫 8-K Item 2.02 제출일 (SEC submissions)
  선택    SUE ≥ 2.0 인 이벤트, 그 날 S&P 500 구성종목
  보유    발표일 이후 첫 주간 신호일부터 발표일 + 84일 전까지 (동일가중, 20개 미만이면
          모자란 비중은 SPY — H2 와 같은 규칙)
  평가 창 2014-01-01 이후 (XBRL EPS 가 2009~2011년에 시작). 보류 기간은 만들 수 없다.

EPS 숫자는 발표일에 이미 공개된 값이라고 가정하고 이후 공시된 XBRL 에서 가져온다 (속보와
공시 숫자는 거의 같다). 그 분기 이전의 σ·전년 값은 모두 그 전에 공시된 것이다.

진단(판정 아님): 이벤트 스터디 — SUE ≥ 2 / 중간 / ≤ −2 의 발표 다음 거래일 시가 이후
5·20·60거래일 초과수익(vs SPY), 발표 분기 묶음 부트스트랩 신뢰구간.

사용법 (Actions: history_long.yml):
    python screener/earnings.py fetch            # SEC submissions → us_earn_dates.json.gz
    python screener/earnings.py run [--json earnings.json]
"""
import gzip
import json
import sys
import time
from bisect import bisect_right
from datetime import date, timedelta
from pathlib import Path
from statistics import mean, median, stdev

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import baseline as B
import evalkit as E
import fundamentals as F
import history as H
import hypotheses as Hy
import pit_fund as PF
import robust as R
import spx_validate as S

EARN_DATES = H.CACHE / "us_earn_dates.json.gz"
SUBS_URL = "https://data.sec.gov/submissions/{name}"
SUE_MIN, SIGMA_MIN, MIN_PREV, N_PREV = 2.0, 0.02, 6, 8
WIN_LO, WIN_HI = 15, 75              # 분기말 + 일
HOLD_DAYS = 84
EVAL_FROM = "2014-01-01"
HORIZONS = (5, 20, 60)
GROUPS = (("hi", "SUE ≥ +2"), ("mid", "−2 < SUE < +2"), ("lo", "SUE ≤ −2"))
FIRST_2_02 = "2004-08-01"            # Item 2.02 가 생긴 때


# ---------------------------------------------------------------- 발표일 (SEC)
def parse_filings(block: dict) -> list[str]:
    """submissions 의 filings 블록(최근/과거 쪽) → 8-K Item 2.02 제출일 목록."""
    out = []
    forms, dts, items = block.get("form", []), block.get("filingDate", []), \
        block.get("items", [])
    for f, d, it in zip(forms, dts, items):
        if f == "8-K" and "2.02" in [x.strip() for x in str(it).split(",")]:
            out.append(d)
    return out


def fetch_one(cik: str) -> list[str] | None:
    r = F.http_get(SUBS_URL.format(name=f"CIK{cik}.json"), headers=F.SEC_HEADERS,
                   quiet_404=True)
    time.sleep(F.SEC_SLEEP)
    if not r:
        return None
    js = r.json()
    filings = js.get("filings", {})
    dates = parse_filings(filings.get("recent", {}))
    for ent in filings.get("files", []):
        if ent.get("filingTo", "9999") < FIRST_2_02:
            continue
        rr = F.http_get(SUBS_URL.format(name=ent["name"]), headers=F.SEC_HEADERS,
                        quiet_404=True)
        time.sleep(F.SEC_SLEEP)
        if rr:
            dates += parse_filings(rr.json())
    return sorted(set(d for d in dates if d >= FIRST_2_02))


def fetch() -> None:
    if not F.SEC_UA:
        raise SystemExit("SEC_USER_AGENT 가 비어 있다")
    m = S.load_members()
    names = sorted(S.all_members(m, "2004-01-01"))
    cmap = F.load_cik_map()
    out, nocik, fail = {}, 0, 0
    t0 = time.time()
    for n, t in enumerate(names, 1):
        cik = cmap.get(t) or cmap.get(t.replace("-", "."))
        if not cik:
            nocik += 1
            continue
        d = fetch_one(cik)
        if d is None:
            fail += 1
        elif d:
            out[t] = d
        if n % 100 == 0:
            print(f"  {n}/{len(names)} ({time.time() - t0:.0f}s)", flush=True)
    EARN_DATES.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(EARN_DATES, "wt", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"))
    counts = [len(v) for v in out.values()]
    print(f"  구성종목 {len(names)} · CIK 없음 {nocik} · 조회 실패 {fail} · 2.02 제출 있음 "
          f"{len(out)} (종목당 중앙값 {median(counts) if counts else 0:.0f}건) → {EARN_DATES} "
          f"({time.time() - t0:.0f}s)", flush=True)


def load_dates() -> dict[str, list[str]]:
    with gzip.open(EARN_DATES, "rt", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------- SUE · 이벤트
def announce_date(end: str, dates: list[str], lo: int = WIN_LO, hi: int = WIN_HI
                  ) -> str | None:
    """분기말 뒤 (lo, hi] 일 안의 첫 제출일."""
    e = date.fromisoformat(end)
    a, b = (e + timedelta(days=lo)).isoformat(), (e + timedelta(days=hi)).isoformat()
    i = bisect_right(dates, a)
    return dates[i] if i < len(dates) and dates[i] <= b else None


def sue_series(quarters: list[dict]) -> dict[str, float]:
    """{분기말: SUE} — quarters 는 오름차순, 'end' 와 'eps_diluted' 가 있는 dict."""
    eps = {q["end"]: q["eps_diluted"] for q in quarters
           if q.get("eps_diluted") is not None}
    ends = sorted(eps)
    delta = {}
    for e in ends:
        d = date.fromisoformat(e)
        best = None
        for e2 in ends:
            gap = abs((d - date.fromisoformat(e2)).days - 365)
            if gap <= 25 and (best is None or gap < best[0]):
                best = (gap, e2)
        if best:
            delta[e] = eps[e] - eps[best[1]]
    de = sorted(delta)
    out = {}
    for i, e in enumerate(de):
        prev = de[max(0, i - N_PREV):i]
        if len(prev) < MIN_PREV:
            continue
        span = (date.fromisoformat(e) - date.fromisoformat(prev[0])).days
        if span > (len(prev) + 1) * 100:             # 중간에 분기가 빠졌다
            continue
        sd = stdev([delta[x] for x in prev])
        if sd < SIGMA_MIN:
            continue
        out[e] = delta[e] / sd
    return out


def events_for(quarters: list[dict], dates: list[str]) -> list[dict]:
    """[{f: 발표일, end: 분기말, sue}] 발표일 오름차순."""
    out = []
    for end, v in sue_series(quarters).items():
        f = announce_date(end, dates)
        if f:
            out.append({"f": f, "end": end, "sue": v})
    return sorted(out, key=lambda x: x["f"])


def index_events(events: dict[str, list[dict]]) -> dict[str, tuple[list[str], list[dict]]]:
    """종목별 (발표일 목록, 이벤트 목록) — 주마다 다시 만들지 않으려고 한 번만."""
    return {t: ([e["f"] for e in evs], evs) for t, evs in events.items()}


def active_names(idx: dict, sig: str, members) -> list[str]:
    """신호일 sig 에 보유 중인 종목: SUE ≥ 2 이벤트의 발표일 f 가 f ≤ sig < f + 84일."""
    lo = (date.fromisoformat(sig) - timedelta(days=HOLD_DAYS)).isoformat()
    out = []
    for t in members:
        ent = idx.get(t)
        if not ent:
            continue
        fs, evs = ent
        for e in evs[bisect_right(fs, lo):bisect_right(fs, sig)]:     # f 가 (lo, sig]
            if e["sue"] >= SUE_MIN:
                out.append(t)
                break
    return sorted(out)


def h4_schedule(ctx: dict, events: dict[str, list[dict]]) -> list[tuple[str, dict]]:
    idx = index_events(events)
    out = []
    for s in ctx["snaps"]:
        sig = s["date"]
        out.append((sig, Hy.h2_weights(active_names(idx, sig, ctx["members"][sig]))))
    return out


# ---------------------------------------------------------------- 이벤트 스터디
def bucket(sue: float) -> str:
    return "hi" if sue >= SUE_MIN else "lo" if sue <= -SUE_MIN else "mid"


def event_study(events: dict[str, list[dict]], p, spy: S.Spy, member_fn,
                horizons=HORIZONS, since: str = EVAL_FROM) -> list[dict]:
    """이벤트마다 발표 다음 거래일 시가 → h 거래일 뒤 시가의 (종목 − SPY) 수익률."""
    rows = []
    for t, evs in events.items():
        j = p.cols.get(t)
        if j is None:
            continue
        for ev in evs:
            if ev["f"] < since or t not in member_fn(ev["f"]):
                continue
            d0 = p.entry_day(ev["f"])
            if d0 is None:
                continue
            i0 = p.pos[d0]
            a = p.o[i0, j]
            if not a == a or a <= 0:
                continue
            row = {"ticker": t, "f": ev["f"], "sue": ev["sue"], "group": bucket(ev["sue"]),
                   "q": f"{ev['f'][:4]}Q{(int(ev['f'][5:7]) - 1) // 3 + 1}"}
            for h in horizons:
                if i0 + h >= len(p.cal):
                    row[h] = None
                    continue
                b = p.o[i0 + h, j]
                if not b == b:                       # 그 날 거래 없음 → 마지막 종가
                    b = p.c[i0 + h - 1, j]
                s0, s1 = spy.at(d0, "open"), spy.at(p.cal[i0 + h], "open")
                row[h] = (b / a - 1) - (s1 / s0 - 1) if b == b and b > 0 else None
            rows.append(row)
    return rows


def study_table(rows: list[dict], horizons=HORIZONS) -> dict:
    out = {}
    for g, _ in GROUPS:
        for h in horizons:
            xs = [{"q": r["q"], "ar": r[h]} for r in rows if r["group"] == g and r[h] is not None]
            if not xs:
                continue
            ci = R.cluster_ci(xs, key="q", value="ar", n=1000)
            out[(g, h)] = {"n": len(xs), "mean": mean(x["ar"] for x in xs),
                           "median": median(x["ar"] for x in xs), "ci": ci,
                           "win": sum(1 for x in xs if x["ar"] > 0) / len(xs)}
    return out


# ---------------------------------------------------------------- 실행
def run(argv: list[str]) -> int:
    out_json = argv[argv.index("--json") + 1] if "--json" in argv else None
    fr, meta, ctx = B.build_frame()
    m = S.load_members()
    dates = load_dates()
    facts = json.load(gzip.open(PF.FACTS, "rt", encoding="utf-8"))
    events, n_ev, n_hi = {}, 0, 0
    for t in set(dates) & set(facts):
        evs = events_for(PF.full_quarters(facts[t]), dates[t])
        if evs:
            events[t] = evs
            n_ev += len(evs)
            n_hi += sum(1 for e in evs if e["sue"] >= SUE_MIN)
    members_cache: dict[str, set] = {}

    def member_fn(d: str) -> set:
        if d not in members_cache:
            members_cache[d] = S.members_at(m["current"], m["changes"], d)
        return members_cache[d]

    print(f"\n{'=' * 104}")
    print(f"  H4 실적 서프라이즈 드리프트 (ROADMAP 단계 2) · 평가 창 {EVAL_FROM}~ · 보류 기간 없음")
    print(f"  8-K 2.02 제출 있는 종목 {len(dates)} · EPS 이력 있는 종목 {len(facts)} · SUE 와 "
          f"발표일이 모두 있는 이벤트 {n_ev} (SUE≥2: {n_hi})")
    by_year: dict[str, list[int]] = {}
    for evs in events.values():
        for e in evs:
            c = by_year.setdefault(e["f"][:4], [0, 0])
            c[0] += 1
            c[1] += e["sue"] >= SUE_MIN
    print("  연도별 이벤트 (전체/SUE≥2): " + " · ".join(
        f"{y} {a}/{b}" for y, (a, b) in sorted(by_year.items()) if y >= "2010"))
    print(f"{'=' * 104}")
    doc: dict = {"events": n_ev, "hi": n_hi, "by_year": by_year}

    # 이벤트 스터디
    rows = event_study(events, ctx["p"], ctx["spy"], member_fn)
    tab = study_table(rows)
    print(f"\n  [이벤트 스터디] 발표 다음 거래일 시가 이후 초과수익(vs SPY) · S&P 500 구성종목 · "
          f"{EVAL_FROM}~ · 95% CI 는 발표 분기 묶음 부트스트랩")
    print(f"  {'그룹':<14} {'호라이즌':>8} {'건수':>6} {'평균':>8} {'중앙값':>8} {'95% CI':>20} "
          f"{'+비율':>6}")
    for g, lbl in GROUPS:
        for h in HORIZONS:
            r = tab.get((g, h))
            if r:
                ci = "-" if r["ci"] is None else \
                    f"[{r['ci'][0] * 100:+.2f}%, {r['ci'][1] * 100:+.2f}%]"
                print(f"  {lbl:<14} {h:>6}일 {r['n']:>6} {E.pct(r['mean'])} {E.pct(r['median'])} "
                      f"{ci:>20} {r['win'] * 100:5.0f}%")
    print(f"  시대별 SUE≥2, 60거래일 평균 초과수익:", end="")
    eras = {}
    for el, lo, hi in E.ERAS:
        xs = [r[60] for r in rows if r["group"] == "hi" and r[60] is not None
              and lo <= r["f"] <= hi]
        if len(xs) >= 20:
            eras[el] = (len(xs), mean(xs))
            print(f"  {el} {E.pct(mean(xs)).strip()} (n={len(xs)})", end="")
    print()
    hi60, lo60 = tab.get(("hi", 60)), tab.get(("lo", 60))
    if hi60 and lo60:
        print(f"  SUE≥2 − SUE≤−2 (60일) = {E.pct(hi60['mean'] - lo60['mean']).strip()}")
    doc["study"] = {f"{g}-{h}": v for (g, h), v in tab.items()}
    doc["study_eras"] = eras

    # 포트폴리오
    sched = {"h4": h4_schedule(ctx, events)}
    fr2, drag = Hy.add_schedules(ctx, sched)
    idx = index_events(events)
    n_act = [len(active_names(idx, s["date"], ctx["members"][s["date"]]))
             for s in ctx["snaps"] if s["date"] >= EVAL_FROM]
    print(f"\n  [H4 포트폴리오] 평가 창 {EVAL_FROM}~ · 평균 보유 종목 {mean(n_act):.0f}개 · "
          f"20개 미만인 주 {sum(1 for n in n_act if n < 20)}/{len(n_act)} · 연 비용 부담 "
          f"{drag['h4'] * 100:.2f}%")
    E.header()
    ev = E.evaluate(fr2, "h4", EVAL_FROM, "9999-12-31")
    E.row("H4 실적 서프라이즈 드리프트", ev)
    if "checks" in ev:
        f = E.fails(ev)
        if f:
            print(f"      탈락 이유: {' / '.join(f)}")
        print(f"      시대: " + " · ".join(
            f"{el} {E.pct(e['cand']).strip()} vs SPY {E.pct(e['spy']).strip()}"
            for el, e in ev["eras"].items()))
        print(f"      위기: " + " · ".join(
            f"{cl} {E.pct(c).strip()} vs {E.pct(sp).strip()}"
            for cl, (c, sp) in ev["crisis"].items()))
        print(f"      같은 풀 동일가중 {E.pct(ev['pool_cagr']).strip()} · 보정치 연 "
              f"{ev['haircut_ann'] * 100:.2f}%p · 보정 전 연복리 {E.pct(ev['cagr_raw']).strip()}")
        print(f"      보류 기간은 만들 수 없다 (EPS 가 2009~2011년부터) — "
              f"{'통과 시 실시간 모의(단계 4)로' if ev['passed'] else '탈락'}")
    doc["verdict"] = ev
    print(f"\n  ※ 생존편향 보정은 하한이다. 발표 시각(장 전/후)은 구분하지 않고 다음 거래일 시가에 "
          f"진입한다 (주간 포트폴리오는 평균 3일 더 늦다).")
    if out_json:
        Path(out_json).write_text(json.dumps(doc, ensure_ascii=False, indent=1, default=str),
                                  encoding="utf-8")
        print(f"  → {out_json}")
    return 0


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else ""
    if cmd == "fetch":
        fetch()
        return 0
    if cmd == "run":
        return run(argv[1:])
    sys.exit(__doc__)


if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main(sys.argv[1:]))
