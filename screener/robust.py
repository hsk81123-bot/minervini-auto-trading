# -*- coding: utf-8 -*-
"""1단계 견고성 점검 — 지금 결론이 실행 가능한 가격과 실제 표본 크기에서도 버티는가.

validate.py 가 '규칙이 좋아 보이는가'를 잰다면, 여기서는 '그 숫자를 믿어도
되는가'를 잰다. 세 가지를 점검한다.

1. 진입 시점
   스크리너는 장 마감 뒤에 돈다. 신호를 만든 그 종가에는 살 수 없다. 신호 당일
   종가 진입(지금까지의 계산, 실행 불가)과 다음날 시가 진입을 나란히 낸다.

2. 통계적 신뢰도
   코호트 19개 x 5종목 = 95거래로 보이지만 같은 종목이 날짜만 바꿔 반복된다.
   - 종목 단위 부트스트랩: 거래가 아니라 종목을 단위로 다시 뽑아 기대값의
     95% 신뢰구간을 낸다. 같은 종목의 거래들은 서로 독립이 아니기 때문이다.
   - 종목당 동일 가중: 종목마다 평균 R을 먼저 내고 그 평균을 낸다.
   - 기여 상위 종목 제외: R을 가장 많이 번 종목부터 1·2·3개를 빼고 다시 잰다.
     몇 종목이 결론을 만들고 있다면 여기서 무너진다.
   - 무작위 대조군 200회: 같은 날 같은 통과 종목 풀에서 무작위로 N개를 뽑는
     것을 200번 반복해 분포를 만든다. 대조군 한 번은 운이 섞이므로, 로직이
     그 분포의 몇 번째 백분위에 있는지로 판정한다.

3. RS 동점 처리
   RS는 1~99 정수라 맨 위에 동점이 몰린다(하루 RS 99가 5~9종목). 그날 상위
   N은 사실상 동점 안에서 VCP 점수 순서로 정해진다. 이 방식을
   - 반올림 전 RS 원점수(스크리너와 같은 공식, 그 날까지의 종가로만 계산) 순서
   - 동점 안에서 무작위(200회)
   와 비교한다.

판정 기준은 결과를 보기 전에 코드에 박아 둔다 (CHECKS).

사용법:
    python screener/robust.py --stop -8 --no-target --trail -15
    python screener/robust.py --stop -8 --target 25 --json robust.json
"""
import json
import random
import sys
from datetime import date, timedelta
from pathlib import Path
from statistics import mean

sys.path.insert(0, str(Path(__file__).resolve().parent))
import backtest as B
import papertrade as P
import validate as V

MARKET = "us"
N_BOOT = 2000            # 부트스트랩 반복
N_PERM = 200             # 무작위 대조군·무작위 동점 처리 반복
SEED = V.SEED
RS_LOOKBACK_DAYS = 420   # 원점수 계산에 필요한 253거래일 + 여유
CI_LEVEL = 0.95
EDGE_PCTL = 0.95         # 로직이 무작위 분포의 이 백분위 이상이어야 통과


# ---------------------------------------------------------------- RS 원점수
def raw_rs(closes: list[float]) -> float | None:
    """screener.rs_raw_score 와 같은 공식 (IBD 방식).

    최근 3개월 수익률 40% + 이전 3개 분기 각 20%. 253거래일 미만이면 None.
    """
    if len(closes) < 253:
        return None
    c = closes[-1]
    q1, q2, q3, q4 = closes[-64], closes[-127], closes[-190], closes[-253]
    if min(q1, q2, q3, q4) <= 0:
        return None
    return 2.0 * (c / q1) + (c / q2) + (c / q3) + (c / q4)


def raw_rs_asof(rows: list[dict], d: str) -> float | None:
    """그 날까지의 종가로만 계산한 원점수 (그 뒤 가격은 쓰지 않는다)."""
    return raw_rs([r["close"] for r in rows if r["date"] <= d])


# ---------------------------------------------------------------- 코호트 변형
def tie_cohorts(snaps: list[dict], top: int, secondary) -> list[tuple]:
    """RS 내림차순, 동점은 secondary(row, date)가 작은 순서로 상위 N.

    secondary 는 정렬 키를 돌려준다. 현재 규칙은 -VCP 점수다.
    """
    out = []
    for s in V.entry_snaps(snaps, 1):
        rows = [{"ticker": t, **v} for t, v in s["rows"].items()]
        rows.sort(key=lambda r: (-(r["rs"] or 0), secondary(r, s["date"]),
                                 r["ticker"]))
        out.append((s["date"], rows[:top]))
    return out


def tie_group(snaps: list[dict], top: int) -> dict[str, list[dict]]:
    """날짜별로 '상위 N 경계의 RS 이상'인 종목들 — 동점 처리가 영향을 주는 범위."""
    out = {}
    for s in V.entry_snaps(snaps, 1):
        rows = sorted(({"ticker": t, **v} for t, v in s["rows"].items()),
                      key=B.rank_key)
        if not rows:
            continue
        cut = rows[min(top, len(rows)) - 1]["rs"] or 0
        out[s["date"]] = [r for r in rows if (r["rs"] or 0) >= cut]
    return out


# ---------------------------------------------------------------- 통계
def expectancy(trades: list[dict]) -> float | None:
    rs = [t["r_multiple"] for t in V.marked(trades)]
    return mean(rs) if rs else None


def cluster_ci(trades: list[dict], key: str = "ticker", n: int = N_BOOT,
               seed: int = SEED, level: float = CI_LEVEL,
               value: str = "r_multiple") -> tuple[float, float] | None:
    """묶음(종목) 단위로 다시 뽑는 부트스트랩 — 기대값의 신뢰구간.

    같은 종목의 거래는 서로 독립이 아니므로 거래 하나하나가 아니라 종목을
    단위로 복원 추출한다. 묶음이 2개 미만이면 None.
    """
    groups: dict[str, list[float]] = {}
    for t in trades:
        if t.get(value) is not None:
            groups.setdefault(t[key], []).append(t[value])
    keys = sorted(groups)
    if len(keys) < 2:
        return None
    rng = random.Random(seed)
    stats = []
    for _ in range(n):
        pool = [r for k in rng.choices(keys, k=len(keys)) for r in groups[k]]
        stats.append(mean(pool))
    stats.sort()
    lo = stats[int((1 - level) / 2 * n)]
    hi = stats[min(n - 1, int((1 + level) / 2 * n))]
    return lo, hi


def per_ticker_mean(trades: list[dict]) -> float | None:
    """종목마다 평균 R을 먼저 내고 그 평균 — 종목 하나가 한 표만 갖는다."""
    g: dict[str, list[float]] = {}
    for t in V.marked(trades):
        g.setdefault(t["ticker"], []).append(t["r_multiple"])
    return mean(mean(v) for v in g.values()) if g else None


def contributions(trades: list[dict]) -> list[tuple[str, int, float, float]]:
    """(종목, 거래 수, R 합계, R 평균) — R 합계가 큰 순서."""
    g: dict[str, list[float]] = {}
    for t in V.marked(trades):
        g.setdefault(t["ticker"], []).append(t["r_multiple"])
    rows = [(k, len(v), sum(v), mean(v)) for k, v in g.items()]
    rows.sort(key=lambda x: (-x[2], x[0]))
    return rows


def without(trades: list[dict], tickers: set[str]) -> list[dict]:
    return [t for t in trades if t["ticker"] not in tickers]


def percentile_of(x: float, dist: list[float]) -> float:
    """x 가 분포에서 몇 번째 백분위인가 (x보다 작은 값의 비율, 동점은 절반)."""
    if not dist:
        return float("nan")
    lt = sum(1 for d in dist if d < x)
    eq = sum(1 for d in dist if d == x)
    return (lt + 0.5 * eq) / len(dist)


def pct_range(dist: list[float], lo: float = 0.05, hi: float = 0.95
              ) -> tuple[float, float]:
    s = sorted(dist)
    return s[int(lo * (len(s) - 1))], s[int(hi * (len(s) - 1))]


# ---------------------------------------------------------------- 판정 기준
# 결과를 보기 전에 정해 둔 통과 조건. 하나라도 실패하면 '검증 통과'라고 말하지 않는다.
CHECKS = (
    ("next_open_positive", "다음날 시가 진입 기대값 > 0"),
    ("cluster_ci_positive", "종목 단위 95% 신뢰구간 하한 > 0"),
    ("beats_random", f"로직 기대값이 무작위 {N_PERM}회 분포의 "
                     f"{EDGE_PCTL * 100:.0f}번째 백분위 이상"),
    ("survives_top2", "기여 상위 2종목을 빼도 기대값 > 0"),
    ("survives_top2_vs_random", "상위 2종목을 빼도 무작위 분포 중앙값보다 높음"),
)


# ---------------------------------------------------------------- 출력
def fmt_ci(ci: tuple[float, float] | None) -> str:
    return "      -        " if ci is None else f"[{ci[0]:+.2f}R, {ci[1]:+.2f}R]"


def perf_line(label: str, m: dict, ci: tuple[float, float] | None) -> str:
    """진입 방식 비교 한 줄."""
    if "expectancy_r" not in m:
        return f"  {B.wpad(label, 26)}    거래 없음"
    pay = "   -  " if m["payoff"] is None else f"{m['payoff']:5.2f}:1"
    return (f"  {B.wpad(label, 26)} {m['measured']:>4}  "
            f"{P.rfmt(m['expectancy_r'])} {m['win_rate'] * 100:5.1f}% {pay} "
            f"{B.pct(m['avg_mae']).strip():>8}  {fmt_ci(ci)}")


def pct_str(x: float | None) -> str:
    return "-" if x is None else f"{x * 100:.1f}번째 백분위"


def median_of(xs: list[float]) -> float | None:
    return sorted(xs)[len(xs) // 2] if xs else None


def mark(ok: bool | None) -> str:
    return "  -  " if ok is None else ("통과" if ok else "실패")


def prepare(snaps: list[dict], top: int) -> dict:
    """점검에 쓸 코호트들을 미리 만든다 — 가격을 받기 전에 필요한 종목을 알기 위해."""
    ties = tie_group(snaps, top)
    rng = random.Random(SEED)
    return {
        "base": V.cohorts(snaps, top, 1),
        "rnd_sets": [V.random_cohorts(snaps, top, 1, seed=SEED + i)
                     for i in range(N_PERM)],
        "ties": ties,
        "tie_rand_keys": [{(d, r["ticker"]): rng.random()
                           for d, rows in ties.items() for r in rows}
                          for _ in range(N_PERM)],
    }


def needed_tickers(prep: dict) -> tuple[set[str], set[str]]:
    """(동점 후보 — 원점수용 긴 이력 필요, 그 외 — 진입일 이후만 필요)."""
    tie = {r["ticker"] for rows in prep["ties"].values() for r in rows}
    other = ({r["ticker"] for _, rows in prep["base"] for r in rows}
             | {r["ticker"] for cs in prep["rnd_sets"]
                for _, rows in cs for r in rows})
    return tie, other - tie


def run_checks(snaps: list[dict], ohlc: dict[str, list[dict]], o: dict,
               prep: dict | None = None,
               index: dict[str, list[str]] | None = None,
               stage: str = "1단계") -> dict:
    """1단계 점검 [1]~[판정]. 스냅샷 출처(git 커밋 / 과거 재구성)와 무관하다.

    ohlc 는 {종목: 일봉 dict 목록}, index 는 {종목: 날짜 목록}(이분 탐색용).
    """
    prep = prep or prepare(snaps, o["top"])
    base, rnd_sets = prep["base"], prep["rnd_sets"]
    ties, tie_rand_keys = prep["ties"], prep["tie_rand_keys"]
    n_coh = len(base)

    def go(cos, entry):
        return V.run(cos, MARKET, ohlc, dict(o, entry=entry), index)

    doc: dict = {"market": MARKET, "rules": {k: o[k] for k in P.DEFAULTS},
                 "cohorts": n_coh, "first": base[0][0], "last": base[-1][0],
                 "n_boot": N_BOOT, "n_perm": N_PERM, "seed": SEED}

    # ---- 1. 진입 시점 ------------------------------------------------------
    print(f"\n  [1] 진입 시점 — 신호 당일 종가(실행 불가) vs 다음날 시가")
    print(f"  {B.wpad('진입', 26)} {V._rpad('거래', 4)}  {V._rpad('기대값', 7)} "
          f"{V._rpad('승률', 6)} {V._rpad('손익비', 7)} {V._rpad('평균MAE', 8)}"
          f"  {V._rpad('종목단위 95% 신뢰구간', 22)}")
    entry_res = {}
    for label, mode in (("신호 당일 종가 (실행 불가)", "close"),
                        ("다음날 시가", "next_open")):
        tr = go(base, mode)
        m = P.perf(V.marked(tr))
        ci = cluster_ci(tr)
        entry_res[mode] = {"trades": tr, "perf": m, "ci": ci}
        print(perf_line(label, m, ci))
    e_close = entry_res["close"]["perf"].get("expectancy_r")
    e_open = entry_res["next_open"]["perf"].get("expectancy_r")
    if e_close is not None and e_open is not None:
        print(f"    → 실행 가능한 가격으로 바꾸면 {e_open - e_close:+.2f}R/거래")
    doc["entry"] = {k: {"perf": v["perf"], "ci": v["ci"]}
                    for k, v in entry_res.items()}

    # 이하 전부 다음날 시가 기준
    top_tr = entry_res["next_open"]["trades"]
    e_top = e_open

    # ---- 2. 통계적 신뢰도 --------------------------------------------------
    print(f"\n  [2] 통계적 신뢰도 — 다음날 시가 기준")
    contrib = contributions(top_tr)
    n_trades = len(V.marked(top_tr))
    print(f"    거래 {n_trades}건 = 고유 종목 {len(contrib)}개 "
          f"(종목당 평균 {n_trades / max(1, len(contrib)):.1f}회 반복)")
    print(f"    거래 단위 기대값 {P.rfmt(e_top)} · 종목당 동일 가중 "
          f"{P.rfmt(per_ticker_mean(top_tr))} · 종목 단위 95% 신뢰구간 "
          f"{fmt_ci(entry_res['next_open']['ci'])}")
    print(f"\n    기여 상위 종목 (R 합계 순)")
    print(f"    {B.wpad('종목', 10)} {V._rpad('거래', 4)} "
          f"{V._rpad('R 합계', 8)} {V._rpad('R 평균', 8)}")
    for t, n, tot, avg in contrib[:8]:
        print(f"    {B.wpad(t, 10)} {n:>4} {tot:+8.2f} {avg:+8.2f}")

    rnd_tr = [go(cs, "next_open") for cs in rnd_sets]
    rnd_e = [e for e in (expectancy(t) for t in rnd_tr) if e is not None]
    pct = (percentile_of(e_top, rnd_e)
           if (e_top is not None and rnd_e) else None)
    lo5, hi95 = pct_range(rnd_e) if rnd_e else (float("nan"),) * 2
    rnd_med = median_of(rnd_e)
    print(f"\n    무작위 대조군 {len(rnd_e)}회 (같은 날 통과 종목 풀에서 "
          f"{o['top']}개씩): 중앙값 {P.rfmt(rnd_med)} · 5~95% "
          f"[{lo5:+.2f}R, {hi95:+.2f}R]")
    edge = (e_top - rnd_med) if (e_top is not None and rnd_med is not None) \
        else None
    print(f"    → 로직 {P.rfmt(e_top)} 은 무작위 분포의 {pct_str(pct)}"
          f" (순위 기여 ≈ {P.rfmt(edge).strip()})")

    print(f"\n    기여 상위 종목을 빼고 다시 잰다 (무작위 쪽에서도 같은 종목을 뺀다)")
    leave = []
    for k in (1, 2, 3):
        drop = {c[0] for c in contrib[:k]}
        e_k = expectancy(without(top_tr, drop))
        r_k = [e for e in (expectancy(without(t, drop)) for t in rnd_tr)
               if e is not None]
        med_k = median_of(r_k)
        pct_k = percentile_of(e_k, r_k) if (e_k is not None and r_k) else None
        leave.append({"dropped": sorted(drop), "expectancy_r": e_k,
                      "random_median_r": med_k, "percentile": pct_k})
        print(f"    상위 {k}개 제외 ({', '.join(c[0] for c in contrib[:k])}): "
              f"로직 {P.rfmt(e_k)} · 무작위 중앙값 {P.rfmt(med_k)} · "
              f"{pct_str(pct_k)}")
    doc["stats"] = {"unique_tickers": len(contrib), "trades": n_trades,
                    "per_ticker_mean_r": per_ticker_mean(top_tr),
                    "contributions": contrib, "random_median_r": rnd_med,
                    "random_5_95": [lo5, hi95], "percentile": pct,
                    "leave_out": leave}

    # ---- 3. RS 동점 처리 ---------------------------------------------------
    print(f"\n  [3] RS 동점 처리 — 상위 {o['top']}을 동점 안에서 무엇으로 고르나")
    raw_cache = {(d, r["ticker"]): (r["raw"] if r.get("raw") is not None
                                    else raw_rs_asof(ohlc.get(r["ticker"], []), d))
                 for d, rows in ties.items() for r in rows}
    missing = sum(1 for v in raw_cache.values() if v is None)
    by_raw = tie_cohorts(snaps, o["top"], lambda r, d: (
        -raw_cache[(d, r["ticker"])]
        if raw_cache.get((d, r["ticker"])) is not None else float("inf")))
    e_raw = expectancy(go(by_raw, "next_open"))
    tie_rand = []
    for keys in tie_rand_keys:
        cos = tie_cohorts(snaps, o["top"],
                          lambda r, d, k=keys: k.get((d, r["ticker"]), 2.0))
        e = expectancy(go(cos, "next_open"))
        if e is not None:
            tie_rand.append(e)
    t_med = median_of(tie_rand)
    t5, t95 = pct_range(tie_rand) if tie_rand else (float("nan"),) * 2
    changed = sum(1 for (_, a), (_, b) in zip(base, by_raw)
                  if {r["ticker"] for r in a} != {r["ticker"] for r in b})
    vcp_pct = (percentile_of(e_top, tie_rand)
               if (e_top is not None and tie_rand) else None)
    raw_pct = (percentile_of(e_raw, tie_rand)
               if (e_raw is not None and tie_rand) else None)
    print(f"    VCP 점수 순 (현재)          {P.rfmt(e_top)}  · 동점 무작위 분포의 "
          f"{pct_str(vcp_pct)}")
    print(f"    RS 원점수 순                {P.rfmt(e_raw)}  · 동점 무작위 분포의 "
          f"{pct_str(raw_pct)}  (현재와 고른 종목이 다른 날 {changed}/{n_coh}일)")
    print(f"    동점 안에서 무작위 {len(tie_rand)}회   중앙값 {P.rfmt(t_med)} · "
          f"5~95% [{t5:+.2f}R, {t95:+.2f}R]")
    if missing:
        print(f"    (원점수를 못 낸 동점 후보 {missing}건 — 상장 1년 미만 등, "
              f"원점수 순에서는 맨 뒤로 보냄)")
    doc["tiebreak"] = {"vcp_r": e_top, "raw_r": e_raw,
                       "random_median_r": t_med, "random_5_95": [t5, t95],
                       "vcp_percentile": vcp_pct,
                       "raw_percentile": raw_pct,
                       "days_changed_by_raw": changed, "raw_missing": missing}

    # ---- 판정 --------------------------------------------------------------
    ci = entry_res["next_open"]["ci"]
    l2 = leave[1]
    res = {
        "next_open_positive": None if e_top is None else e_top > 0,
        "cluster_ci_positive": None if ci is None else ci[0] > 0,
        "beats_random": None if pct is None else pct >= EDGE_PCTL,
        "survives_top2": (None if l2["expectancy_r"] is None
                          else l2["expectancy_r"] > 0),
        "survives_top2_vs_random": (
            None if l2["expectancy_r"] is None or l2["random_median_r"] is None
            else l2["expectancy_r"] > l2["random_median_r"]),
    }
    print(f"\n  [판정] 결과를 보기 전에 정한 기준")
    for key, desc in CHECKS:
        print(f"    {mark(res[key])}  {desc}")
    passed = all(v is True for v in res.values())
    print(f"    → {stage} {'통과' if passed else '실패 — 지금 결론을 그대로 믿을 수 없다'}")
    doc["checks"] = res
    doc["passed"] = passed
    # 연도별·국면별로 다시 쪼갤 수 있게 거래 목록을 넘긴다 (JSON 에는 넣지 않는다)
    doc["_top_trades"] = top_tr
    doc["_rnd_trades"] = rnd_tr
    return doc


def main(argv: list[str]) -> int:
    o, _, out_json = P.parse_args([a for a in argv if a not in B.CFG])
    snaps = B.snapshots(MARKET, o["days"])
    prep = prepare(snaps, o["top"])
    base = prep["base"]
    if not base:
        print(f"  스냅샷이 2개 미만이다 (최근 {o['days']}일)")
        return 1
    tie_tickers, other = needed_tickers(prep)
    start = base[0][0]
    long_start = (date.fromisoformat(start)
                  - timedelta(days=RS_LOOKBACK_DAYS)).isoformat()
    bt = B.CFG[MARKET]["bench"]

    print(f"\n{'=' * 92}")
    print(f"  US 1단계 견고성 점검  코호트 {len(base)}개 ({start} ~ {base[-1][0]})"
          f" · 코호트당 상위 {o['top']}")
    print(f"{'=' * 92}")
    print(f"  매도 규칙: {P.rules_line(dict(o, entry='next_open'))}")
    print(f"  기준: 전량 청산 · 부트스트랩 {N_BOOT}회 · 무작위 {N_PERM}회 · "
          f"시드 {SEED}")
    print(f"  일봉 수집: 동점 후보 {len(tie_tickers)}종목(원점수용 {long_start}~)"
          f" + 그 외 {len(other)}종목 …", flush=True)
    ohlc = P.fetch_ohlc(sorted(other) + [bt], start)
    ohlc.update(P.fetch_ohlc(sorted(tie_tickers), long_start))

    doc = run_checks(snaps, ohlc, o, prep)
    doc = {k: v for k, v in doc.items() if not k.startswith("_")}
    if out_json:
        path = Path(out_json)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=2, default=str)
        print(f"\n  → {path}")
    return 0


if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main(sys.argv[1:]))
