# -*- coding: utf-8 -*-
"""S&P 500 풀 · S&P 500 기준 검증.

풀:  그 날 S&P 500 구성종목이던 종목 중 트렌드 템플릿 통과 종목.
     구성종목은 지금 목록에서 위키백과 변경 이력을 거꾸로 되돌려 시점별로 만든다
     — 지금 목록을 과거에 쓰면 '올라서 편입된 종목'을 미리 아는 셈이 된다.
     RS 백분위는 스크리너처럼 전체 시장 모집단으로 낸다.
기준: SPY (배당 포함 수정주가 — 종목 가격도 배당 반영이라 같은 조건)
     - 거래마다 같은 보유 기간의 SPY 수익률을 빼서 초과수익을 잰다
     - 20슬롯 포트폴리오 연복리 수익률을 같은 기간 SPY 보유와 비교한다

규칙 7개 (candidates.py 와 같은 선정·청산): 현재 규칙(RS 상위 5), C1~C5, C1+C5.

판정 (결과를 보기 전에 고정):
  설계 2019-02 ~ 2023-12 — 아래를 모두 넘어야 한다. 규칙이 7개라 CI 는 99%.
    1) 거래당 평균 초과수익(비용 후, 같은 기간 SPY 대비) > 0
    2) 종목 단위 부트스트랩 99% 신뢰구간 하한 > 0
    3) 기여 상위 2종목을 빼도 평균 초과수익 > 0
    4) 평균 초과수익이 플러스인 해가 절반 초과
    5) 20슬롯 포트폴리오 연복리 > 같은 기간 SPY 보유 연복리
  보류 2024-01 ~ 끝 — 설계 통과 규칙만, 같은 기준(CI 95%). 사후 등록은 없다.

비용: 편도 5bp (매수·매도). SPY 쪽은 비용 없음 (보수적).
포트폴리오: 자본을 20칸으로 나눠 신호 순서대로 빈 칸에 넣는다(칸 크기 = 전날 평가액
÷ 20). 칸이 차 있으면 그 신호는 건너뛴다. 현금 이자는 0.

한계: 위키백과 변경 이력의 누락, 그리고 상장폐지된 옛 구성종목은 가격이 없어 빠진다
(빠진 비율을 출력한다 — 그만큼 결과가 좋게 기운다).

사용법 (Actions: history.yml):
    python screener/spx_validate.py members    # 위키백과 → .cache/sp500_pit.json
    python screener/spx_validate.py prices     # 유니버스 밖 옛 구성종목 + SPY
    python screener/spx_validate.py rebuild    # 시점별 S&P 500 풀 주간 리스트
    python screener/spx_validate.py validate [--json spx.json]
"""
import gzip
import io
import json
import pickle
import sys
from bisect import bisect_right
from datetime import date
from pathlib import Path
from statistics import mean

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import candidates as C
import history as H
import pit_fund as PF
import robust as R

CACHE = H.CACHE
MEMBERS = CACHE / "sp500_pit.json"
EXTRA = CACHE / "sp500_extra.pkl"
SNAPS = CACHE / "sp500_snaps.json.gz"
WIKI = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
UA = {"User-Agent": "Mozilla/5.0 (minervini-auto-trading research)"}
BENCH = "SPY"

SLOTS = 20
COST = 0.0005                    # 편도
DESIGN_CI, HOLDOUT_CI = 0.99, 0.95
CHECKS = (
    ("positive", "거래당 평균 초과수익 > 0"),
    ("ci", "종목 단위 신뢰구간 하한 > 0 (설계 99% · 보류 95%)"),
    ("survives_top2", "기여 상위 2종목을 빼도 평균 초과수익 > 0"),
    ("years", "평균 초과수익 플러스인 해가 절반 초과"),
    ("portfolio", "20슬롯 포트폴리오 연복리 > SPY 보유 연복리"),
)


# ---------------------------------------------------------------- 구성종목
def tk(x) -> str | None:
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return None
    s = str(x).strip().upper().replace(".", "-")
    return s if s and s != "NAN" else None


def flat(col) -> str:
    return " ".join(str(c) for c in col) if isinstance(col, tuple) else str(col)


def parse_wiki(html: str) -> tuple[list[str], list[dict]]:
    """위키백과 S&P 500 문서 → (지금 구성종목, 변경 이력 [{date, added, removed}])."""
    current, changes = None, None
    for t in pd.read_html(io.StringIO(html)):
        cols = [flat(c) for c in t.columns]
        if current is None and "Symbol" in cols:
            current = sorted({x for x in map(tk, t["Symbol"]) if x})
            continue
        date_c = next((c for c in cols if "Date" in c), None)
        add_c = next((c for c in cols if "Added" in c and "Ticker" in c), None)
        rem_c = next((c for c in cols if "Removed" in c and "Ticker" in c), None)
        if changes is None and date_c and add_c and rem_c:
            t.columns = cols
            ds = pd.to_datetime(t[date_c], errors="coerce", format="mixed")
            changes = [{"date": d.strftime("%Y-%m-%d"), "added": tk(a),
                        "removed": tk(r)}
                       for d, a, r in zip(ds, t[add_c], t[rem_c])
                       if not pd.isna(d) and (tk(a) or tk(r))]
    if not current or changes is None:
        raise ValueError("위키백과 표를 찾지 못했다 (구성종목 표 · 변경 이력 표)")
    return current, sorted(changes, key=lambda c: c["date"])


def members_at(current: list[str], changes: list[dict], d: str) -> set[str]:
    """d 일의 구성종목 — 지금 목록에서 d 이후(d 초과)에 일어난 변경을 거꾸로 되돌린다.

    변경일(effective date)에는 이미 바뀐 상태다.
    """
    state = set(current)
    for ch in reversed(changes):
        if ch["date"] <= d:
            break
        if ch["added"]:
            state.discard(ch["added"])
        if ch["removed"]:
            state.add(ch["removed"])
    return state


def members() -> None:
    import requests
    r = requests.get(WIKI, headers=UA, timeout=30)
    r.raise_for_status()
    current, changes = parse_wiki(r.text)
    CACHE.mkdir(parents=True, exist_ok=True)
    MEMBERS.write_text(json.dumps({"fetched": date.today().isoformat(),
                                   "current": current, "changes": changes}),
                       encoding="utf-8")
    since = [c for c in changes if c["date"] >= "2019-01-01"]
    print(f"  S&P 500 지금 {len(current)}종목 · 변경 이력 {len(changes)}건 "
          f"(2019년 이후 {len(since)}건, 가장 오래된 {changes[0]['date']}) "
          f"→ {MEMBERS}", flush=True)


def load_members() -> dict:
    return json.loads(MEMBERS.read_text(encoding="utf-8"))


def all_members(m: dict, since: str) -> set[str]:
    """since 이후 한 번이라도 구성종목이던 종목."""
    out = members_at(m["current"], m["changes"], since)
    for ch in m["changes"]:
        if ch["date"] > since:
            out |= {x for x in (ch["added"], ch["removed"]) if x}
    return out | set(m["current"])


# ---------------------------------------------------------------- 가격
def first_signal(start: str) -> pd.Timestamp:
    return pd.Timestamp(start) + pd.Timedelta(days=H.SIGNAL_LAG_DAYS)


def prices() -> None:
    """유니버스 캐시에 없는 옛·현 구성종목과 SPY(OHLC, 배당 반영)를 받는다."""
    import yfinance as yf
    data = H.load_prices()
    m = load_members()
    need = all_members(m, first_signal(data["start"]).strftime("%Y-%m-%d"))
    missing = sorted(need - set(data["prices"]))
    got, _ = H.fetch_prices(missing, data["start"]) if missing else ({}, 0)
    spy = yf.download(BENCH, start=data["start"], interval="1d",
                      auto_adjust=True, progress=False)
    if isinstance(spy.columns, pd.MultiIndex):
        spy.columns = spy.columns.get_level_values(0)
    spy = spy[["Open", "High", "Low", "Close"]].dropna().astype("float64")
    with open(EXTRA, "wb") as f:
        pickle.dump({"prices": got, "missing": missing, "spy": spy}, f)
    print(f"  기간 중 구성종목 {len(need)} · 유니버스 캐시에 있음 "
          f"{len(need) - len(missing)} · 추가로 받음 {len(got)} · 가격 없음 "
          f"{len(missing) - len(got)} · SPY {len(spy)}일 → {EXTRA}", flush=True)


def load_extra() -> dict:
    with open(EXTRA, "rb") as f:
        return pickle.load(f)


def all_prices() -> tuple[dict, dict, dict]:
    data, ex = H.load_prices(), load_extra()
    return {**data["prices"], **ex["prices"]}, data, ex


# ---------------------------------------------------------------- 재구성
def rebuild() -> None:
    px, data, ex = all_prices()
    m = load_members()
    cal = pd.DatetimeIndex(data["bench"].index)
    weekly = [d for d in H.weekly_dates(cal) if d >= first_signal(data["start"])]
    wset = {d.strftime("%Y-%m-%d") for d in weekly}
    mem = {ds: members_at(m["current"], m["changes"], ds) for ds in wset}
    names = json.loads(H.UNIVERSE.read_text(encoding="utf-8"))
    snaps, _ = H.build_snaps(px, pd.DatetimeIndex(weekly), H.screener_cfg(),
                             names, weekly_set=wset,
                             keep=lambda ds, t: t in mem[ds])
    # 구성종목 중 그 날 가격이 있는 비율 — 없는 종목은 풀에 들어올 수 없다
    cover = {}
    for ds, ms in mem.items():
        ts = pd.Timestamp(ds)
        have = sum(1 for t in ms if t in px and ts in px[t].index)
        cover[ds] = (len(ms), have)
    with gzip.open(SNAPS, "wt", encoding="utf-8") as f:
        json.dump({"snaps": snaps, "members": cover}, f)
    n = [len(s["rows"]) for s in snaps]
    sizes = [v[0] for v in cover.values()]
    have = sum(v[1] for v in cover.values()) / sum(sizes)
    print(f"  주간 {len(snaps)}개 · 구성종목 수 {min(sizes)}~{max(sizes)} · 그 날 "
          f"가격 있음 {have * 100:.1f}% · 통과 종목 평균 {mean(n):.0f} "
          f"(최소 {min(n)}, 최대 {max(n)}) → {SNAPS}", flush=True)


def load_snaps() -> tuple[list[dict], dict]:
    with gzip.open(SNAPS, "rt", encoding="utf-8") as f:
        d = json.load(f)
    return d["snaps"], d["members"]


# ---------------------------------------------------------------- 초과수익
class Spy:
    def __init__(self, df: pd.DataFrame):
        self.dates = [d.strftime("%Y-%m-%d") for d in df.index]
        self.open = df["Open"].tolist()
        self.close = df["Close"].tolist()

    def at(self, d: str, field: str) -> float:
        """d 일의 값. 그 날이 없으면 그 전 마지막 거래일 종가."""
        i = bisect_right(self.dates, d) - 1
        if i < 0:
            raise ValueError(f"SPY 가격 없음: {d}")
        if self.dates[i] != d:
            return self.close[i]
        return (self.open if field == "open" else self.close)[i]


def net_ret(ret: float) -> float:
    return (1 + ret) * (1 - COST) / (1 + COST) - 1


def add_excess(trades: list[dict], spy: Spy) -> list[dict]:
    """거래마다 같은 보유 기간 SPY 수익률과 초과수익. 시가 체결 청산이면 SPY 도 시가."""
    out = []
    for t in trades:
        if t.get("ret") is None:
            continue
        exit_f = "open" if "시가" in t.get("reason", "") else "close"
        s = spy.at(t["exit_date"], exit_f) / spy.at(t["entry_date"], "open") - 1
        out.append(dict(t, spy_ret=s, excess=net_ret(t["ret"]) - s))
    return out


def excess_contrib(trades: list[dict]) -> list[tuple[str, float]]:
    g: dict[str, float] = {}
    for t in trades:
        g[t["ticker"]] = g.get(t["ticker"], 0.0) + t["excess"]
    return sorted(g.items(), key=lambda x: (-x[1], x[0]))


# ---------------------------------------------------------------- 포트폴리오
def portfolio(trades: list[dict], ohlc: dict, index: dict, spy: Spy,
              lo: str, hi: str, slots: int = SLOTS) -> dict:
    """lo~hi 동안 신호 순서대로 빈 칸에 넣는 포트폴리오 vs SPY 보유.

    trades 는 진입 순서(코호트 → 선정 순위)대로. 청산가는 simulate 의 체결가.
    hi 에 보유 중인 종목은 hi 종가로 평가한다.
    """
    days = [d for d in spy.dates if lo <= d <= hi]
    if len(days) < 2:
        return {}
    todo: dict[str, list[dict]] = {}
    for t in trades:
        if lo <= t["entry_date"] <= hi:
            todo.setdefault(t["entry_date"], []).append(t)

    def close(tkr: str, d: str, fallback: float) -> float:
        idx = index[tkr]
        i = bisect_right(idx, d) - 1
        return ohlc[tkr][i]["close"] if i >= 0 else fallback

    cash, held, curve = 1.0, [], []
    taken = skipped = 0
    prev = 1.0
    for d in days:
        # 1) 어제까지 산 종목의 청산
        keep = []
        for p in held:
            if p["exit_date"] == d:
                cash += p["shares"] * p["exit"] * (1 - COST)
            else:
                keep.append(p)
        held = keep
        # 2) 오늘 시가 진입 (칸 크기 = 전날 평가액 / 칸 수)
        for t in todo.get(d, []):
            size = min(prev / slots, cash)
            if len(held) >= slots or size <= 0:
                skipped += 1
                continue
            taken += 1
            cash -= size
            p = {"ticker": t["ticker"], "shares": size / (t["entry"] * (1 + COST)),
                 "exit_date": t["exit_date"] if t["status"] != "보유" else None,
                 "exit": t["exit"], "entry": t["entry"]}
            if p["exit_date"] == d:                  # 산 날 청산
                cash += p["shares"] * p["exit"] * (1 - COST)
            else:
                held.append(p)
        prev = cash + sum(p["shares"] * close(p["ticker"], d, p["entry"])
                          for p in held)
        curve.append(prev)

    def stats(xs: list[float], start: float) -> tuple[float, float]:
        yrs = (date.fromisoformat(days[-1]) - date.fromisoformat(days[0])).days / 365.25
        peak, mdd = start, 0.0
        for x in xs:
            peak = max(peak, x)
            mdd = min(mdd, x / peak - 1)
        return (xs[-1] / start) ** (1 / yrs) - 1, mdd

    cagr, mdd = stats(curve, 1.0)
    s0 = spy.at(days[0], "open")
    spy_curve = [spy.at(d, "close") for d in days]
    s_cagr, s_mdd = stats(spy_curve, s0)
    return {"from": days[0], "to": days[-1], "cagr": cagr, "mdd": mdd,
            "total": curve[-1] - 1, "spy_cagr": s_cagr, "spy_mdd": s_mdd,
            "spy_total": spy_curve[-1] / s0 - 1, "taken": taken,
            "skipped": skipped}


# ---------------------------------------------------------------- 판정
def evaluate(trades: list[dict], port: dict, level: float) -> dict:
    ex = [t["excess"] for t in trades]
    e = mean(ex) if ex else None
    ci = R.cluster_ci(trades, value="excess", level=level)
    contrib = excess_contrib(trades)
    drop = {k for k, _ in contrib[:2]}
    rest = [t["excess"] for t in trades if t["ticker"] not in drop]
    e2 = mean(rest) if rest else None
    years: dict[str, list[float]] = {}
    for t in trades:
        years.setdefault(t["cohort"][:4], []).append(t["excess"])
    pos = sum(1 for v in years.values() if mean(v) > 0)
    res = {
        "positive": None if e is None else e > 0,
        "ci": None if ci is None else ci[0] > 0,
        "survives_top2": None if e2 is None else e2 > 0,
        "years": (pos > len(years) / 2) if years else None,
        "portfolio": None if not port else port["cagr"] > port["spy_cagr"],
    }
    return {"trades": len(trades), "tickers": len(contrib), "excess": e,
            "ci": ci, "win_vs_spy": (sum(1 for x in ex if x > 0) / len(ex)
                                     if ex else None),
            "spy_ret": mean(t["spy_ret"] for t in trades) if trades else None,
            "top2_dropped": sorted(drop), "excess_top2": e2,
            "years": {y: mean(v) for y, v in sorted(years.items())},
            "years_pos": pos, "years_n": len(years), "portfolio": port,
            "checks": res, "passed": all(v is True for v in res.values())}


def pct(x: float | None, w: int = 7) -> str:
    return f"{'-':>{w}}" if x is None else f"{x * 100:+{w - 1}.2f}%"


def ci_str(ci) -> str:
    return "        -         " if ci is None else \
        f"[{ci[0] * 100:+.2f}%, {ci[1] * 100:+.2f}%]"


def header() -> None:
    print(f"  {C.B_wpad('규칙', 24)} {'거래':>5} {'종목':>4} {'초과/거래':>8} "
          f"{'종목단위 CI':>20} {'상위2제외':>8} {'플러스해':>5} "
          f"{'포트 연복리':>9} {'SPY 연복리':>9} {'포트 MDD':>8}  판정")


def show(label: str, ev: dict) -> None:
    p = ev["portfolio"] or {}
    print(f"  {C.B_wpad(label, 24)} {ev['trades']:>5} {ev['tickers']:>4} "
          f"{pct(ev['excess'], 8)} {ci_str(ev['ci']):>20} "
          f"{pct(ev['excess_top2'], 8)} {ev['years_pos']}/{ev['years_n']:<3} "
          f"{pct(p.get('cagr'), 9)} {pct(p.get('spy_cagr'), 9)} "
          f"{pct(p.get('mdd'), 8)}  {'통과' if ev['passed'] else '탈락'}")


def reasons(evs: dict) -> None:
    for k, ev in evs.items():
        fails = [d for key, d in CHECKS if ev["checks"][key] is not True]
        if fails:
            print(f"    {k} 탈락 이유: {' / '.join(fails)}")


def validate(argv: list[str]) -> int:
    out_json = argv[argv.index("--json") + 1] if "--json" in argv else None
    px, data, ex = all_prices()
    snaps_all, cover = load_snaps()
    snaps = snaps_all[:-1]                      # 마지막은 진입 불가
    first, last = snaps[0]["date"], snaps[-1]["date"]
    spy = Spy(ex["spy"])
    pool = {t for s in snaps for t in s["rows"]}
    sizes = [v[0] for v in cover.values()]
    have = sum(v[1] for v in cover.values()) / sum(sizes)
    n_pass = [len(s["rows"]) for s in snaps]

    print(f"\n{'=' * 104}")
    print(f"  S&P 500 풀 · S&P 500(SPY) 기준 검증  주간 신호 {len(snaps)}개 "
          f"({first} ~ {last}) · 설계 ~{C.DESIGN_END} / 보류 2024-01-01~")
    print(f"{'=' * 104}")
    print(f"  풀: 그 날의 S&P 500 구성종목(위키백과 변경 이력으로 되돌림, "
          f"{min(sizes)}~{max(sizes)}종목) 중 트렌드 템플릿 통과 · 주 평균 "
          f"{mean(n_pass):.0f}종목 · 고유 {len(pool)}종목")
    print(f"  기준: SPY(배당 포함) 같은 보유 기간 · 비용 편도 {COST * 1e4:.0f}bp · "
          f"포트폴리오 {SLOTS}칸 · 부트스트랩 {R.N_BOOT}회")
    print(f"  청산: candidates.py 와 같음 (다음날 시가 진입 · 손절 → 손절폭 × "
          f"{C.TARGET_R:.0f} 익절)")
    print(f"  ※ 구성종목 중 그 날 가격이 있는 비율 {have * 100:.1f}% — 나머지(주로 "
          f"상장폐지)는 빠져 결과가 좋게 기운다", flush=True)

    aux = {t: C.aux_indicators(px[t]) for t in pool if t in px}
    ohlc, index = H.to_ohlc(px, pool, first)
    timeline = PF.load_timeline() if PF.TIMELINE.exists() else None
    cands, meta, ref = C.build(snaps, px, aux, timeline)
    rules = {"현재 규칙(RS 상위5)": ref, **cands}
    trades = {k: add_excess(C.simulate_picks(v, ohlc, index), spy)
              for k, v in rules.items()}
    order = {k: {(c, p["ticker"]): i for i, (c, ps) in enumerate(v)
                 for p in ps} for k, v in rules.items()}
    for k in trades:                            # 포트폴리오는 신호 순서대로
        trades[k].sort(key=lambda t: (t["entry_date"],
                                      order[k].get((t["cohort"], t["ticker"]), 0)))

    doc = {"first": first, "last": last, "member_price_coverage": have,
           "slots": SLOTS, "cost": COST, "c5": meta, "design": {}, "holdout": {}}
    periods = {"설계": (first, C.DESIGN_END), "보류": ("2024-01-01", last)}

    lo, hi = periods["설계"]
    print(f"\n  [설계 기간] {lo} ~ {hi} — 종목 단위 CI {DESIGN_CI * 100:.0f}%")
    header()
    passed = []
    for k, tr in trades.items():
        sub = C.in_period(tr, lo, hi)
        ev = evaluate(sub, portfolio(sub, ohlc, index, spy, lo, hi), DESIGN_CI)
        doc["design"][k] = ev
        show(k, ev)
        if ev["passed"]:
            passed.append(k)
    reasons(doc["design"])

    lo, hi = periods["보류"]
    print(f"\n  [보류 기간] {lo} ~ {hi} — 설계 통과 규칙만 한 번 계산 · CI "
          f"{HOLDOUT_CI * 100:.0f}%")
    if not passed:
        print(f"    설계 기간을 통과한 규칙이 없다 — 보류 기간은 계산하지 않는다")
    else:
        header()
        for k in passed:
            sub = C.in_period(trades[k], lo, hi)
            ev = evaluate(sub, portfolio(sub, ohlc, index, spy, lo, last),
                          HOLDOUT_CI)
            doc["holdout"][k] = ev
            show(k, ev)
        reasons(doc["holdout"])

    # 참고: 연도별 거래당 초과수익 (판정에는 안 쓴다)
    print(f"\n  [참고] 연도별 거래당 평균 초과수익 (SPY 대비) — 설계 기간 숫자만")
    yrs = sorted({y for ev in doc["design"].values() for y in ev["years"]})
    print(f"  {C.B_wpad('규칙', 24)} " + " ".join(f"{y:>7}" for y in yrs))
    for k, ev in doc["design"].items():
        print(f"  {C.B_wpad(k, 24)} "
              + " ".join(pct(ev["years"].get(y)) for y in yrs))
    print(f"\n  ※ 수익률은 배당 포함 수정주가 기준. 생존 편향이 남아 있어 실제보다 좋게 "
          f"나왔을 가능성이 크다")

    if out_json:
        Path(out_json).write_text(json.dumps(doc, ensure_ascii=False, indent=2,
                                             default=str), encoding="utf-8")
        print(f"  → {out_json}")
    return 0


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else ""
    if cmd == "members":
        members()
    elif cmd == "prices":
        prices()
    elif cmd == "rebuild":
        rebuild()
    elif cmd == "validate":
        return validate(argv[1:])
    else:
        sys.exit(__doc__)
    return 0


if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main(sys.argv[1:]))
