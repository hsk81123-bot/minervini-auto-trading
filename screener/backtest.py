# -*- coding: utf-8 -*-
"""스크리너 로직 백테스트 — git에 쌓인 일별 스냅샷으로 과거 추천을 재현한다.

입력: git 이력의 docs/data/results[_kr].json (워크플로가 매일 커밋하는 통과 목록)
출력: 표준출력 리포트 (+ --json 으로 기계 판독용 사본)

설계 근거:
- 과거 추천을 되돌릴 때 지금 지표를 다시 계산하면 룩어헤드가 들어간다. 그래서
  '그 날 커밋된 파일' 자체를 읽는다. 당시 리스트에 실제로 있던 종목만, 당시
  RS·VCP 점수 그대로 쓴다. 이력이 길어지면 검증 기간도 저절로 늘어난다.
- 같은 날 두 번 커밋된 경우가 있다(수동 재실행). data_date로 묶고 날짜당 가장
  나중 커밋만 남긴다.
- 청산 규칙이 결과를 지배한다. 두 가지를 모두 낸다:
    gap  (기본) 리스트에서 처음 빠진 날 종가 매도. 실제로 운용 가능한 규칙.
    last        마지막으로 등장한 날 종가 매도. '마지막'인지는 나중에야 아는
                사실이라 사후 정보가 섞인다 — 낙관적 상한으로만 읽어야 한다.
  빠졌다 다시 들어오는 종목이 많아(reentries) 두 규칙의 차이가 크다.
- 가격은 yfinance 수정종가(분할·배당 반영)를 쓴다. 스냅샷의 price는 미수정가라
  진입·청산을 섞으면 분할 종목에서 가짜 수익이 난다. 그래서 수정종가를 받으면
  진입·청산 양쪽을 거기서 읽고, 한쪽이라도 없으면 측정 불가로 둔다.
  --no-prices는 스냅샷 가격만 쓰는 오프라인 모드로, gap 청산일은 리스트에 없는
  날이라 가격이 없다. 그래서 이 모드는 rule=last로 강제한다.
- 생존 편향을 따로 측정한다. '지금 리스트에 남아있는 종목의 수익률'은 구조적으로
  부풀려진 숫자다. 시나리오 B로 분리해 성과가 아니라 편향의 크기로 읽게 한다.
- 수수료·슬리피지·배당·세금 미반영. 동일 비중, 종가 단일 가격.
- 평균은 한 종목이 지배할 수 있어 중앙값을 함께 낸다. 둘이 벌어지면 중앙값을
  중심값으로 읽는다.

사용법:
    python screener/backtest.py                 # 미국, 최근 30일
    python screener/backtest.py kr              # 한국
    python screener/backtest.py us --days 90    # 기간 지정
    python screener/backtest.py us --rule last  # 사후 규칙(낙관적 상한)
    python screener/backtest.py us --no-prices  # 네트워크 없이(스냅샷 종가만)
    python screener/backtest.py us --top 20     # 오늘의 추천 상위 20 출력
    python screener/backtest.py us --json bt.json
"""
import json
import subprocess
import sys
import unicodedata
from datetime import date, timedelta
from pathlib import Path
from statistics import mean, median

ROOT = Path(__file__).resolve().parent.parent

CFG = {
    "us": {
        "rel": "docs/data/results.json",
        "fund": "docs/data/fundamentals.json",
        "bench": "^GSPC",
        "bench_name": "S&P 500",
    },
    "kr": {
        "rel": "docs/data/results_kr.json",
        "fund": "docs/data/fundamentals_kr.json",
        "bench": "^KS11",
        "bench_name": "KOSPI",
    },
}

DEFAULT_DAYS = 30
TOPN = (20, 50)      # 상위 N 부분집합
VCP_CUT = 50         # VCP 점수 필터 기준
COMMIT_SLACK = 14    # 커밋일 ≥ data_date 이므로 이만큼 여유를 두고 자른다


# ---------------------------------------------------------------- git 스냅샷
def git(*args: str) -> str:
    r = subprocess.run(["git", "-C", str(ROOT), *args],
                       capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout


def commit_shas(rel: str, since: date) -> list[str]:
    """해당 파일을 건드린 커밋 (최신순).

    커밋 시각은 항상 data_date 이후다(장 마감 후에 돌리므로). 커밋일이 창
    시작보다 한참 앞이면 data_date도 창 밖이니, 여유를 두고 잘라낸다. 이력이
    몇 년 쌓여도 읽는 파일 수가 창 길이에 비례해 유지된다.
    """
    cut = (since - timedelta(days=COMMIT_SLACK)).isoformat()
    out = git("log", f"--since={cut}", "--format=%H", "--", rel)
    return [l.strip() for l in out.splitlines() if l.strip()]


def parse_snapshot(doc: dict) -> dict | None:
    """스냅샷 문서 → {date, rows: {ticker: {name, rs, vcp, price, pivot, vstop}}}

    pivot·vstop은 그 날 스크리너가 찍은 VCP 돌파 기준가·손절가다. price와 같은
    날 같은 미수정가 기준이라 괴리율(price / pivot)을 섞임 없이 잴 수 있다.
    """
    d = doc.get("data_date")
    rows = doc.get("results")
    if not isinstance(d, str) or not isinstance(rows, list):
        return None
    out: dict[str, dict] = {}
    for r in rows:
        t = r.get("ticker")
        if not t:
            continue
        vcp = r.get("vcp") or {}
        out[t] = {
            "name": r.get("name") or t,
            "rs": r.get("rs_rank"),
            "vcp": vcp.get("score"),
            "price": r.get("price"),
            "pivot": vcp.get("pivot"),
            "vstop": vcp.get("stop"),
        }
    return {"date": d, "rows": out}


def snapshots(market: str, days: int) -> list[dict]:
    """창 안의 스냅샷을 날짜 오름차순으로. data_date 중복은 최신 커밋만."""
    rel = CFG[market]["rel"]
    start = (date.today() - timedelta(days=days)).isoformat()
    seen: dict[str, dict] = {}
    for sha in commit_shas(rel, date.today() - timedelta(days=days)):
        try:
            doc = json.loads(git("show", f"{sha}:{rel}"))
        except (RuntimeError, json.JSONDecodeError):
            continue          # 그 커밋에 파일이 없거나 깨진 경우 — 건너뛴다
        snap = parse_snapshot(doc)
        if snap and snap["date"] not in seen:   # 최신순이라 먼저 본 쪽이 최신
            seen[snap["date"]] = snap
    return [s for d, s in sorted(seen.items()) if d >= start]


def snapshot_prices(snaps: list[dict]) -> dict[str, dict[str, float]]:
    """스냅샷에 적힌 종가 {ticker: {date: price}} — 오프라인 모드용(미수정가)."""
    out: dict[str, dict[str, float]] = {}
    for s in snaps:
        for t, info in s["rows"].items():
            p = info.get("price")
            if isinstance(p, (int, float)) and p > 0:
                out.setdefault(t, {})[s["date"]] = float(p)
    return out


# ---------------------------------------------------------------- 포지션
def reentries(dates: list[str], seen: set[str]) -> int:
    """빠졌다가 다시 들어온 횟수."""
    flags = [d in seen for d in dates]
    return sum(1 for i in range(1, len(flags)) if flags[i] and not flags[i - 1])


def positions(snaps: list[dict], rule: str) -> list[dict]:
    """진입 스냅샷의 전 종목에 청산일을 매긴다 (가격은 아직 안 붙인다)."""
    dates = [s["date"] for s in snaps]
    entry_d, final_d = dates[0], dates[-1]
    out = []
    for t, info in snaps[0]["rows"].items():
        seen = {s["date"] for s in snaps if t in s["rows"]}
        if rule == "gap":
            # 연속 등장이 끊긴 첫 날에 매도. 끝까지 붙어 있으면 보유 중.
            exit_d, held = final_d, True
            for d in dates[1:]:
                if d not in seen:
                    exit_d, held = d, False
                    break
        else:
            last = max(seen)
            exit_d, held = last, last == final_d
        out.append({
            "ticker": t,
            "name": info["name"],
            "rs": info["rs"],
            "vcp": info["vcp"],
            "entry_date": entry_d,
            "exit_date": exit_d,
            "held": held,                      # 창 끝까지 보유 중
            "survivor": final_d in seen,       # 마지막 스냅샷에 남아있다
            "appearances": len(seen),
            "reentries": reentries(dates, seen),
            "days": (date.fromisoformat(exit_d)
                     - date.fromisoformat(entry_d)).days,
        })
    out.sort(key=rank_key)
    return out


def rank_key(p: dict) -> tuple:
    """웹앱과 같은 정렬 — RS 내림, 같으면 VCP 내림 (screener.py와 동일)."""
    return (-(p["rs"] or 0), -(p["vcp"] or 0), p["ticker"])


# ---------------------------------------------------------------- 가격
def closes_from(df, ticker: str) -> dict[str, float]:
    """yf.download 결과에서 한 종목의 종가 시계열을 뽑는다.

    group_by="ticker" 면 열이 (종목, 항목) 2단이지만, 배치에 종목이 하나면
    버전에 따라 단층으로 올 때가 있다. 양쪽을 다 받는다.
    """
    for key in ((ticker, "Close"), "Close"):
        try:
            s = df[key]
        except (KeyError, TypeError, IndexError):
            continue
        if s is None or getattr(s, "ndim", 1) != 1:
            continue
        s = s.dropna()
        if len(s):
            return {str(getattr(k, "date", lambda: k)()): float(v)
                    for k, v in s.items()}
    return {}


def fetch_prices(tickers: list[str], start: str, end: str
                 ) -> dict[str, dict[str, float]]:
    """yfinance 수정종가 {ticker: {date: close}}. 실패한 종목은 빠진다."""
    import time

    import yfinance as yf

    uniq = sorted(set(tickers))
    stop = (date.fromisoformat(end) + timedelta(days=1)).isoformat()  # end 배타
    out: dict[str, dict[str, float]] = {}
    for i in range(0, len(uniq), 100):
        batch = uniq[i:i + 100]
        df = yf.download(batch, start=start, end=stop, interval="1d",
                         auto_adjust=True, group_by="ticker",
                         threads=True, progress=False)
        for t in batch:
            series = closes_from(df, t)
            if series:
                out[t] = series
        print(f"  가격 {min(i + 100, len(uniq))}/{len(uniq)}", flush=True)
        time.sleep(0.5)
    if not out:
        print("  경고: 가격을 한 건도 받지 못했다 (네트워크·차단 확인). "
              "--no-prices 로 스냅샷 종가만 쓸 수 있다.", flush=True)
    return out


def close_at(series: dict[str, float], d: str) -> float | None:
    """그 날 종가. 휴장·거래정지면 직전 종가. 그보다 앞선 자료가 없으면 None."""
    if not series:
        return None
    if d in series:
        return series[d]
    prior = [k for k in series if k <= d]
    return series[max(prior)] if prior else None


def apply_prices(pos: list[dict], prices: dict[str, dict[str, float]]) -> None:
    """포지션에 진입·청산가와 수익률을 채운다. 한쪽이라도 없으면 ret=None."""
    for p in pos:
        s = prices.get(p["ticker"], {})
        e = close_at(s, p["entry_date"])
        x = close_at(s, p["exit_date"])
        p["entry_price"], p["exit_price"] = e, x
        p["ret"] = (x / e - 1.0) if (e and x and e > 0) else None


# ---------------------------------------------------------------- 집계
def stats(rows: list[dict], bench: float | None = None) -> dict:
    """부분집합 1개의 요약. 측정 불가 종목은 배제하고 개수로만 남긴다."""
    rets = [p["ret"] for p in rows if p["ret"] is not None]
    d: dict = {"n": len(rows), "measured": len(rets),
               "unmeasured": len(rows) - len(rets)}
    if not rets:
        return d
    d["mean"] = mean(rets)
    d["median"] = median(rets)
    d["win"] = sum(1 for r in rets if r > 0) / len(rets)
    d["days"] = mean([p["days"] for p in rows if p["ret"] is not None])
    if bench is not None:
        d["excess"] = d["mean"] - bench
    return d


def subsets(pos: list[dict]) -> list[tuple[str, list[dict]]]:
    """리포트에 낼 부분집합들. pos는 이미 rank_key 순으로 정렬돼 있다."""
    out: list[tuple[str, list[dict]]] = [(f"전체 {len(pos)}종목", pos)]
    for n in TOPN:
        if len(pos) > n:
            out.append((f"상위 {n} (RS→VCP)", pos[:n]))
    hi = [p for p in pos if (p["vcp"] or 0) >= VCP_CUT]
    if hi:
        out.append((f"VCP ≥ {VCP_CUT} ({len(hi)}종목)", hi))
    return out


def bias(pos: list[dict]) -> dict:
    """생존 편향 = 남은 종목 평균 − 빠진 종목 평균."""
    sv = stats([p for p in pos if p["survivor"]])
    dr = stats([p for p in pos if not p["survivor"]])
    gap = (sv.get("mean", 0.0) - dr.get("mean", 0.0)
           if "mean" in sv and "mean" in dr else None)
    return {"survivors": sv, "dropouts": dr, "gap": gap}


def coverage_skew(pos: list[dict]) -> float | None:
    """측정률이 생존과 상관되는지 — 남은 종목 측정률 − 빠진 종목 측정률.

    가격을 일부만 구하면 보통 '아직 상장·거래 중인 종목'만 채워진다. 그 상태로
    평균을 내면 생존 편향이 측정 단계에서 한 번 더 들어간다. 눈에 보이게 낸다.
    """
    sv = [p for p in pos if p["survivor"]]
    dr = [p for p in pos if not p["survivor"]]
    if not sv or not dr:
        return None
    rate = lambda g: sum(1 for p in g if p["ret"] is not None) / len(g)
    return rate(sv) - rate(dr)


# ---------------------------------------------------------------- 출력
def wpad(text: str, width: int) -> str:
    """표시 너비로 자르고 채운다 — 한글·한자는 한 글자가 두 칸이다."""
    out, used = [], 0
    for c in text:
        cw = 2 if unicodedata.east_asian_width(c) in "WF" else 1
        if used + cw > width:
            break
        out.append(c)
        used += cw
    return "".join(out) + " " * (width - used)


def pct(v: float | None, sign: bool = True) -> str:
    if v is None:
        return "    -   "
    return f"{v * 100:+8.2f}%" if sign else f"{v * 100:7.1f}%"


def print_row(label: str, s: dict) -> None:
    if "mean" not in s:
        print(f"  {wpad(label, 24)} 측정 불가 ({s['n']}종목)")
        return
    line = (f"  {wpad(label, 24)} 평균 {pct(s['mean'])}  중앙값 {pct(s['median'])}"
            f"  승률 {s['win'] * 100:5.1f}%  n={s['measured']}")
    if "excess" in s:
        line += f"  초과 {pct(s['excess'])}"
    print(line)


def print_extremes(pos: list[dict], k: int = 5) -> None:
    done = sorted([p for p in pos if p["ret"] is not None],
                  key=lambda p: p["ret"], reverse=True)
    if not done:
        return
    for title, rows in (("최고", done[:k]), ("최저", done[-k:][::-1])):
        s = "  ".join(f"{p['ticker']} {p['ret'] * 100:+.0f}%" for p in rows)
        print(f"  {title} {k}: {s}")




def report(market: str, days: int, rule: str, snaps: list[dict],
           pos: list[dict], bench: float | None, offline: bool) -> dict:
    """리포트를 찍고, 같은 내용을 JSON으로 쓸 수 있게 되돌린다."""
    cfg = CFG[market]
    entry_d, final_d = snaps[0]["date"], snaps[-1]["date"]
    rule_note = ("리스트에서 처음 빠진 날 매도 (사후 정보 없음)" if rule == "gap"
                 else "마지막 등장일 매도 — 사후 정보 포함, 낙관적 상한")
    re_n = sum(1 for p in pos if p["reentries"])

    print(f"\n{'=' * 72}")
    print(f"  {market.upper()} 로직 백테스트  {entry_d} → {final_d} "
          f"({(date.fromisoformat(final_d) - date.fromisoformat(entry_d)).days}일)")
    print(f"{'=' * 72}")
    print(f"  스냅샷 {len(snaps)}개 · 진입 {len(pos)}종목 · 동일 비중 · 종가")
    print(f"  청산 규칙: {rule}  — {rule_note}")
    print(f"  가격: {'스냅샷 종가(미수정)' if offline else 'yfinance 수정종가'}")
    print(f"  벤치마크 {cfg['bench_name']}: {pct(bench)}")
    print(f"  빠졌다 재진입한 종목: {re_n}/{len(pos)}  "
          f"(규칙 간 차이가 이만큼 생긴다)")
    print(f"  수수료·슬리피지·배당·세금 미반영\n")

    print("  [시나리오 A] 규칙대로 운용 — 실제 성과로 읽는 숫자")
    subs = subsets(pos)
    for label, rows in subs:
        print_row(label, stats(rows, bench))
    print()
    print_extremes(pos)
    print(f"  평균 보유 {stats(pos).get('days', 0):.1f}일 · "
          f"측정 불가 {stats(pos)['unmeasured']}/{len(pos)}종목")
    skew = coverage_skew(pos)
    if skew is not None and abs(skew) > 0.10:
        print(f"  경고: 측정률이 생존과 {skew * 100:+.0f}%p 상관됐다. 가격을 구한"
              f" 종목이 한쪽에 쏠려 위 평균 자체가 편향됐다 — 숫자를 믿지 마라.")

    b = bias(pos)
    print("\n  [시나리오 B] 생존 편향 측정 — 성과로 읽으면 안 되는 숫자")
    print_row("끝까지 남은 종목", b["survivors"])
    print_row("중간에 빠진 종목", b["dropouts"])
    if b["gap"] is not None:
        print(f"  → 생존 편향 {pct(b['gap'])}  "
              f"('지금 리스트의 수익률'이 이만큼 부풀려진다)")

    print(f"\n{'-' * 72}")
    print("  한 달은 전략 검증에 턱없이 짧다. 상승·하락 국면이 모두 든 구간이")
    print("  아니면 추세추종 로직의 약점이 드러나지 않는다. 스냅샷이 쌓이는 만큼")
    print("  --days 를 늘려 다시 돌려야 의미가 생긴다.")
    print(f"{'-' * 72}\n")

    return {
        "market": market,
        "days": days,
        "rule": rule,
        "offline": offline,
        "entry_date": entry_d,
        "exit_date": final_d,
        "snapshots": len(snaps),
        "benchmark": {"ticker": cfg["bench"], "name": cfg["bench_name"],
                      "return": bench},
        "reentry_count": re_n,
        "scenario_a": {label: stats(rows, bench) for label, rows in subs},
        "scenario_b": b,
        "coverage_skew": coverage_skew(pos),
        "positions": pos,
    }


def print_top(market: str, n: int) -> None:
    """오늘(최신 스냅샷)의 추천 상위 N — 작업 디렉터리의 현재 파일을 읽는다."""
    cfg = CFG[market]
    try:
        doc = json.loads((ROOT / cfg["rel"]).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"  {cfg['rel']} 를 읽을 수 없다: {e}")
        return
    snap = parse_snapshot(doc)
    if not snap:
        print(f"  {cfg['rel']} 에 data_date·results 가 없다")
        return
    fund_path = ROOT / cfg["fund"]
    fund = {}
    if fund_path.exists():
        try:
            fund = json.loads(fund_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    rows = sorted(
        [{"ticker": t, **v} for t, v in snap["rows"].items()], key=rank_key)
    print(f"\n  {market.upper()} 추천 리스트 {snap['date']} "
          f"(통과 {len(rows)}종목) 상위 {n}")
    print(f"  {'#':>3}  {wpad('종목', 26)} {'RS':>3} {'VCP':>4} "
          f"{wpad('코드33', 6)}")
    for i, r in enumerate(rows[:n], 1):
        c33 = (fund.get(r["ticker"]) or {}).get("code33")
        label = wpad(f"{r['ticker']} {r['name']}", 26)
        print(f"  {i:>3}  {label} {r['rs'] or 0:>3} {r['vcp'] or 0:>4} "
              f"{'-' if c33 is None else round(c33):>6}")
    if not fund:
        print("  * 코드33은 펀더멘털 수집분에만 붙는다 — 아직 전 종목이 아니다")


# ---------------------------------------------------------------- 진입점
def main(argv: list[str]) -> int:
    market = "us"
    days, rule, offline, out_json, top = DEFAULT_DAYS, "gap", False, None, 0
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in CFG:
            market = a
        elif a == "--days" and i + 1 < len(argv):
            i += 1
            days = int(argv[i])
        elif a == "--rule" and i + 1 < len(argv):
            i += 1
            rule = argv[i]
        elif a == "--no-prices":
            offline = True
        elif a == "--json" and i + 1 < len(argv):
            i += 1
            out_json = argv[i]
        elif a == "--top" and i + 1 < len(argv):
            i += 1
            top = int(argv[i])
        else:
            sys.exit(f"알 수 없는 인자: {a}\n{__doc__}")
        i += 1
    if rule not in ("gap", "last"):
        sys.exit(f"--rule 은 gap|last (받은 값: {rule})")
    if offline and rule == "gap":
        # gap 청산일은 그 종목이 리스트에 없는 날이라 스냅샷에 가격이 없다.
        print("  --no-prices 는 gap 청산가를 구할 수 없다 → rule=last 로 전환")
        rule = "last"

    if top:
        print_top(market, top)

    snaps = snapshots(market, days)
    if len(snaps) < 2:
        print(f"\n스냅샷이 {len(snaps)}개뿐이다 — 최근 {days}일로는 백테스트할 "
              f"수 없다. --days 를 늘려라.")
        return 1

    pos = positions(snaps, rule)
    entry_d, final_d = snaps[0]["date"], snaps[-1]["date"]
    bench = None
    if offline:
        apply_prices(pos, snapshot_prices(snaps))
    else:
        bt = CFG[market]["bench"]
        print(f"  수정종가 수집 {len(pos)}종목 + 벤치마크 …", flush=True)
        prices = fetch_prices([p["ticker"] for p in pos] + [bt],
                              entry_d, final_d)
        apply_prices(pos, prices)
        be = close_at(prices.get(bt, {}), entry_d)
        bx = close_at(prices.get(bt, {}), final_d)
        bench = (bx / be - 1.0) if (be and bx and be > 0) else None

    doc = report(market, days, rule, snaps, pos, bench, offline)
    if out_json:
        path = Path(out_json)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=2)
        print(f"  → {path}")
    return 0


if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main(sys.argv[1:]))
