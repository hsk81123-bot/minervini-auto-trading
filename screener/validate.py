# -*- coding: utf-8 -*-
"""규칙 엄격 검증 — 한 번의 운을 전략으로 착각하지 않기 위한 방침을 코드로 박는다.

입력: git 이력의 docs/data/results[_kr].json + yfinance 일봉
출력: 코호트 집계 · 무작위 대조군 · 파라미터 격자

왜 이 파일이 따로 있는가:
  papertrade.py 한 번 실행은 '진입일 하나'의 결과다. 진입일을 바꾸면 숫자가
  크게 움직이므로, 좋은 날을 골라 보고하면 아무 규칙이나 좋아 보인다. 여기서는
  고를 수 없게 만든다.

검증 방침 (전부 이 파일이 강제한다):
  1. 진입일을 고르지 않는다 (코호트/워크포워드)
     스냅샷 전체를 차례로 진입일로 써서 같은 규칙을 반복한다. 보고하는 숫자는
     모든 코호트를 합친 것이고, 코호트별 분산도 함께 낸다. 특정 날짜만 좋았다면
     분산에서 드러난다.
  2. 무작위 대조군을 둔다
     같은 날 '통과 종목에서 무작위 N개'를 뽑아 같은 규칙으로 굴린다. 상위 N이
     무작위보다 낫지 않으면 RS 순위는 값을 더하지 않은 것이다. 통과 필터 자체의
     기여와 순위의 기여를 분리한다.
  3. 파라미터 하나에 기대지 않는다 (민감도 격자)
     손절·익절을 격자로 훑어 기대값 지도를 만든다. 한 칸만 좋고 이웃이 나쁘면
     그건 과최적화다. 이웃까지 양수인 영역을 찾는 것이 목적이다.
  4. 표본이 적으면 판정을 내지 않는다
     MIN_TRADES 미만이면 수치는 내되 '판정 보류'로 표시한다. 10거래로 승률을
     말하는 것은 통계가 아니다.
  5. 벤치마크와 비용을 항상 함께 둔다
     같은 기간 지수 수익률을 옆에 적는다. 비용은 papertrade.py가 반영한다.
  6. 미래를 쓰지 않는다
     진입 종목은 그 날 커밋된 스냅샷에서만, 손절선은 전일까지의 고가로만.
     (backtest.py / papertrade.py가 같은 규칙을 공유한다)
  7. 끝나지 않은 거래를 성과로 세지 않는다
     아직 보유 중인 거래는 따로 집계한다. 미실현 이익을 실현 성과에 섞으면
     최근 상승분이 그대로 수익률로 들어온다.

사용법:
    python screener/validate.py                      # 미국, 기본 규칙
    python screener/validate.py kr
    python screener/validate.py us --stop -8 --trail -15 --no-target
    python screener/validate.py us --top 5 --every 3     # 3스냅샷마다 진입
    python screener/validate.py us --grid               # 파라미터 격자까지
    python screener/validate.py us --days 90 --json val.json
"""
import json
import random
import sys
from pathlib import Path
from statistics import mean, median, pstdev

sys.path.insert(0, str(Path(__file__).resolve().parent))
import backtest as B
import papertrade as P

MIN_TRADES = 30          # 이 미만이면 판정 보류
SEED = 20261011          # 무작위 대조군 재현용
GRID_STOPS = (-5.0, -8.0, -12.0)
GRID_TARGETS = (15.0, 25.0, 40.0, None)   # None = 고정 익절 없음


# ---------------------------------------------------------------- 코호트
def cohorts(market: str, days: int, top: int, every: int
            ) -> list[tuple[str, list[dict]]]:
    """스냅샷마다 (진입일, 상위 N) — 진입일을 고를 수 없게 전부 쓴다.

    마지막 스냅샷은 진입 후 경과일이 0이라 제외한다.
    """
    snaps = B.snapshots(market, days)
    out = []
    for s in snaps[:-1][::every] if every > 1 else snaps[:-1]:
        rows = sorted(({"ticker": t, **v} for t, v in s["rows"].items()),
                      key=B.rank_key)
        out.append((s["date"], rows[:top]))
    return out


def random_cohorts(market: str, days: int, top: int, every: int,
                   seed: int = SEED) -> list[tuple[str, list[dict]]]:
    """같은 진입일, 같은 종목 수 — 다만 통과 종목에서 무작위로 뽑는다.

    통과 필터(트렌드 템플릿 8조건) 자체의 기여는 양쪽에 똑같이 들어가므로,
    차이는 RS→VCP 순위가 더한 값이다.
    """
    rng = random.Random(seed)
    snaps = B.snapshots(market, days)
    out = []
    for s in snaps[:-1][::every] if every > 1 else snaps[:-1]:
        rows = [{"ticker": t, **v} for t, v in s["rows"].items()]
        out.append((s["date"], rng.sample(rows, min(top, len(rows)))))
    return out


def run(cohort: list[tuple[str, list[dict]]], market: str,
        ohlc: dict[str, list[dict]], o: dict) -> list[dict]:
    """코호트 전체를 같은 규칙으로 굴려 거래 목록을 만든다."""
    trades = []
    for entry_date, rows in cohort:
        for r in rows:
            sim = P.simulate(ohlc.get(r["ticker"], []), entry_date,
                             o["stop"], o["target"], o["trail"],
                             o["breakeven"], o["time-stop"])
            if sim["status"] == "측정 불가":
                continue
            trades.append({"market": market, "ticker": r["ticker"],
                           "cohort": entry_date, "rs": r["rs"],
                           "vcp": r["vcp"], **sim})
    return trades


def split_closed(trades: list[dict]) -> tuple[list[dict], list[dict]]:
    """실현 거래와 보유 중 거래를 나눈다 (방침 7)."""
    closed = [t for t in trades if t["status"] != "보유"]
    open_ = [t for t in trades if t["status"] == "보유"]
    return closed, open_


def by_cohort(trades: list[dict]) -> dict[str, float]:
    """코호트별 기대값 — 특정 진입일만 좋았는지 본다 (방침 1)."""
    g: dict[str, list[float]] = {}
    for t in trades:
        if t.get("r_multiple") is not None:
            g.setdefault(t["cohort"], []).append(t["r_multiple"])
    return {d: mean(v) for d, v in sorted(g.items())}


# ---------------------------------------------------------------- 출력
def verdict(m: dict) -> str:
    """표본이 모자라면 판정을 내지 않는다 (방침 4)."""
    n = m.get("measured", 0)
    if n < MIN_TRADES:
        return f"판정 보류 — 실현 {n}거래 (최소 {MIN_TRADES})"
    e = m.get("expectancy_r")
    if e is None:
        return "판정 불가"
    if e > 0.2:
        return "기대값 양수 — 굴릴 수 있다"
    if e > 0:
        return "기대값 미약 — 비용·슬리피지에 지워질 수 있다"
    return "기대값 음수 — 이 규칙으로는 굴릴 수 없다"


def show(label: str, trades: list[dict], bench: float | None = None) -> dict:
    closed, open_ = split_closed(trades)
    m = P.perf(closed)
    print(f"\n  [{label}]  실현 {len(closed)}거래 · 보유 중 {len(open_)}건")
    if "expectancy_r" not in m:
        print(f"    실현 거래 없음")
        return {"perf": m, "open": len(open_)}
    print(f"    기대값 {P.rfmt(m['expectancy_r'])}/거래 · 중앙값 "
          f"{P.rfmt(m['median_r'])} · 승률 {m['win_rate'] * 100:.1f}%")
    print(f"    실측 손익비 "
          + ("정의 불가" if m["payoff"] is None else f"{m['payoff']:.2f}:1")
          + f" (평균이익 {P.rfmt(m['avg_win_r'])} / 평균손실 "
          + f"{P.rfmt(m['avg_loss_r'])})")
    print(f"    최고 {P.rfmt(m['best_r'])} · 최저 {P.rfmt(m['worst_r'])} · "
          f"평균 최대역행 {B.pct(m['avg_mae'])}")
    ck = by_cohort(closed)
    if len(ck) > 1:
        vals = list(ck.values())
        pos = sum(1 for v in vals if v > 0)
        print(f"    코호트 {len(ck)}개 중 기대값 양수 {pos}개 · 코호트 평균 "
              f"{P.rfmt(mean(vals))} · 표준편차 {pstdev(vals):.2f}R")
    if bench is not None:
        print(f"    같은 기간 벤치마크 {B.pct(bench)}")
    print(f"    → {verdict(m)}")
    if open_:
        om = P.perf(open_)
        print(f"    (보유 중 {len(open_)}건은 위 수치에서 제외 — 평가 "
              f"{P.rfmt(om.get('expectancy_r'))})")
    return {"perf": m, "open": len(open_), "by_cohort": ck,
            "verdict": verdict(m)}


def grid(cohort, market, ohlc, o) -> list[dict]:
    """손절·익절 격자. 한 칸만 좋으면 과최적화다 (방침 3)."""
    print(f"\n  [파라미터 민감도]  이웃 칸까지 양수인 영역을 찾는다")
    hdr = "  " + "손절\\익절".ljust(12) + "".join(
        f"{('없음' if t is None else f'+{t:.0f}%'):>12}" for t in GRID_TARGETS)
    print(hdr)
    rows = []
    for st in GRID_STOPS:
        cells = []
        for tg in GRID_TARGETS:
            oo = dict(o, stop=st, target=tg)
            closed, _ = split_closed(run(cohort, market, ohlc, oo))
            m = P.perf(closed)
            e = m.get("expectancy_r")
            cells.append(f"{e:+11.2f}R" if e is not None else "          -")
            rows.append({"stop": st, "target": tg,
                         "expectancy_r": e, "trades": len(closed),
                         "payoff": m.get("payoff")})
        print(f"  {st:+.0f}%".ljust(14) + "".join(cells))
    return rows


# ---------------------------------------------------------------- 진입점
def main(argv: list[str]) -> int:
    market = "us"
    rest, every, do_grid = [], 1, False
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in B.CFG:
            market = a
        elif a == "--every" and i + 1 < len(argv):
            i += 1
            every = max(1, int(argv[i]))
        elif a == "--grid":
            do_grid = True
        else:
            rest.append(a)
        i += 1
    o, _, out_json = P.parse_args([x for x in rest])

    cos = cohorts(market, o["days"], o["top"], every)
    if not cos:
        print(f"  {market}: 최근 {o['days']}일 스냅샷이 2개 미만이다")
        return 1
    rnd = random_cohorts(market, o["days"], o["top"], every)

    tickers = {r["ticker"] for _, rows in cos + rnd for r in rows}
    bt = B.CFG[market]["bench"]
    start = cos[0][0]
    print(f"\n{'=' * 92}")
    print(f"  {market.upper()} 규칙 엄격 검증  코호트 {len(cos)}개 "
          f"({start} ~ {cos[-1][0]}) · 코호트당 상위 {o['top']}종목")
    print(f"{'=' * 92}")
    print(f"  규칙: {P.rules_line(o)}")
    print(f"  비용: 편도 {P.FEE_BPS:.0f}bp + 한국 매도세 "
          f"{P.TAX_BPS['kr']:.0f}bp (R-배수는 비용 전 가격 기준)")
    print(f"  {len(tickers)}종목 일봉 수집 …", flush=True)
    ohlc = P.fetch_ohlc(sorted(tickers) + [bt], start)

    bench = None
    bs = ohlc.get(bt, [])
    if bs:
        bench = bs[-1]["close"] / bs[0]["close"] - 1.0

    top_tr = run(cos, market, ohlc, o)
    rnd_tr = run(rnd, market, ohlc, o)
    a = show(f"상위 {o['top']} — 로직 적용", top_tr, bench)
    b = show(f"무작위 {o['top']} — 통과 종목 중 무작위 (대조군)", rnd_tr, bench)

    ea = a["perf"].get("expectancy_r")
    eb = b["perf"].get("expectancy_r")
    if ea is not None and eb is not None:
        diff = ea - eb
        print(f"\n  [순위의 기여]  상위 {ea:+.2f}R − 무작위 {eb:+.2f}R = "
              f"{diff:+.2f}R/거래")
        if diff <= 0:
            print(f"    RS→VCP 순위가 무작위보다 낫지 않다. 통과 필터만 쓰고")
            print(f"    순위는 버리거나 다시 설계해야 한다.")
        elif a["perf"]["measured"] < MIN_TRADES:
            print(f"    차이는 양수지만 표본이 모자라 아직 판정할 수 없다.")
        else:
            print(f"    순위가 값을 더한다 — 다만 비용·슬리피지를 넘는지는 "
                  f"기대값 절대 크기로 봐야 한다.")

    g = grid(cos, market, ohlc, o) if do_grid else None
    if g:
        pos = [c for c in g if (c["expectancy_r"] or -1) > 0]
        print(f"\n    기대값 양수 칸 {len(pos)}/{len(g)}개"
              + ("  — 한두 칸만 양수면 과최적화를 의심해야 한다"
                 if 0 < len(pos) <= 2 else ""))

    doc = {"market": market, "rules": {k: o[k] for k in P.DEFAULTS},
           "cohorts": len(cos), "every": every, "min_trades": MIN_TRADES,
           "seed": SEED, "benchmark": {"ticker": bt, "return": bench},
           "top": a, "random": b, "rank_edge_r": (ea - eb)
           if (ea is not None and eb is not None) else None, "grid": g}
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
